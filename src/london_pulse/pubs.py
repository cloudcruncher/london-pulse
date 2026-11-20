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
  * Extra open signals (v2): the OSM website domain on a group's own domain ("osm:website"), an OSM `brewery` tag
    naming one family/regional brewer ("osm:brewery", a tied-house signal), the FSA business name when it differs
    from OSM's ("fsa:name"), and Wikidata (CC0) ownership: P127/P137/P749 and reverse P1830, followed up to 4 hops
    from the pub's own `wikidata` item ("wikidata:owner", its own family) or from its `brand:wikidata` /
    `operator:wikidata` tags ("osm:wikidata-owner", OSM family: it only resolves the mapper's claim) to a group QID.
    Source families for "confirmed": osm (incl. website/brewery), camden, fsa, wikidata, override.
  * Independent ("indie", "likely"): no group matched and the OSM operator or Camden licensee is a name that appears
    on <= 3 London pubs (or the operator is literally "Free House"), or the pub's website domain is shared by 2-3
    pubs at different places with no group link (a small group). A shared domain on more than 3 pubs that is not a
    verified group blocks indie. Everything else is "unknown": not a claim of independence.
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
WIKIDATA = "https://query.wikidata.org/sparql"
WIKIDATA_CACHE = "wikidata_pubs.json"
WIKIDATA_HOPS = 4
WIKIDATA_PROPS = ("P127", "P137", "P749")  # owned by, operator, parent organization (+ reverse P1830 owner of)
# QIDs never followed as owners: Wikidata's "Punch Pubs" item still links pubs from before the 2017 split of Punch
# between Heineken (Star Pubs) and Patron (today's Punch), so it cannot tell which of the two owns a pub now
WIKIDATA_AMBIGUOUS = {"Q2118336"}
WIKIDATA_CACHE_VERSION = 2
# reference properties that do not make an ownership statement checkable: Pubs Galore ID (the 2019 bulk import),
# retrieved date, "imported from Wikimedia project" and its URL, based on heuristic. A statement whose references
# use only these (or that has none) is "weak": it never counts as its own family and is never sole evidence.
WIKIDATA_WEAK_REFS = {"P7411", "P813", "P143", "P4656", "P887", "P1932"}
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
    r"|students? union|\bsu\b"
    r"|publove|whelan",  # multi-site operators whose landlord/owner we could not verify (Whelan's leases from Star Pubs)
    re.IGNORECASE,
)
DOMAIN_MIN_SPREAD_M = 150  # two OSM objects on one domain closer than this are the same pub mapped twice, not a group
# hosting/social/listing platforms: a shared one says nothing about ownership
GENERIC_DOMAINS = re.compile(
    r"(^|\.)(facebook|instagram|twitter|x|tiktok|linktr|linkin|wixsite|wix|squarespace|google|goo|business\.site|"
    r"beerintheevening|edan|tripadvisor|yelp|opentable|designmynight|resdiary|sevenrooms|untappd|whatpub|camra|"
    r"wordpress|blogspot|weebly|godaddysites|webflow|carrd|bit|tinyurl|ueniweb|mailchimpsites|site123|jimdosite|"
    r"square|toasttab|uber|deliveroo|justeat|just-eat|wikipedia|wikidata|openstreetmap|youtube)\.[a-z.]+$",
    re.IGNORECASE,
)
MIN_TOTAL = 2500  # sanity floor for the published file (the workflow guard uses the same number)

EVIDENCE_RANK = [
    "camden:licensee",
    "osm:operator",
    "osm:brand",
    "osm:wikidata",
    "osm:website",
    "name",
    "fsa:name",
    "osm:brewery",
    "wikidata:owner",
    "osm:wikidata-owner",
]

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
        "id": "wikidata",
        "name": "Wikidata ownership statements (owned by, operator, parent organization)",
        "publisher": "Wikidata contributors",
        "licence": "CC0 1.0",
        "attribution": "Wikidata",
        "url": "https://www.wikidata.org/",
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
    "rating",
    "rating_date",
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


