"""Who lives around an area: council-tenure share, income and deprivation per LSOA, joined to recorded crime.

One record per London LSOA (~1,700 people, ~700 households), written to site/api/v1/areas.json:
  council_pct   share of households renting from the council or a local authority (Census 2021, TS054, code 4).
                Housing-association rentals are kept apart in other_social_pct, so "council" means council.
  income        ONS model-based small-area household income, MSOA level (an LSOA inherits its MSOA's figure),
                net, equivalised (adjusted for household size), before and after housing costs, FYE 2023.
  income_dep    IMD 2025 Income Score: share of residents in income-deprived households.
  crimes        police-recorded crimes (data.police.uk) over the last three months, counted by the LSOA each crime's
                snapped location falls in. Counts per LSOA only; never individual crimes.
  venues        FSA-registered food premises, a footfall proxy: busy centres record crime from visitors, not residents.
Also published: how crime rates vary with council share, with and without holding deprivation fixed. It is
a description of recorded crime near where people live, not a verdict on any estate or its residents.

Run after crime.py (which saves the crime points): python -m london_pulse.neighbourhoods
"""
import json
import math
from datetime import date
from pathlib import Path

import duckdb

from .http import download, get_json

ARCGIS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
BOUNDARIES = f"{ARCGIS}/Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5/FeatureServer/0/query"
LONDON_BBOX = "-0.52,51.28,0.34,51.70"
NOMIS_TENURE = ("https://www.nomisweb.co.uk/api/v01/dataset/NM_2072_1.data.csv?date=latest&geography=TYPE151"
                "&c2021_tenure_9=0,4,5,1001,1004&measures=20100&select=geography_code,c2021_tenure_9,obs_value")
IMD_CSV = ("https://assets.publishing.service.gov.uk/media/691ded56d140bbbaa59a2a7d/"
           "File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv")
INCOME_XLSX = ("https://www.ons.gov.uk/file?uri=/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/"
               "smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales/financialyearending2023/datasetfinal.xlsx")
MIN_POP = 500             # below this a rate per 1,000 residents is noise (central business districts)
BANDS = [(0, 5), (5, 15), (15, 30), (30, 101)]   # council share of households, %


def fetch_boundaries(dest: Path) -> None:
    feats, offset = [], 0
    while True:
        page = get_json(f"{BOUNDARIES}?where=1%3D1&geometry={LONDON_BBOX}&geometryType=esriGeometryEnvelope&inSR=4326"
                        f"&spatialRel=esriSpatialRelIntersects&outFields=LSOA21CD&outSR=4326&geometryPrecision=5"
                        f"&orderByFields=LSOA21CD&resultOffset={offset}&resultRecordCount=1000&f=geojson")
        feats += page["features"]
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
        if not page["features"] or len(page["features"]) < 1000 and not more:
            break
        offset += len(page["features"])
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))


def fetch_tenure(dest: Path) -> None:
    """Nomis returns at most 25,000 rows a request, so page through England and Wales (~175k rows)."""
    lines, offset = [], 0
    while True:
        page = dest.with_name(dest.name + ".page")
        download(f"{NOMIS_TENURE}&recordoffset={offset}", page)
        got = page.read_text().splitlines()
        page.unlink()
        lines += got if not lines else got[1:]
        if len(got) - 1 < 25000:
            break
        offset += 25000
    dest.write_text("\n".join(lines) + "\n")


