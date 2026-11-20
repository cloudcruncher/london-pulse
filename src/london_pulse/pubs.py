"""Pubs ownership layer: every London pub (OpenStreetMap) labelled with the group that owns or runs it.

Sources (all open, see docs/PUBS-SPEC.md): OSM amenity=pub via Overpass (ODbL), the FSA register already in
work/latest.parquet (OGL), Camden's premises-licence register (OGL), a curated group table (pub_groups.py) and
reviewed corrections (data/pubs_overrides.csv).

Method in short:
  * London filter: an OSM pub is kept if it links to a London FSA record, or lies within 400 m of any London FSA
    premises (cheap and robust: the FSA register covers every food premises, so anywhere with no FSA premises within
    400 m is outside London or open ground). Borough = FSA authority of the linked record, else of the nearest premises.
  * FSA link: OSM fhrs:id when that id is on the register, else the nearest "Pub/bar/nightclub" within 60 m whose
    name matches (normalised token overlap). One FSA record is linked to at most one pub.
  * Camden licence -> pub: nearest pub within 40 m with a matching name, or the same postcode and a matching name.
  * Group: override > group matches on OSM operator/brand/wikidata, Camden licensee and name. "confirmed" only when two
    independent source families agree on a group (OSM tags or name, and the Camden licensee) or a reviewed override
    says so; tags on the same OSM object, however many, give "likely". Conflicting groups: licensee (the current legal
    holder) beats operator beats brand beats the rest.
  * Independent ("indie", "likely"): no group matched and the OSM operator or Camden licensee is a name that appears
    on <= 3 London pubs (or the operator is literally "Free House"). Everything else is "unknown": not a claim of
    independence.
"""

import argparse
import csv
import difflib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import duckdb

OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_UA = "london-pulse/0.1 (open-data research)"
OVERPASS_QUERY = '[out:json][timeout:100][bbox:51.28,-0.52,51.70,0.34];nwr["amenity"="pub"];out center tags;'
CAMDEN = "https://opendata.camden.gov.uk/resource/rv9z-rsui.json"
OSM_CACHE = "osm_pubs.json"
CAMDEN_CACHE = "camden_licences.json"
CACHE_DAYS = 7

FSA_PUB_TYPE = "Pub/bar/nightclub"
LINK_M = 60  # OSM pub -> FSA pub
CAMDEN_M = 40  # Camden licence -> OSM pub
LONDON_M = 400  # OSM pub must be this close to some FSA premises to count as London
INDIE_MAX = 3  # an operator/licensee on at most this many London pubs reads as a small independent
# multi-site brewers/pub companies (or non-pub chains) whose current owner we could not verify: never read as independent
NOT_INDIE = re.compile(
    r"charles wells|\bwells (&|and) co|courage|wadworth|generator|british waterways|\bantic\b|castle pubs"
    r"|brunning|holiday inn|\bssp\b|heritage pubs|company secretary|^freehold$"
    r"|students? union|\bsu\b",
    re.IGNORECASE,
)
MIN_TOTAL = 2500  # sanity floor for the published file (the workflow guard uses the same number)

EVIDENCE_RANK = ["camden:licensee", "osm:operator", "osm:brand", "osm:wikidata", "name"]

SOURCES = [
    {
        "id": "osm",
        "name": "OpenStreetMap pubs (amenity=pub)",
        "publisher": "OpenStreetMap contributors",
        "licence": "ODbL 1.0",
        "attribution": "© OpenStreetMap contributors",
        "url": "https://www.openstreetmap.org/copyright",
    },
    {
        "id": "fsa",
        "name": "Food hygiene rating scheme register",
        "publisher": "Food Standards Agency",
        "licence": "OGL v3.0",
        "attribution": "Contains FSA data, Open Government Licence v3.0",
        "url": "https://ratings.food.gov.uk/open-data",
    },
    {
        "id": "camden",
        "name": "Camden licensing register (premises licences)",
        "publisher": "London Borough of Camden Council",
        "licence": "OGL v3.0",
        "attribution": "London Borough of Camden Council",
        "url": "https://opendata.camden.gov.uk/resource/rv9z-rsui",
    },
    {
        "id": "groups",
        "name": "Curated pub group table",
        "publisher": "London Pulse",
        "licence": "Short factual statements, each with its public source",
        "attribution": "London Pulse",
        "url": "https://github.com/cloudcruncher/london-pulse/blob/main/src/london_pulse/pub_groups.py",
    },
]
FIELDS = [
    "name",
    "lon",
    "lat",
    "postcode",
    "borough",
    "fhrsid",
    "osm",
    "group",
    "category",
    "confidence",
    "evidence",
    "cask",
    "craft",
    "website",
]

