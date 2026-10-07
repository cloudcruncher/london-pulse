"""Daily pipeline: fetch snapshot -> diff against previous -> history -> insights.

Usage: python -m london_pulse.run --prev work/prev.parquet
"""
import argparse
from datetime import date
from pathlib import Path

from .diff import diff_snapshots
from .fsa import fetch_snapshot
from .insights import append_history, build

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", type=Path, help="previous snapshot parquet (optional)")
    ap.add_argument("--out", type=Path, default=ROOT / "work" / "latest.parquet")
    args = ap.parse_args()
    today = date.today()

    n = fetch_snapshot(args.out)
    print(f"snapshot: {n} establishments -> {args.out}")
    if args.prev and args.prev.exists():
        counts = diff_snapshots(args.prev, args.out, ROOT / "data" / "events" / f"{today.isoformat()}.csv", today)
        print("changes since previous snapshot:", counts)
    else:
        print("no previous snapshot: baseline day, no diff")
    append_history(args.out, ROOT / "data" / "history.csv", today)
    build(args.out, ROOT / "data" / "events", ROOT / "data" / "history.csv", ROOT / "site" / "data" / "insights.json", today)
    print("insights written")


if __name__ == "__main__":
    main()
