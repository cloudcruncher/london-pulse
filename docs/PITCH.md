# London Pulse: the pitch

## One line
A free, daily-refreshed picture of London's food and drink scene, built from open data, with every number
traceable to a query you can run yourself.

## Why it works as a data engineering showcase
It is a small but complete data product, and each layer is something a hiring manager or client can inspect:

| Layer | What it shows |
|---|---|
| Ingestion | FSA, Companies House, TfL and police APIs with retries, paging and recursive splitting on errors |
| Quality gates | Row-count floor and a 10% day-over-day swing guard stop a bad snapshot from publishing |
| Storage | Daily snapshots kept as release assets; history as zstd Parquet; GeoParquet for spatial work |
| Transformation | DuckDB SQL: diffing consecutive days, brand matching, H3 hexagon aggregation |
| Serving | Versioned static JSON API plus Parquet downloads, no servers to run |
| Analytics | DuckDB-WASM SQL lab in the browser, a no-SQL question builder, shareable query links |
| Automation | GitHub Actions on a schedule; monthly jobs for slow-moving sources |
| Cost | Effectively zero to run; scales by adding files, not machines |

## What it answers (the product)
- What opened, closed or was re-rated yesterday, borough by borough.
- Which brands are everywhere, and which roasters or breweries are growing.
- Moving somewhere? Cafés, specialty coffee, pubs, hygiene, stations and crime within walking distance,
  and compare two areas side by side.
- Where are the hygiene hotspots, the new-opening clusters, the chain-free neighbourhoods (3D hex map).

## Findings you can lead with
Run these in the SQL lab and quote the live numbers (they change daily):
1. Share of venues whose rating is more than two years old.
2. Which postcode districts have the most venues awaiting first inspection (new-opening clusters).
3. Specialty coffee density by hexagon: the "Caravan effect" map.
4. Chain share by hexagon: where the high street is still independent.

## For roasters, breweries and cafés
The offer is a conversation, not a sales pitch: "I track the open data on your market. Here is where your
sites sit, who opened near you this month, and where there is a gap. Tell me what you would want to see."
Useful for them: new openings near their sites, competitor counts per catchment, wholesale prospects
(new cafés and pubs in the first weeks, before they pick a supplier), and hygiene-rating context.
Useful for you: real questions to learn from, case studies, and a track record before any paid work.

Small deliverables to offer first, free:
- A one-page catchment brief for one of their sites.
- A monthly "new openings near you" list.
- A dashboard on their own data, built in an afternoon with the same stack.

## Caveats (say them first, it builds trust)
- Hygiene ratings measure food safety, not taste.
- "Removed" from the register is not "closed".
- Brand matching is by name, so it is a good guide, not a census.
- Crime figures are counts by area, not a verdict on a street.

## Before any paid work
Check the NatWest outside-work policy first. Free, public, open-source work is the safe route until then.
