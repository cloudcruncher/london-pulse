"""Postcode report card reference data: the London distribution of each metric, written to site/api/v1/report.json.

The page (site/assets/report.js) computes the same metrics live for one postcode and places it on these distributions.
So the catchment rule here mirrors whoNearby() in site/assets/app.js exactly: every LSOA centroid within CATCH_M, else the
single nearest within 2 km, population weighted. Venues and stations are straight-line distance from the point.
Here every LSOA centroid is used as the point (about 5,000 values per metric); 0.01 degree grid bucketing keeps it fast.

Usage: python -m london_pulse.report
"""
import json
import math
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "site" / "api" / "v1"
CATCH_M = 800
FALLBACK_KM = 2.0          # no LSOA centre in the catchment: use the nearest one if it is within this
WALK_M_PER_MIN = 80
LINES_KM = 1.0
CELL = 0.01
BUSY_SHARE = 0.25

METRICS = {  # key: (label, unit, direction, badge, source ids)
    "crime_rate": ("Recorded crimes per 1,000 residents a year", "per 1,000 a year", "-", "measured", ["crime", "venues"]),
    "income_dep_pct": ("Residents in income-deprived households", "%", "0", "measured", ["deprivation"]),
    "venues_800": ("Food and drink businesses within 800 m", "venues", "+", "measured", ["venues"]),
    "five_pct_800": ("Rated venues scoring 5 for hygiene within 800 m", "%", "+", "measured", ["venues"]),
    "walk_min": ("Walk to the nearest station", "minutes", "-", "modelled", ["tfl"]),
    "lines_1km": ("Rail and tube lines within 1 km", "lines", "0", "modelled", ["tfl"]),
    "income_ahc": ("Modelled household income after housing costs", "GBP a year", "0", "modelled", ["income"]),
    "fresh_pct": ("New food premises awaiting inspection, share of the district's venues", "%", "0", "proxy", ["venues"]),
    # prices.json metrics: never rated, because cheaper is not better; skipped when prices.json is absent
    "median_price": ("Median sale price in the postcode sector, last 12 months", "£", "0", "measured", ["lr_ppd"]),
    "rent_2bed": ("Average two-bed private rent in the borough", "£/month", "0", "proxy", ["ons_pipr"]),
}
PRICE_KEYS = ("median_price", "rent_2bed")
SOURCES = [
    {"id": "tfl", "name": "TfL station and line data (Unified API)", "publisher": "Transport for London", "vintage": "Monthly refresh",
     "licence": "Powered by TfL Open Data", "url": "https://api.tfl.gov.uk/",
     "caveat": "Straight-line distance at 80 m a minute; no buses, frequency or step-free access."},
]
CHECK_ANCHORS = [("central", -0.1246, 51.5081), ("residential", -0.0195, 51.5830), ("suburban", 0.0200, 51.3700)]


class MissingInput(Exception):
    pass


def km(a, b, c, d):
    """Same flat-earth distance as km() in app.js."""
    r = math.pi / 180
    x = (c - a) * r * math.cos((b + d) / 2 * r)
    y = (d - b) * r
    return 6371 * math.hypot(x, y)


