"""Data contract: the published API files must have the shape the site depends on, and stay within size budgets.

Runs in CI after the pipeline and before anything is committed or deployed, so a bad upstream change fails the
run instead of publishing. Locally: API_DIR=work/api uv run pytest tests/test_contract.py
"""

import json
import os
from pathlib import Path

import pytest

API = Path(os.environ.get("API_DIR", Path(__file__).resolve().parents[1] / "site" / "api" / "v1"))
LONDON = (-0.6, 0.4, 51.2, 51.8)  # lon min, lon max, lat min, lat max
KB = 1024


def load(name: str):
    p = API / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    return json.loads(p.read_text())


def test_status_is_sane():
    s = load("status.json")
    assert s["rows"] >= 70_000
    assert s["as_of"] and s["generated_at"]


def test_summary_totals_add_up():
    t = load("summary.json")["totals"]
    assert t["eating_drinking"] <= t["premises"]
    assert t["five_star"] > 0 and t["awaiting"] >= 0
    assert load("summary.json")["stories"], "home page stories missing"


def test_boroughs_cover_london():
    b = load("boroughs.json")["boroughs"]
    assert len(b) == 33
    assert all(x["name"] for x in b) if "name" in b[0] else True


def test_venues_shape_and_bounds():
    v = load("venues.json")
    assert len(v["boroughs"]) == 33
    assert "Other catering premises" in v["types"], "delivery kitchens must stay on the map"
    rows = v["venues"]
    assert len(rows) > 30_000
    lo, hi, la, lb = LONDON
    sample = rows[:: max(1, len(rows) // 2000)]
    for lon, lat, t, r, b, name, pc in sample:
        assert lo <= lon <= hi and la <= lat <= lb, (name, lon, lat)
        assert 0 <= t < len(v["types"]) and 0 <= r < len(v["ratings"]) and 0 <= b < len(v["boroughs"])
        assert name


def test_hex_layer_has_every_map_measure():
    h = load("hex.geojson")["features"]
    assert len(h) > 500
    need = {"n", "five_pct", "awaiting", "awaiting_pct", "coffee", "takeaway_pct", "pubs", "specialty", "chain_pct", "borough"}
    assert need <= set(h[0]["properties"])
    assert h[0]["geometry"]["type"] == "Polygon"


def test_brands_include_known_names_with_plausible_counts():
    by = {b["id"]: b for b in load("brands.json")["brands"]}
    assert by["pret"]["n"] > 100 and by["gails"]["n"] > 50
    assert len(by) > 100
    assert all(b["n"] >= 1 and b["sites"] for b in by.values())


def test_area_context_files():
    st = load("stations.json")["stations"]
    assert len(st) > 500 and all("lines" in s and "modes" in s for s in st[:50])
    c = load("crime.json")
    assert len(c["cells"]) > 3000 and len(c["categories"]) >= 10
    assert all(len(row[2]) == len(c["categories"]) for row in c["cells"][:200])


def test_areas_join_tenure_deprivation_income_and_crime():
    a = load("areas.json")
    f = a["fields"]
    assert 4900 <= len(a["areas"]) <= 5100 and all(len(r) == len(f) for r in a["areas"][:200])
    row = lambda r: dict(zip(f, r))  # noqa: E731
    rs = [row(r) for r in a["areas"]]
    assert all(LONDON[0] <= r["lon"] <= LONDON[1] and LONDON[2] <= r["lat"] <= LONDON[3] for r in rs)
    assert all(0 <= r["council_pct"] <= 100 and 0 <= r["owned_pct"] <= 100 and 1 <= r["imd_decile"] <= 10 for r in rs)
    assert all(r["council_pct"] + r["other_social_pct"] + r["private_pct"] + r["owned_pct"] <= 100.5 for r in rs)
    assert sum(1 for r in rs if r["net_income_bhc"]) > 4900            # income is MSOA-level but covers every LSOA
    assert sum(1 for r in rs if r["council_pct"] >= 30) > 300           # London has plenty of council-majority LSOAs
    assert all(len(r["crimes"]) == len(a["categories"]) for r in rs[:200])
    assert sum(sum(r["crimes"]) for r in rs) > 100_000                 # three months of London crime, joined
    assert all({"name", "publisher", "url", "licence", "vintage", "caveat"} <= set(x) and x["url"].startswith("https://") for x in a["sources"])
    assert {"tenure", "crime"} <= set(a["proof"]) and "{code}" in a["proof"]["tenure"]
    s = a["analysis"]["spearman"]
    assert all(v is None or -1 <= v <= 1 for v in s.values()) and len(a["analysis"]["bands"]) == 4


def test_character_has_what_the_insights_tab_reads():
    c = load("character.json")
    assert len(c["boroughs"]) == 33 and len(c["districts"]) >= 50
    assert c["headlines"] and all(h["text"] for h in c["headlines"])
    stages = {"Hot and still growing", "Established scene", "Emerging", "Steady"}
    for a in c["boroughs"] + c["districts"]:
        assert a["stage"] in stages and a["venues"] > 0
        assert 0 <= a["chain_pct"] <= 100 and 0 <= a["fresh_pct"] <= 100
        assert all(m["lq"] >= 1.5 for m in a["signature"])
    assert c["pace"]["days_covered"] >= 1


def test_operators_has_what_the_insights_tab_reads():
    o = load("operators.json")
    assert {"food_drink", "craft", "creative", "tech"} <= set(o["momentum"])
    f = o["momentum"]["food_drink"]
    assert f["last6"] > 500 and f["prior6"] > 500, "food and drink formation implausibly low"
    assert len(o["seasonality"]["food_drink"]["months"]) == 12
    assert len(o["districts"]) >= 50 and o["headlines"]
    p = o["new_company_profile"]
    assert p["companies"] > 1000 and 0 <= p["linked_pct"] <= 100
    w = o["who_is_opening"]
    assert w["matched"] <= w["new_premises_awaiting"] and w["examples"]
    assert all(e["url"].startswith("https://find-and-update.company-information.service.gov.uk/company/") for e in w["examples"])


@pytest.mark.parametrize(
    "name,budget_kb",
    [("venues.json", 3000), ("brands.json", 900), ("hex.geojson", 650), ("crime.json", 450), ("areas.json", 900), ("summary.json", 40),
     ("character.json", 450), ("operators.json", 150)],
)
# raw (uncompressed) sizes with ~30% headroom; Pages serves these gzipped, so downloads are far smaller
def test_size_budgets(name, budget_kb):
    p = API / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    assert p.stat().st_size <= budget_kb * KB, f"{name} is {p.stat().st_size // KB} KB, budget {budget_kb} KB"
