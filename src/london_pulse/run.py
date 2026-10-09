"""Daily pipeline: fetch snapshot -> diff against previous -> history -> insights.

Usage: python -m london_pulse.run --prev work/prev.parquet
"""
import argparse
import json
from datetime import datetime, timezone

import duckdb
from datetime import date
from pathlib import Path

from .diff import diff_snapshots
from .fsa import fetch_snapshot
from .insights import append_history, build
from .report import write as write_report

ROOT = Path(__file__).resolve().parents[2]
MIN_ROWS = 70_000          # London has ~80k FSA establishments; far fewer means a partial fetch
MAX_DAY_CHANGE = 0.10      # >10% swing in one day is treated as a bad fetch, not real change


def sanity_check(n: int, prev: Path | None) -> int | None:
    """Refuse to publish a partial or corrupted snapshot. Returns the previous row count if any."""
    if n < MIN_ROWS:
        raise SystemExit(f"Snapshot has {n} rows (< {MIN_ROWS}); refusing to publish.")
    if not prev or not prev.exists():
        return None
    prev_n = duckdb.connect().execute(f"SELECT count(*) FROM '{prev}'").fetchone()[0]
    if abs(n - prev_n) / prev_n > MAX_DAY_CHANGE:
        raise SystemExit(f"Row count moved {prev_n} -> {n} (> {MAX_DAY_CHANGE:.0%}); refusing to publish.")
    return prev_n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", type=Path, help="previous snapshot parquet (optional)")
    ap.add_argument("--out", type=Path, default=ROOT / "work" / "latest.parquet")
    args = ap.parse_args()
    today = date.today()

    n = fetch_snapshot(args.out)
    print(f"snapshot: {n} establishments -> {args.out}")
    prev_n = sanity_check(n, args.prev)
    counts = {}
    if args.prev and args.prev.exists():
        counts = diff_snapshots(args.prev, args.out, ROOT / "data" / "events" / f"{today.isoformat()}.csv", today)
        print("changes since previous snapshot:", counts)
    else:
        print("no previous snapshot: baseline day, no diff")
    append_history(args.out, ROOT / "data" / "history.csv", today)
    build(args.out, ROOT / "data" / "events", ROOT / "data" / "history.csv", ROOT / "site" / "api" / "v1", today)
    print("insights written")
    api = ROOT / "site" / "api" / "v1"
    try:  # derived from the committed monthly files (areas, stations) plus today's venues; never blocks the daily run
        print(f"report card data written: {write_report(api)}")
    except Exception as e:  # noqa: BLE001 - the report card must never fail the daily data run
        print(f"::warning::report.json skipped: {e}")
    (api / "status.json").write_text(json.dumps({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": today.isoformat(),
        "source": "Food Standards Agency FHRS open data (ratings.food.gov.uk), Open Government Licence v3.0",
        "rows": n, "previous_rows": prev_n, "changes": counts,
        "schedule": {"fsa": "daily 06:17 UTC", "companies_house": "monthly, 3rd"},
    }))


if __name__ == "__main__":
    main()
