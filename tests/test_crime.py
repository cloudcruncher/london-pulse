import pytest

from london_pulse.crime import check_failures


def test_no_failures_passes():
    check_failures(0, 1000)


def test_at_tolerance_passes_and_above_fails():
    check_failures(5, 1000)                      # exactly 0.5%
    with pytest.raises(SystemExit):
        check_failures(6, 1000)


def test_empty_query_does_not_divide_by_zero():
    check_failures(0, 0)


def test_failed_leaf_is_weighted_in_query_cell_units(monkeypatch):
    from london_pulse import crime

    def boom(*a, **k):
        raise RuntimeError("503")

    monkeypatch.setattr(crime, "get_json", boom)
    rows, failed = crime.fetch_cell(51.5, -0.1, 0.04, "2026-08")
    assert rows == [] and failed == pytest.approx(1.0)   # 16 leaves x 1/16 = the one query cell
