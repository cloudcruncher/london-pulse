import pytest

from london_pulse.neighbourhoods import analyse, band_of, partial_spearman, rank, spearman


def test_rank_averages_ties():
    assert rank([10, 20, 20, 30]) == [1, 2.5, 2.5, 4]


def test_spearman_is_monotone_not_linear():
    assert spearman([1, 2, 3, 4], [1, 10, 100, 1000]) == pytest.approx(1)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1)


def test_partial_spearman_removes_a_shared_driver():
    # crime and council share both just follow deprivation, so holding deprivation fixed leaves no link
    dep = list(range(1, 41))
    council = [d + (3 if i % 2 else -3) for i, d in enumerate(dep)]
    crime = [d + (2 if i % 3 else -2) for i, d in enumerate(dep)]
    assert spearman(council, crime) > 0.9
    assert abs(partial_spearman(council, crime, dep)) < 0.5


def test_bands_cover_zero_to_hundred():
    assert [band_of(x) for x in (0, 4.9, 5, 14.9, 15, 29.9, 30, 100)] == [0, 0, 1, 1, 2, 2, 3, 3]


def test_analyse_ignores_busy_centres_and_unrated_areas():
    def area(council, rate, busy=False, dec=5):
        return {"council_pct": council, "rate": rate, "busy": busy, "imd_decile": dec, "income_decile": dec, "income_dep": council / 100 + (0.05 if council % 4 else 0)}
    rows = [area(c, 10 + c) for c in range(0, 100, 2)] + [area(1, 900, busy=True), area(1, None)]
    a = analyse(rows)
    assert a["n"] == 50                      # the busy centre and the area with no usable population are left out
    assert a["bands"][0]["median_rate"] < a["bands"][3]["median_rate"]
    assert a["spearman"]["council_vs_crime"] == pytest.approx(1)


def test_band_of_clamps_out_of_range():
    assert band_of(100.4) == 3 and band_of(-1) == 0


def test_same_deprivation_needs_enough_areas_per_cell():
    def area(council, rate):
        return {"council_pct": council, "rate": rate, "busy": False, "imd_decile": 1, "income_decile": 1, "income_dep": .3}
    rows = [area(2, 50)] * 20 + [area(40, 80)] * 5       # only 5 council-majority areas: too few to quote a median
    cells = analyse(rows)["same_deprivation"][0]["bands"]
    assert cells[0]["median_rate"] == 50 and cells[3]["median_rate"] is None
