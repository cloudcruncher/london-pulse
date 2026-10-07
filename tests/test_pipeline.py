from datetime import date

import duckdb
import pytest

from london_pulse.diff import diff_snapshots
from london_pulse.run import sanity_check


def snap(path, rows):
    con = duckdb.connect()
    con.execute("CREATE TABLE t (fhrsid BIGINT, name VARCHAR, business_type VARCHAR, address VARCHAR, postcode VARCHAR, rating VARCHAR, rating_date VARCHAR, authority VARCHAR, lon DOUBLE, lat DOUBLE)")
    for r in rows:
        con.execute("INSERT INTO t VALUES (?,?,?,?,?,?,?,?,?,?)", [r[0], r[1], "Pub/bar/nightclub", "", "E1 1AA", r[2], None, "Camden", -0.1, 51.5])
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")


def test_diff_finds_new_removed_rerated_with_coords(tmp_path):
    snap(tmp_path / "p.parquet", [(1, "A", "5"), (2, "B", "4"), (3, "C", "3")])
    snap(tmp_path / "c.parquet", [(1, "A", "5"), (3, "C", "5"), (4, "D", "AwaitingInspection")])
    out = tmp_path / "e.csv"
    diff_snapshots(tmp_path / "p.parquet", tmp_path / "c.parquet", out, date(2026, 10, 8))
    rows = {r[1]: r for r in duckdb.sql(f"SELECT event_date, event, name, lon, lat FROM read_csv('{out}', header=true)").fetchall()}
    assert set(rows) == {"new", "removed", "rating_changed"}
    assert rows["new"][2] == "D" and rows["removed"][2] == "B" and rows["rating_changed"][2] == "C"
    assert rows["removed"][3] == -0.1


def test_sanity_gate(tmp_path):
    snap(tmp_path / "p.parquet", [(i, "x", "5") for i in range(10)])
    with pytest.raises(SystemExit):      # far below the minimum London size
        sanity_check(10, None)
    with pytest.raises(SystemExit):      # >10% swing against previous day
        sanity_check(70_001, _prev(tmp_path, 100_000))
    assert sanity_check(80_000, None) is None


def _prev(tmp, n):
    con = duckdb.connect()
    con.execute(f"COPY (SELECT range AS fhrsid FROM range({n})) TO '{tmp / 'big.parquet'}' (FORMAT parquet)")
    return tmp / "big.parquet"


def test_brand_patterns_match_variants_not_lookalikes():
    import re
    from london_pulse.brands import CURATED, norm
    rx = {i: re.compile(p) for i, _, _, p in CURATED}
    for n in ["GAIL’s Bakery Putney", "Gails Cheapside", "Gail's", "GAILS Hammersmith"]:
        assert rx["gails"].search(norm(n)), n
    assert not rx["gails"].search(norm("Gail Bennett"))
    assert rx["caffe-nero"].search(norm("Caffè Nero")) and rx["costa"].search(norm("COSTA"))
    assert not rx["costa"].search(norm("Costa Rican Grill"))
