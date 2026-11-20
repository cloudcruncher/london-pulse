"""Headless-browser smoke test of the deployed site. Skipped unless SMOKE_URL is set.

    SMOKE_URL=https://cloudcruncher.github.io/london-pulse/ uv run pytest tests/smoke -q

Covers: every tab loads without page errors, the home stories render, the Area guide and compare work, the SQL lab runs
a query and refuses outside reads, shared links never auto-run, the Mapbox map draws (when a token is deployed),
and the page does not scroll sideways on a phone.
"""

import json
import os
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")
URL = os.environ.get("SMOKE_URL", "").rstrip("/") + "/"
pytestmark = pytest.mark.skipif(not os.environ.get("SMOKE_URL"), reason="SMOKE_URL not set")
TABS = ["overview", "insights", "map", "area", "changes", "boroughs", "brands", "pubs", "craft", "report", "sql", "about"]


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, service_workers="block")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


def open_tab(page, tab, rest=""):
    page.goto(f"{URL}?smoke=1#{tab}{rest}", wait_until="domcontentloaded")
    page.wait_for_selector(f"#v-{tab}:not([hidden])", timeout=15000)


@pytest.mark.parametrize("tab", TABS)
def test_tab_loads_without_errors(page, tab):
    open_tab(page, tab)
    page.wait_for_timeout(1500)
    assert not page.errors, page.errors


def test_home_has_numbers_and_stories(page):
    open_tab(page, "overview")
    page.wait_for_function("document.querySelectorAll('#kpis .kpi, #kpis > *').length >= 3", timeout=15000)
    page.wait_for_function("document.querySelectorAll('#stories > *').length >= 3", timeout=15000)
    assert "Loading" not in page.inner_text("#asof")


def test_area_guide_and_compare(page):
    open_tab(page, "area", "/E8/1000")
    page.wait_for_selector("#areaout .areagrid", timeout=20000)
    text = page.inner_text("#areaout")
    assert "food and drink businesses" in text and "crimes recorded" in text and "London avg" in text
    page.fill("#areaq2", "N16")
    page.click("#areacmpgo")
    page.wait_for_selector("#areacmp table", timeout=20000)
    assert not page.errors, page.errors


def test_sql_lab_query_chart_and_lockdown(page):
    open_tab(page, "sql", "/q=" + "SELECT%20authority%2C%20count(*)%20AS%20venues%20FROM%20venues%20GROUP%20BY%201%20ORDER%20BY%202%20DESC%20LIMIT%205")
    page.wait_for_timeout(1500)
    assert page.locator("#sqlout tbody tr").count() == 0, "shared link must not run by itself"
    page.click("#sqlrun")
    page.wait_for_selector("#sqlout tbody tr", state="attached", timeout=90000)
    assert page.locator("#sqlout tbody tr").count() == 5
    assert page.locator("#sqlchart .hbars").count() == 1
    page.fill("#sqlbox", "SELECT * FROM read_csv('https://example.com/x.csv')")
    page.click("#sqlrun")
    page.wait_for_function("document.getElementById('sqlstat').textContent.includes('disabled')", timeout=20000)


def test_map_draws_with_mapbox(page):
    open_tab(page, "map")
    cfg = page.evaluate("window.LP_CONFIG && window.LP_CONFIG.mapboxToken || ''")
    if not cfg.startswith("pk."):
        pytest.skip("no public Mapbox token deployed")
    page.wait_for_selector("#gl .mapboxgl-canvas", timeout=30000)
    assert page.evaluate("document.getElementById('gl-layer').options.length") >= 7
    assert not page.errors, page.errors


def test_no_sideways_scroll_on_phone(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 800}, service_workers="block")
    pg = ctx.new_page()
    for tab in ["overview", "area", "brands", "pubs", "report", "sql", "about"]:
        pg.goto(f"{URL}?smoke=1#{tab}", wait_until="domcontentloaded")
        pg.wait_for_selector(f"#v-{tab}:not([hidden])", timeout=15000)
        pg.wait_for_timeout(1200)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{tab} scrolls sideways"
    ctx.close()


# ---------- postcode report card ----------
PC_OK = '{"status":200,"result":{"latitude":51.5458,"longitude":-0.0645,"region":"London","country":"England","outcode":"E8","admin_district":"Hackney"}}'
PC_OUT = '{"status":200,"result":{"latitude":53.48,"longitude":-2.24,"region":"North West","country":"England","outcode":"M1","admin_district":"Manchester"}}'


