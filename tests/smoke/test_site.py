"""Headless-browser smoke test of the deployed site. Skipped unless SMOKE_URL is set.

    SMOKE_URL=https://cloudcruncher.github.io/london-pulse/ uv run pytest tests/smoke -q

Covers: every tab loads without page errors, the home stories render, the Area guide and compare work, the SQL lab runs
a query and refuses outside reads, shared links never auto-run, the Mapbox map draws (when a token is deployed),
and the page does not scroll sideways on a phone.
"""

import os

import pytest

pw = pytest.importorskip("playwright.sync_api")
URL = os.environ.get("SMOKE_URL", "").rstrip("/") + "/"
pytestmark = pytest.mark.skipif(not os.environ.get("SMOKE_URL"), reason="SMOKE_URL not set")
TABS = ["overview", "insights", "map", "area", "changes", "boroughs", "brands", "craft", "report", "sql", "about"]


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
    for tab in ["overview", "area", "brands", "report", "sql", "about"]:
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
    for row in page.locator(".rc-row").all():
        assert row.locator(".pill").count() >= 1 and row.locator(".badge").count() == 1
        assert row.locator("svg[role=img]").count() == 1 and row.locator("details").count() == 1
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
