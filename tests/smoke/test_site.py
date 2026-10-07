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
TABS = ["overview", "map", "area", "changes", "boroughs", "brands", "craft", "sql", "about"]


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
    for tab in ["overview", "area", "brands", "sql", "about"]:
        pg.goto(f"{URL}?smoke=1#{tab}", wait_until="domcontentloaded")
        pg.wait_for_selector(f"#v-{tab}:not([hidden])", timeout=15000)
        pg.wait_for_timeout(1200)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{tab} scrolls sideways"
    ctx.close()