# ---------------------------------------------------------------- text helpers

_STOP = {
    "the",
    "pub",
    "inn",
    "bar",
    "and",
    "public",
    "house",
    "ph",
    "ltd",
    "limited",
    "of",
}
_PC = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")


def norm(s) -> str:
    """Lower case, curly quotes straightened, '&' as 'and', punctuation to spaces."""
    s = str(s or "").lower().replace("’", "'").replace("‘", "'").replace("&", " and ")
    s = s.replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def tokens(s: str) -> set[str]:
    t = norm(s).split()
    core = {w for w in t if w not in _STOP}
    return core or set(t)


def names_match(a: str, b: str) -> bool:
    """Normalised token overlap (Jaccard >= 0.5), or near-identical spelling."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    if len(ta & tb) / len(ta | tb) >= 0.5:
        return True
    return (
        difflib.SequenceMatcher(
            None, " ".join(sorted(ta)), " ".join(sorted(tb))
        ).ratio()
        >= 0.8
    )


def names_close(a: str, b: str, threshold: float = 0.8) -> bool:
    """Stricter than names_match: Jaccard of name tokens or spelling ratio >= threshold."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    if len(ta & tb) / len(ta | tb) >= threshold:
        return True
    return difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio() >= threshold


def norm_postcode(s) -> str:
    return re.sub(r"\s+", "", str(s or "").upper())


def find_postcode(s) -> str:
    m = _PC.search(str(s or "").upper())
    return m.group(1) + m.group(2) if m else ""


def fmt_postcode(pc: str) -> str:
    pc = norm_postcode(pc)
    return f"{pc[:-3]} {pc[-3:]}" if len(pc) > 3 else pc


def cask_flag(v) -> int | None:
    """OSM real_ale: yes / a positive count / free text naming beers -> 1; no / 0 -> 0; absent -> None."""
    v = norm(v)
    if not v:
        return None
    if v in ("no", "none", "never", "false", "0"):
        return 0
    if v == "unknown":
        return None
    if v.isdigit():
        return 1 if int(v) > 0 else 0
    return 1


def craft_flag(tags: dict) -> int | None:
    yes = lambda k: norm(tags.get(k)) == "yes"
    return (
        1
        if yes("craft_beer") or yes("drink:craft_beer") or yes("microbrewery")
        else None
    )


# ---------------------------------------------------------------- spatial helpers

_KX = 111_320 * math.cos(math.radians(51.5))
_KY = 110_574
_CELL = 500


def xy(lon: float, lat: float) -> tuple[float, float]:
    return lon * _KX, lat * _KY