def mock_pc(page, mode="ok"):
    def handler(route):
        url = route.request.url
        if mode == "abort":
            return route.abort()
        if "ZZ11ZZ" in url:
            return route.fulfill(status=404, content_type="application/json", body='{"status":404}', headers={"access-control-allow-origin": "*"})
        body = PC_OUT if mode == "out" else PC_OK
        route.fulfill(status=200, content_type="application/json", body=body, headers={"access-control-allow-origin": "*"})
    page.route("https://api.postcodes.io/**", handler)


def test_report_card_renders(page):
    mock_pc(page)
    open_tab(page, "report", "/E8%203QW")
    page.wait_for_selector("#rpout .rc-row", timeout=30000)
    assert page.locator(".rc-row").count() == 6
    # Affordability carries two extra lines (price, rent) when prices.json and its metrics exist
    extra = 2 if page.locator("#rc-afford .rc-sub").count() else 0
    for row in page.locator(".rc-row").all():
        n = 1 + (extra if (row.get_attribute("id") == "rc-afford") else 0)
        assert row.locator(".pill").count() >= 1 and row.locator(".badge").count() == n
        assert row.locator("svg[role=img]").count() == n and row.locator("details").count() == n
    for sub in page.locator(".rc-sub").all():
        assert sub.locator(".badge").count() == 1 and sub.locator("summary", has_text="Show the working").count() == 1
    assert "No overall score" in page.inner_text("#rpout")
    assert not page.errors, page.errors


def test_report_compare_has_two_markers(page):
    mock_pc(page)
    open_tab(page, "report", "/E8%203QW,N16%205AA")
    page.wait_for_selector("#rpout .rc-row", timeout=30000)
    for svg in page.locator(".rc-row svg[role=img]").all():
        assert svg.locator(".mk").count() == 2


def test_report_not_a_postcode(page):
    mock_pc(page)
    open_tab(page, "report", "/ZZ1%201ZZ")
    page.wait_for_function("document.getElementById('rpout').innerText.includes('not a current postcode')", timeout=30000)


def test_report_outside_london(page):
    mock_pc(page, "out")
    open_tab(page, "report", "/M1%201AE")
    page.wait_for_function("document.getElementById('rpout').innerText.includes('Outside London')", timeout=30000)


def test_report_lookup_down_falls_back_to_approximate(page):
    mock_pc(page, "abort")
    open_tab(page, "report", "/E8%203QW")
    page.wait_for_selector("#rpout .rc-row", timeout=30000)
    assert "Approximate location" in page.inner_text("#rpout")


def test_report_matches_python_checks(page):
    open_tab(page, "report")
    res = page.evaluate(
        """async () => {
          const R = await fetch('api/v1/report.json').then(r => r.json()), m = await import('./assets/report.js'), out = [];
          for (const c of R.checks) { const M = await m.metricsFor({lon: c.lon, lat: c.lat}, c.outcode); out.push([c, M.values]); }
          return out; }"""
    )
    assert res
    for c, got in res:
        for k, want in c["values"].items():
            assert got[k] is not None and abs(got[k] - want) <= max(0.005 * abs(want), 0.05), (c["code"], k, got[k], want)


def test_report_prices_lines_and_graceful_absence(page):
    mock_pc(page)
    open_tab(page, "report", "/E8%203QW,N16%205AA")
    page.wait_for_selector("#rpout .rc-row", timeout=30000)
    have = page.evaluate("fetch('api/v1/prices.json').then(r => r.ok).catch(() => false)")
    subs = page.locator("#rc-afford .rc-sub")
    if have:
        assert subs.count() == 2
        txt = page.inner_text("#rc-afford")
        assert "Typical sale price" in txt and "two-bed private rent" in txt and "official statistics in development" in txt
        assert "Council tax, service charges, what is currently advertised" in txt
        for sub in subs.all():
            assert sub.locator("svg .mk").count() >= 1
    else:
        assert subs.count() == 0
    assert not page.errors, page.errors