class Grid:
    """Points bucketed into CELL-degree squares; within(lon, lat, r_km) yields (distance, item) for items in range."""

    def __init__(self, pts):  # pts: iterable of (lon, lat, item)
        self.cells = {}
        for lon, lat, it in pts:
            self.cells.setdefault((int(lon // CELL), int(lat // CELL)), []).append((lon, lat, it))

    def within(self, lon, lat, r):
        dlat = r / 111.19 + 1e-9
        dlon = r / (111.19 * math.cos(math.radians(lat))) + 1e-9
        out = []
        for ix in range(int((lon - dlon) // CELL), int((lon + dlon) // CELL) + 1):
            for iy in range(int((lat - dlat) // CELL), int((lat + dlat) // CELL) + 1):
                for x, y, it in self.cells.get((ix, iy), ()):
                    d = km(lon, lat, x, y)
                    if d <= r:
                        out.append((d, it))
        return out

    def nearest(self, lon, lat, start=1.0, limit=64.0):
        r = start
        while r <= limit:
            hit = self.within(lon, lat, r)
            if hit:
                return min(hit, key=lambda t: t[0])
            r *= 2
        return None


def load(api: Path, name: str):
    p = api / name
    if not p.exists():
        raise MissingInput(f"{name} not found in {api}")
    return json.loads(p.read_text())


def percentile(sorted_xs, p):
    """Linear interpolation, p in 0..100 (numpy's default). None for an empty series."""
    if not sorted_xs:
        return None
    k = (len(sorted_xs) - 1) * p / 100
    f = math.floor(k)
    c = min(f + 1, len(sorted_xs) - 1)
    return sorted_xs[f] + (sorted_xs[c] - sorted_xs[f]) * (k - f)


class Context:
    def __init__(self, areas, venues, stations, character, prices=None):
        self.f = {k: i for i, k in enumerate(areas["fields"])}
        self.areas = areas["areas"]
        self.months = len(areas["crime_months"])
        self.min_pop = areas["min_pop_for_rate"]
        f = self.f
        self.lsoa_grid = Grid((a[f["lon"]], a[f["lat"]], a) for a in self.areas)
        self.rated = {str(i) for i in range(6)}
        rt = venues["ratings"]
        self.venue_grid = Grid((v[0], v[1], v[3]) for v in venues["venues"])
        self.five_idx = rt.index("5")
        self.rated_idx = {i for i, r in enumerate(rt) if r in self.rated}
        # lines are unioned across stations, so merging same-named platforms (as the page does) changes nothing
        self.stn_grid = Grid((s["lon"], s["lat"], s["lines"]) for s in stations["stations"])
        self.districts = {d["name"]: d for d in character["districts"]}
        pc = lambda p: (p or "").upper().replace(" ", "")
        self.pc_grid = Grid((v[0], v[1], pc(v[6])) for v in venues["venues"] if v[6])
        self.has_prices = bool(prices)
        self.sector_price = {k: v[0] for k, v in (prices or {}).get("sectors", {}).items()}
        self.borough_rent = {b["name"]: b["two_bed"] for b in (prices or {}).get("rents", {}).get("boroughs", {}).values()}

    def outcode_at(self, lon, lat):
        hit = self.pc_grid.nearest(lon, lat)
        return hit[1][:-3] if hit and len(hit[1]) > 3 else None

    def sector_at(self, lon, lat):
        """Postcode sector of the nearest venue's postcode, e.g. "E8 1" (outward code, space, first inward digit)."""
        hit = self.pc_grid.nearest(lon, lat)
        return f"{hit[1][:-3]} {hit[1][-3]}" if hit and len(hit[1]) > 4 else None

    def metrics(self, lon, lat, outcode=None, sector=None):
        f = self.f
        inr = [a for _, a in self.lsoa_grid.within(lon, lat, CATCH_M / 1000)]
        if not inr:
            n = self.lsoa_grid.within(lon, lat, FALLBACK_KM)
            inr = [min(n, key=lambda t: t[0])[1]] if n else []
        out = dict.fromkeys(METRICS)
        if inr:
            pop = sum(a[f["pop"]] or 0 for a in inr)

            def wavg(k):
                n = d = 0.0
                for a in inr:
                    v, w = a[f[k]], a[f["pop"]]
                    if v is not None and w:
                        n += v * w
                        d += w
                return n / d if d else None

            crimes = sum(sum(a[f["crimes"]]) for a in inr)
            out["crime_rate"] = crimes * 12 / self.months / pop * 1000 if pop >= self.min_pop else None
            out["income_dep_pct"] = wavg("income_dep_pct")
            out["income_ahc"] = wavg("net_income_ahc")
            out["_busy_share"] = sum(a[f["pop"]] or 0 for a in inr if a[f["busy"]]) / pop if pop else 0
        near = [r for _, r in self.venue_grid.within(lon, lat, CATCH_M / 1000)]
        out["venues_800"] = len(near)
        rated = [r for r in near if r in self.rated_idx]
        out["five_pct_800"] = sum(1 for r in rated if r == self.five_idx) / len(rated) * 100 if rated else None
        s = self.stn_grid.nearest(lon, lat)
        out["walk_min"] = s[0] * 1000 / WALK_M_PER_MIN if s else None
        out["lines_1km"] = len({ln for _, lines in self.stn_grid.within(lon, lat, LINES_KM) for ln in lines})
        near_lsoa = self.lsoa_grid.nearest(lon, lat)
        out["median_price"] = self.sector_price.get(sector) if sector else None
        out["rent_2bed"] = self.borough_rent.get(near_lsoa[1][f["borough"]]) if near_lsoa else None
        d = self.districts.get(outcode) if outcode else None
        out["fresh_pct"] = d["fresh_pct"] if d else None
        return out


def distribution(key, values):
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    q = [percentile(xs, p) for p in range(0, 101, 5)]
    lo1, hi1 = percentile(xs, 1), percentile(xs, 99)
    edges = [lo1 + (hi1 - lo1) * i / 20 for i in range(21)]
    counts = [0] * 20
    for v in xs:
        counts[min(19, max(0, int((v - lo1) / (hi1 - lo1) * 20))) if hi1 > lo1 else 0] += 1
    label, unit, direction, badge, src = METRICS[key]
    r = lambda x: round(x, 3)
    return {"label": label, "unit": unit, "direction": direction, "badge": badge, "n": len(xs), "q": [r(x) for x in q],
            "hist": {"edges": [r(e) for e in edges], "counts": counts}, "lo": r(percentile(xs, 33)), "hi": r(percentile(xs, 67)),
            "source_ids": src}


def pick_checks(ctx: Context):
    """Three real LSOA centroids (nearest to a central, a residential and a suburban anchor) where every metric is defined."""
    f = ctx.f
    out = []
    for name, lon, lat in CHECK_ANCHORS:
        for _, a in sorted(ctx.lsoa_grid.within(lon, lat, 3.0), key=lambda t: t[0]):
            la, lo = a[f["lat"]], a[f["lon"]]
            oc = ctx.outcode_at(lo, la)
            sec = ctx.sector_at(lo, la)
            m = ctx.metrics(lo, la, oc, sec)
            m.pop("_busy_share")
            if all(v is not None for k, v in m.items() if ctx.has_prices or k not in PRICE_KEYS):
                m = {k: v for k, v in m.items() if ctx.has_prices or k not in PRICE_KEYS}
                out.append({"code": a[f["code"]], "lon": lo, "lat": la, "outcode": oc, "sector": sec, "values": {k: round(v, 4) for k, v in m.items()}})
                break
        else:
            raise MissingInput(f"no complete check point near {name}")
    return out


def build_report(api: Path = API) -> dict:
    ar, ve, st, ch = (load(api, n) for n in ("areas.json", "venues.json", "stations.json", "character.json"))
    try:
        pr = load(api, "prices.json")
    except MissingInput:
        print("warning: prices.json not found; skipping median_price and rent_2bed")
        pr = None
    ctx = Context(ar, ve, st, ch, pr)
    f = ctx.f
    series = {k: [] for k in METRICS}
    for a in ctx.areas:
        lon, lat = a[f["lon"]], a[f["lat"]]
        m = ctx.metrics(lon, lat)
        m.pop("_busy_share", None)
        for k, v in m.items():
            if k == "fresh_pct" or k in PRICE_KEYS:
                continue
            if k == "crime_rate" and (a[f["busy"]] or v is None):
                continue  # as analysis does: residential neighbourhoods only
            series[k].append(v)
    series["fresh_pct"] = [d["fresh_pct"] for d in ch["districts"] if d.get("fresh_pct") is not None]
    if pr:
        series["median_price"] = [v[0] for v in pr["sectors"].values() if v[0] is not None and v[1] >= pr["min_sales"]]
        series["rent_2bed"] = [b["two_bed"] for b in pr["rents"]["boroughs"].values() if b["two_bed"] is not None]
    vintage = {"crime_rate": ar.get("as_of"), "income_dep_pct": "IMD 2025", "income_ahc": "ONS FYE2023",
               "walk_min": st.get("as_of"), "lines_1km": st.get("as_of")}
    if pr:
        vintage.update(median_price=pr["as_of"], rent_2bed=pr["rents"]["as_of"])
    metrics = {}
    for k in METRICS:  # a metric with no values at all is left out; the page skips its row
        d = distribution(k, series[k])
        if d:
            metrics[k] = {**d, "as_of": vintage.get(k) or ve.get("as_of")}
    srcs = [s for s in ar["sources"] if s["id"] in {"deprivation", "income", "crime", "venues"}] + SOURCES + (pr["sources"] if pr else [])
    return {
        "schema_version": 1, "as_of": ve.get("as_of") or date.today().isoformat(), "generated": date.today().isoformat(),
        "source": "Derived from areas.json (police, IMD 2025, ONS income), venues.json (FSA), stations.json (TfL), character.json and, when present, prices.json (Land Registry sales, ONS rents)",
        "licence": "Open Government Licence v3.0; station data Powered by TfL Open Data",
        "method": (f"Each metric is computed for the {CATCH_M} m catchment of every London LSOA centre, exactly as the page does for a "
                   "postcode: LSOA centres within the catchment (else the nearest within 2 km) weighted by population; venues and "
                   "stations by straight-line distance. Crime uses residential neighbourhoods only. median_price is distributed across postcode sectors with enough sales and rent_2bed across boroughs. lo and hi are the 33rd and 67th percentiles."),
        "catchment_m": CATCH_M, "months": ctx.months,
        "metrics": metrics,
        "sources": srcs, "checks": pick_checks(ctx),
    }


def write(api: Path = API) -> Path:
    rep = build_report(api)
    p = api / "report.json"
    p.write_text(json.dumps(rep, separators=(",", ":")))
    return p


if __name__ == "__main__":
    p = write()
    print(f"report -> {p} ({p.stat().st_size // 1024} KB)")
