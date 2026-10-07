# Static JSON API (v1)

Everything the website shows is served as static JSON under `/api/v1/`. Each file carries `schema_version` and `as_of`.
Changes within v1 are additive only; a breaking change ships as `/api/v2/` alongside v1.

| File | Contents | Updated |
|---|---|---|
| `summary.json` | London totals, rating distribution, premises by type, newest unrated venues | daily |
| `boroughs.json` | Per-borough venue counts, % rated 5, low-rated, awaiting inspection, coffee-named | daily |
| `events.json` | Day-over-day changes (new / removed / re-rated) over the last 30 days, by day and borough | daily |
| `history.json` | Daily per-borough counts since tracking started | daily |
| `venues.json` | Compact dictionary-encoded venue list for the map: `[lon, lat, type, rating, borough, name, postcode]` | daily |
| `companies.json` | Companies House company formation for coffee roasting, brewing, distilling, pubs and bars | monthly |

Data: FSA hygiene ratings and Companies House, both under the Open Government Licence v3.0. Attribute the sources.
