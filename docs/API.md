# Static JSON API (v1)

Everything the website shows is served as static JSON under `/api/v1/`. Each file carries `schema_version`, `as_of`, `generated` (ISO date of the run), `source` and `licence`; derived files also carry a `method` sentence.
`stations.json` is under the TfL open data licence ("Powered by TfL Open Data"), not the OGL; every other file is OGL v3.0.
Changes within v1 are additive only; a breaking change ships as `/api/v2/` alongside v1.

| File | Contents | Updated |
|---|---|---|
| `summary.json` | London totals, rating distribution, premises by type, newest unrated venues | daily |
| `boroughs.json` | Per-borough venue counts, % rated 5, low-rated, awaiting inspection, coffee-named | daily |
| `events.json` | Day-over-day changes (new / removed / re-rated) over the last 30 days, by day and borough | daily |
| `history.json` | Daily per-borough counts since tracking started | daily |
| `venues.json` | Compact dictionary-encoded venue list for the map: `[lon, lat, type, rating, borough, name, postcode, fhrsid, approx_loc]` (`venue_columns` names each index; `fhrsid` links to `https://ratings.food.gov.uk/business/{fhrsid}`; `approx_loc` 1 = FSA published no coordinates, so the point is the median of its postcode, else postcode sector) | daily |
| `character.json` | What each borough and postcode district is becoming: venue mix, specialty scene, fresh supply, stage, plus plain-English headlines | daily |
| `operators.json` | Company momentum by sector and district, seasonality, who is opening (age and linkage of the company behind new premises) | monthly |
| `crime.json` | Recorded crime per ~550 x 350 m grid cell; `queried_cells` / `failed_cells` count API areas requested and not returned (the run fails above 0.5%) | monthly |
| `stations.json` | London rail stations with `naptan` id (https://api.tfl.gov.uk/StopPoint/{naptan}) | occasional |
| `companies.json` | Companies House company formation for coffee roasting, brewing, distilling, pubs and bars | monthly |
| `report.json` | London distributions (percentiles, histogram, tercile cut-offs) of the postcode report card metrics (eight, plus `median_price` and `rent_2bed` when `prices.json` exists), plus three check points | daily |
| `prices.json` | Median sale price per postcode sector and district (HM Land Registry, last 12 months) and the latest private rent per borough (ONS PIPR) | monthly |
| `areas.json` | One record per London LSOA: council-rented share of households, deprivation, small-area income, recent recorded crime by category, food-premises count; plus an `analysis` block | monthly |

### `areas.json`

`fields` names the columns; `areas` is an array of rows in that order (compact, like `venues.json`). Fields: `code`, `name`,
`borough`, `lon`, `lat` (centroid), `pop`, `households`, `council_pct`, `other_social_pct`, `private_pct`, `owned_pct`
(share of households, Census 2021 TS054; council means "rents from council or local authority", housing associations are
`other_social_pct`), `imd_decile` (IMD 2025, 1 = most deprived 10% of England), `income_dep_pct` (IMD income score: % of
residents in income-deprived households), `net_income_bhc` / `net_income_ahc` (ONS model-based net household income, before
and after housing costs, FYE 2023, equivalised; an LSOA carries its MSOA's figure), `income_ci_ahc` (ONS 95% interval width, kept for compatibility), `venues` (FSA premises), `busy` (1 if the
LSOA is in the top decile of `venues` per resident), `crimes`, then appended: `income_lo_ahc` / `income_hi_ahc` (ONS lower and upper 95% confidence limits of the MSOA's after-housing-costs income; asymmetric, so do not derive them from `income_ci_ahc`) and `msoa` (MSOA code, joined via the ONS LSOA-MSOA lookup). Top-level `as_of` is the last crime month.

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

### `prices.json`

Sale prices and private rents for the postcode report card. Built monthly by `python -m london_pulse.prices`; `report.json` reads it.

- `window` (`from`, `to`, `YYYY-MM`; the 12 months to the latest sale month `as_of`), `min_sales` (10), `caveats`, `method`.
- `columns`: `median_all, n_all, median_flat, n_flat, median_house, n_house`. `sectors` maps a postcode sector (outward code, a space,
  the first inward digit: `"E8 1"`) to those six values; `districts` maps an outward code (`"E8"`) to `[median_all, n_all]`. A median is
  `null` when its `n` is under `min_sales`; `n` is always present. Medians are of individual sale prices, not averages of areas.
  Flats are type F; houses are detached, semi-detached and terraced.
- Sales are Greater London (county `GREATER LONDON`, which includes the City of London), standard market sales only (Price Paid category A; category B is left out: repossessions, buy-to-lets where identified by a mortgage, transfers to companies and other non-private buyers),
  deleted records dropped and corrections applied. The latest month is thinner because Land Registry records arrive with a lag.
- `rents`: `as_of`, `source`, `url`, `licence`, `caveat`, `boroughs` keyed by ONS area code (`E09000012`) with `name`, `all`, `one_bed`,
  `two_bed`, `three_bed` (GBP a month, `null` if not published) and `annual_change_pct`. The City of London is not published by ONS, so there
  are 32 boroughs. Official statistics in development: read as trends; a modelled borough average, not a specific street; private rent only.
- A sector's typical price mostly reflects its mix of flats and houses and of leasehold and freehold homes. A postcode district alone gets the district figure; a borough name gets no sale price.
- `proof.ppd`: the Land Registry price paid search, to look up the individual sales behind a postcode's median.
- `sources`: `lr_ppd` and `ons_pipr` entries (also copied into `report.json`).

Data: contains HM Land Registry data (c) Crown copyright and database right 2026, licensed under the Open Government Licence v3.0; ONS data under OGL v3.0.

### `report.json`

Reference distributions for the postcode report card; the page computes one postcode's metrics live and places it on these.
Derived from `areas.json`, `venues.json`, `stations.json` and `character.json`; rebuilt daily from the committed monthly files
(skipped with a warning if one is missing).

- `catchment_m` (800) and `months` (crime months counted). Each metric is computed for the 800 m catchment of every LSOA centre
  (about 5,000 values): LSOA centres within the catchment, else the nearest within 2 km, weighted by population; venues and
  stations by straight-line distance; walking at 80 m a minute.
- `metrics`: keys `crime_rate`, `income_dep_pct`, `venues_800`, `five_pct_800`, `walk_min`, `lines_1km`, `income_ahc`, `fresh_pct`.
  Each has `label`, `unit`, `direction` (`+` higher is favourable, `-` lower is, `0` never rated), `badge` (`measured`, `modelled`,
  `proxy`), `n`, `q` (21 percentiles, p0 to p100 in steps of 5), `hist` (`edges` 21 and `counts` 20, clipped at p1 and p99),
  `lo` / `hi` (p33 / p67), `as_of`, `source_ids` (into `sources`). `crime_rate` uses residential (non-busy) neighbourhoods only;
  `fresh_pct` is distributed across postcode districts.
- `median_price` (distribution of sector medians with at least `min_sales` sales, GBP) and `rent_2bed` (distribution of the 32 boroughs'
  two-bed monthly rent): direction `0` (never rated; cheaper is not better), badges `measured` and `proxy`, sources `lr_ppd` / `ons_pipr`.
  Both are left out, with a warning, if `prices.json` is absent.
- `checks`: three real LSOA centres with `code`, `lon`, `lat`, `outcode` (postcode district of the nearest venue, used for `fresh_pct`),
  `sector` (its postcode sector, used for `median_price`; the borough of the nearest LSOA gives `rent_2bed`)
  and `values` for every metric, so the page's calculation can be tested against this one.
