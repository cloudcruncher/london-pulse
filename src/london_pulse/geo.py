"""Geospatial outputs built with DuckDB's spatial and H3 extensions.

- hex.geojson: H3 resolution-8 hexagons (~0.7 km2) with venue density, hygiene, new-opening and coffee measures,
  drawn on the map as 3D columns and choropleths.
- venues-geo.parquet: all venues as GeoParquet (WKB point geometry), loadable in QGIS, GeoPandas or DuckDB.

Both are best-effort: if an extension cannot be installed the daily run still publishes everything else.
"""

import json
import re
from pathlib import Path

from .brands import CURATED

H3_RES = 8
MIN_VENUES = 5  # hide hexagons too sparse to say anything about
SPECIALTY_RE = "|".join(rx for _, _, kind, rx in CURATED if kind in ("Specialty coffee", "Bakery"))
CHAIN_RE = "|".join(rx for _, _, kind, rx in CURATED if kind in ("Chain", "Coffee chain"))
COFFEE_RE = "coffee|espresso|roastery|barista|caffe|caffè"


def _ring(wkt: str) -> list[list[float]]:
    pts = re.findall(r"(-?\d+\.\d+) (-?\d+\.\d+)", wkt)
    return [[round(float(x), 5), round(float(y), 5)] for x, y in pts]


def build_geo(con, api_dir: Path, meta: dict) -> None:
    con.execute("INSTALL spatial; LOAD spatial; INSTALL h3 FROM community; LOAD h3;")
    rows = con.execute(f"""
        WITH v AS (
            SELECT *, lp_norm(name) AS nn, h3_latlng_to_cell(lat, lon, {H3_RES}) AS cell FROM fd
            WHERE lat BETWEEN 51.2 AND 51.8 AND lon BETWEEN -0.6 AND 0.4)
        SELECT h3_h3_to_string(cell) AS id, h3_cell_to_boundary_wkt(cell) AS wkt, count(*) AS n,
            count(*) FILTER (rating ~ '^[0-5]$') AS rated,
            count(*) FILTER (rating = '5') AS five,
            count(*) FILTER (rating IN ('0','1','2')) AS low,
            count(*) FILTER (rating = 'AwaitingInspection') AS awaiting,
            count(*) FILTER (regexp_matches(lower(name), '{COFFEE_RE}')) AS coffee,
            count(*) FILTER (business_type = 'Takeaway/sandwich shop') AS takeaway,
            count(*) FILTER (business_type = 'Pub/bar/nightclub') AS pubs,
            count(*) FILTER (regexp_matches(nn, '{SPECIALTY_RE.replace(chr(39), chr(39)*2)}')) AS specialty,
            count(*) FILTER (regexp_matches(nn, '{CHAIN_RE.replace(chr(39), chr(39)*2)}')) AS chains,
            mode(authority) AS borough
        FROM v GROUP BY cell HAVING count(*) >= {MIN_VENUES}""").fetchall()
    feats = []
    for (
        hid,
        wkt,
        n,
        rated,
        five,
        low,
        awaiting,
        coffee,
        takeaway,
        pubs,
        specialty,
        chains,
        borough,
    ) in rows:
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [_ring(wkt)]},
                "properties": {
                    "id": hid,
                    "n": n,
                    "five_pct": round(100 * five / rated, 1) if rated >= 5 else None,
                    "low_pct": round(100 * low / rated, 1) if rated >= 5 else None,
                    "awaiting": awaiting,
                    "awaiting_pct": round(100 * awaiting / n, 1),
                    "coffee": coffee,
                    "takeaway_pct": round(100 * takeaway / n, 1),
                    "pubs": pubs,
                    "specialty": specialty,
                    "chain_pct": round(100 * chains / n, 1),
                    "borough": borough,
                },
            }
        )
    (api_dir / "hex.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                **{k: v for k, v in meta.items()},
                "resolution": H3_RES,
                "features": feats,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    con.execute(f"""COPY (SELECT fhrsid, name, business_type, address, postcode, rating, rating_date, authority,
            ST_Point(lon, lat) AS geometry FROM s WHERE lon IS NOT NULL AND lat IS NOT NULL)
        TO '{api_dir / "venues-geo.parquet"}' (FORMAT parquet, COMPRESSION zstd)""")
