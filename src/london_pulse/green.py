"""Green space per LSOA: how much of the area is green, and how far the nearest decent park is.

  green_pct  share of the LSOA's area covered by green space (public parks and gardens, playing fields, cemeteries,
             allotments, play spaces), 1 dp, capped at 100. Overlapping sites are unioned before measuring.
  park_m     straight-line metres from the LSOA's polygon centroid to the edge of the nearest public park or garden
             of at least 2 ha (0 if the centroid is inside one): the classic "2 ha within 400 m / 1 km" access standard.

Data: OS Open Greenspace (OGL v3.0), downloaded without an API key from the OS Data Hub downloads API. Only the
GreenspaceSite polygons for the grid squares covering Greater London (TQ, TL) are kept, as a small parquet in ref_dir.
"""
import zipfile
from pathlib import Path

import duckdb

from .http import download

OS_API = "https://api.os.uk/downloads/v1/products/OpenGreenspace/downloads"
OS_AREAS = ("TQ", "TL")   # 100 km grid squares covering Greater London (TL: the Enfield/Barnet fringe)
CACHE = "os_greenspace.parquet"
GREEN_FUNCTIONS = ("Public Park Or Garden", "Playing Field", "Cemetery",
                   "Allotments Or Community Growing Spaces", "Play Space")
PARK_FUNCTION = "Public Park Or Garden"
PARK_MIN_M2 = 20_000   # 2 ha
SOURCES = [
    {"id": "greenspace", "name": "OS Open Greenspace (GreenspaceSite polygons)", "publisher": "Ordnance Survey",
     "url": "https://www.ordnancesurvey.co.uk/products/os-open-greenspace", "licence": "OGL v3.0",
     "vintage": "OS Open Greenspace, April 2026 release",
     "caveat": "Includes some private or restricted green space (e.g. cemeteries, allotments, sports grounds with limited access). "
               "Area of green is not quality or access; distance is in a straight line to the park edge, not a walking route."},
]


def _fetch(ref_dir: Path) -> Path:
    """Download the OS shapefiles for Greater London once; keep only the site polygons (as WKB) in a parquet."""
    out = ref_dir / CACHE
    if out.exists():
        return out
    ref_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    parts = []
    for area in OS_AREAS:
        zpath = ref_dir / f"os_greenspace_{area}.zip"
        download(f"{OS_API}?area={area}&format=ESRI%C2%AE+Shapefile&redirect", zpath)
        with zipfile.ZipFile(zpath) as z:
            stem = next(n for n in z.namelist() if n.endswith(f"{area}_GreenspaceSite.shp"))[:-4]
            tmp = ref_dir / f"os_greenspace_{area}"
            tmp.mkdir(exist_ok=True)
            for ext in ("shp", "shx", "dbf", "prj"):
                (tmp / f"site.{ext}").write_bytes(z.read(f"{stem}.{ext}"))
        zpath.unlink()
        parts.append(f"SELECT function, ST_AsWKB(geom) AS wkb FROM ST_Read('{tmp / 'site.shp'}')")
    con.execute(f"COPY ({' UNION ALL '.join(parts)}) TO '{out}' (FORMAT parquet)")
    for area in OS_AREAS:
        tmp = ref_dir / f"os_greenspace_{area}"
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()
    return out


def compute(lsoa_geojson: Path, ref_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    sites = _fetch(Path(ref_dir))
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"""
        CREATE TABLE lsoa AS
        SELECT f.properties.LSOA21CD AS code,
               ST_MakeValid(ST_Transform(ST_GeomFromGeoJSON(f.geometry), 'EPSG:4326', 'EPSG:27700', true)) AS geom
        FROM (SELECT unnest(features) AS f FROM read_json('{lsoa_geojson}'))""")
    con.execute(f"CREATE TABLE site AS SELECT function, ST_MakeValid(ST_GeomFromWKB(wkb)) AS geom FROM read_parquet('{sites}')")
    return metrics_from_tables(con), SOURCES


def metrics_from_tables(con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    """Tables lsoa(code, geom) and site(function, geom), both EPSG:27700 metres."""
    greens = ", ".join(f"'{f}'" for f in GREEN_FUNCTIONS)
    con.execute(f"""
        CREATE TABLE green AS
        SELECT l.code, least(100.0, 100.0 * ST_Area(ST_Intersection(any_value(l.geom), ST_Union_Agg(s.geom))) / ST_Area(any_value(l.geom))) AS pct
        FROM lsoa l JOIN site s ON ST_Intersects(l.geom, s.geom) AND s.function IN ({greens})
        GROUP BY l.code""")
    con.execute(f"""
        CREATE TABLE bigpark AS
        SELECT geom FROM site WHERE function = '{PARK_FUNCTION}' AND ST_Area(geom) >= {PARK_MIN_M2}""")
    rows = con.execute("""
        SELECT l.code, coalesce(g.pct, 0), (SELECT min(ST_Distance(ST_Centroid(l.geom), p.geom)) FROM bigpark p)
        FROM lsoa l LEFT JOIN green g USING (code)""").fetchall()
    return {code: {"green_pct": round(min(100.0, pct), 1), "park_m": None if d is None else round(d)}
            for code, pct, d in rows}
