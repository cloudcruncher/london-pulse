"""Transport noise around an area: share of each LSOA's land inside the modelled Lden >= 55 dB (and >= 65 dB) contour.

Source: Defra / Environmental Noise Directive strategic noise mapping, England Round 4 (2022, modelled for 2021), served
by the Defra Data Services Platform as 10 m Lden rasters (WCS, EPSG:27700). Three layers, London extent only:
  road   major roads (Road_Noise_Lden_England_Round_4_Maj)
  rail   major railways (Rail_Noise_Lden_England_Round_4_Maj)
  air    all modelled airports (Airport_Noise_ALL_Lden; Heathrow, London City, Gatwick, Luton, Stansted, Southend)
Per LSOA, <type>_noise_55_pct / <type>_noise_65_pct = % of the LSOA's area covered by cells at or above that level (1 dp).

The rasters are read with a small stdlib GeoTIFF reader (no numpy/GDAL), turned into disjoint rectangles of cells at or
above each threshold (so their union is the contour), and overlaid with the LSOAs in duckdb spatial on EPSG:27700.
These are modelled contours for major roads, railways and airports only, not a measurement at any address.

compute() caches the downloaded rasters in ref_dir. Run standalone: python -m london_pulse.noise
"""

import array
import itertools
import struct
import sys
from pathlib import Path

import duckdb

from .http import download

WCS = "https://environment.data.gov.uk/spatialdata/{svc}/wcs"
# London in British National Grid (covers the lon/lat box -0.52,51.28,0.34,51.70 with a margin)
BNG_BBOX = (500000, 150000, 565000, 205000)
SCALE = 0.5  # request 20 m cells rather than 10 m: ~4x less data, the share of an LSOA barely moves
THRESHOLDS = (55, 65)  # dB Lden
TOP = 130.0  # anything above is a nodata sentinel, not a noise level
LAYERS = {  # key -> (WCS service, coverage id)
    "road": (
        "road-noise-all-metrics-england-round-4",
        "562c9d56-7c2d-4d42-83bb-578d6e97a517__Road_Noise_Lden_England_Round_4_Maj",
    ),
    "rail": (
        "noise-data",
        "3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947__Rail_Noise_Lden_England_Round_4_Maj",
    ),
    "air": (
        "airport-noise-all-metrics-england-round-4",
        "dac9cba4-abe7-43bd-b8e9-8a83da52edd8__Airport_Noise_ALL_Lden",
    ),
}
_OGL = "OGL v3.0"
_CAVEAT = (
    "Modelled contours for major roads, railways and airports only (not local traffic, nightlife or construction); "
    "not a measurement at any address. Share of the neighbourhood's area at or above the stated Lden level "
    "(a 24-hour weighted average, evening and night penalised)."
)
_PAGE = "https://environment.data.gov.uk/dataset/"
SOURCES = [
    {
        "id": "noise_road",
        "name": "Road Noise: All Metrics, England Round 4 (Lden, major roads)",
        "publisher": "Defra, Environmental Noise Directive strategic noise mapping (Data Services Platform)",
        "url": _PAGE + "562c9d56-7c2d-4d42-83bb-578d6e97a517",
        "licence": _OGL,
        "vintage": "Round 4, published 2022; traffic of 2021",
        "caveat": _CAVEAT,
    },
    {
        "id": "noise_rail",
        "name": "Rail Noise: All Metrics, England Round 4 (Lden, major railways)",
        "publisher": "Defra, Environmental Noise Directive strategic noise mapping (Data Services Platform)",
        "url": _PAGE + "3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947",
        "licence": _OGL,
        "vintage": "Round 4, published 2022; traffic of 2021",
        "caveat": _CAVEAT,
    },
    {
        "id": "noise_air",
        "name": "Airport Noise: All Metrics, England Round 4 (Lden)",
        "publisher": "Defra, Environmental Noise Directive strategic noise mapping (Data Services Platform)",
        "url": _PAGE + "dac9cba4-abe7-43bd-b8e9-8a83da52edd8",
        "licence": _OGL,
        "vintage": "Round 4, published 2022; flights of 2021",
        "caveat": _CAVEAT,
    },
]
_TYPES = {"road": "noise_road", "rail": "noise_rail", "air": "noise_air"}