_SLD = {"co", "org", "ac", "gov", "me", "ltd", "plc", "net", "sch"}


def site_domain(url) -> str:
    """Registrable domain of a website tag ("https://www.x.co.uk/a" -> "x.co.uk"); "" if none."""
    u = str(url or "").strip().split(";")[0].strip()
    if not u:
        return ""
    if "//" not in u:
        u = "http://" + u
    try:
        host = (urllib.parse.urlsplit(u).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    parts = [x for x in host.split(".") if x]
    if len(parts) < 2:
        return ""
    n = 3 if len(parts) >= 3 and parts[-1] == "uk" and parts[-2] in _SLD else 2
    return ".".join(parts[-n:])


def brewery_values(v) -> list[str]:
    """OSM brewery tag -> normalised values ("Sam_Smith's;various" -> ["sam smith's", "various"])."""
    return [" ".join(x.replace("_", " ").lower().split()) for x in str(v or "").replace("’", "'").split(";") if x.strip()]


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


def _sparql(query: str) -> list[dict]:
    body = urllib.parse.urlencode({"query": query, "format": "json"}).encode()
    err: Exception | None = None
    for i in range(3):
        try:
            req = urllib.request.Request(
                WIKIDATA,
                data=body,
                headers={"User-Agent": OVERPASS_UA, "Accept": "application/sparql-results+json"},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)["results"]["bindings"]
        except Exception as e:  # noqa: BLE001 - WDQS rate limits and times out
            err = e
            time.sleep(5 * (i + 1))
    raise RuntimeError("Wikidata query failed") from err


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def fetch_wikidata(ref_dir: Path, qids, refresh: bool = False, batch: int = 200) -> dict:
    """Current ownership statements for the given QIDs and their owners, up to WIKIDATA_HOPS, plus English labels.

    Returns {"edges": {qid: [[owner_qid, strong], ...]}, "labels": {qid: label}}. Only statements without an end time
    (pq:P582) and not deprecated are kept; `strong` is True when some reference uses a property outside
    WIKIDATA_WEAK_REFS (e.g. a reference URL). Cached in work/reference/wikidata_pubs.json for CACHE_DAYS; a fresh
    cache is topped up with new QIDs only, and a cache from an older format is refetched.
    """
    p = ref_dir / WIKIDATA_CACHE
    cache = json.loads(p.read_text()) if _fresh(p, refresh) else {}
    if cache.get("version") != WIKIDATA_CACHE_VERSION:
        cache = {"edges": {}, "labels": {}, "queried": []}
    strong: dict[tuple[str, str], bool] = {(k, o): bool(st) for k, v in cache["edges"].items() for o, st in v}
    labels: dict[str, str] = dict(cache["labels"])
    queried = set(cache["queried"])
    frontier = {q for q in qids if re.fullmatch(r"Q\d+", q)}
    pairs = " ".join(f"(p:{x} ps:{x})" for x in WIKIDATA_PROPS)
    current = "FILTER NOT EXISTS { ?st pq:P582 [] } FILTER NOT EXISTS { ?st wikibase:rank wikibase:DeprecatedRank }"

    def nexts(qs):
        return {o for (q, o) in strong if q in qs}

    for _ in range(WIKIDATA_HOPS):
        todo = sorted(frontier - queried)
        for i in range(0, len(todo), batch):
            vals = " ".join("wd:" + q for q in todo[i : i + batch])
            rows = _sparql(
                f"SELECT ?i ?o ?rp WHERE {{ VALUES ?i {{ {vals} }} "
                f"{{ VALUES (?p ?ps) {{ {pairs} }} ?i ?p ?st . ?st ?ps ?o . }} "
                f"UNION {{ ?o p:P1830 ?st . ?st ps:P1830 ?i . }} "
                f"FILTER(isIRI(?o)) {current} "
                f"OPTIONAL {{ ?st prov:wasDerivedFrom ?ref . ?ref ?rp ?rv . }} }}"
            )
            for b in rows:
                key = (_qid(b["i"]["value"]), _qid(b["o"]["value"]))
                ref_prop = b.get("rp", {}).get("value", "")
                ok = "/prop/reference/" in ref_prop and _qid(ref_prop) not in WIKIDATA_WEAK_REFS
                strong[key] = strong.get(key, False) or ok
            for b in _sparql(
                f'SELECT ?i ?l WHERE {{ VALUES ?i {{ {vals} }} ?i rdfs:label ?l FILTER(lang(?l) = "en") }}'
            ):
                labels[_qid(b["i"]["value"])] = b["l"]["value"]
        queried |= set(todo)
        frontier = nexts(frontier)
    edges: dict[str, list] = defaultdict(list)
    for (q, o), st in sorted(strong.items()):
        edges[q].append([o, st])
    ref_dir.mkdir(parents=True, exist_ok=True)
    out = {"edges": dict(edges), "labels": labels}
    p.write_text(json.dumps({"version": WIKIDATA_CACHE_VERSION, **out, "queried": sorted(queried)}, separators=(",", ":")))
    return out


def wikidata_owner_groups(qids, edges: dict, group_qids: dict[str, set[str]]) -> dict[str, bool]:
    """Groups reached from the pub's QIDs by following current ownership edges -> {group: strong}.

    The nearest hop with a group QID wins; a path is strong only if every edge on it is. Hop 0 (the tags themselves)
    is the plain osm:wikidata match and is skipped here. Edges are [owner, strong] pairs.
    """
    seen = set(qids)
    level = {q: True for q in qids}
    for _ in range(WIKIDATA_HOPS):
        nxt: dict[str, bool] = {}
        for q, ok in level.items():
            for o, st in edges.get(q, ()):
                if o in seen or o in WIKIDATA_AMBIGUOUS:
                    continue
                nxt[o] = nxt.get(o, False) or (ok and bool(st))
        if not nxt:
            return {}
        seen |= set(nxt)
        level = nxt
        hit = {gid: any(level[q] for q in qs & level.keys()) for gid, qs in group_qids.items() if qs & level.keys()}
        if hit:
            return hit
    return {}


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
        own = [w.strip() for w in str(t.get("wikidata", "")).split(";") if w.strip()]
        website = t.get("website") or t.get("contact:website") or ""
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
                "website": website,
                "domain": site_domain(website),
                "qids": [q for q in dict.fromkeys(own + wd) if re.fullmatch(r"Q\d+", q)],
                "own_qids": [q for q in dict.fromkeys(own) if re.fullmatch(r"Q\d+", q)],
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


# evidence that may support a group but never stands alone ("wikidata:weak": an unreferenced or bulk-import statement)
WEAK_EVIDENCE = {"wikidata:weak"}


def _family(e: str) -> str | None:
    """Source family of an evidence code. fsa:name is the same trading name as OSM's, so it is OSM family;
    weak Wikidata statements belong to no family (they can never help reach "confirmed")."""
    if e in WEAK_EVIDENCE:
        return None
    head = e.split(":", 1)[0]
    return head if head in ("camden", "wikidata", "override") else "osm"


def _families(ev) -> set[str]:
    return {f for f in map(_family, ev) if f}


def extra_matches(p: dict, groups: list[dict], match_group, wd: dict | None) -> list[tuple[str, str]]:
    """v2 signals beyond match_group: website domain, brewery tag, FSA business name, Wikidata ownership.

    wd is fetch_wikidata()'s {"edges", "labels"}. A chain from the pub's own item counts as Wikidata evidence only if
    every statement on it is referenced (else "wikidata:weak") and the item's label still matches the OSM name (an
    item for a renamed or replaced pub is dropped). A chain from brand:/operator:wikidata resolves the OSM mapper's
    claim, so it is OSM family ("osm:wikidata-owner"), or "wikidata:weak" if any statement on it is unreferenced.
    """
    out = []
    dom = p.get("domain") or ""
    brew = brewery_values(p["tags"].get("brewery"))
    group_qids = {}
    for g in groups:
        m = g.get("match") or {}
        if dom and any(dom == d or dom.endswith("." + d) for d in m.get("domain") or ()):
            out.append((g["id"], "osm:website"))
        rx = m.get("brewery")
        if rx and len(brew) == 1 and re.fullmatch(rx, brew[0], re.IGNORECASE):
            out.append((g["id"], "osm:brewery"))
        if m.get("wikidata"):
            group_qids[g["id"]] = set(m["wikidata"])
    fsa_name = (p["fsa"] or {}).get("name") or ""
    if fsa_name and norm(fsa_name) != norm(p["name"]):
        out += [(gid, "fsa:name") for gid, e in match_group(None, None, None, fsa_name, ()) if e == "name"]
    if wd and p.get("qids"):
        edges, labels = wd.get("edges") or {}, wd.get("labels") or {}
        own = [q for q in p.get("own_qids", []) if q in p["qids"] and names_match(labels.get(q, ""), p["name"])]
        tagged = [q for q in p["qids"] if q not in p.get("own_qids", [])]
        for qs, code in ((own, "wikidata:owner"), (tagged, "osm:wikidata-owner")):
            if qs:
                out += [(gid, code if ok else "wikidata:weak") for gid, ok in sorted(wikidata_owner_groups(qs, edges, group_qids).items())]
    return out


def classify(
    pubs: list[dict], groups: list[dict], match_group, overrides: list[dict], wd: dict | None = None
) -> None:
    """Sets group, category, confidence, evidence on every pub."""
    cat_of = {g["id"]: g["category"] for g in groups}
    ov_osm = {o["osm"]: o for o in overrides if o.get("osm")}
    ov_fhrs = {int(o["fhrsid"]): o for o in overrides if o.get("fhrsid", "").isdigit()}
    op_count = Counter(norm(p["operator"]) for p in pubs if norm(p["operator"]))
    lic_count = Counter(norm(p["licensee"]) for p in pubs if norm(p["licensee"]))
    group_domains = {g["id"]: tuple((g.get("match") or {}).get("domain") or ()) for g in groups}

    def usable(p: dict, matches: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Drop groups backed only by evidence that may never stand alone: a weak Wikidata statement, or a brewery
        tag on a pub whose own website is not the group's (a free house can sell a brewer's beer)."""
        by: dict[str, set[str]] = defaultdict(set)
        for gid, e in matches:
            by[gid].add(e)
        dom = p.get("domain") or ""
        own_site = lambda gid: bool(dom) and not any(dom == d or dom.endswith("." + d) for d in group_domains.get(gid, ()))

        def alone(gid: str) -> bool:
            ev = by[gid] - WEAK_EVIDENCE
            return not ev or (ev == {"osm:brewery"} and own_site(gid))

        return [(gid, e) for gid, e in matches if not alone(gid)]

    won = {
        p["osm"]: _winner(
            usable(
                p,
                list(match_group(p["operator"], p["brand"], p["licensee"], p["name"], p["wikidata"]))
                + extra_matches(p, groups, match_group, wd),
            )
        )
        for p in pubs
    }
    # Wikidata ownership can be stale (e.g. a pub sold on with its old item unchanged): a Wikidata-only hit yields to a
    # current OSM operator or Camden licensee that names someone else
    wd_only = {"wikidata:owner", "osm:wikidata-owner"} | WEAK_EVIDENCE
    for p in pubs:
        w = won[p["osm"]]
        if w and set(w[1]) <= wd_only and (norm(p["operator"]) or norm(p["licensee"])):
            won[p["osm"]] = None
    # operator/licensee names seen on any group pub (e.g. a holding company or a placeholder) are never an indie signal
    grouped = {n for p in pubs if won[p["osm"]] for n in (norm(p["operator"]), norm(p["licensee"])) if n}
    # website domains: a domain shared by 2-3 pubs at different places with no group link reads as a small group;
    # one on more than INDIE_MAX pubs that is not a verified group blocks indie altogether
    on_domain: dict[str, list[dict]] = defaultdict(list)
    for p in pubs:
        if p.get("domain") and not GENERIC_DOMAINS.search(p["domain"]):
            on_domain[p["domain"]].append(p)
    big_domain = {d for d, ps in on_domain.items() if len(ps) > INDIE_MAX and not any(won[q["osm"]] for q in ps)}

    def small_domain(d: str) -> bool:
        ps = on_domain.get(d) or []
        if not 2 <= len(ps) <= INDIE_MAX or NOT_INDIE.search(d) or any(won[q["osm"]] for q in ps):
            return False
        pts = [xy(q["lon"], q["lat"]) for q in ps]
        return any(math.dist(a, b) >= DOMAIN_MIN_SPREAD_M for i, a in enumerate(pts) for b in pts[i + 1 :])

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
            p["confidence"] = None if p["category"] == "unknown" else "likely" if p["category"] == "indie" else "confirmed"
            continue
        w = won[p["osm"]]
        if w:
            gid, ev = w
            p["group"], p["category"] = gid, cat_of.get(gid, "unknown")
            # "Independent" is always "likely" (spec): independence is an absence of ties, never confirmed
            p["confidence"] = "confirmed" if len(_families(ev)) >= 2 and p["category"] != "indie" else "likely"
            p["evidence"] = ev
            continue
        if p.get("domain") in big_domain:
            continue
        op, lic = norm(p["operator"]), norm(p["licensee"])
        free_house = op in ("free house", "freehouse") and op not in grouped
        small = lambda n, c: bool(n) and c[n] <= INDIE_MAX and n not in grouped and not NOT_INDIE.search(n)
        signals = (
            ("osm:operator", free_house or small(op, op_count)),
            ("camden:licensee", small(lic, lic_count)),
            ("osm:website", bool(p.get("domain")) and small_domain(p["domain"])),
        )
        if any(ok for _, ok in signals):
            p["category"], p["confidence"] = "indie", "likely"
            p["evidence"] = [e for e, ok in signals if ok]


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
    wd: dict | None = None,
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
    classify(pubs, groups, match_group, overrides, wd)
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
                (p["fsa"] or {}).get("rating") or None,
                _iso((p["fsa"] or {}).get("rating_date")),
            ]
        )
    ix = {f: i for i, f in enumerate(FIELDS)}
    by_group = Counter(r[ix["group"]] for r in rows if r[ix["group"]])
    doc = {
        "schema_version": 1,
        "as_of": as_of or date.today().isoformat(),
        "generated": date.today().isoformat(),
        "licence": "ODbL 1.0 (contains OpenStreetMap data) and OGL v3.0",
        "source": "OpenStreetMap contributors (ODbL); Food Standards Agency and Camden Council (OGL v3.0); Wikidata (CC0); "
        "curated group table",
        "method": "OSM pubs inside London, linked to the FSA register and Camden licences, matched to a curated group table "
        "on operator, brand, licensee, name, website domain, brewery tag, FSA name and Wikidata ownership; "
        "two agreeing source families = confirmed, one = likely.",
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


def _iso(d) -> str | None:
    if not d:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()[:10]
    s = str(d)[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


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
        help="ignore the cached OSM, Camden and Wikidata downloads",
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
    qids = sorted({q for p in osm_points(osm) for q in p["qids"]})
    try:
        wd = fetch_wikidata(ref, qids, args.refresh)
    except Exception as e:  # noqa: BLE001
        print(f"pubs: Wikidata skipped: {e}", file=sys.stderr)
        wd, skipped = None, skipped + [{"source": "wikidata", "reason": str(e)[:200]}]
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
        wd=wd,
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
