# Pubs ownership layer: build spec (shared contract for workers)

Goal: every London pub on one map, labelled by who ultimately owns or runs it, so people can find independent pubs
(and pubs with cask ale) and see which belong to big groups (Stonegate/TDR Capital, Heineken/Star Pubs, Greene King/CK Asset,
Mitchells & Butlers, Wetherspoon, Punch, Admiral, ...). Inspired by london-food-money-matrix.vercel.app (categories, owner, note, source link,
"suggest a place").

## Licence rule (hard)
Only sources with a confirmed open licence. Allowed:
- OpenStreetMap via Overpass, ODbL 1.0, attribution "© OpenStreetMap contributors" (https://www.openstreetmap.org/copyright).
  The derived pubs.json is therefore ODbL (share-alike); say so in `licence`.
- FSA food hygiene register (already in `work/latest.parquet`), OGL v3.0.
- Camden licensing register, Socrata dataset `rv9z-rsui` on opendata.camden.gov.uk, OGL v3.0, attribution "London Borough of Camden Council".
  Fields used: premises_name, premises_address, licensee, licence_type (keep "Premises Licence" types), status = "In Force", latitude, longitude.
- Curated group table `src/london_pulse/pub_groups.py`: our own short factual statements, each with a public source URL.
Not allowed: scraping pub-company "find a pub" sites, CAMRA WhatPub, Untappd, Google.

## Overpass query (works; needs a User-Agent header, area queries 406 without it)
POST https://overpass-api.de/api/interpreter, header `User-Agent: london-pulse/0.1 (open-data research)`, body
`data=[out:json][timeout:100][bbox:51.28,-0.52,51.70,0.34];nwr["amenity"="pub"];out center tags;`
then keep only points inside a London borough (FSA authority via nearest/fhrs join, or the existing borough polygons if available; bbox alone over-includes).
As of 2026-11-20: 3,655 pubs in the bbox; tags: brand 708, brand:wikidata 694, operator 489, operator:wikidata 179, brewery 327,
real_ale 485 (400 "yes", 16 "no", others are counts/free text), fhrs:id 2,318, website 1,679.

## Module interface: `src/london_pulse/pub_groups.py` (owned by the research worker)
```python
CATEGORIES = {  # order = display order
  "pe":      {"label": "PE / investor-owned", "short": "PE"},
  "corp":    {"label": "Corporate group",      "short": "CORP"},   # global brewers, listed chains, conglomerates
  "family":  {"label": "Family brewer",        "short": "FAMILY"}, # independent family/regional brewers
  "indie":   {"label": "Independent",          "short": "INDIE"},  # single pubs and small independent groups
  "unknown": {"label": "No group link found",  "short": "?"},
}
GROUPS = [  # one dict per group/brand family
  {"id": "stonegate", "name": "Stonegate Group", "category": "pe",
   "owner": "TDR Capital (private equity)", "model": "mixed",   # managed | tenanted | mixed
   "note": "One plain-English sentence.", "src": "https://...",
   "match": {"operator": r"...", "brand": r"...", "licensee": r"...", "name": r"..." or None,
             "wikidata": ["Q..."]}},   # regexes are case-insensitive, applied to normalised text
  ...
]
```
`model` drives wording: tenanted/leased pubs are run by an independent tenant but owned by the group and usually tied to its beer.

## Output: `site/api/v1/pubs.json`
```json
{
 "schema_version": 1, "as_of": "YYYY-MM-DD", "generated": "YYYY-MM-DD",
 "licence": "ODbL 1.0 (contains OpenStreetMap data) and OGL v3.0",
 "source": "...", "method": "one sentence",
 "sources": [{"id":"osm","name":"...","publisher":"...","licence":"...","attribution":"...","url":"..."}, ...],
 "categories": {...CATEGORIES},
 "groups": [{"id","name","category","owner","model","note","src"}],   // no regexes
 "fields": ["name","lon","lat","postcode","borough","fhrsid","osm","group","category","confidence","evidence","cask","craft","website"],
 "pubs": [[...], ...],
 "counts": {"total": n, "by_category": {...}, "by_group": {...}, "cask": n, "on_fsa": n}
}
```
Field meanings:
- `osm`: "n123"/"w123"/"r123" (link https://www.openstreetmap.org/node/123 etc.); `fhrsid`: int or null (FSA link).
- `group`: group id or null. `category`: one of CATEGORIES keys.
- `confidence`: "confirmed" (two independent source families agree: OSM tags/name and the Camden licensee; or a curated override) | "likely" (one family, even if several OSM tags on the same object agree) ; null for unknown.
- `evidence`: list of short codes: "osm:operator", "osm:brand", "osm:wikidata", "camden:licensee", "name", "override".
- `cask`: 1 if OSM real_ale is "yes" or a positive number/free text naming beers, 0 if "no", null unknown.
- `craft`: 1 if OSM craft_beer/drink:craft_beer = yes or microbrewery=yes, else null. (No open keg-line data exists; UI says so.)
- Independent ("indie", confidence "likely"): no big-group match from any source AND a positive signal: OSM operator or Camden licensee names a
  company/person that appears on <= 3 London pubs and is not a known group, or a curated indie group/override. Otherwise "unknown".
- Pub universe: OSM amenity=pub inside London. Link to FSA by fhrs:id, else nearest FSA "Pub/bar/nightclub" within 60 m with name similarity.
  `on_fsa` count = linked pubs.
- Overrides: `data/pubs_overrides.csv` columns `osm,fhrsid,group,category,cask,craft,source_url,note` (reviewed community corrections; wins over everything,
  evidence "override", confidence "confirmed"). Ship with header only plus maybe zero rows.

## Robustness
- Network fetches cached under `work/reference/` (osm_pubs.json, camden_licences.json); a failed source -> run continues without it and records it in
  `skipped`; if OSM fails, exit non-zero and the workflow keeps the committed pubs.json.
- Monthly refresh: a step in `.github/workflows/context.yml` after environment, with continue-on-error + "keep committed file" guard, and pubs.json added
  to the commit list. Contract test covers it.

## Site: new tab "Pubs" (owned by the frontend worker)
- Header line in plain English; counts strip per category (tap to filter); filters: category, "Cask ale" toggle, borough, name search.
- Postcode / "near me" box -> nearest independent pubs first (reuse existing postcode lookup in app.js).
- Map of pubs coloured by category (reuse the existing canvas map approach or a light self-contained canvas; no new heavy deps),
  plus a list. Pub detail: owner, group note with source link, model wording ("owned by Heineken's Star Pubs, run by a tenant"),
  evidence chips linking to OSM / FSA / Camden, cask/craft, and "Suggest a correction" link that opens a prefilled GitHub issue on
  cloudcruncher/london-pulse (title "Pub ownership: <name>", body with osm id, fhrsid, fields to fill). No backend.
- Framing: "No group link found" is not a claim of independence; independent labels say "likely". Attribution line "© OpenStreetMap contributors (ODbL)" visible.
- Light/dark, phone width, matches existing app.css tokens.

---
# v2 (2026-11-20): coverage, insights, interactivity

The v1 rules above still hold: open licences only, "Independent" is always "likely", "No group link found" is never a claim of independence, and Camden licensee names of individuals are never published or logged.

## A. Coverage worker (owns `pub_groups.py`, `pubs.py`, `tests/test_pubs.py`, `tests/test_pub_groups.py`, `data/pubs_overrides.csv`)
Goal: cut `unknown` (2,163 of 3,140 in v1) as far as honest evidence allows, and report before/after counts by category.
Open signals, in order of value:
1. **Website domain.** OSM `website` / `contact:website` (ODbL). Add `domain=` to `_g(...)` for groups with a verified group-wide domain, giving evidence `osm:website`.
   Examples to verify: geronimo-inns.co.uk (Young's), brunningandprice.co.uk (Mitchells & Butlers), craftunionpubs.com (Stonegate), greatukpubs.co.uk, urbanpubsandbars.com, frontierpubs.co.uk, whelanspubs.co.uk, feeneyspubs.com, thechaptercollection.co.uk, publove.co.uk, bohembrewery.com, thecraftbeerco.com, mcmullens.co.uk.
   A shared non-group domain across two or more pubs is a "small group" signal for indie (with three or fewer pubs and not on NOT_INDIE).
2. **Wikidata (CC0).** Pubs carry `wikidata` (328 of the unknown), `brand:wikidata` and `operator:wikidata`. Run a batched SPARQL query against https://query.wikidata.org/sparql with a User-Agent, following P127 (owned by), P137 (operator), P749 (parent organization) and P1830 (owner of) up to the known group QIDs. Cache the result in `work/reference/wikidata_pubs.json` and give evidence `wikidata:owner`. Map only to groups already in GROUPS; otherwise ignore.
3. **New verified groups.** Research these and add each with a working public source URL (Wikipedia, the company's own about page, Companies House, or the trade press): Interpub, Etive Pubs, Golden Brick Pubs, Four Acres Taverns, Big Smoke Inns (Big Smoke Brew Co), Bohem, Portobello Brewery, Signature Brew, Husk, Wildman Project, El Vino (Davy's?), Urban Leisure, Alliance Pub Co, Laine Pub Co (Brewdog), Geronimo, Frontier, Whelan's, Feeney's, Chapter Collection, Publove, Craft Union, Great UK Pubs, Truman's, Hippo Inns, London Village Inns, Livelyhood, Draft House (BrewDog?), Faucet Inn, Cubitt House, Three Cheers Pub Co, Salt Beer Factory, Mother Kelly's, Gipsy Hill, Brick, Battersea Brew and Rusty Bike (London Village Inns?).
   Never guess. If a source can't be checked, leave the group out and list it in the report.
4. **Brewery tag.** A pub whose OSM `brewery` names a single family or regional brewer (Shepherd Neame, Samuel Smith/sam_smiths, Young's, Fuller's, Greene King, McMullen) is a "tied house" signal that maps to that group with evidence `osm:brewery`. A plain "various" value is a free-house hint for indie, with the usual guards.
5. **FSA business name** (OGL): when FSA's name differs from OSM's and contains a group/brand pattern, use evidence `fsa:name`.
6. **Companies House** (OGL; `companies.py`, `work/co_london.parquet` SIC 56302): use only if it gives a clean, testable link. Optional.
Confidence: "confirmed" still needs two different source families (osm, camden, wikidata, fsa, override). `osm:website`, `osm:brewery` and `osm:operator` all belong to the OSM family.
Add these fields to the end of `fields` (append only; existing order unchanged):
`"rating"` (the FSA rating string for the linked fhrsid, e.g. "5" or "Exempt", else null) and `"rating_date"` ("YYYY-MM-DD" or null).
Keep `pubs.json` under 1,500 KB.

## B. Insights worker (owns the new `src/london_pulse/pubs_insights.py`, `tests/test_pubs_insights.py`, the context.yml pubs step, the test_contract entry, and README/API docs)
Output: `site/api/v1/pubs-insights.json`. It is built from pubs.json plus FSA latest/history/events plus areas.json, is a pure function, and runs right after pubs.json in context.yml. Use the same guard: keep the committed file on failure.
```json
{ "schema_version": 1, "as_of": "...", "generated": "...", "licence": "ODbL 1.0 (contains OpenStreetMap data) and OGL v3.0", "method": "...",
  "notes": ["plain-English caveats, e.g. 'unknown' is not 'independent'"],
  "london": {"total": n, "by_category": {cat: n}, "known": n, "group_share": 0.0, "indie_share_of_known": 0.0, "cask": n,
             "top_groups": [{"id": "...", "n": n, "share": 0.0}], "hhi": 0.0, "top5_share": 0.0},
  "boroughs": [{"borough": "...", "total": n, "by_category": {}, "group_share": 0.0, "indie_n": n, "cask_n": n,
               "top_group": "id|null", "top_group_n": n, "hhi": 0.0, "groups": {"id": n},
               "imd_decile_avg": x|null, "income_avg": x|null, "rating_avg": x|null, "rating_n": n, "pubs_per_km2": x|null}],
  "groups": [{"id": "...", "n": n, "boroughs": n, "cask_n": n, "cask_known": n, "rating_avg": x|null, "rating_n": n,
              "rating_dist": {"5": n, "4": n, "3": n, "2": n, "1": n, "0": n}, "top_boroughs": [["borough", n]]}],
  "by_category": {cat: {"n": n, "cask_n": n, "cask_known": n, "rating_avg": x|null, "rating_n": n, "rating_dist": {}, "imd_decile_avg": x|null}},
  "deprivation": [{"decile": 1, "total": n, "by_category": {cat: n}}],   // IMD decile of the pub's LSOA (point-in-polygon, or the nearest LSOA centroid if there are no polygons), 1 = most deprived
  "churn": {"window_days": n, "by_category": {cat: {"new": n, "removed": n}}} | null   // FSA new/removed events for fhrsids linked to pubs; null if not defensible
}
```
Categories are always the pubs.json category keys. Shares are 0 to 1, rounded to 3 decimal places. Drop any metric whose input data is missing (use null; never invent).

## C. Frontend worker (owns `site/assets/pubs.js`, the `/* Pubs */` block in `app.css`, the `#v-pubs` and `pb-*` markup in `index.html`, `sw.js` VERSION/SHELL, and the Pubs tests in `tests/smoke/test_site.py` plus fixtures)
Reads pubs.json (required) and pubs-insights.json (optional; when it is missing, compute what it can client-side or hide that section). Vanilla JS, no build step, no new dependencies, inline SVG charts.
Must have:
- Two modes: **Explore** (map and list, as now) and **Insights** (analysis).
- **Insights:** a London headline (share of known pubs held by groups, top-5 share, HHI in plain words); a group league table (sortable; tap a row to filter the map to that group); stacked bars per borough by category (sortable by group share or by independent count); a cask share by owner type chart; an FSA hygiene rating by owner type chart; deprivation decile against ownership mix; churn if present. Every chart needs a one-line takeaway and a caveat.
- **Borough compare:** pick two boroughs and see them side by side.
- **Group drill-down:** a group page with its owner, model, note and source, count, the boroughs it is in, a map of its pubs only, and the list.
- **Better map:** density/cluster at low zoom, category shapes, a selected-pub highlight, a legend, and smooth pan/zoom (keep the custom canvas; use mapbox.js only if the Map tab already uses it and it's free to reuse).
- **Shareable URL state:** `#pubs?cat=indie&b=Hackney&g=stonegate&q=...&mode=insights&pc=E83QW` restores the view.
- **Polish:** loading skeleton, empty states, keyboard access, aria labels, phone width at 360 px, light/dark, no layout shift, and copy that keeps the framing rules. Show the FSA rating in the drawer when present.
- Smoke tests: insights render from a fixture; group drill-down filters; URL state round-trip; it still works when insights 404.
