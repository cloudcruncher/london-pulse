"""Neighbourhood flood exposure: share of each LSOA's area inside Environment Agency flood extents.

Two open EA datasets, both OGL v3.0, fetched for the London bounding box only and cached in ref_dir:

* Flood Map for Planning, Flood Zones 2 and 3 (rivers and sea): vector polygons via the Defra Data Services Platform WFS.
  Zone 3 = at least 1% (rivers) / 0.5% (sea) annual chance; Zone 2 = at least 0.1%. Modelled WITHOUT flood defences
  (so the Thames Barrier and walls are ignored). rivers_sea_z2_pct is the extent of zone 2 AND zone 3 together
  ("at least 0.1%/yr"), so it is always >= rivers_sea_z3_pct.
* Risk of Flooding from Surface Water (NaFRA2): the EA publishes this for London only as a WMS (no WFS or bulk download
  without an interactive area request), so it is read as 10 m rendered map pixels in 20 km tiles (the layer stops rendering
  coarser than 1:50,000). High = at least 3.3%/yr, Medium = 1% to 3.3%. surface_water_med_pct is High + Medium
  ("at least 1%/yr"). Values are therefore pixel-count estimates, good to a fraction of a percentage point.

Everything is a % of the LSOA's own area (EPSG:27700), rounded to 1 dp and capped at 100. No property-level data.
"""

import json
import re
import struct
import tempfile
import zlib
from pathlib import Path

import duckdb

from .http import (  # noqa: F401  (get_json kept for parity with sibling modules)
    download,
    get_json,
)

CRS = "EPSG:27700"
BBOX = "-0.52,51.28,0.34,51.70"  # London, lon/lat
FZ_DATASET = "04532375-a198-476e-985e-0579a0a11b47"
FZ_WFS = f"https://environment.data.gov.uk/geoservices/datasets/{FZ_DATASET}/wfs"
FZ_TYPE = f"dataset-{FZ_DATASET}:Flood_Zones_2_3_Rivers_and_Sea"
FZ_PAGE = 1000
SW_WMS = "https://environment.data.gov.uk/spatialdata/nafra2-risk-of-flooding-from-surface-water/wms"
SW_TILE_M = 20_000  # tile edge, metres
SW_PX = 2000  # pixels per tile edge -> 10 m pixels
SW_PIXEL_M = SW_TILE_M // SW_PX
# legend colours of the rofsw layer (RGB); matched with a small tolerance to survive anti-aliased edges
SW_HIGH, SW_MED = (85, 91, 157), (154, 159, 222)
SW_TOL = 6

SOURCES = [
    {
        "id": "flood_rivers_sea",
        "name": "Flood Map for Planning (Rivers and Sea), Flood Zones 2 and 3",
        "publisher": "Environment Agency, via the Defra Data Services Platform",
        "url": f"https://environment.data.gov.uk/dataset/{FZ_DATASET}",
        "licence": "OGL v3.0",
        "vintage": "Live service; EA record last updated July 2026",
        "caveat": "Modelled extent of flooding from rivers and the sea ignoring flood defences, so it overstates the risk where defences such as the Thames Barrier exist. Zone 3 is at least a 1 in 100 (rivers) or 1 in 200 (sea) yearly chance; Zone 2 here includes Zone 3 (at least 1 in 1,000). A share of the area, not of homes, and says nothing about any one property.",
    },
    {
        "id": "flood_surface_water",
        "name": "Risk of Flooding from Surface Water (NaFRA2), high and medium likelihood",
        "publisher": "Environment Agency, via the Defra Data Services Platform",
        "url": "https://environment.data.gov.uk/dataset/b5aaa28d-6eb9-460e-8d6f-43caa71fbe0e",
        "licence": "OGL v3.0",
        "vintage": "Live service; EA record last updated July 2026",
        "caveat": "Modelled ponding from heavy rain: high is at least a 1 in 30 yearly chance, medium (as reported here) at least 1 in 100. Measured from 10 m map pixels, so figures are estimates. Drainage is only roughly represented; it flags where water collects, not which buildings flood.",
    },
]
METRICS = (
    "rivers_sea_z3_pct",
    "rivers_sea_z2_pct",
    "surface_water_high_pct",
    "surface_water_med_pct",
)


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute("SET enable_progress_bar = false")
    return con