def test_report_without_prices_omits_lines(page):
    page.route("**/api/v1/prices.json", lambda route: route.fulfill(status=404, body="nope"))
    mock_pc(page)
    open_tab(page, "report", "/E8%203QW")
    page.wait_for_selector("#rpout .rc-row", timeout=30000)
    assert page.locator(".rc-sub").count() == 0
    assert not [e for e in page.errors if "prices" not in str(e)], page.errors


# ---------- pubs tab ----------
PUBS_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pubs_sample.json"


PUBS_INSIGHTS_FIXTURE = PUBS_FIXTURE.with_name("pubs_insights_sample.json")


def mock_pubs(page, missing=False, insights=True):
    body = PUBS_FIXTURE.read_text()
    ins = PUBS_INSIGHTS_FIXTURE.read_text()
    page.route("**/api/v1/pubs.json", lambda r: r.fulfill(status=404, body="") if missing else r.fulfill(status=200, content_type="application/json", body=body))
    page.route("**/api/v1/pubs-insights.json", lambda r: r.fulfill(status=200, content_type="application/json", body=ins) if insights else r.fulfill(status=404, body=""))


def test_pubs_missing_data_is_friendly(page):
    mock_pubs(page, missing=True)
    open_tab(page, "pubs")
    page.wait_for_function("document.getElementById('pb-status').textContent.includes('not available')", timeout=15000)
    assert not page.errors, page.errors


def test_pubs_filters_list_and_detail(page):
    mock_pubs(page)
    n = len(json.loads(PUBS_FIXTURE.read_text())["pubs"])
    open_tab(page, "pubs")
    page.wait_for_selector("#pb-list button", timeout=15000)
    assert page.locator("#pb-counts button").count() == 5
    assert "No group link found" in page.inner_text("#pb-counts")
    assert page.inner_text("#pb-count").startswith(f"{n} pubs")
    page.click('#pb-counts button[data-cat="indie"]')
    assert page.locator('#pb-counts button[aria-pressed="true"]').count() == 1
    assert page.locator("#pb-list button").count() == 10
    page.click('#pb-counts button[data-cat="indie"]')
    page.check("#pb-cask")
    assert 0 < page.locator("#pb-list button").count() < n
    page.uncheck("#pb-cask")
    page.select_option("#pb-borough", "Hackney")
    assert page.locator("#pb-list button").count() == n // 5
    page.select_option("#pb-borough", "")
    page.fill("#pb-q", "Test Pub 03")
    page.wait_for_function("document.querySelectorAll('#pb-list button').length === 1")
    page.click("#pb-list button")
    d = page.inner_text("#pb-drawer")
    assert "Test Pub 03" in d and "Keg lines: no open data yet" in d and "Suggest a correction" in d
    assert "openstreetmap.org/" in page.locator("#pb-drawer a.pb-chip").first.get_attribute("href")
    href = page.locator("#pb-drawer a.btn").get_attribute("href")
    assert href.startswith("https://github.com/cloudcruncher/london-pulse/issues/new?title=Pub%20ownership") and "OSM%20id" in href
    assert not page.errors, page.errors


def test_pubs_postcode_sorts_by_distance_and_map_tap(page):
    mock_pc(page)
    mock_pubs(page)
    open_tab(page, "pubs")
    page.wait_for_selector("#pb-list button", timeout=15000)
    page.fill("#pb-pc", "E8 3QW")
    page.click("#pb-go")
    page.wait_for_selector("#pb-list .pb-dist", timeout=15000)
    first = page.locator("#pb-list li").first
    assert "Independent" in first.inner_text()
    assert page.locator("#pb-clear:not([hidden])").count() == 1
    box = page.locator("#pb-map").bounding_box()
    assert box and box["width"] > 100
    # tapping the map must not raise, whether or not it hits a pub
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    assert not page.errors, page.errors