def load_reference(con: duckdb.DuckDBPyConnection, work: Path) -> None:
    """Fetch (cached in work/) the open datasets and build tables lsoa, ref."""
    work.mkdir(exist_ok=True)
    files = {"lsoa.geojson": fetch_boundaries, "tenure.csv": fetch_tenure,
             "imd.csv": lambda p: download(IMD_CSV, p), "income.xlsx": lambda p: download(INCOME_XLSX, p)}
    for name, fn in files.items():
        if not (work / name).exists():
            fn(work / name)
    con.execute("INSTALL spatial; LOAD spatial; INSTALL excel; LOAD excel")
    con.execute(f"""
        CREATE TABLE lsoa AS
        SELECT f.properties.LSOA21CD AS code, ST_GeomFromGeoJSON(f.geometry) AS geom
        FROM (SELECT unnest(features) AS f FROM read_json('{work / "lsoa.geojson"}'))""")
    con.execute(f"""
        CREATE TABLE imd AS
        SELECT "LSOA code (2021)" AS code, "LSOA name (2021)" AS name, "Local Authority District name (2024)" AS borough,
               "Total population: mid 2022"::INT AS pop, "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)"::INT AS imd_decile,
               "Income Score (rate)"::DOUBLE AS income_dep,
               "Income Decile (where 1 is most deprived 10% of LSOAs)"::INT AS income_decile
        FROM read_csv('{work / "imd.csv"}', header=true)
        WHERE "Local Authority District code (2024)" LIKE 'E09%'""")
    con.execute(f"""
        CREATE TABLE tenure AS
        SELECT geography_code AS code,
               max(obs_value) FILTER (c2021_tenure_9 = 0) AS hh,
               max(obs_value) FILTER (c2021_tenure_9 = 4) AS council,
               max(obs_value) FILTER (c2021_tenure_9 = 5) AS other_social,
               max(obs_value) FILTER (c2021_tenure_9 = 1001) AS owned,
               max(obs_value) FILTER (c2021_tenure_9 = 1004) AS private
        FROM read_csv('{work / "tenure.csv"}', header=true) GROUP BY 1""")
    sheet = lambda name: f"read_xlsx('{work / 'income.xlsx'}', sheet='{name}', range='A4:J9000', header=true)"  # noqa: E731
    con.execute(f"""
        CREATE TABLE income AS
        SELECT b."MSOA name" AS msoa, b."Disposable (net) annual income before housing costs (£)"::INT AS net_bhc,
               a."Disposable (net) annual income after housing costs (£)"::INT AS net_ahc
        FROM {sheet("Net income before housing costs")} b
        JOIN {sheet("Net income after housing costs")} a USING ("MSOA code")
        WHERE b."MSOA code" IS NOT NULL""")


def rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=xs.__getitem__)
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    var = sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(var) if var else 0.0       # a constant series has no correlation to speak of


def spearman(a: list[float], b: list[float]) -> float:
    return pearson(rank(a), rank(b))


def partial_spearman(x: list[float], y: list[float], z: list[float]) -> float | None:
    """Rank correlation of x and y after removing what z explains in both (None if z duplicates x or y)."""
    rxy, rxz, ryz = spearman(x, y), spearman(x, z), spearman(y, z)
    d = (1 - rxz**2) * (1 - ryz**2)
    return (rxy - rxz * ryz) / math.sqrt(d) if d > 1e-9 else None


def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def band_of(pct: float) -> int:
    pct = min(max(pct, 0.0), 100.0)
    return next(i for i, (lo, hi) in enumerate(BANDS) if lo <= pct < hi)


def analyse(rows: list[dict]) -> dict:
    """How recorded crime varies with council share, among residential LSOAs (not busy centres)."""
    res = [r for r in rows if not r["busy"] and r["rate"] is not None]
    out = {"n": len(res), "median_rate": round(median([r["rate"] for r in res]), 1),
           "median_council_pct": round(median([r["council_pct"] for r in res]), 1)}
    out["bands"] = []
    for i, (lo, hi) in enumerate(BANDS):
        g = [r for r in res if band_of(r["council_pct"]) == i]
        out["bands"].append({"from": lo, "to": min(hi, 100), "n": len(g),
                             "median_rate": round(median([r["rate"] for r in g]), 1) if g else None,
                             "median_income_dep_pct": round(median([r["income_dep"] for r in g]) * 100, 1) if g else None})
    # the same comparison among places with similar income deprivation (England-wide income deciles, not the all-in IMD,
    # which has a crime domain of its own): most deprived 3 deciles, middle 4, least deprived 3
    out["same_deprivation"] = []
    for name, lo, hi in [("the most income-deprived 30% of England's neighbourhoods", 1, 3),
                         ("the middle 40% for income deprivation", 4, 7),
                         ("the least income-deprived 30% of England's neighbourhoods", 8, 10)]:
        pool = [r for r in res if lo <= r["income_decile"] <= hi]
        cells = []
        for i in range(len(BANDS)):
            g = [r for r in pool if band_of(r["council_pct"]) == i]
            cells.append({"n": len(g), "median_rate": round(median([r["rate"] for r in g]), 1) if len(g) >= 15 else None})
        out["same_deprivation"].append({"group": name, "n": len(pool), "bands": cells})
    rate, council, dep = ([r[k] for r in res] for k in ("rate", "council_pct", "income_dep"))
    out["spearman"] = {"council_vs_crime": round(spearman(council, rate), 2),
                       "income_deprivation_vs_crime": round(spearman(dep, rate), 2),
                       "council_vs_crime_same_deprivation": None if (pr := partial_spearman(council, rate, dep)) is None else round(pr, 2)}
    return out


