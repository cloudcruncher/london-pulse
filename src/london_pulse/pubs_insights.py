"""Analysis of who owns London's pubs: site/api/v1/pubs-insights.json, built from pubs.json.

build() is pure (inputs in, dict out). The loaders around it read the other inputs, each optional:
- FSA hygiene ratings by fhrsid (pubs.json's own `rating` field when present, else work/latest.parquet);
- the LSOA each pub sits in (point-in-polygon on work/reference/lsoa.geojson, else the nearest LSOA centroid in areas.json),
  giving the IMD 2025 decile and the ONS MSOA net income after housing costs from areas.json;
- borough land areas (sum of the London LSOA polygons, in British National Grid square metres).
A missing input makes its metrics null; nothing is filled in or guessed.

Shares are of *known* pubs (those with a group or a likely-independent label) unless the key says `_of_total`.
Groups are far easier to identify than independents (they have brands, websites and Wikidata entries), so the share of
known pubs held by groups overstates the true share; `_of_total` gives the floor.

Churn is null: the pub list comes from OpenStreetMap, which lags new openings and keeps closed pubs, so FSA new/removed
events joined to it by fhrsid are lopsided (in the first 44 days of events: 1 new against 18 removed) and say more about
OSM's lag than about which owners are opening or closing pubs.

Run: python -m london_pulse.pubs_insights
"""

import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

RATINGS = ["5", "4", "3", "2", "1", "0"]
GROUPED = ("pe", "corp", "family")  # categories that mean "owned by a group"
DECILES = range(1, 11)
LICENCE = "ODbL 1.0 (contains OpenStreetMap data) and OGL v3.0"
SOURCE = (
    "pubs.json (OpenStreetMap contributors, ODbL; FSA and Camden Council, OGL v3.0); FSA Food Hygiene Rating Scheme (OGL v3.0); "
    "English Indices of Deprivation 2025 and ONS small-area income estimates via areas.json (OGL v3.0)"
)
METHOD = (
    "Counts of pubs.json by category, group and borough. Shares are of pubs with a known owner type (a group, or likely independent); "
    "HHI is the sum of squared group shares of known pubs, with each independent counted as its own owner. Ratings are the FSA "
    "hygiene rating (0 to 5) of the linked FSA record. Deprivation and income are those of the LSOA each pub stands in "
    "(IMD 2025 decile, 1 = most deprived) and its MSOA (ONS net household income after housing costs), averaged over pubs. "
    "ex_camden repeats the figures without Camden, whose open licence register finds independents no other borough can."
)
NOTES = [
    "'No group link found' (unknown) is not the same as independent: most of those pubs simply have no open record of their owner.",
    "Groups are easier to identify than independents, so shares of known pubs overstate groups; the _of_total figures are the floor.",
    "Independent labels are 'likely': no group link from any source plus a positive sign of independence.",
    "Hygiene ratings measure food safety on the day of inspection, not the quality of the pub or its beer.",
    ("Deprivation and income describe the small area a pub stands in, not its customers. Income is the ONS MSOA estimate of "
     "net household income after housing costs (an MSOA is a neighbourhood of about 8,000 people)."),
    "Cask figures count only pubs whose OpenStreetMap entry says whether they serve real ale.",
    "Churn is left out: OpenStreetMap lags openings and keeps closed pubs, so FSA opening and closing events joined to it are lopsided.",
]