def _pct(part_m2: float, whole_m2: float) -> float:
    """Share of an area as a percentage, rounded to 1 dp and capped to [0, 100]."""
    if not whole_m2 or whole_m2 <= 0:
        return 0.0
    return round(min(100.0, max(0.0, 100.0 * part_m2 / whole_m2)), 1)


def _load_lsoa(con: duckdb.DuckDBPyConnection, lsoa_geojson: Path) -> None:
    con.execute(f"""CREATE TABLE lsoa AS
        SELECT LSOA21CD AS code,
               ST_MakeValid(ST_Transform(geom, 'EPSG:4326', '{CRS}', always_xy := true)) AS g
        FROM ST_Read('{lsoa_geojson}')""")
    con.execute("UPDATE lsoa SET g = ST_MakeValid(g)")
    con.execute("CREATE TABLE lsoa_area AS SELECT code, ST_Area(g) AS area FROM lsoa")


# ---------- rivers and sea: vector overlay ----------


def fetch_flood_zones(dest: Path) -> None:
    """Page the WFS (London bbox, EPSG:27700) into one GeoJSON FeatureCollection."""
    feats: list[dict] = []
    start = 0
    matched = None
    with tempfile.TemporaryDirectory() as td:
        while True:
            page = Path(td) / f"p{start}.json"
            download(
                f"{FZ_WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames={FZ_TYPE}"
                f"&outputFormat=application/json&srsName={CRS}&count={FZ_PAGE}&startIndex={start}"
                f"&bbox={BBOX},EPSG:4326",
                page,
            )
            doc = json.loads(page.read_text())
            matched = doc.get("numberMatched", matched)
            got = doc["features"]
            feats += got
            if len(got) < FZ_PAGE:
                break
            start += FZ_PAGE
    if not feats:
        raise RuntimeError("flood zones WFS returned no features")
    seen: set = set()
    uniq = [f for f in feats if not (f["id"] in seen or seen.add(f["id"]))]
    if isinstance(matched, int) and len(uniq) != matched:   # a short or unstable page would under-report flood risk
        raise RuntimeError(f"flood zones WFS: got {len(uniq)} features, service says {matched} match")
    out = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}},
        "features": uniq,
    }
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(dest)


def overlay_pct(con: duckdb.DuckDBPyConnection, flood_table: str) -> dict[str, float]:
    """% of each LSOA covered by the union of the polygons in `flood_table` (column g, EPSG:27700).

    Intersections are unioned per LSOA before measuring, so overlapping flood polygons are not double counted.
    """
    rows = con.execute(f"""
        SELECT l.code, ST_Area(ST_Union_Agg(ST_Intersection(l.g, f.g))) AS a, any_value(la.area)
        FROM lsoa l JOIN {flood_table} f ON ST_Intersects(l.g, f.g) JOIN lsoa_area la ON la.code = l.code
        GROUP BY l.code""").fetchall()
    return {code: _pct(a, area) for code, a, area in rows}


# ---------- surface water: WMS pixels ----------


