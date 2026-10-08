[![Daily update](https://github.com/cloudcruncher/london-pulse/actions/workflows/daily.yml/badge.svg)](https://github.com/cloudcruncher/london-pulse/actions/workflows/daily.yml)
# London Pulse

Daily, neighbourhood-level signals of what is opening, closing and changing across London's 33 boroughs, built from open data. Live dashboard: https://cloudcruncher.github.io/london-pulse/

## How it works
0. Data is served as a versioned static JSON API under `site/api/v1/` (see `docs/API.md`); the site is an installable PWA (see `docs/APP.md`).
1. `fetch_snapshot` pulls every FSA food-hygiene establishment for the 33 London authorities (Open Government Licence v3.0).
2. `diff_snapshots` (DuckDB) compares today's snapshot with the previous day's: new, removed and re-rated premises, written to `data/events/YYYY-MM-DD.csv`.
3. `insights.build` (DuckDB) aggregates the latest snapshot, history and events into `site/data/insights.json`.
4. A GitHub Action (`.github/workflows/daily.yml`) runs this daily at 06:30 UTC, keeps snapshots as release assets (last 14), commits the small event/history files and publishes `site/` to GitHub Pages.

Run locally: `PYTHONPATH=src uv run python -m london_pulse.run [--prev previous.parquet]`

## Caveats
A premises disappearing from the FSA register can be a closure, a change of owner or an administrative removal. "Awaiting inspection" includes recent openings but is not a count of them. A monthly workflow adds Companies House company formation (coffee roasting, brewing, distilling, pubs and bars) for London postcode districts. Licensing applications are planned. An evaluation of Overture Maps places found it unusable for closures in the 2026-09-23 release (3 of 496,244 London places marked permanently closed, the rest null).

Not affiliated with the Food Standards Agency or any council.
