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
