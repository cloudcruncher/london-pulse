[![Daily update](https://github.com/cloudcruncher/london-pulse/actions/workflows/daily.yml/badge.svg)](https://github.com/cloudcruncher/london-pulse/actions/workflows/daily.yml)
# London Pulse

Daily, neighbourhood-level signals of what is opening, closing and changing across London's 33 boroughs, built from open data. Live dashboard: https://cloudcruncher.github.io/london-pulse/

## What the site shows
Tabs: Overview, Insights, Map, Area guide, Report card, Changes, Boroughs, Brands, Craft drinks, SQL lab, About.

- **Map and Insights:** every food and drink premises, H3 hexagon layers, and plain-English stories from the data.
- **Area guide** (postcode in, client side): nearby food and drink, brands, nearest stations, recorded crime, and who lives nearby (council tenure, income, deprivation). **Environment nearby** adds flood zones (rivers and sea) and surface-water flood risk from the Environment Agency, road, rail and aircraft noise from Defra (round 4) and green space with distance to a park from OS Open Greenspace.
- **Report card:** a postcode placed against London on crime, deprivation, venues, walking time to a station, income, sale prices (Land Registry) and rents (ONS).
- **Changes, Boroughs, Brands, Craft drinks:** day-over-day openings, removals and re-ratings; per-borough tables; brand tracker; Companies House formation of roasters, breweries, distilleries, pubs and bars.
- **SQL lab:** DuckDB-WASM over the published Parquet.

Signals, not verdicts: the site never labels an area safe or unsafe, shows tenure and crime as separate facts, and flood zones are modelled ignoring defences such as the Thames Barrier.

## How it works
Data is served as a versioned static JSON API under `site/api/v1/` (see `docs/API.md`); the site is an installable PWA (see `docs/APP.md`). Design in `docs/ARCHITECTURE.md`, schedules and safeguards in `docs/REFRESH.md`.

1. `fetch_snapshot` pulls every FSA food-hygiene establishment for the 33 London authorities (Open Government Licence v3.0).
2. `diff_snapshots` (DuckDB) compares today's snapshot with the previous day's: new, removed and re-rated premises, written to `data/events/YYYY-MM-DD.csv`.
3. `insights.build` (DuckDB) aggregates the latest snapshot, history and events into the JSON and Parquet files in `site/api/v1/`; `report.write` builds the report card distributions from the committed monthly files.
4. `.github/workflows/daily.yml` runs this twice a day (06:17 and 12:17 UTC; the FSA sometimes publishes late and the second run is idempotent), gates on contract and browser smoke tests, keeps snapshots as release assets (last 14), commits the small event/history files and publishes `site/` to GitHub Pages.
5. Monthly workflows add the slower sources: `companies.yml` (3rd, Companies House) and `context.yml` (5th: stations, recorded crime, areas, prices and environment).

Run locally: `PYTHONPATH=src uv run python -m london_pulse.run [--prev previous.parquet]`. Tests: `PYTHONPATH=src uv run pytest` (browser smoke tests in `tests/smoke` need Playwright and `SMOKE_URL`).

## Data and licences
No dataset ships without a confirmed open licence and attribution. FSA hygiene ratings, Companies House, ONS (Census 2021, income estimates, PIPR rents), MHCLG (IMD 2025), data.police.uk, HM Land Registry, Environment Agency, Defra and OS Open Greenspace are Open Government Licence v3.0; each file lists its sources and attribution. TfL station data is under the TfL open data licence ("Powered by TfL Open Data"). Air quality (LAEI) is deliberately not included: the London Datastore lists its licence as "Not Specified".

## Caveats
A premises disappearing from the FSA register can be a closure, a change of owner or an administrative removal. "Awaiting inspection" includes recent openings but is not a count of them. Crime, tenure, income, flood, noise and green-space figures describe neighbourhoods (LSOAs of about 1,700 residents), not addresses or people, and are modelled or recorded values, not surveys. Licensing applications are planned. An evaluation of Overture Maps places found it unusable for closures in the 2026-09-23 release (3 of 496,244 London places marked permanently closed, the rest null).

Not affiliated with the Food Standards Agency or any council.