def _tiff_rgba(b: bytes) -> tuple[bytes, bytes, bytes, bytes]:
    """Return R, G, B, A planes of an uncompressed 8-bit RGBA TIFF as returned by the EA WMS."""
    if b[:2] not in (b"MM", b"II"):
        raise ValueError("WMS did not return a TIFF: " + b[:200].decode("latin1"))
    bo = ">" if b[:2] == b"MM" else "<"

    off = struct.unpack(bo + "I", b[4:8])[0]
    n = struct.unpack(bo + "H", b[off : off + 2])[0]
    tags = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack(
            bo + "HHI4s", b[off + 2 + 12 * i : off + 14 + 12 * i]
        )
        tags[tag] = (typ, cnt, val)
    dim = lambda t: struct.unpack(bo + ("H" if tags[t][0] == 3 else "I"), tags[t][2][: 2 if tags[t][0] == 3 else 4])[0]  # noqa: E731
    if dim(256) != SW_PX or dim(257) != SW_PX:
        raise ValueError(f"WMS tile is {dim(256)}x{dim(257)}, expected {SW_PX}x{SW_PX}")
    if (
        struct.unpack(bo + "H", tags[259][2][:2])[0] != 1
        or struct.unpack(bo + "H", tags[277][2][:2])[0] != 4
    ):
        raise ValueError("unexpected TIFF layout (need uncompressed RGBA)")
    cnt = tags[273][1]
    so = struct.unpack(bo + "I", tags[273][2])[0] if cnt > 1 else None
    co = struct.unpack(bo + "I", tags[279][2])[0] if cnt > 1 else None
    offs = (
        struct.unpack(bo + f"{cnt}I", b[so : so + 4 * cnt])
        if so
        else (struct.unpack(bo + "I", tags[273][2])[0],)
    )
    cnts = (
        struct.unpack(bo + f"{cnt}I", b[co : co + 4 * cnt])
        if co
        else (struct.unpack(bo + "I", tags[279][2])[0],)
    )
    d = b"".join(b[o : o + c] for o, c in zip(offs, cnts))
    return d[0::4], d[1::4], d[2::4], d[3::4]


def _band(plane: bytes, centre: int) -> int:
    """Big-int mask (0xFF where |value - centre| <= tolerance) so channels can be combined with fast bitwise AND."""
    tbl = bytes(255 if abs(v - centre) <= SW_TOL else 0 for v in range(256))
    return int.from_bytes(plane.translate(tbl), "big")


def classify_tile(tiff: bytes) -> tuple[bytes, bytes]:
    """(high_plane, medplus_plane): one byte per pixel, 1 where the pixel is High / High-or-Medium risk."""
    r, g, b, a = _tiff_rgba(tiff)
    if not any(v >= 128 for v in set(a)):
        # every London tile has at least the low-risk class drawn; a blank one is a service fault, not "no flood risk"
        raise ValueError("WMS tile is fully transparent")
    opaque = int.from_bytes(
        a.translate(bytes(255 if v >= 128 else 0 for v in range(256))), "big"
    )
    size = len(r)

    def match(rgb: tuple[int, int, int]) -> int:
        return _band(r, rgb[0]) & _band(g, rgb[1]) & _band(b, rgb[2]) & opaque

    high, med = match(SW_HIGH), match(SW_MED)
    one = int.from_bytes(b"\x01" * size, "big")
    return (
        (high & one).to_bytes(size, "big"),
        ((high | med) & one).to_bytes(size, "big"),
    )


def _tile_planes(cache: Path, x0: int, y0: int) -> tuple[bytes, bytes]:
    """Cached (high, medplus) planes for the 20 km tile whose south-west corner is (x0, y0)."""
    f = cache / f"rofsw_{SW_PIXEL_M}m_{x0}_{y0}.zlib"
    n = SW_PX * SW_PX
    if f.exists() and f.stat().st_size:
        raw = zlib.decompress(f.read_bytes())
        return raw[:n], raw[n:]
    with tempfile.TemporaryDirectory() as td:
        t = Path(td) / "t.tif"
        download(
            f"{SW_WMS}?service=WMS&version=1.3.0&request=GetMap&layers=rofsw&styles=&crs={CRS}"
            f"&bbox={x0},{y0},{x0 + SW_TILE_M},{y0 + SW_TILE_M}&width={SW_PX}&height={SW_PX}"
            f"&format=image/tiff&transparent=true",
            t,
            timeout=300,
        )
        high, med = classify_tile(t.read_bytes())
    tmp = f.with_name(f.name + ".part")
    tmp.write_bytes(zlib.compress(high + med, 6))
    tmp.replace(f)
    return high, med


def _runs(plane: bytes, x0: int, y0: int) -> list[tuple[int, int, int]]:
    """Horizontal runs of flagged pixels as (first pixel-centre easting, northing of the row, pixel count)."""
    out = []
    for row in range(SW_PX):
        line = plane[row * SW_PX : (row + 1) * SW_PX]
        if line.find(1) < 0:
            continue
        y = y0 + SW_TILE_M - (row + 0.5) * SW_PIXEL_M
        for m in re.finditer(rb"\x01+", line):
            out.append(
                (int(x0 + (m.start() + 0.5) * SW_PIXEL_M), int(y), m.end() - m.start())
            )
    return out


