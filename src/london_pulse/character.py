"""What kind of place each area is becoming: venue mix, specialty and independent signals, fresh supply, pace.

Built daily from the FSA snapshot (name and business-type keywords, so every share is a proxy, not a survey).
The "stage" of an area compares two things per postcode district:
  scene  = specialty coffee, bakeries, wine/craft and plant-based places per 100 eating and drinking venues
  fresh  = share of venues still awaiting their first inspection (new premises or a change of operator)
and the quadrant they fall in: established, hot and growing, emerging, or steady. It is a signal, not a verdict.
"""

import json
from datetime import date
from pathlib import Path

from .brands import CURATED

MIN_DISTRICT_VENUES = 150
MIN_SHARE_N = 8  # a category needs this many venues in an area before it can be called a signature
SIGNATURE_LQ = 1.5  # at least 1.5x London's share
COFFEE_RE = "coffee|espresso|roastery|barista|caffe|caffè"
PACE_MIN_DAYS = 14  # do not call a trend from less than two weeks of daily snapshots

# category id -> (label, regex on the lower-cased name). Order is display order.
CATEGORIES = {
    "specialty_coffee": (
        "Specialty coffee",
        "|".join(rx for _, _, k, rx in CURATED if k == "Specialty coffee")
        + "|roastery|roasters",
    ),
    "coffee": ("Coffee shops", COFFEE_RE),
    "bakery": (
        "Bakeries",
        r"bakery|bakehouse|patisserie|boulangerie|sourdough|bagel|viennoiserie",
    ),
    "wine_craft": (
        "Wine, craft and cocktails",
        r"wine|vino|taproom|brewery|brewing|craft beer|cocktail|distill|\bgin\b",
    ),
    "plant_based": ("Plant-based", r"vegan|plant|veggie|vegetarian"),
    "pizza": ("Pizza", r"pizz"),
    "chicken": ("Chicken shops", r"chicken|wings\b"),
    "grill_kebab": (
        "Kebab and grill",
        r"kebab|shawarma|doner|mangal|ocakbasi|\bgrill\b|falafel|lebanese|turkish|persian",
    ),
    "south_asian": (
        "South Asian",
        r"indian|curry|tandoor|biryani|balti|dosa|tikka|masala|punjabi|bangla|pakistan",
    ),
    "east_asian": (
        "East Asian",
        r"chinese|noodle|dim sum|\bwok\b|ramen|sushi|thai|korean|japan|\bpho\b|vietnam|\bbao\b|izakaya",
    ),
    "caribbean_african": (
        "Caribbean and African",
        r"caribbean|jerk|african|nigerian|ethiopian|ghana|jamaican|\bpatty|afro",
    ),
    "italian": ("Italian", r"italian|trattoria|osteria|pasta|ristorante|pizzeria"),
    "sweet": (
        "Desserts and bubble tea",
        r"bubble|boba|dessert|ice cream|gelato|waffle|crepe|cake|donut|doughnut",
    ),
}
SCENE = ("specialty_coffee", "bakery", "wine_craft", "plant_based")
CHAIN_RE = "|".join(
    rx for _, _, kind, rx in CURATED if kind in ("Chain", "Coffee chain")
)


def _tier(scene_pct: float, fresh_pct: float) -> str:
    """Quadrant from percentile ranks (0-100) of the scene and fresh-supply measures."""
    if scene_pct >= 75 and fresh_pct >= 60:
        return "Hot and still growing"
    if scene_pct >= 75:
        return "Established scene"
    if fresh_pct >= 75:
        return "Emerging"
    return "Steady"


def _pace(con, history_csv: Path, events_dir: Path) -> dict:
    """Net change in London's eating and drinking premises over the daily history we have so far."""
    out = {"days_covered": 0, "verdict": None}
    if not history_csv.exists():
        return out
    h = con.execute(f"""SELECT snapshot_date d, sum(eating_drinking) ed FROM read_csv('{history_csv}', header=true)
                        GROUP BY 1 ORDER BY 1""").fetchall()
    out["days_covered"] = len(h)
    if len(h) >= 2:
        span = (h[-1][0] - h[0][0]).days
        out.update(
            first=str(h[0][0]),
            last=str(h[-1][0]),
            net=int(h[-1][1] - h[0][1]),
            span_days=span,
        )
    if len(h) >= PACE_MIN_DAYS:
        half = len(h) // 2
        a = (h[half][1] - h[0][1]) / max(1, half)
        b = (h[-1][1] - h[half][1]) / max(1, len(h) - 1 - half)
        out["per_day_first_half"], out["per_day_second_half"] = round(a, 1), round(b, 1)
        out["verdict"] = (
            "accelerating" if b > a * 1.15 else "slowing" if b < a * 0.85 else "steady"
        )
    return out


