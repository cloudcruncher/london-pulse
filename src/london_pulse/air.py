"""Modelled annual-mean air pollution per LSOA, from the London Atmospheric Emissions Inventory (LAEI).

The LAEI (GLA / King's College London / Ricardo) models concentrations on a 20 m grid across Greater London.
Per LSOA we take the mean of all grid cells whose centre falls inside it (nearest cell if none does), for:
  no2   nitrogen dioxide, annual mean, ug/m3
  pm25  PM2.5, annual mean, ug/m3
  pm10  PM10, annual mean, ug/m3
GUIDELINES holds the WHO 2021 guideline values and UK legal limits so the UI can compare.

Python API: compute(lsoa_geojson, ref_dir) -> (metrics by LSOA21CD, sources). Needs work/reference/lsoa.geojson.
"""
import zipfile
from pathlib import Path

import duckdb

from .http import download

LAEI_YEAR = 2022
DATASET = "https://data.london.gov.uk/dataset/london-atmospheric-emissions-inventory-laei-2022-2lg5g"
CSV_ZIP = "https://data.london.gov.uk/download/2lg5g/7jy/LAEI2022-Concentrations-Data-CSV.zip"
GRID_FILES = {"no2": "LAEI2022_V1_NO2.csv", "pm25": "LAEI2022_V1_PM25.csv", "pm10": "LAEI2022_V1_PM10m.csv"}  # PM10m = annual mean
CELLS = "laei2022_cells.parquet"   # compact cache: x, y (OSGB36 metres), no2, pm25, pm10; ~100 MB vs ~190 MB zip + 400 MB CSVs
NEAREST_RADIUS_M = 1000            # how far to look for the nearest cell when no cell centre falls inside an LSOA

# ug/m3, annual mean. who: WHO global air quality guidelines 2021. uk_limit: legal limits in England
# (NO2 and PM10 limits in force; PM2.5 20 is the Air Quality Standards Regulations 2010 limit).
# uk_target_2040: Environmental Targets (Fine Particulate Matter) (England) Regulations 2023, PM2.5 annual mean 10 by 2040.
GUIDELINES = {
    "no2": {"who": 10, "uk_limit": 40},
    "pm25": {"who": 5, "uk_limit": 20, "uk_target_2040": 10},
    "pm10": {"who": 15, "uk_limit": 40},
}
SOURCES = [
    {"id": "air", "name": "London Atmospheric Emissions Inventory (LAEI) 2022: modelled annual-mean concentrations (NO2, PM2.5, PM10)",
     "publisher": "Greater London Authority (London Datastore), modelled by Ricardo / King's College London",
     "url": DATASET, "licence": "Greater London Authority open data, London Datastore terms (typically UK OGL v3.0)",
     "vintage": "Modelled for calendar year 2022 (published 2026); older than today",
     "caveat": "A computer model of the whole city on a 20 m grid, not a measurement at your street. Each neighbourhood figure is the average of the grid cells inside it, so a busy road in an otherwise quiet area is smoothed out. It describes 2022, and traffic and fleet have changed since. WHO 2021 guideline values (NO2 10, PM2.5 5, PM10 15 ug/m3) are far stricter than the UK legal limits (NO2 40, PM10 40, PM2.5 20; England's PM2.5 target is 10 by 2040)."},
]


def _build_cells(con: duckdb.DuckDBPyConnection, zip_path: Path, dest: Path) -> None:
    """Join the three annual-mean grids on (x, y) and write them to one parquet file."""
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.mkdir(exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = {m.rsplit("/", 1)[-1]: m for m in z.namelist()}
            for f in GRID_FILES.values():
                (tmp / f).write_bytes(z.read(members[f]))   # one 134 MB file at a time
        rd = lambda k: f"read_csv('{tmp / GRID_FILES[k]}', header=true, columns={{'x': 'INT', 'y': 'INT', 'conc': 'DOUBLE'}})"
        con.execute(f"""
            COPY (SELECT a.x, a.y, a.conc AS no2, b.conc AS pm25, c.conc AS pm10
                  FROM {rd('no2')} a JOIN {rd('pm25')} b USING (x, y) JOIN {rd('pm10')} c USING (x, y))
            TO '{dest.with_name(dest.name + '.part')}' (FORMAT parquet)""")
        dest.with_name(dest.name + ".part").replace(dest)
    finally:
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()


def fetch_cells(ref_dir: Path) -> Path:
    """Download (once, cached in ref_dir) the LAEI 2022 grids and reduce them to a parquet file of cells."""
    dest = ref_dir / CELLS
    if dest.exists():
        return dest
    ref_dir.mkdir(parents=True, exist_ok=True)
    zip_path = ref_dir / "laei2022_csv.zip"
    download(CSV_ZIP, zip_path, timeout=600)
    try:
        _build_cells(duckdb.connect(), zip_path, dest)
    finally:
        zip_path.unlink(missing_ok=True)
    return dest


def aggregate(con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    """Mean concentration per LSOA from tables lsoa(code, geom in EPSG:27700) and cells(x, y, no2, pm25, pm10).

    Cell centres inside an LSOA are averaged; an LSOA with none takes the nearest cell within NEAREST_RADIUS_M.
    """
    con.execute("""
        CREATE OR REPLACE TABLE hit AS
        SELECT l.code, avg(c.no2) AS no2, avg(c.pm25) AS pm25, avg(c.pm10) AS pm10, count(*) AS cells
        FROM lsoa l JOIN cells c ON ST_Contains(l.geom, ST_Point(c.x, c.y)) GROUP BY l.code""")
    con.execute(f"""
        CREATE OR REPLACE TABLE near AS
        SELECT code, no2, pm25, pm10, 0 AS cells FROM (
            SELECT l.code, c.no2, c.pm25, c.pm10,
                   row_number() OVER (PARTITION BY l.code ORDER BY ST_Distance(l.geom, ST_Point(c.x, c.y))) AS rn
            FROM (SELECT * FROM lsoa WHERE code NOT IN (SELECT code FROM hit)) l
            JOIN cells c ON c.x BETWEEN ST_XMin(l.geom) - {NEAREST_RADIUS_M} AND ST_XMax(l.geom) + {NEAREST_RADIUS_M}
                        AND c.y BETWEEN ST_YMin(l.geom) - {NEAREST_RADIUS_M} AND ST_YMax(l.geom) + {NEAREST_RADIUS_M})
        WHERE rn = 1""")
    rows = con.execute("SELECT * FROM hit UNION ALL SELECT * FROM near").fetchall()
    return {code: {"no2": round(no2, 1), "pm25": round(pm25, 1), "pm10": round(pm10, 1)}
            for code, no2, pm25, pm10, _ in rows}


def compute(lsoa_geojson: Path, ref_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    """Per-LSOA modelled annual-mean NO2, PM2.5 and PM10 (ug/m3, 1 dp) plus the source records."""
    cells = fetch_cells(Path(ref_dir))
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"""
        CREATE TABLE lsoa AS
        SELECT f.properties.LSOA21CD AS code,
               ST_Transform(ST_GeomFromGeoJSON(f.geometry), 'EPSG:4326', 'EPSG:27700', always_xy := true) AS geom
        FROM (SELECT unnest(features) AS f FROM read_json('{lsoa_geojson}'))""")
    con.execute(f"CREATE VIEW cells AS SELECT * FROM read_parquet('{cells}')")
    return aggregate(con), SOURCES
