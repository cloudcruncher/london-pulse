"""Brand tracker: multi-site names (curated London favourites plus repeated names) from the FSA register.

Matching is by premises name, so it is a good guide, not a company register: sites can be franchised, trade under
a company name, or be missing if registered with another name. Counts are premises on the FSA register today.
"""

import json
import re

# (id, display name, kind, regex on the normalised name: lower case, curly quotes straightened)
CURATED = [
    ("caravan", "Caravan", "Specialty coffee", r"^caravan\b"),
    ("monmouth", "Monmouth Coffee", "Specialty coffee", r"^monmouth coffee"),
    ("workshop", "Workshop Coffee", "Specialty coffee", r"^workshop coffee"),
    (
        "allpress",
        "Allpress Espresso",
        "Specialty coffee",
        r"^allpress (espresso|coffee)",
    ),
    ("grind", "Grind", "Specialty coffee", r"^grind\b"),
    ("origin", "Origin Coffee", "Specialty coffee", r"^origin coffee"),
    ("notes", "Notes Coffee", "Specialty coffee", r"^notes( coffee)?$|^notes coffee"),
    ("redemption", "Redemption Roasters", "Specialty coffee", r"^redemption roasters"),
    (
        "dept-coffee",
        "Department of Coffee",
        "Specialty coffee",
        r"^department of coffee",
    ),
    ("kiss-hippo", "Kiss the Hippo", "Specialty coffee", r"^kiss the hippo"),
    ("harris-hoole", "Harris + Hoole", "Specialty coffee", r"^harris (\+|and|&) hoole"),
    (
        "store-street",
        "Store Street Espresso",
        "Specialty coffee",
        r"^store street espresso",
    ),
    ("rosslyn", "Rosslyn Coffee", "Specialty coffee", r"^rosslyn coffee"),
    ("perky", "Perky Blenders", "Specialty coffee", r"^perky blenders"),
    ("square-mile", "Square Mile Coffee", "Specialty coffee", r"^square mile coffee"),
    ("dark-arts", "Dark Arts Coffee", "Specialty coffee", r"^dark arts coffee"),
    ("ozone", "Ozone Coffee", "Specialty coffee", r"^ozone coffee"),
    ("climpson", "Climpson & Sons", "Specialty coffee", r"^climpson"),
    ("beavertown", "Beavertown", "Craft beer", r"^beavertown"),
    ("camden-town", "Camden Town Brewery", "Craft beer", r"^camden town brewery"),
    ("fourpure", "Fourpure", "Craft beer", r"^fourpure"),
    ("five-points", "Five Points", "Craft beer", r"^five points"),
    ("brewhouse-kitchen", "Brewhouse & Kitchen", "Craft beer", r"^brewhouse (&|and) kitchen"),
    ("black-sheep", "Black Sheep Coffee", "Coffee chain", r"^black sheep coffee"),
    ("blank-street", "Blank Street", "Coffee chain", r"^blank street"),
    ("esquires", "Esquires Coffee", "Coffee chain", r"^esquires"),
    ("gails", "GAIL's", "Bakery", r"^gail(s|'s)\b"),
    ("pavilion", "Pavilion Bakery", "Bakery", r"^pavilion bakery"),
    ("e5", "E5 Bakehouse", "Bakery", r"^e5 (bakehouse|poplar)"),
    ("dishoom", "Dishoom", "Restaurant group", r"^dishoom"),
    ("franco-manca", "Franco Manca", "Restaurant group", r"^franco manca"),
    ("granger", "Granger & Co", "Restaurant group", r"^granger (&|and) co"),
    ("joe-juice", "Joe & the Juice", "Restaurant group", r"^joe (&|and) the juice"),
    ("brewdog", "BrewDog", "Craft beer", r"^brewdog"),
    ("pret", "Pret A Manger", "Chain", r"^pret( a manger)?\b"),
    ("costa", "Costa Coffee", "Chain", r"^costa( coffee)?$|^costa coffee"),
    ("starbucks", "Starbucks", "Chain", r"^starbucks"),
    ("caffe-nero", "Caffè Nero", "Chain", r"^(caffe?|cafe) nero"),
    ("greggs", "Greggs", "Chain", r"^greggs"),
    ("subway", "Subway", "Chain", r"^subway\b"),
    ("mcdonalds", "McDonald's", "Chain", r"^mcdonald'?s\b"),
    ("nandos", "Nando's", "Chain", r"^nando'?s\b"),
    ("dominos", "Domino's", "Chain", r"^domino'?s"),
    ("pizza-express", "Pizza Express", "Chain", r"^pizza express"),
    ("pizza-hut", "Pizza Hut", "Chain", r"^pizza hut"),
    ("itsu", "itsu", "Chain", r"^itsu\b"),
    ("five-guys", "Five Guys", "Chain", r"^five guys"),
    ("wagamama", "Wagamama", "Chain", r"^wagamama"),
]
BRAND_TYPES = (
    "Restaurant/Cafe/Canteen",
    "Takeaway/sandwich shop",
    "Pub/bar/nightclub",
    "Retailers - other",
    "Other catering premises",
)
AUTO_TYPES = BRAND_TYPES[
    :2
]  # repeated-name detection: skip pubs (many pubs simply share a name like "Red Lion")
AUTO_MIN_SITES = 3
MAX_SITES = 400