def pixel_pct(con: duckdb.DuckDBPyConnection, runs_csv: Path) -> dict[str, float]:
    """% of each LSOA covered by the flagged pixels (runs expanded to pixel-centre points, joined with ST_Within)."""
    con.execute(f"""CREATE OR REPLACE TABLE pts AS
        SELECT ST_Point(x + i * {SW_PIXEL_M}, y) AS g
        FROM (SELECT x, y, unnest(range(n)) AS i FROM read_csv('{runs_csv}', columns={{'x':'INT','y':'INT','n':'INT'}}))""")
    rows = con.execute(f"""
        SELECT l.code, count(*) * {SW_PIXEL_M * SW_PIXEL_M}, any_value(la.area)
        FROM lsoa l JOIN pts p ON ST_Within(p.g, l.g) JOIN lsoa_area la ON la.code = l.code
        GROUP BY l.code""").fetchall()
    return {code: _pct(a, area) for code, a, area in rows}


def _tiles(con: duckdb.DuckDBPyConnection) -> list[tuple[int, int]]:
    xmin, ymin, xmax, ymax = con.execute(
        "SELECT min(ST_XMin(g)), min(ST_YMin(g)), max(ST_XMax(g)), max(ST_YMax(g)) FROM lsoa"
    ).fetchone()
    out = []
    for x in range(int(xmin // SW_TILE_M) * SW_TILE_M, int(xmax) + 1, SW_TILE_M):
        for y in range(int(ymin // SW_TILE_M) * SW_TILE_M, int(ymax) + 1, SW_TILE_M):
            hit = con.execute(
                f"SELECT count(*) FROM lsoa WHERE ST_Intersects(g, ST_MakeEnvelope({x},{y},{x + SW_TILE_M},{y + SW_TILE_M}))"
            ).fetchone()[0]
            if hit:
                out.append((x, y))
    return out


def compute(lsoa_geojson: Path, ref_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    """Per-LSOA flood metrics (see module docstring) and the SOURCES entries describing them."""
    ref_dir = Path(ref_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)
    fz = ref_dir / "flood_zones_rivers_sea.geojson"
    if not fz.exists() or not fz.stat().st_size:
        fetch_flood_zones(fz)

    con = _con()
    _load_lsoa(con, Path(lsoa_geojson))
    con.execute(f"""CREATE TABLE fz AS
        SELECT flood_zone, ST_MakeValid(geom) AS g FROM ST_Read('{fz}') WHERE flood_zone IN ('FZ2', 'FZ3')""")
    z3 = overlay_pct(con, "(SELECT g FROM fz WHERE flood_zone = 'FZ3')")
    z2 = overlay_pct(con, "fz")  # zone 2 and zone 3 together

    hi_runs: list[tuple[int, int, int]] = []
    med_runs: list[tuple[int, int, int]] = []
    for x0, y0 in _tiles(con):
        high, med = _tile_planes(ref_dir, x0, y0)
        hi_runs += _runs(high, x0, y0)
        med_runs += _runs(med, x0, y0)
    sw: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory() as td:
        for key, runs in (("high", hi_runs), ("med", med_runs)):
            p = Path(td) / f"{key}.csv"
            p.write_text(
                "x,y,n\n" + "\n".join(f"{x},{y},{n}" for x, y, n in runs) + "\n"
            )
            sw[key] = pixel_pct(con, p) if runs else {}

    metrics: dict[str, dict] = {}
    for (code,) in con.execute("SELECT code FROM lsoa").fetchall():
        metrics[code] = {
            "rivers_sea_z3_pct": z3.get(code, 0.0),
            "rivers_sea_z2_pct": max(z2.get(code, 0.0), z3.get(code, 0.0)),
            "surface_water_high_pct": sw["high"].get(code, 0.0),
            "surface_water_med_pct": max(
                sw["med"].get(code, 0.0), sw["high"].get(code, 0.0)
            ),
        }
    return metrics, [dict(s) for s in SOURCES]


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    m, _ = compute(
        root / "work" / "reference" / "lsoa.geojson", root / "work" / "reference"
    )
    print(f"{len(m)} LSOAs")