def build(fsa_parquet: Path, points_parquet: Path, api_dir: Path, work: Path, months: list[str]) -> None:
    con = duckdb.connect()
    load_reference(con, work)
    con.execute(f"CREATE TABLE pts AS SELECT lon, lat, cat FROM '{points_parquet}'")
    con.execute(f"""
        CREATE TABLE pt_lsoa AS
        SELECT l.code, p.cat, count(*) AS n FROM pts p JOIN lsoa l ON ST_Contains(l.geom, ST_Point(p.lon, p.lat)) GROUP BY ALL""")
    con.execute(f"""
        CREATE TABLE venue_lsoa AS
        SELECT l.code, count(*) AS n FROM '{fsa_parquet}' v JOIN lsoa l ON ST_Contains(l.geom, ST_Point(v.lon, v.lat))
        WHERE v.lon IS NOT NULL GROUP BY ALL""")
    cats = [r[0] for r in con.execute("SELECT DISTINCT cat FROM pts ORDER BY 1").fetchall()]
    crimes = {}
    for code, cat, n in con.execute("SELECT code, cat, n FROM pt_lsoa").fetchall():
        crimes.setdefault(code, [0] * len(cats))[cats.index(cat)] = n
    recs = con.execute("""
        SELECT i.code, i.name, i.borough, i.pop, i.imd_decile, i.income_dep, i.income_decile, t.hh, t.council, t.other_social, t.private, t.owned,
               inc.net_bhc, inc.net_ahc, coalesce(v.n, 0) AS venues, ST_X(ST_Centroid(l.geom)) AS lon, ST_Y(ST_Centroid(l.geom)) AS lat
        FROM imd i JOIN lsoa l USING (code) JOIN tenure t USING (code)
        LEFT JOIN income inc ON inc.msoa = regexp_replace(i.name, '[A-Z]$', '')
        LEFT JOIN venue_lsoa v USING (code) ORDER BY i.code""").fetchall()
    rows = []
    for code, name, borough, pop, dec, dep, idec, hh, council, other, private, owned, bhc, ahc, venues, lon, lat in recs:
        c = crimes.get(code, [0] * len(cats))
        total = sum(c)
        pct = lambda x: round(100 * x / hh, 1) if hh else 0.0  # noqa: E731
        rows.append({"code": code, "name": name, "borough": borough, "pop": pop, "imd_decile": dec, "income_decile": idec, "income_dep": dep, "hh": hh,
                     "council_pct": pct(council), "other_social_pct": pct(other), "private_pct": pct(private), "owned_pct": pct(owned),
                     "net_bhc": bhc, "net_ahc": ahc, "venues": venues, "venues_per_1000": venues / pop * 1000 if pop else 0,
                     "lon": lon, "lat": lat, "crimes": c, "total": total,
                     "rate": total * (12 / len(months)) / pop * 1000 if pop >= MIN_POP else None})
    # a busy centre has many food premises per resident: its recorded crime comes from visitors as much as residents
    busy_at = sorted(r["venues_per_1000"] for r in rows)[int(len(rows) * 0.9)]
    for r in rows:
        r["busy"] = r["venues_per_1000"] >= busy_at
    fields = ["code", "name", "borough", "lon", "lat", "pop", "households", "council_pct", "other_social_pct", "private_pct",
              "owned_pct", "imd_decile", "income_dep_pct", "net_income_bhc", "net_income_ahc", "venues", "busy", "crimes"]
    data = [[r["code"], r["name"], r["borough"], round(r["lon"], 4), round(r["lat"], 4), r["pop"], r["hh"], r["council_pct"],
             r["other_social_pct"], r["private_pct"], r["owned_pct"], r["imd_decile"], round(r["income_dep"] * 100, 1),
             r["net_bhc"], r["net_ahc"], r["venues"], int(r["busy"]), r["crimes"]] for r in rows]
    (api_dir / "areas.json").write_text(json.dumps({
        "schema_version": 1, "generated": date.today().isoformat(), "crime_months": months, "categories": cats,
        "busy_venues_per_1000": round(busy_at, 1), "min_pop_for_rate": MIN_POP, "fields": fields, "areas": data,
        "analysis": analyse(rows),
        "sources": ["Census 2021 TS054 tenure (ONS, Nomis)", "English Indices of Deprivation 2025 (MHCLG)",
                    "Small area income estimates FYE 2023, MSOA (ONS)", "data.police.uk", "FSA hygiene ratings",
                    "LSOA 2021 boundaries (ONS Open Geography)"],
    }, separators=(",", ":")))
    print(f"{len(rows)} LSOAs written; analysis: {json.dumps(analyse(rows)['spearman'])}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    w = root / "work"
    build(w / "latest.parquet", w / "crime_points.parquet", root / "site" / "api" / "v1", w / "reference",
          json.loads((w / "crime_months.json").read_text()))