def fetch_layer(key: str, dest: Path) -> None:
    svc, cov = LAYERS[key]
    x0, y0, x1, y1 = BNG_BBOX
    download(
        f"{WCS.format(svc=svc)}?service=WCS&version=2.0.1&request=GetCoverage&coverageId={cov}&format=image/tiff"
        f"&subset=E({x0},{x1})&subset=N({y0},{y1})&scaleFactor={SCALE}",
        dest,
        timeout=600,
    )


def read_geotiff(path: Path):
    """(rows, x0, y_top, pixel) of a single-band float32/float64 GeoTIFF (tiled or stripped, uncompressed) as a list of arrays."""
    b = Path(path).read_bytes()
    e = ">" if b[:2] == b"MM" else "<"
    if struct.unpack(e + "H", b[2:4])[0] != 42:
        raise ValueError("not a classic TIFF")
    ifd = struct.unpack(e + "I", b[4:8])[0]
    n = struct.unpack(e + "H", b[ifd : ifd + 2])[0]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 12: 8, 16: 8}
    fmts = {1: "B", 2: "B", 3: "H", 4: "I", 12: "d", 16: "Q"}
    tags = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack(
            e + "HHI4s", b[ifd + 2 + 12 * i : ifd + 14 + 12 * i]
        )
        nbytes = sizes[typ] * cnt
        raw = (
            val[:nbytes]
            if nbytes <= 4
            else b[struct.unpack(e + "I", val)[0] :][:nbytes]
        )
        if typ in fmts:
            tags[tag] = struct.unpack(e + f"{cnt}{fmts[typ]}", raw)
    one = lambda t: tags[t][0]
    bits = one(258)
    if one(259) != 1 or bits not in (32, 64) or tags.get(339, (3,))[0] != 3:
        raise ValueError("expected uncompressed float32/float64 GeoTIFF")
    code, size = ("f", 4) if bits == 32 else ("d", 8)
    w, h = one(256), one(257)
    rows = [array.array(code, bytes(size * w)) for _ in range(h)]
    if 322 in tags:  # tiled
        tw, th = one(322), one(323)
        across = -(-w // tw)
        blocks = [
            (off, cnt, (i % across) * tw, (i // across) * th, tw, th)
            for i, (off, cnt) in enumerate(zip(tags[324], tags[325]))
        ]
    else:  # strips
        rps = tags.get(278, (h,))[0]
        blocks = [
            (off, cnt, 0, i * rps, w, rps)
            for i, (off, cnt) in enumerate(zip(tags[273], tags[279]))
        ]
    for off, cnt, bx, by, bw, bh in blocks:
        for r in range(bh):
            if by + r >= h:
                break
            a = array.array(code, b[off + size * bw * r : off + size * bw * (r + 1)])
            if (e == ">") == (sys.byteorder == "little"):
                a.byteswap()
            n_in = min(bw, w - bx)
            rows[by + r][bx : bx + n_in] = a[:n_in]
    m = tags[34264]  # ModelTransformation: [sx 0 0 x0, 0 -sy 0 y0, ...]
    return rows, m[3], m[7], m[0]


def rects_from_rows(rows, thr: float, x0: float, y_top: float, px: float):
    """Disjoint (xmin, ymin, xmax, ymax) rectangles covering exactly the cells with thr <= value <= TOP.

    Horizontal runs are merged down into taller rectangles when identical on consecutive rows.
    """
    out, open_ = [], {}
    for r, row in enumerate(itertools.chain(rows, [()])):
        runs = set()
        if row and thr <= max(row):
            c = 0
            for hit, grp in itertools.groupby(row, key=lambda v: thr <= v <= TOP):
                n = sum(1 for _ in grp)
                if hit:
                    runs.add((c, c + n))
                c += n
        for key in [k for k in open_ if k not in runs]:
            r0 = open_.pop(key)
            out.append(
                (x0 + key[0] * px, y_top - r * px, x0 + key[1] * px, y_top - r0 * px)
            )
        for key in runs:
            open_.setdefault(key, r)
    return out


def overlay_pct(
    con: duckdb.DuckDBPyConnection, rects, lsoa_table: str = "noise_lsoa"
) -> dict[str, float]:
    """% of each LSOA (geom in a projected CRS, metres) covered by the union of the rectangles, 1 dp, capped at 100.

    Rectangles must be disjoint (as rects_from_rows yields), so summing their intersections equals the union's.
    """
    con.execute("DROP TABLE IF EXISTS noise_rect")
    con.execute("CREATE TEMP TABLE noise_rect (geom GEOMETRY)")
    for i in range(0, len(rects), 20000):   # bulk insert: row-by-row executemany takes minutes for ~100k rectangles
        vals = ",".join(f"({a!r},{b!r},{c!r},{d!r})" for a, b, c, d in rects[i:i + 20000])
        con.execute(f"INSERT INTO noise_rect SELECT ST_MakeEnvelope(a, b, c, d) FROM (VALUES {vals}) t(a, b, c, d)")
    rows = con.execute(f"""
        SELECT l.code, least(100.0, round(100.0 * coalesce(sum(CASE WHEN ST_Within(m.geom, l.geom) THEN ST_Area(m.geom)
                                     ELSE ST_Area(ST_Intersection(l.geom, m.geom)) END), 0) / l.area, 1))
        FROM {lsoa_table} l LEFT JOIN noise_rect m ON ST_Intersects(l.geom, m.geom)
        GROUP BY l.code, l.area""").fetchall()
    return {c: float(p) for c, p in rows}


SKIPPED: list[str] = []   # layers dropped in the last compute(); environment.py reports them


def compute(lsoa_geojson: Path, ref_dir: Path) -> tuple[dict[str, dict], list[dict]]:
    """Per-LSOA noise shares {code: {road_noise_55_pct, ..., air_noise_65_pct}} and the sources that actually loaded.

    A layer that cannot be fetched or read is skipped (its keys are absent) so the others still work.
    """
    SKIPPED.clear()
    ref_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"""
        CREATE TABLE noise_lsoa AS
        SELECT code, geom, ST_Area(geom) AS area FROM (
            SELECT f.properties.LSOA21CD AS code,
                   ST_MakeValid(ST_Transform(ST_GeomFromGeoJSON(f.geometry), 'EPSG:4326', 'EPSG:27700', always_xy := true)) AS geom
            FROM (SELECT unnest(features) AS f FROM read_json('{lsoa_geojson}')))
        WHERE ST_Area(geom) > 0""")
    metrics: dict[str, dict] = {
        c: {} for (c,) in con.execute("SELECT code FROM noise_lsoa").fetchall()
    }
    sources = []
    for key in LAYERS:
        path = ref_dir / f"noise_{key}_lden_r4.tif"
        try:
            if not path.exists():
                fetch_layer(key, path)
            rows, x0, y_top, px = read_geotiff(path)
        except Exception as e:  # noqa: BLE001 - one unobtainable layer must not sink the rest
            path.unlink(missing_ok=True)   # an error page saved as .tif must not be reused from the cache
            SKIPPED.append(f"noise/{key}: {e}"[:300])
            print(f"noise: {key} layer unavailable ({e}); skipped", file=sys.stderr)
            continue
        for thr in THRESHOLDS:
            for code, pct in overlay_pct(
                con, rects_from_rows(rows, thr, x0, y_top, px)
            ).items():
                metrics[code][f"{key}_noise_{thr}_pct"] = pct
        sources.append(next(s for s in SOURCES if s["id"] == _TYPES[key]))
    return metrics, sources


if __name__ == "__main__":
    m, s = compute(Path("work/reference/lsoa.geojson"), Path("work/reference"))
    print(len(m), "LSOAs;", [x["id"] for x in s])
