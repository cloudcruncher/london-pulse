# Data refresh plan

| Source | Cadence | Job | Output |
|---|---|---|---|
| FSA hygiene ratings (33 boroughs) | daily, 06:30 UTC | `daily.yml` | snapshot (release asset), events, `site/api/v1/*` |
| Companies House bulk file | monthly, 3rd, 08:00 UTC | `companies.yml` | `companies.json`, then triggers `daily.yml` |

## Safeguards
- **Sanity gate** (`run.py`): fewer than 70,000 rows, or a >10% change in one day, fails the run before anything is published. The site keeps serving the last good data.
- **Status file**: `api/v1/status.json` records when the data was generated, row counts and change counts.
- **Freshness banner**: the app shows a warning if `generated_at` is older than 48 hours.
- **Failure alerts**: GitHub emails the repo owner when a scheduled workflow fails (Settings → Notifications).
- **Snapshots**: the last 14 daily parquet snapshots are kept as release assets; the repo holds only small outputs.
- **Idempotent**: re-running a day treats it as a baseline, so manual re-runs never invent changes.

## Mapbox token
The token is the `MAPBOX_API_KEY` secret on the `github-pages` environment. The deploy workflow writes it to `site/config.js` at build time (never committed). It is a public `pk.` token by design, so restrict it by URL in the Mapbox dashboard to `https://cloudcruncher.github.io/*` and `http://localhost:*`. Locally, `scripts/serve.sh` reads it from `.env` (`MAPBOX_API_KEY=`) and serves a throwaway copy.
