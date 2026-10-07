"""Fetch a snapshot of FSA food hygiene establishments for all London boroughs.

Source: https://api.ratings.food.gov.uk (Open Government Licence v3.0).
"""
import json
import tempfile
import time
import urllib.request
from pathlib import Path

import duckdb

from .london import LONDON_AUTHORITIES

BASE = "https://api.ratings.food.gov.uk"
HEADERS = {"x-api-version": "2", "accept": "application/json"}


def _get(path: str) -> dict:
    for attempt in range(4):
        try:
            req = urllib.request.Request(BASE + path, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def fetch_snapshot(out_parquet: Path) -> int:
    """Download all London establishments and write them as zstd parquet. Returns row count."""
    ids = {a["Name"]: a["LocalAuthorityId"] for a in _get("/Authorities/basic")["authorities"]}
    missing = [n for n in LONDON_AUTHORITIES if n not in ids]
    if missing:
        raise SystemExit(f"Authority names not found in FSA API: {missing}")

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.jsonl"
        with raw.open("w", encoding="utf-8") as f:
            for name in LONDON_AUTHORITIES:
                page = 1
                while True:
                    d = _get(f"/Establishments?localAuthorityId={ids[name]}&pageSize=5000&pageNumber={page}")
                    for e in d["establishments"]:
                        g = e.get("geocode") or {}
                        f.write(json.dumps({
                            "fhrsid": e["FHRSID"],
                            "name": (e.get("BusinessName") or "").strip(),
                            "business_type": e.get("BusinessType"),
                            "address": ", ".join(p for p in (e.get(k) for k in ("AddressLine1", "AddressLine2", "AddressLine3", "AddressLine4")) if p),
                            "postcode": (e.get("PostCode") or "").strip(),
                            "rating": str(e.get("RatingValue")),
                            "rating_date": e.get("RatingDate"),
                            "authority": e.get("LocalAuthorityName"),
                            "lon": float(g["longitude"]) if g.get("longitude") else None,
                            "lat": float(g["latitude"]) if g.get("latitude") else None,
                        }, ensure_ascii=False) + "\n")
                    if page >= d["meta"]["totalPages"]:
                        break
                    page += 1
        con = duckdb.connect()
        out_parquet.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"""
            COPY (SELECT fhrsid::BIGINT fhrsid, name, business_type, address, postcode, rating,
                         try_cast(rating_date AS DATE) AS rating_date, authority, lon, lat
                  FROM read_json('{raw}', format='newline_delimited')
                  WHERE fhrsid IS NOT NULL)
            TO '{out_parquet}' (FORMAT parquet, COMPRESSION zstd)""")
        return con.execute(f"SELECT count(*) FROM '{out_parquet}'").fetchone()[0]