def build_character(
    con, api_dir: Path, meta: dict, history_csv: Path, events_dir: Path
) -> None:
    """Expects views `fd` (eating and drinking premises with coordinates filled) and `s` from insights.build."""

    def q(rx: str) -> str:  # some patterns hold an apostrophe (mcdonald'?s)
        return rx.replace("'", "''")

    cases = ",\n".join(
        f"regexp_matches(n, '{q(rx)}') AS c_{k}" for k, (_, rx) in CATEGORIES.items()
    )
    con.execute(f"""CREATE OR REPLACE TABLE ch AS
        SELECT authority, upper(split_part(postcode, ' ', 1)) AS district, rating,
               business_type = 'Pub/bar/nightclub' AS is_pub,
               regexp_matches(n, '{q(CHAIN_RE)}') AS is_chain,
               {cases}
        FROM (SELECT *, lower(replace(replace(name, '’', ''''), '‘', '''')) AS n FROM fd) """)
    ks = list(CATEGORIES)
    agg = ",\n".join(f"sum(c_{k}::INT) AS {k}" for k in ks)
    base = f"""count(*) AS n, sum(is_chain::INT) AS chain, sum(is_pub::INT) AS pubs,
               count(*) FILTER (rating = 'AwaitingInspection') AS awaiting,
               count(*) FILTER (rating = '5') AS five, count(*) FILTER (rating ~ '^[0-5]$') AS rated, {agg}"""

    def table(group: str, min_n: int, where: str = "") -> list[dict]:
        cur = con.execute(
            f"SELECT {group} AS name, {base} FROM ch {where} GROUP BY 1 HAVING count(*) >= {min_n}"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    london = con.execute(f"SELECT {base} FROM ch").fetchone()
    lcols = [d[0] for d in con.description]
    lon = dict(zip(lcols, london))
    lon_share = {k: lon[k] / lon["n"] for k in ks}

    def profile(r: dict) -> dict:
        n = r["n"]
        mix = []
        for k in ks:
            share = r[k] / n
            lq = share / lon_share[k] if lon_share[k] else 0
            mix.append(
                {
                    "id": k,
                    "label": CATEGORIES[k][0],
                    "n": int(r[k]),
                    "pct": round(100 * share, 1),
                    "lq": round(lq, 2),
                }
            )
        sig = sorted(
            (
                m
                for m in mix
                if m["n"] >= MIN_SHARE_N
                and m["lq"] >= SIGNATURE_LQ
                and m["id"] != "coffee"
            ),
            key=lambda m: -m["lq"],
        )[:3]
        scene = sum(r[k] for k in SCENE) / n * 100
        return {
            "name": r["name"],
            "venues": int(n),
            "mix": mix,
            "signature": sig,
            "scene_per_100": round(scene, 1),
            "chain_pct": round(100 * r["chain"] / n, 1),
            "independent_pct": round(100 - 100 * r["chain"] / n, 1),
            "fresh_pct": round(100 * r["awaiting"] / n, 1),
            "pubs_pct": round(100 * r["pubs"] / n, 1),
            "five_star_pct": round(100 * r["five"] / r["rated"], 1)
            if r["rated"]
            else None,
        }

    def rank_pct(values: list[float], v: float) -> float:
        return 100.0 * sum(1 for x in values if x <= v) / len(values)

    boroughs = [profile(r) for r in table("authority", 1)]
    districts = [
        profile(r)
        for r in table("district", MIN_DISTRICT_VENUES, "WHERE district <> ''")
    ]
    for group in (boroughs, districts):
        scenes, fresh = (
            [g["scene_per_100"] for g in group],
            [g["fresh_pct"] for g in group],
        )
        for g in group:
            g["scene_rank"], g["fresh_rank"] = (
                round(rank_pct(scenes, g["scene_per_100"])),
                round(rank_pct(fresh, g["fresh_pct"])),
            )
            g["stage"] = _tier(g["scene_rank"], g["fresh_rank"])
    boroughs.sort(key=lambda g: -g["venues"])
    districts.sort(key=lambda g: -g["venues"])

    pace = _pace(con, history_csv, events_dir)
    headlines = _headlines(boroughs, districts, lon, pace)
    london_profile = {
        "venues": int(lon["n"]),
        "chain_pct": round(100 * lon["chain"] / lon["n"], 1),
        "fresh_pct": round(100 * lon["awaiting"] / lon["n"], 1),
        "scene_per_100": round(sum(lon[k] for k in SCENE) / lon["n"] * 100, 1),
    }
    payload = {
        "london": london_profile,
        "headlines": headlines,
        "pace": pace,
        "boroughs": boroughs,
        "districts": districts,
        "method": "Keyword matches on premises names within eating and drinking premises, and share of premises awaiting "
        "their first FSA inspection. Proxies: a name can mislead, and awaiting inspection also covers changes of operator.",
    }
    (api_dir / "character.json").write_text(
        json.dumps({**meta, **payload}, ensure_ascii=False, separators=(",", ":"))
    )


def _headlines(
    boroughs: list[dict], districts: list[dict], lon: dict, pace: dict
) -> list[dict]:
    """Plain-English findings, each backed by the numbers it quotes. `where` is a tab or area the reader can open."""
    out: list[dict] = []

    def best(group, cat, min_n=15):
        c = [(g, next(m for m in g["mix"] if m["id"] == cat)) for g in group]
        c = [(g, m) for g, m in c if m["n"] >= min_n]
        return max(c, key=lambda x: x[1]["lq"]) if c else None

    for cat, tmpl in (
        (
            "specialty_coffee",
            "{a} has London's highest concentration of specialty coffee: {n} venues, {lq}x the London rate.",
        ),
        ("bakery", "{a} leads on bakeries and bagel shops: {lq}x the London rate."),
        (
            "wine_craft",
            "{a} has the most wine, craft and cocktail bars for its size ({lq}x London).",
        ),
        ("plant_based", "{a} is where plant-based places cluster ({lq}x London)."),
    ):
        b = best(boroughs, cat, 10 if cat != "bakery" else 20)
        if b:
            out.append(
                {
                    "kind": "signature",
                    "text": tmpl.format(a=b[0]["name"], n=b[1]["n"], lq=b[1]["lq"]),
                    "area": b[0]["name"],
                }
            )
    hot = [d for d in districts if d["stage"] == "Hot and still growing"]
    if hot:
        top = max(hot, key=lambda d: d["scene_per_100"] + d["fresh_pct"])
        out.append(
            {
                "kind": "stage",
                "area": top["name"],
                "text": f"{top['name']} looks like a scene that is still growing: {top['scene_per_100']} specialty, bakery, craft or "
                f"plant-based places per 100 venues and {top['fresh_pct']}% of venues awaiting a first inspection (new premises, new operators or backlog).",
            }
        )
    emerging = sorted(
        (d for d in districts if d["stage"] == "Emerging"),
        key=lambda d: -d["fresh_pct"],
    )[:3]
    if emerging:
        out.append(
            {
                "kind": "stage",
                "area": emerging[0]["name"],
                "text": "Emerging, lots of openings but the scene has not formed yet: "
                + ", ".join(d["name"] for d in emerging)
                + ".",
            }
        )
    est = sorted(
        (d for d in districts if d["stage"] == "Established scene"),
        key=lambda d: -d["scene_per_100"],
    )[:3]
    if est:
        out.append(
            {
                "kind": "stage",
                "area": est[0]["name"],
                "text": "Established scenes with little new supply: "
                + ", ".join(d["name"] for d in est)
                + ".",
            }
        )
    ch = sorted(boroughs, key=lambda g: -g["chain_pct"])
    out.append(
        {
            "kind": "chains",
            "area": ch[0]["name"],
            "text": f"{ch[0]['name']} is the most chain-heavy borough, by the national chains we track ({ch[0]['chain_pct']}% of venues) and {ch[-1]['name']} the least "
            f"({ch[-1]['chain_pct']}%). London overall: {round(100 * lon['chain'] / lon['n'], 1)}%.",
        }
    )
    fr = max(districts, key=lambda d: d["fresh_pct"])
    out.append(
        {
            "kind": "fresh",
            "area": fr["name"],
            "text": f"{fr['name']} has the freshest supply: {fr['fresh_pct']}% of its {fr['venues']:,} venues are awaiting a first inspection.",
        }
    )
    if pace.get("verdict"):
        out.append(
            {
                "kind": "pace",
                "text": f"London's eating and drinking supply is {pace['verdict']}: {pace['per_day_first_half']} net new premises a day "
                f"early in the window, {pace['per_day_second_half']} a day recently.",
            }
        )
    else:
        out.append(
            {
                "kind": "pace",
                "text": f"Pace of change needs {PACE_MIN_DAYS} days of daily snapshots to call a trend; {pace.get('days_covered', 0)} so far.",
            }
        )
    return out