def test_pubs_insights_render_from_fixture(page):
    mock_pubs(page)
    open_tab(page, "pubs", "?mode=insights")
    page.wait_for_selector("#pb-s-league tbody tr", timeout=15000)
    assert page.locator("#pb-insights:not([hidden])").count() == 1
    assert page.locator("#pb-explore[hidden]").count() == 1
    assert page.locator(".pb-hero").count() == 1
    for sec in ("league", "boroughs", "cmp", "dep", "churn"):
        assert page.locator(f"#pb-s-{sec}").count() == 1, sec
    assert page.locator("#pb-s-league tbody tr").count() == 3
    assert page.locator("#pb-s-boroughs svg").count() >= 1
    page.select_option("#pb-cmp-a", "Camden")
    page.select_option("#pb-cmp-b", "Hackney")
    assert "Camden" in page.inner_text("#pb-s-cmp") and "Hackney" in page.inner_text("#pb-s-cmp")
    assert page.locator(".pb-calcnote").count() == 0
    # league row drills into the group in the explore view
    page.locator('#pb-s-league [data-g="testpe"]').first.click()
    page.wait_for_selector("#pb-group:not([hidden])", timeout=5000)
    assert "g=testpe" in page.evaluate("location.hash")
    assert not page.errors, page.errors


def test_pubs_group_drilldown_filters(page):
    mock_pubs(page)
    open_tab(page, "pubs", "?g=testpe")
    page.wait_for_selector("#pb-group:not([hidden])", timeout=15000)
    page.wait_for_function("document.querySelectorAll('#pb-list button').length === 7", timeout=5000)
    assert page.locator("#pb-gsel").input_value() == "testpe"
    page.click("#pb-group .pb-back")
    page.wait_for_function("document.querySelectorAll('#pb-list button').length === 40", timeout=5000)
    assert "g=" not in page.evaluate("location.hash")
    assert not page.errors, page.errors


def test_pubs_url_state_round_trip(page):
    mock_pubs(page)
    open_tab(page, "pubs", "?cat=indie&b=Hackney&q=Test")
    page.wait_for_selector("#pb-list button", timeout=15000)
    assert page.locator("#pb-borough").input_value() == "Hackney"
    assert page.locator("#pb-q").input_value() == "Test"
    assert page.locator('#pb-counts button[data-cat="indie"][aria-pressed="true"]').count() == 1
    page.select_option("#pb-borough", "Camden")
    page.wait_for_function("location.hash.includes('b=Camden')", timeout=5000)
    h = page.evaluate("location.hash")
    assert "cat=indie" in h and "q=Test" in h
    page.goto(f"{URL}?smoke=1{h}", wait_until="domcontentloaded")
    page.wait_for_selector("#pb-list button", timeout=15000)
    assert page.locator("#pb-borough").input_value() == "Camden"
    assert not page.errors, page.errors


def test_pubs_insights_404_falls_back_to_computed(page):
    mock_pubs(page, insights=False)
    open_tab(page, "pubs", "?mode=insights")
    page.wait_for_selector("#pb-s-league tbody tr", timeout=15000)
    assert page.locator(".pb-calcnote").count() == 1
    assert page.locator("#pb-s-dep").count() == 0 and page.locator("#pb-s-churn").count() == 0
    assert "Camden" in page.inner_text(".pb-notes")  # core caveats are built in, not only in the file
    page.click("#pb-tab-explore")
    page.wait_for_selector("#pb-list button", timeout=5000)
    assert not page.errors, page.errors


def test_pubs_stale_insights_file_is_ignored(page):
    mock_pubs(page)
    stale = json.loads(PUBS_INSIGHTS_FIXTURE.read_text())
    stale["as_of"] = "2000-01-01"
    page.route("**/api/v1/pubs-insights.json", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(stale)))
    open_tab(page, "pubs", "?mode=insights")
    page.wait_for_selector("#pb-s-league tbody tr", timeout=15000)
    assert page.locator(".pb-calcnote").count() == 1
    assert page.locator("#pb-s-dep").count() == 0
    assert not page.errors, page.errors


def test_pubs_postcode_never_in_url_or_auto_queried(page):
    calls = []
    mock_pc(page)
    page.on("request", lambda r: calls.append(r.url) if "postcodes.io" in r.url else None)
    mock_pubs(page)
    open_tab(page, "pubs", "?pc=E83QW&b=Hackney")
    page.wait_for_selector("#pb-list button", timeout=15000)
    page.wait_for_timeout(500)
    assert not calls, calls  # a shared link must not send the postcode anywhere by itself
    assert page.locator("#pb-pc").input_value() == "E83QW"
    assert "pc=" not in page.evaluate("location.hash")
    page.click("#pb-go")
    page.wait_for_selector("#pb-list .pb-dist", timeout=15000)
    assert calls and "pc=" not in page.evaluate("location.hash")
    assert not page.errors, page.errors