def _share(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _mean(xs: list[float], nd: int = 2) -> float | None:
    return round(sum(xs) / len(xs), nd) if xs else None


def _hhi(group_counts: Counter, indie: int) -> float | None:
    """Herfindahl index (0 to 1) of known pubs: groups by size, each independent pub its own owner."""
    known = sum(group_counts.values()) + indie
    if not known:
        return None
    return round(
        sum((n / known) ** 2 for n in group_counts.values()) + indie / known**2, 3
    )


def _rating_block(rs: list[str | None]) -> dict:
    nums = [int(r) for r in rs if r in RATINGS]
    return {
        "rating_avg": _mean(nums),
        "rating_n": len(nums),
        "rating_dist": {k: sum(1 for r in rs if r == k) for k in RATINGS},
    }


def norm_borough(name: str) -> str:
    """FSA authority names ('Richmond-Upon-Thames', 'City of London Corporation') to the ONS names areas.json uses."""
    return name.lower().replace("-", " ").removesuffix(" corporation").strip()


def build(
    pubs: dict,
    *,
    ratings: dict[int, str] | None = None,
    pub_lsoa: list[str | None] | None = None,
    areas: dict | None = None,
    borough_km2: dict[str, float] | None = None,
    today: date | None = None,
) -> dict:
    """pubs: pubs.json. ratings: fhrsid -> FSA rating string, used when pubs.json has no `rating` field.
    pub_lsoa: LSOA code per pub row (same order as pubs['pubs']). areas: areas.json. borough_km2: normalised borough name -> km2."""
    cats = list(pubs["categories"])
    F = pubs["fields"]
    rows = [dict(zip(F, r)) for r in pubs["pubs"]]
    has_rating = "rating" in F
    for r in rows:
        if not has_rating:
            r["rating"] = (
                ratings.get(r["fhrsid"])
                if ratings is not None and r["fhrsid"]
                else None
            )
    rating_known = has_rating or ratings is not None

    lsoa_info: dict[str, tuple] = {}
    if areas:
        A = areas["fields"]
        ic, idec, iinc = (
            A.index("code"),
            A.index("imd_decile"),
            A.index("net_income_ahc"),
        )
        lsoa_info = {a[ic]: (a[idec], a[iinc]) for a in areas["areas"]}
    geo_known = bool(pub_lsoa) and bool(lsoa_info)
    for i, r in enumerate(rows):
        info = lsoa_info.get(pub_lsoa[i]) if geo_known and pub_lsoa[i] else None
        r["imd"], r["income"] = info if info else (None, None)

    def summary(rs: list[dict]) -> dict:
        by_cat = Counter(r["category"] for r in rs)
        groups = Counter(
            r["group"] for r in rs if r["group"] and r["category"] in GROUPED
        )
        grouped = sum(by_cat[c] for c in GROUPED)
        known = grouped + by_cat["indie"]
        return {
            "by_cat": {c: by_cat.get(c, 0) for c in cats},
            "groups": groups,
            "grouped": grouped,
            "known": known,
            "indie": by_cat["indie"],
            "cask": sum(1 for r in rs if r["cask"] == 1),
        }

    s = summary(rows)
    total = len(rows)
    top = s["groups"].most_common()
    london = {
        "total": total,
        "by_category": s["by_cat"],
        "known": s["known"],
        "group_share": _share(s["grouped"], s["known"]),
        "group_share_of_total": _share(s["grouped"], total),
        "indie_share_of_known": _share(s["indie"], s["known"]),
        "cask": s["cask"],
        "cask_known": sum(1 for r in rows if r["cask"] is not None),
        "top_groups": [
            {
                "id": g,
                "n": n,
                "share": _share(n, s["known"]),
                "share_of_total": _share(n, total),
            }
            for g, n in top[:10]
        ],
        "hhi": _hhi(s["groups"], s["indie"]),
        "top5_share": _share(sum(n for _, n in top[:5]), s["known"]),
        "top5_share_of_total": _share(sum(n for _, n in top[:5]), total),
    }
    is_camden = [norm_borough(r["borough"]) == "camden" for r in rows]
    ex_rows = [r for r, c in zip(rows, is_camden) if not c]
    xs = summary(ex_rows)
    london["ex_camden"] = {
        "total": len(ex_rows),
        "known": xs["known"],
        "group_share": _share(xs["grouped"], xs["known"]),
        "indie_n": xs["indie"],
    }
    camden_indie = s["indie"] - xs["indie"]
    notes = [*NOTES]
    if s["indie"]:
        notes.insert(
            3,
            f"{camden_indie} of the {s['indie']} likely independents are in Camden, the only borough that publishes its licence "
            "register, so borough and owner-type comparisons partly reflect where evidence exists; ex_camden figures leave it out.",
        )

    by_b: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_b[r["borough"]].append(r)
    boroughs = []
    for b in sorted(by_b):
        rs = by_b[b]
        bs = summary(rs)
        tg = bs["groups"].most_common(1)
        rb = _rating_block([r["rating"] for r in rs])
        km2 = borough_km2.get(norm_borough(b)) if borough_km2 else None
        boroughs.append(
            {
                "borough": b,
                "total": len(rs),
                "by_category": bs["by_cat"],
                "known": bs["known"],
                "group_share": _share(bs["grouped"], bs["known"]),
                "indie_n": bs["indie"],
                "cask_n": bs["cask"],
                "top_group": tg[0][0] if tg else None,
                "top_group_n": tg[0][1] if tg else 0,
                "hhi": _hhi(bs["groups"], bs["indie"]),
                "groups": dict(bs["groups"].most_common()),
                "imd_decile_avg": _mean([r["imd"] for r in rs if r["imd"] is not None])
                if geo_known
                else None,
                "income_avg": _mean(
                    [r["income"] for r in rs if r["income"] is not None], 0
                )
                if geo_known
                else None,
                "rating_avg": rb["rating_avg"] if rating_known else None,
                "rating_n": rb["rating_n"] if rating_known else 0,
                "pubs_per_km2": round(len(rs) / km2, 2) if km2 else None,
            }
        )

    def profile(rs: list[dict]) -> dict:
        rb = (
            _rating_block([r["rating"] for r in rs])
            if rating_known
            else {"rating_avg": None, "rating_n": 0, "rating_dist": None}
        )
        return {
            "n": len(rs),
            "cask_n": sum(1 for r in rs if r["cask"] == 1),
            "cask_known": sum(1 for r in rs if r["cask"] is not None),
            **rb,
        }

    by_g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["group"]:
            by_g[r["group"]].append(r)
    groups = []
    for g, rs in sorted(by_g.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        bc = Counter(r["borough"] for r in rs)
        groups.append(
            {
                "id": g,
                "category": rs[0]["category"],
                **profile(rs),
                "boroughs": len(bc),
                "top_boroughs": [
                    [b, n]
                    for b, n in sorted(bc.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
                ],
            }
        )

    def cat_profile(rs: list[dict]) -> dict:
        out = {}
        for c in cats:
            x = [r for r in rs if r["category"] == c]
            imd = (
                _mean([r["imd"] for r in x if r["imd"] is not None])
                if geo_known
                else None
            )
            out[c] = {**profile(x), "imd_decile_avg": imd}
        return out

    by_category = cat_profile(rows)
    by_category_ex_camden = cat_profile(ex_rows)

    deprivation = None
    if geo_known:
        deprivation = []
        for d in DECILES:
            rs = [r for r in rows if r["imd"] == d]
            deprivation.append(
                {
                    "decile": d,
                    "total": len(rs),
                    "by_category": {
                        c: sum(1 for r in rs if r["category"] == c) for c in cats
                    },
                }
            )

    return {
        "schema_version": 1,
        "as_of": pubs["as_of"],
        "generated": (today or date.today()).isoformat(),
        "licence": LICENCE,
        "source": SOURCE,
        "method": METHOD,
        "notes": notes,
        "inputs": {
            "ratings": rating_known,
            "deprivation": geo_known,
            "borough_areas": bool(borough_km2),
            "pubs_located": sum(1 for r in rows if r["imd"] is not None),
        },
        "categories": pubs["categories"],
        "london": london,
        "boroughs": boroughs,
        "groups": groups,
        "by_category": {**by_category, "ex_camden": by_category_ex_camden},
        "deprivation": deprivation,
        "churn": None,
    }


# ---- loaders (impure) -------------------------------------------------------------------------------------------------


def load_ratings(latest_parquet: Path) -> dict[int, str] | None:
    if not latest_parquet.exists():
        return None
    import duckdb

    return dict(
        duckdb.sql(
            f"SELECT fhrsid, rating FROM '{latest_parquet}' WHERE fhrsid IS NOT NULL"
        ).fetchall()
    )


def _polygons(pubs: dict, london: dict[str, str], lsoa_geojson: Path) -> tuple[list[str | None], dict[str, float]]:
    """Point-in-polygon LSOA per pub and borough km2 (LSOA areas in British National Grid), via DuckDB spatial."""
    import duckdb

    F = pubs["fields"]
    ilon, ilat = F.index("lon"), F.index("lat")
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"""CREATE TABLE lsoa AS SELECT f.properties.LSOA21CD AS code, ST_GeomFromGeoJSON(f.geometry) AS geom
                    FROM (SELECT unnest(features) AS f FROM read_json('{lsoa_geojson}'))""")
    con.execute("CREATE TABLE p (i INT, lon DOUBLE, lat DOUBLE)")
    con.executemany("INSERT INTO p VALUES (?, ?, ?)", [(i, r[ilon], r[ilat]) for i, r in enumerate(pubs["pubs"])])
    hit = dict(con.execute(
        "SELECT p.i, min(l.code) FROM p JOIN lsoa l ON ST_Contains(l.geom, ST_Point(p.lon, p.lat)) GROUP BY 1").fetchall())
    codes = [hit.get(i) if hit.get(i) in london else None for i in range(len(pubs["pubs"]))]
    km2: dict[str, float] = defaultdict(float)
    for code, m2 in con.execute(
        "SELECT code, ST_Area(ST_Transform(geom, 'EPSG:4326', 'EPSG:27700', always_xy := true)) FROM lsoa").fetchall():
        if code in london:
            km2[norm_borough(london[code])] += m2 / 1e6
    return codes, dict(km2)


def locate(pubs: dict, areas: dict, lsoa_geojson: Path) -> tuple[list[str | None], dict[str, float] | None]:
    """LSOA code per pub, plus borough km2 by normalised name (None without polygons).
    Uses the LSOA polygons when present and DuckDB spatial works; otherwise the nearest LSOA centroid in areas.json."""
    F = pubs["fields"]
    ilon, ilat = F.index("lon"), F.index("lat")
    A = areas["fields"]
    ic, ib, ilo, ila = A.index("code"), A.index("borough"), A.index("lon"), A.index("lat")
    london = {a[ic]: a[ib] for a in areas["areas"]}
    if lsoa_geojson.exists():
        try:
            return _polygons(pubs, london, lsoa_geojson)
        except Exception as e:  # noqa: BLE001  (extension download, bad geometry: centroids still give a usable answer)
            print(f"LSOA polygons unusable ({type(e).__name__}: {e}); using nearest LSOA centroids")
    # equirectangular distance is fine at London's scale
    cents = [(a[ic], a[ilo], a[ila]) for a in areas["areas"]]
    k = math.cos(math.radians(51.5))
    codes = [min(cents, key=lambda c: ((c[1] - r[ilon]) * k) ** 2 + (c[2] - r[ilat]) ** 2)[0] for r in pubs["pubs"]]
    return codes, None


def main(root: Path, geo: bool = True) -> dict:
    """geo=False skips the LSOA lookup and borough areas (deprivation, income and density become null)."""
    api, work = root / "site" / "api" / "v1", root / "work"
    pubs = json.loads((api / "pubs.json").read_text())
    areas_p = api / "areas.json"
    areas = json.loads(areas_p.read_text()) if geo and areas_p.exists() else None
    pub_lsoa, km2 = locate(pubs, areas, work / "reference" / "lsoa.geojson") if areas else (None, None)
    ratings = None if "rating" in pubs["fields"] else load_ratings(work / "latest.parquet")
    out = build(pubs, ratings=ratings, pub_lsoa=pub_lsoa, areas=areas, borough_km2=km2)
    (api / "pubs-insights.json").write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    lo = out["london"]
    print(f"pubs-insights.json: {lo['total']} pubs, known {lo['known']}, group share {lo['group_share']}, hhi {lo['hhi']}, inputs {out['inputs']}")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-geo", action="store_true", help="skip the LSOA lookup (deprivation, income and density become null)")
    main(Path(__file__).resolve().parents[2], geo=not ap.parse_args().no_geo)
