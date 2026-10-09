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
| `character.json` | What each borough and postcode district is becoming: venue mix, specialty scene, fresh supply, stage, plus plain-English headlines | daily |
| `operators.json` | Company momentum by sector and district, seasonality, who is opening (age and linkage of the company behind new premises) | monthly |
| `companies.json` | Companies House company formation for coffee roasting, brewing, distilling, pubs and bars | monthly |
| `areas.json` | One record per London LSOA: council-rented share of households, deprivation, small-area income, recent recorded crime by category, food-premises count; plus an `analysis` block | monthly |

### `areas.json`

`fields` names the columns; `areas` is an array of rows in that order (compact, like `venues.json`). Fields: `code`, `name`,
`borough`, `lon`, `lat` (centroid), `pop`, `households`, `council_pct`, `other_social_pct`, `private_pct`, `owned_pct`
(share of households, Census 2021 TS054; council means "rents from council or local authority", housing associations are
`other_social_pct`), `imd_decile` (IMD 2025, 1 = most deprived 10% of England), `income_dep_pct` (IMD income score: % of
residents in income-deprived households), `net_income_bhc` / `net_income_ahc` (ONS model-based net household income, before
and after housing costs, FYE 2023, equivalised; an LSOA carries its MSOA's figure), `venues` (FSA premises), `busy` (1 if the
LSOA is in the top decile of `venues` per resident), `crimes`.

- `categories`: police crime categories; `crimes` is an array of counts in this order. `crime_months`: the months counted
  (last 3 available). Counts only, never individual crimes.
- `busy_venues_per_1000`: the venues-per-1,000-residents threshold behind `busy`. Venue counts are a footfall proxy: busy
  centres record crime from visitors as much as residents.
- `min_pop_for_rate`: LSOAs below this population get no annualised rate.
- `analysis`: residential (non-busy) LSOAs only. `n`, `median_rate` (crimes per 1,000 residents, annualised),
  `median_council_pct`; `bands` (council share 0-5, 5-15, 15-30, 30+%: `n`, `median_rate`, `median_income_dep_pct`);
  `same_deprivation` (the same bands within most deprived 30%, middle 40%, least deprived 30% by IMD decile; a cell is null
  below 15 areas); `spearman` (rank correlations of council share and of income deprivation with crime rate, and council share
  with crime among areas of equal deprivation).
- `sources`: list of datasets used.

Caveats: crime is assigned to the LSOA containing the police's snapped location (anonymised to a street point), not where the
offender or victim lives. Association is not cause: council share and crime both track deprivation, hence `same_deprivation`.
Tenure is Census 2021 (March 2021), so it can lag stock changes. Income is modelled at MSOA level, not measured per LSOA.
Describes areas, not estates or residents.

Data: FSA hygiene ratings, Companies House, ONS (Census 2021, income estimates), MHCLG (IMD 2025) and data.police.uk, all under the Open Government Licence v3.0. Attribute the sources.