class Grid:
    """Points bucketed in 500 m cells; within(x, y, r) yields (distance, item) for r <= 500 m, nearest first."""

    def __init__(self):
        self.cells: dict[tuple[int, int], list] = defaultdict(list)

    def add(self, lon: float, lat: float, item) -> None:
        x, y = xy(lon, lat)
        self.cells[(int(x // _CELL), int(y // _CELL))].append((x, y, item))

    def within(self, lon: float, lat: float, r: float) -> list[tuple[float, object]]:
        x, y = xy(lon, lat)
        cx, cy = int(x // _CELL), int(y // _CELL)
        out = []
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for px, py, item in self.cells.get((i, j), ()):
                    d = math.hypot(px - x, py - y)
                    if d <= r:
                        out.append((d, item))
        out.sort(key=lambda t: t[0])
        return out


# ---------------------------------------------------------------- fetching


def _post_overpass(tries: int = 5) -> dict:
    body = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    err: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                OVERPASS, data=body, headers={"User-Agent": OVERPASS_UA}
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            if "elements" not in d or "remark" in d and not d["elements"]:
                raise ValueError(f"Overpass returned no elements: {d.get('remark')}")
            return d
        except Exception as e:  # noqa: BLE001 - Overpass is rate limited and flaky
            err = e
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"Overpass failed after {tries} tries") from err


def _fresh(p: Path, refresh: bool) -> bool:
    return (
        not refresh
        and p.exists()
        and (time.time() - p.stat().st_mtime) < CACHE_DAYS * 86400
    )


def fetch_osm(ref_dir: Path, refresh: bool = False) -> dict:
    p = ref_dir / OSM_CACHE
    if _fresh(p, refresh):
        return json.loads(p.read_text())
    ref_dir.mkdir(parents=True, exist_ok=True)
    d = _post_overpass()
    p.write_text(json.dumps(d, separators=(",", ":")))
    return d


def fetch_camden(ref_dir: Path, refresh: bool = False) -> list[dict]:
    p = ref_dir / CAMDEN_CACHE
    if _fresh(p, refresh):
        return json.loads(p.read_text())
    ref_dir.mkdir(parents=True, exist_ok=True)
    where = urllib.parse.quote(
        "status='In Force' AND licence_type like '%Premises Licence%'"
    )
    rows: list[dict] = []
    page = 5000
    while True:
        url = f"{CAMDEN}?$where={where}&$order=licence_reference&$limit={page}&$offset={len(rows)}"
        err: Exception | None = None
        for i in range(4):
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": OVERPASS_UA}),
                    timeout=90,
                ) as r:
                    batch = json.load(r)
                break
            except Exception as e:  # noqa: BLE001
                err = e
                time.sleep(3 * (i + 1))
        else:
            raise RuntimeError("Camden licensing fetch failed") from err
        rows += batch
        if len(batch) < page:
            break
    if not rows:
        raise RuntimeError("Camden licensing returned no rows")
    p.write_text(json.dumps(rows, separators=(",", ":")))
    return rows


def load_fsa(parquet: Path) -> list[dict]:
    cols = "fhrsid,name,business_type,address,postcode,rating,rating_date,authority,lon,lat"
    con = duckdb.connect()
    cur = con.execute(
        f"SELECT {cols} FROM read_parquet('{parquet}') WHERE lon IS NOT NULL AND lat IS NOT NULL"
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def load_overrides(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


# ---------------------------------------------------------------- build


def osm_points(osm: dict) -> list[dict]:
    pubs = []
    for e in osm.get("elements", []):
        t = e.get("tags") or {}
        lon = e.get("lon", (e.get("center") or {}).get("lon"))
        lat = e.get("lat", (e.get("center") or {}).get("lat"))
        if lon is None or lat is None or not t.get("name"):
            continue
        fh = str(t.get("fhrs:id", "")).strip()
        wd = [
            w.strip()
            for k in ("brand:wikidata", "operator:wikidata")
            for w in str(t.get(k, "")).split(";")
            if w.strip()
        ]
        pubs.append(
            {
                "osm": f"{e['type'][0]}{e['id']}",
                "name": t["name"],
                "lon": lon,
                "lat": lat,
                "tags": t,
                "fhrs": int(fh) if fh.isdigit() else None,
                "operator": t.get("operator", ""),
                "brand": t.get("brand", ""),
                "wikidata": wd,
                "postcode": t.get("addr:postcode", ""),
                "website": t.get("website") or t.get("contact:website") or "",
            }
        )
    return pubs


def link_fsa(pubs: list[dict], fsa: list[dict]) -> None:
    """Sets pub['fsa'] (record or None), pub['near'] (nearest FSA premises within LONDON_M, or None)."""
    by_id = {r["fhrsid"]: r for r in fsa}
    pub_grid, all_grid = Grid(), Grid()
    for r in fsa:
        all_grid.add(r["lon"], r["lat"], r)
        if r["business_type"] == FSA_PUB_TYPE:
            pub_grid.add(r["lon"], r["lat"], r)
    claimed: set[int] = set()
    for p in pubs:
        p["fsa"] = None
        r = by_id.get(p["fhrs"])
        if r is not None and r["fhrsid"] not in claimed:
            p["fsa"] = r
            claimed.add(r["fhrsid"])
    for p in pubs:
        if p["fsa"] is None:
            for _d, r in pub_grid.within(p["lon"], p["lat"], LINK_M):
                if r["fhrsid"] not in claimed and names_match(p["name"], r["name"]):
                    p["fsa"] = r
                    claimed.add(r["fhrsid"])
                    break
        near = all_grid.within(p["lon"], p["lat"], LONDON_M)
        p["near"] = p["fsa"] or (near[0][1] if near else None)


def link_camden(pubs: list[dict], licences: list[dict]) -> int:
    """Sets pub['licensee'] from the best matching Camden licence. Returns the number of pubs linked."""
    grid, by_pc = Grid(), defaultdict(list)
    for p in pubs:
        grid.add(p["lon"], p["lat"], p)
        pc = norm_postcode(p["postcode"] or (p["fsa"] or {}).get("postcode"))
        if pc:
            by_pc[pc].append(p)
    best: dict[str, tuple[float, str]] = {}
    for lic in licences:
        who = (lic.get("licensee") or "").strip()
        name = lic.get("premises_name") or ""
        if not who or not name:
            continue
        cands: dict[str, tuple[float, dict, bool]] = {}
        try:
            for d, p in grid.within(
                float(lic["longitude"]), float(lic["latitude"]), CAMDEN_M
            ):
                cands[p["osm"]] = (d, p, True)
        except (KeyError, TypeError, ValueError):
            pass
        for p in by_pc.get(find_postcode(lic.get("premises_address")), ()):
            cands.setdefault(p["osm"], (CAMDEN_M, p, False))
        for d, p, near in cands.values():
            # a postcode covers many premises, so without the distance check demand a near-identical name
            if names_match(name, p["name"]) if near else names_close(name, p["name"]):
                score = -d
                if p["osm"] not in best or score > best[p["osm"]][0]:
                    best[p["osm"]] = (score, who)
    for p in pubs:
        p["licensee"] = best[p["osm"]][1] if p["osm"] in best else ""
    return len(best)


def _winner(matches: list[tuple[str, str]]) -> tuple[str, list[str]] | None:
    """matches: (group_id, evidence). Returns (group_id, evidence codes) preferring licensee > operator > brand > rest."""
    if not matches:
        return None
    ev: dict[str, set[str]] = defaultdict(set)
    for gid, e in matches:
        ev[gid].add(e)

    def key(gid: str):
        ranks = [
            EVIDENCE_RANK.index(e) if e in EVIDENCE_RANK else len(EVIDENCE_RANK)
            for e in ev[gid]
        ]
        return (min(ranks), -len(ev[gid]), gid)

    gid = min(ev, key=key)
    return gid, sorted(
        ev[gid], key=lambda e: EVIDENCE_RANK.index(e) if e in EVIDENCE_RANK else 99
    )


def classify(
    pubs: list[dict], groups: list[dict], match_group, overrides: list[dict]
) -> None:
    """Sets group, category, confidence, evidence on every pub."""
    cat_of = {g["id"]: g["category"] for g in groups}
    ov_osm = {o["osm"]: o for o in overrides if o.get("osm")}
    ov_fhrs = {int(o["fhrsid"]): o for o in overrides if o.get("fhrsid", "").isdigit()}
    op_count = Counter(norm(p["operator"]) for p in pubs if norm(p["operator"]))
    lic_count = Counter(norm(p["licensee"]) for p in pubs if norm(p["licensee"]))
    won = {
        p["osm"]: _winner(
            list(match_group(p["operator"], p["brand"], p["licensee"], p["name"], p["wikidata"]))
        )
        for p in pubs
    }
    # operator/licensee names seen on any group pub (e.g. a holding company or a placeholder) are never an indie signal
    grouped = {n for p in pubs if won[p["osm"]] for n in (norm(p["operator"]), norm(p["licensee"])) if n}
    for p in pubs:
        p["group"], p["category"], p["confidence"], p["evidence"] = (
            None,
            "unknown",
            None,
            [],
        )
        fid = p["fsa"]["fhrsid"] if p["fsa"] else None
        o = ov_osm.get(p["osm"]) or ov_fhrs.get(fid)
        if o and (o.get("group") or o.get("category")):
            p["group"] = o.get("group") or None
            p["category"] = o.get("category") or cat_of.get(p["group"], "unknown")
            p["evidence"] = ["override"]
            p["confidence"] = None if p["category"] == "unknown" else "confirmed"
            continue
        w = won[p["osm"]]
        if w:
            gid, ev = w
            p["group"], p["category"] = gid, cat_of.get(gid, "unknown")
            families = {"camden" if e.startswith("camden:") else "osm" for e in ev}
            p["confidence"] = "confirmed" if len(families) >= 2 else "likely"
            p["evidence"] = ev
            continue
        op, lic = norm(p["operator"]), norm(p["licensee"])
        free_house = op in ("free house", "freehouse") and op not in grouped
        small = lambda n, c: bool(n) and c[n] <= INDIE_MAX and n not in grouped and not NOT_INDIE.search(n)
        if free_house or small(op, op_count) or small(lic, lic_count):
            p["category"], p["confidence"] = "indie", "likely"
            p["evidence"] = [
                e
                for e, ok in (
                    ("osm:operator", free_house or small(op, op_count)),
                    ("camden:licensee", small(lic, lic_count)),
                )
                if ok
            ]


def apply_overrides_tags(pubs: list[dict], overrides: list[dict]) -> None:
    """Overrides may also correct cask/craft."""
    ov_osm = {o["osm"]: o for o in overrides if o.get("osm")}
    ov_fhrs = {int(o["fhrsid"]): o for o in overrides if o.get("fhrsid", "").isdigit()}
    for p in pubs:
        o = ov_osm.get(p["osm"]) or ov_fhrs.get(
            p["fsa"]["fhrsid"] if p["fsa"] else None
        )
        if not o:
            continue
        for k in ("cask", "craft"):
            if o.get(k) in ("0", "1"):
                p[k] = int(o[k])


def valid_overrides(overrides: list[dict], group_ids: set[str], categories: dict) -> list[dict]:
    """Drop (with a warning) override rows naming a group or category that does not exist."""
    ok = []
    for o in overrides:
        g, c = o.get("group", ""), o.get("category", "")
        if (g and g not in group_ids) or (c and c not in categories):
            print(f"pubs: skipping override {o.get('osm') or o.get('fhrsid')}: unknown group {g!r} or category {c!r}", file=sys.stderr)
            continue
        ok.append(o)
    return ok


def build(
    osm: dict,
    fsa: list[dict],
    camden: list[dict] | None,
    groups: list[dict],
    match_group,
    overrides: list[dict] | None = None,
    as_of: str | None = None,
    categories: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Pure function from the data sets plus the group table to (pubs.json document, classified pubs)."""
    if categories is None:
        from .pub_groups import CATEGORIES as categories
    overrides = valid_overrides(overrides or [], {g["id"] for g in groups}, categories)
    pubs = osm_points(osm)
    link_fsa(pubs, fsa)
    pubs = [p for p in pubs if p["near"] is not None]  # London filter
    link_camden(pubs, camden or [])
    for p in pubs:
        t = p["tags"]
        p["cask"], p["craft"] = cask_flag(t.get("real_ale")), craft_flag(t)
    classify(pubs, groups, match_group, overrides)
    apply_overrides_tags(pubs, overrides)
    pubs.sort(key=lambda p: (p["near"]["authority"], p["name"].lower(), p["osm"]))
    rows = []
    for p in pubs:
        pc = p["postcode"] or (p["fsa"] or {}).get("postcode") or ""
        rows.append(
            [
                p["name"],
                round(p["lon"], 5),
                round(p["lat"], 5),
                fmt_postcode(pc),
                p["near"]["authority"],
                p["fsa"]["fhrsid"] if p["fsa"] else None,
                p["osm"],
                p["group"],
                p["category"],
                p["confidence"],
                p["evidence"],
                p["cask"],
                p["craft"],
                p["website"] or None,
            ]
        )
    ix = {f: i for i, f in enumerate(FIELDS)}
    by_group = Counter(r[ix["group"]] for r in rows if r[ix["group"]])
    doc = {
        "schema_version": 1,
        "as_of": as_of or date.today().isoformat(),
        "generated": date.today().isoformat(),
        "licence": "ODbL 1.0 (contains OpenStreetMap data) and OGL v3.0",
        "source": "OpenStreetMap contributors (ODbL); Food Standards Agency and Camden Council (OGL v3.0); curated group table",
        "method": "OSM pubs inside London, linked to the FSA register and Camden licences, matched to a curated group table "
        "on operator, brand, licensee and name; two agreeing sources = confirmed, one = likely.",
        "sources": SOURCES,
        "categories": categories,
        "groups": [
            {
                k: g.get(k)
                for k in ("id", "name", "category", "owner", "model", "note", "src")
            }
            for g in groups
        ],
        "fields": FIELDS,
        "pubs": rows,
        "counts": {
            "total": len(rows),
            "by_category": dict(Counter(r[ix["category"]] for r in rows)),
            "by_group": dict(by_group.most_common()),
            "cask": sum(1 for r in rows if r[ix["cask"]] == 1),
            "on_fsa": sum(1 for r in rows if r[ix["fhrsid"]]),
        },
    }
    return doc, pubs


_COMPANY = re.compile(r"\b(ltd|limited|plc|llp)\b", re.IGNORECASE)


def unmatched(pubs: list[dict], n: int = 20) -> tuple[list, list, int]:
    """Top unmatched operators and licensees that look like companies (never print people), plus how many names were left out."""
    ops = Counter(p["operator"] for p in pubs if p["operator"] and p["group"] is None)
    lics = Counter(p["licensee"] for p in pubs if p["licensee"] and p["group"] is None)
    names = list(ops.items()) + list(lics.items())
    hidden = sum(1 for k, _ in names if not _COMPANY.search(k))
    keep = lambda c: [(k, v) for k, v in c.most_common() if _COMPANY.search(k)][:n]
    return keep(ops), keep(lics), hidden


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cached OSM and Camden downloads",
    )
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    ref, api = root / "work" / "reference", root / "site" / "api" / "v1"
    from .pub_groups import GROUPS, match_group

    try:
        osm = fetch_osm(ref, args.refresh)
    except Exception as e:  # noqa: BLE001
        print(
            f"pubs: OSM fetch failed, keeping the committed file: {e}", file=sys.stderr
        )
        return 1
    skipped = []
    try:
        camden = fetch_camden(ref, args.refresh)
    except Exception as e:  # noqa: BLE001
        print(f"pubs: Camden licensing skipped: {e}", file=sys.stderr)
        camden, skipped = None, [{"source": "camden", "reason": str(e)[:200]}]
    fsa = load_fsa(root / "work" / "latest.parquet")
    ts = (osm.get("osm3s") or {}).get("timestamp_osm_base", "")[:10]
    as_of = (
        ts
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts)
        else datetime.fromtimestamp((ref / OSM_CACHE).stat().st_mtime)
        .date()
        .isoformat()
    )
    doc, pubs = build(
        osm,
        fsa,
        camden,
        GROUPS,
        match_group,
        load_overrides(root / "data" / "pubs_overrides.csv"),
        as_of,
    )
    doc["skipped"] = skipped
    api.mkdir(parents=True, exist_ok=True)
    (api / "pubs.json").write_text(
        json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    )
    c = doc["counts"]
    print(
        f"pubs.json: {c['total']} pubs, {c['on_fsa']} on FSA, {c['cask']} cask, skipped {[s['source'] for s in skipped]}"
    )
    print("by category:", c["by_category"])
    print("by group:", c["by_group"])
    ops, lics, hidden = unmatched(pubs)
    print("top unmatched company OSM operators:", ops)
    print("top unmatched company Camden licensees:", lics)
    print(f"{hidden} other unmatched operator/licensee names not shown (not companies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
