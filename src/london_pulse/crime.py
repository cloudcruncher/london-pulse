"""Street-level crime for Greater London from data.police.uk, aggregated to a ~500 m grid.

Only counts per grid cell and category are published (never individual crimes). Writes site/api/v1/crime.json.
Run monthly: python -m london_pulse.crime
"""
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import duckdb

from .http import get_json

API = "https://data.police.uk/api"
CELL = 0.005            # output grid (degrees, ~550 m x 350 m)
QUERY_CELL = 0.01       # request area; split in four if the API says there are too many results
MONTHS = 3


def month_list(last: str, n: int) -> list[str]:
    y, m = int(last[:4]), int(last[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]   # oldest first


def fetch_cell(lat0: float, lon0: float, size: float, month: str, depth: int = 0) -> list[tuple[int, int, str]]:
    lat1, lon1 = lat0 + size, lon0 + size
    poly = f"{lat0:.5f},{lon0:.5f}:{lat0:.5f},{lon1:.5f}:{lat1:.5f},{lon1:.5f}:{lat1:.5f},{lon0:.5f}"
    try:
        crimes = get_json(f"{API}/crimes-street/all-crime?poly={poly}&date={month}")
    except Exception:  # noqa: BLE001 - 503 (>10k results) or 400: split, otherwise give up on this cell
        if depth >= 2:
            return []
        h = size / 2
        return [r for dy in (0, h) for dx in (0, h) for r in fetch_cell(lat0 + dy, lon0 + dx, h, month, depth + 1)]
    out = []
    for c in crimes:
        loc = c.get("location") or {}
        try:
            out.append((math.floor(float(loc["longitude"]) / CELL), math.floor(float(loc["latitude"]) / CELL), c["category"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def build(fsa_parquet: Path, api_dir: Path, months: int = MONTHS) -> None:
    last = get_json(f"{API}/crime-last-updated")["date"][:7]
    ms = month_list(last, months)
    bbox = duckdb.sql(f"SELECT min(lon), max(lon), min(lat), max(lat) FROM '{fsa_parquet}' WHERE lon IS NOT NULL AND lat IS NOT NULL AND lat BETWEEN 51.2 AND 51.8 AND lon BETWEEN -0.6 AND 0.4").fetchone()
    occupied = {(math.floor(lon / QUERY_CELL), math.floor(lat / QUERY_CELL)) for lon, lat in
                duckdb.sql(f"SELECT lon, lat FROM '{fsa_parquet}' WHERE lat BETWEEN 51.2 AND 51.8 AND lon BETWEEN -0.6 AND 0.4").fetchall()}
    cells = sorted({(x + dx, y + dy) for x, y in occupied for dx in (-1, 0, 1) for dy in (-1, 0, 1)})
    print(f"months {ms}; {len(cells)} query cells in {bbox}")
    counts: dict[tuple, dict] = {}
    cats: list[str] = []
    for mi, month in enumerate(ms):
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda c: fetch_cell(c[1] * QUERY_CELL, c[0] * QUERY_CELL, QUERY_CELL, month), cells))
        total = 0
        for rows in results:
            for ix, iy, cat in rows:
                if cat not in cats:
                    cats.append(cat)
                rec = counts.setdefault((ix, iy), {"m": [0] * len(ms), "c": {}})
                rec["m"][mi] += 1
                if mi == len(ms) - 1:
                    rec["c"][cat] = rec["c"].get(cat, 0) + 1
                total += 1
        print(f"{month}: {total} crimes")
    cats.sort()
    rows = [[ix, iy, [rec["c"].get(c, 0) for c in cats], *rec["m"][:-1]] for (ix, iy), rec in sorted(counts.items())]
    (api_dir / "crime.json").write_text(json.dumps(
        {"schema_version": 1, "as_of": ms[-1], "generated": date.today().isoformat(), "months": ms, "cell": CELL,
         "categories": cats, "cells": rows, "source": "data.police.uk (Open Government Licence)"},
        separators=(",", ":")))
    print(f"{len(rows)} grid cells written")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    build(root / "work" / "latest.parquet", root / "site" / "api" / "v1")