def norm(name: str) -> str:
    s = (name or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(ltd|limited|uk|london)\b\.?", " ", s)
    s = re.sub(r"[^a-z0-9&+' ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def build_brands(con, api_dir, meta: dict) -> None:
    """Needs view `s` (the snapshot). Writes brands.json."""
    # name normalisation in SQL (mirrors norm() below), so no Python UDF / numpy is needed
    con.execute(r"""CREATE OR REPLACE TEMP MACRO lp_norm(x) AS trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
        replace(replace(lower(x), '’', ''''), '‘', ''''), '\(.*?\)', ' ', 'g'),
        '\b(ltd|limited|uk|london)\b\.?', ' ', 'g'), '[^a-z0-9&+'' ]', ' ', 'g'), '\s+', ' ', 'g'))""")
    bt = ",".join(f"'{t}'" for t in BRAND_TYPES)
    at = ",".join(f"'{t}'" for t in AUTO_TYPES)
    con.execute(
        f"CREATE OR REPLACE TEMP VIEW bn AS SELECT *, lp_norm(name) AS nn FROM s WHERE business_type IN ({bt})"
    )
    brands = []

    def summarise(bid, name, kind, curated, where, params):
        cur = con.execute(
            f"""SELECT name, postcode, rating, authority, lon, lat, business_type FROM bn WHERE {where}
            ORDER BY authority, name""",
            params,
        )
        sites = cur.fetchall()
        if not sites:
            return
        by_b: dict[str, int] = {}
        for r in sites:
            by_b[r[3]] = by_b.get(r[3], 0) + 1
        rated = [int(r[2]) for r in sites if r[2] in "012345" and len(r[2]) == 1]
        brands.append(
            {
                "id": bid,
                "name": name,
                "kind": kind,
                "curated": curated,
                "n": len(sites),
                "boroughs": len(by_b),
                "top": sorted(by_b.items(), key=lambda kv: -kv[1])[:8],
                "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
                "five_star_pct": round(
                    100 * sum(1 for x in rated if x == 5) / len(rated), 1
                )
                if rated
                else None,
                "sites": [list(r) for r in sites[:MAX_SITES]],
            }
        )

    for bid, name, kind, rx in CURATED:
        summarise(bid, name, kind, True, "regexp_matches(nn, ?)", [rx])
        if brands and brands[-1]["id"] == bid:
            # SQL lab form of the same match, over the raw name (the lab has no lp_norm)
            brands[-1]["sql"] = (
                "regexp_matches(lower(replace(replace(name, chr(8217), chr(39)), chr(8216), chr(39))), '"
                + rx.replace("'", "''")
                + "')"
            )
    taken = {b["id"] for b in brands}
    covered = "|".join(rx for *_, rx in CURATED)
    rep = con.execute(
        f"""SELECT nn, count(*) n, mode(name) disp FROM bn WHERE business_type IN ({at}) AND length(nn) >= 4
        AND NOT regexp_matches(nn, ?) GROUP BY 1 HAVING count(*) >= {AUTO_MIN_SITES} ORDER BY n DESC LIMIT 400""",
        [covered],
    ).fetchall()
    for nn, _, disp in rep:
        bid = "r-" + re.sub(r"[^a-z0-9]+", "-", nn)
        if bid not in taken:
            summarise(
                bid,
                disp,
                "Repeated name",
                False,
                f"nn = ? AND business_type IN ({at})",
                [nn],
            )
            if brands and brands[-1]["id"] == bid:
                brands[-1]["sql"] = (
                    "lower(name) = '" + disp.lower().replace("'", "''") + "'"
                )
    brands.sort(key=lambda b: -b["n"])
    (api_dir / "brands.json").write_text(
        json.dumps(
            {**meta, "brands": brands},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    )
