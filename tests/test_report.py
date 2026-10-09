import json
import math
from pathlib import Path

import pytest

from london_pulse.report import API, Context, MissingInput, build_report, km, percentile

KEYS = {"crime_rate", "income_dep_pct", "venues_800", "five_pct_800", "walk_min", "lines_1km", "income_ahc", "fresh_pct"}
PRICE_KEYS = {"median_price", "rent_2bed"}


@pytest.fixture(scope="module")
def report():
    if not all((API / n).exists() for n in ("areas.json", "venues.json", "stations.json", "character.json")):
        pytest.skip("inputs not present")
    return build_report(API)


def test_distributions_are_well_formed(report):
    assert set(report["metrics"]) in (KEYS, KEYS | PRICE_KEYS)
    for k, m in report["metrics"].items():
        assert len(m["q"]) == 21 and m["q"] == sorted(m["q"]), k
        assert len(m["hist"]["edges"]) == 21 and len(m["hist"]["counts"]) == 20 and sum(m["hist"]["counts"]) == m["n"], k
        assert m["lo"] <= m["hi"] and m["badge"] in {"measured", "modelled", "proxy"} and m["direction"] in {"+", "-", "0"}, k
    assert report["metrics"]["crime_rate"]["n"] > 4000
    assert report["metrics"]["venues_800"]["n"] > 4000


def test_checks_recompute_to_published(report):
    ids = {s["id"] for s in report["sources"]}
    assert all(i in ids for m in report["metrics"].values() for i in m["source_ids"])
    names = ("areas.json", "venues.json", "stations.json", "character.json") + (("prices.json",) if (API / "prices.json").exists() else ())
    ctx = Context(*(json.loads((API / n).read_text()) for n in names))
    assert len(report["checks"]) == 3
    for c in report["checks"]:
        m = ctx.metrics(c["lon"], c["lat"], c["outcode"], c["sector"])
        for k, v in c["values"].items():
            assert m[k] == pytest.approx(v, rel=1e-4, abs=1e-4), (c["code"], k)


def test_missing_input_raises(tmp_path):
    with pytest.raises(MissingInput):
        build_report(tmp_path)


def test_percentile_interpolates():
    assert percentile([0, 10], 25) == 2.5 and percentile([1, 2, 3], 100) == 3


def test_km_matches_page_formula():
    assert km(0, 51.5, 0, 51.5090) == pytest.approx(1.0, rel=0.01)
    assert not math.isnan(km(-0.1, 51.5, 0.0, 51.4))


def test_empty_series_is_skipped_not_fatal():
    from london_pulse.report import distribution
    assert percentile([], 50) is None
    assert distribution("crime_rate", []) is None
    assert distribution("crime_rate", [None, None]) is None


def test_pctof_ties_use_lower_index():
    import shutil
    import subprocess
    import textwrap
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not available")
    src = (Path(__file__).resolve().parent.parent / "site" / "assets" / "report.js").read_text()
    body = src[src.index("function pctOf"):src.index("function label")]
    js = textwrap.dedent("""
        let R = {metrics: {k: {q: [0, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]}}};
        %s
        console.log(JSON.stringify([pctOf('k', -1), pctOf('k', 0), pctOf('k', 1), pctOf('k', 18), pctOf('k', 1.5)]));
    """) % body
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out[:4] == [0, 0, 5, 100]
    assert 5 < out[4] < 20
