"""Price aggregation on a tiny synthetic Price Paid CSV, and the PIPR rent parse on synthetic rows."""
import csv
from datetime import date

import pytest

from london_pulse.prices import CoverageError, aggregate_sales, month_back, rents_from_rows


def row(id_, price, day, pc, ptype="F", cat="A", status="A", county="GREATER LONDON"):
    return [id_, str(price), f"{day} 00:00", pc, ptype, "N", "L", "1", "", "ST", "", "LONDON", "HACKNEY", county, cat, status]


@pytest.fixture
def ppd(tmp_path):
    rows = []
    # E8 1: 12 flats (prices 100k..1.1m, median is 600k but the mean is the same, so add an outlier below) and 3 houses
    rows += [row(f"f{i}", 100_000 * (i + 1), "2026-06-15", "E8 1AB") for i in range(11)]  # 100k..1.1m
    rows += [row("outlier", 9_000_000, "2026-06-20", "e8 1ZZ")]                          # mean 1.3m+, median 650k; lower case too
    rows += [row(f"h{i}", 700_000, "2026-05-01", "E8 1CD", ptype="T") for i in range(3)]
    rows += [row("catb", 50_000_000, "2026-06-01", "E8 1AB", cat="B")]                   # additional category: excluded
    rows += [row("del", 5_000_000, "2026-06-01", "E8 1AB", status="D")]                  # deleted: excluded
    rows += [row("old", 1_000, "2025-01-01", "E8 1AB")]                                  # outside the 12 month window
    rows += [row("kent", 1_000_000, "2026-06-01", "ME1 1AA", county="KENT")]             # not London
    rows += [row(f"s{i}", 300_000, "2026-06-02", "E8 2AB") for i in range(4)]            # a sector with too few sales
    rows += [row("nopc", 300_000, "2026-06-02", "")]                                     # no postcode: skipped
    # a change record replaces its earlier add
    rows += [row("chg", 111_000, "2026-06-03", "E8 3AA"), row("chg", 222_000, "2026-06-03", "E8 3AA", status="C")]
    rows += [row(f"c{i}", 500_000, "2026-06-03", "E8 3AA") for i in range(9)]
    p = tmp_path / "pp.csv"
    with open(p, "w", newline="") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerows(rows)
    return aggregate_sales([p])


def test_window_and_as_of(ppd):
    assert ppd["as_of"] == "2026-06" and ppd["window"] == {"from": "2025-07", "to": "2026-06"}


def test_sector_is_outward_plus_first_inward_digit(ppd):
    assert "E8 1" in ppd["sectors"] and "E8 2" in ppd["sectors"] and "E8 3" in ppd["sectors"]
    assert "ME1 1" not in ppd["sectors"]


def test_median_not_mean_and_exclusions(ppd):
    all_, n_all, flat, n_flat, house, n_house = ppd["sectors"]["E8 1"]
    # 11 flats 100k..1.1m + the 9m outlier + 3 houses at 700k: 15 sales, category B / deleted / old / other counties excluded
    assert n_all == 15 and n_flat == 12 and n_house == 3
    assert all_ == 700_000 and all_ != round(sum([*range(100_000, 1_200_000, 100_000), 9_000_000, 700_000 * 3]) / 15)
    assert flat == 650_000                      # median of 12 flats: mean of 600k and 700k
    assert house is None                        # 3 < min_sales


def test_min_sales_null_rule(ppd):
    assert ppd["sectors"]["E8 2"] == [None, 4, None, 4, None, 0]
    assert ppd["districts"]["E8"][1] == 15 + 4 + 10


def test_change_record_replaces_add(ppd):
    m, n, *_ = ppd["sectors"]["E8 3"]
    assert n == 10 and m == 500_000             # 9 x 500k and one 222k: median 500k; the 111k add is gone


def test_district_median(ppd):
    assert ppd["districts"]["E8"][0] is not None


def test_no_london_sales_raises(tmp_path):
    p = tmp_path / "pp.csv"
    p.write_text(",".join(f'"{x}"' for x in row("a", 1, "2026-01-01", "ME1 1AA", county="KENT")) + "\n")
    with pytest.raises(RuntimeError):
        aggregate_sales([p])


def test_month_back():
    assert month_back("2026-08", 11) == "2025-09" and month_back("2026-01", 1) == "2025-12"


def test_rents_latest_month_london_boroughs_only():
    s = lambda d: (d - date(1899, 12, 30)).days
    old, new = s(date(2026, 7, 1)), s(date(2026, 8, 1))
    rows = [
        (old, "E09000012", "Hackney", 2600.0, 1990.0, 2450.0, 2800.0, 3.0),
        (new, "E09000012", "Hackney", 2658.4, 2001.0, 2489.0, 2846.0, 3.64),
        (new, "E09000002", "Barking and Dagenham", 1698.0, None, 1721.0, None, None),
        (new, "E06000001", "Hartlepool", 600.0, 500.0, 600.0, 700.0, 1.0),     # not London
    ]
    r = rents_from_rows(rows)
    assert r["as_of"] == "2026-08" and set(r["boroughs"]) == {"E09000012", "E09000002"}
    assert r["boroughs"]["E09000012"] == {"name": "Hackney", "all": 2658, "one_bed": 2001, "two_bed": 2489, "three_bed": 2846,
                                          "annual_change_pct": 3.6}
    assert r["boroughs"]["E09000002"]["one_bed"] is None and r["boroughs"]["E09000002"]["annual_change_pct"] is None


def test_rents_without_london_raises():
    with pytest.raises(RuntimeError):
        rents_from_rows([(46235, "E06000001", "Hartlepool", 1.0, 1.0, 1.0, 1.0, 1.0)])


def test_window_not_covered_raises(tmp_path):
    """January case: latest sale 2026-12 means a window from 2026-01, but only 2026-03 onwards is held."""
    rows = [row("a", 500_000, "2026-12-01", "E8 1AB"), row("b", 500_000, "2026-03-01", "E8 1AB")]
    p = tmp_path / "pp.csv"
    with open(p, "w", newline="") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerows(rows)
    with pytest.raises(CoverageError, match="2026-01"):
        aggregate_sales([p])
