# Data refresh plan

| Source | Cadence | Job | Output |
|---|---|---|---|
| FSA hygiene ratings (33 boroughs) | daily, twice: 06:17 and 12:17 UTC | `daily.yml` | snapshot (release asset), events, `site/api/v1/*` |
| Companies House bulk file | monthly, 3rd, 08:00 UTC | `companies.yml` | `companies.json`, `operators.json`, then triggers `daily.yml` |
| TfL stations, police crime, Census 2021 / IMD 2025 / ONS income (areas), Land Registry sales and ONS rents (prices), Environment Agency flood, Defra noise, OS Open Greenspace (environment) | monthly, 5th, 09:00 UTC | `context.yml` | `stations.json`, `crime.json`, `areas.json`, `prices.json`, `environment.json`, then triggers `daily.yml` |

## Why the daily run is twice a day
On some days the FSA data lands after the first run (12 Oct 2026: 6 changes at 07:40, 92 at 11:31). The 12:17 run catches that. It is idempotent: it diffs against the newest snapshot from an earlier day, so the day's events, history rows and snapshot are replaced, not added to. If both runs see the same data, nothing changes.

## Safeguards
- **Sanity gate** (`run.py`): fewer than 70,000 rows, or a >10% change in one day, fails the run before anything is published. The site keeps serving the last good data.
- **Status file**: `api/v1/status.json` records when the data was generated, row counts and change counts.
- **Freshness banner**: the app shows a warning if `generated_at` is older than 48 hours.
- **Failure alerts**: GitHub emails the repo owner when a scheduled workflow fails (Settings → Notifications).
- **Snapshots**: the last 14 daily parquet snapshots are kept as release assets; the repo holds only small outputs.
- **Idempotent**: a re-run on the same day diffs against yesterday's snapshot (the newest earlier than today), not its own earlier output, and overwrites that day's events, history rows and snapshot. Manual and second daily runs never invent changes.
- **Contract and smoke gates**: `tests/test_contract.py` and the browser smoke test run before anything is committed or deployed. `smoke.yml` re-tests the live site after each successful deploy.
- **Monthly job keeps the last good file**: in `context.yml`, `prices.json` and `environment.json` are best-effort (`continue-on-error`). If `environment.json` has any skipped topic or fewer than 3 topics, the committed file is restored (`git checkout`) with a warning, so the page never loses a topic. Monthly contract tests run before commit.
- **Reference caches**: downloads live in `work/reference`, with a cache key per module (`neighbourhoods.py`; `flood.py`, `noise.py`, `green.py`), so changing a source module refreshes its cache. The large Price Paid files are deleted before the cache saves.
- **Licence gate**: a dataset is added only with a confirmed open licence and attribution. Air quality (LAEI) was left out: the London Datastore lists its licence as "Not Specified".

## Mapbox token
The token is the `MAPBOX_API_KEY` secret on the `github-pages` environment. The deploy workflow writes it to `site/config.js` at build time (never committed). It is a public `pk.` token by design, so restrict it by URL in the Mapbox dashboard to `https://cloudcruncher.github.io/*` and `http://localhost:*`. Locally, `scripts/serve.sh` reads it from `.env` (`MAPBOX_API_KEY=`) and serves a throwaway copy.

## Backup trigger for the daily run

GitHub's cron has not been reliable for this repo (a test workflow on a 5-minute schedule never fired in 30 minutes), so a
macOS `launchd` agent backs it up. At 08:30 local time it runs `scripts/trigger-daily-if-missing.sh`, which starts the
Daily update only if none has succeeded or is running since 00:00 UTC. It covers a missed first run only: the 12:17 run is not backed up, and any successful run that day (the 06:17 one, or a manual one) satisfies it. When GitHub's own schedule works, the backup does nothing.

- Agent: `~/Library/LaunchAgents/com.londonpulse.daily-backup.plist` (not in the repo). Log: `~/Library/Logs/london-pulse-backup.log`.
- It needs the Mac awake and `gh` logged in. If the Mac is asleep at 08:30, launchd runs it on wake.
- Reinstall: copy the plist back and `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.londonpulse.daily-backup.plist`.
- Test: `DRY_RUN=1 TODAY=2099-01-01 scripts/trigger-daily-if-missing.sh` should say it would trigger.
