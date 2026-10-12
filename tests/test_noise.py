import struct

import duckdb
import pytest

from london_pulse import noise


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("INSTALL spatial; LOAD spatial")
    c.execute("""CREATE TABLE noise_lsoa AS SELECT code, geom, ST_Area(geom) AS area FROM (VALUES
        ('A', ST_GeomFromText('POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))')),
        ('B', ST_GeomFromText('POLYGON((200 0, 300 0, 300 100, 200 100, 200 0))')),
        ('C', ST_GeomFromText('POLYGON((500 0, 600 0, 600 100, 500 100, 500 0))'))) t(code, geom)""")
    return c


def test_partial_cover_is_area_share(con):
    # left quarter of A; B and C untouched
    assert noise.overlay_pct(con, [(0, 0, 25, 100)]) == {"A": 25.0, "B": 0.0, "C": 0.0}


def test_rect_straddling_two_lsoas_counts_in_both(con):
    got = noise.overlay_pct(con, [(90, 0, 210, 50)])
    assert got["A"] == 5.0 and got["B"] == 5.0 and got["C"] == 0.0


def test_disjoint_rects_add_up_and_round_to_one_decimal(con):
    assert noise.overlay_pct(con, [(0, 0, 10, 100), (50, 0, 53.33, 100)])["A"] == 13.3


def test_overlapping_cover_is_capped_at_100(con):
    # a rect bigger than the LSOA, and duplicated rects that double count: never above 100
    assert noise.overlay_pct(con, [(-50, -50, 150, 150)])["A"] == 100.0
    assert noise.overlay_pct(con, [(0, 0, 100, 100), (0, 0, 100, 100)])["A"] == 100.0


def test_no_rects_gives_zero_everywhere(con):
    assert noise.overlay_pct(con, []) == {"A": 0.0, "B": 0.0, "C": 0.0}


def test_rects_from_rows_threshold_and_merge():
    nd = -3.4e38
    rows = [[nd, 56.0, 70.0, nd], [nd, 57.0, 66.0, nd], [nd, nd, 58.0, nd]]
    # cells are 10 m, grid origin (1000, 5000); >=55 covers cols 1-2 rows 0-1 (merged) and col 2 row 2
    assert sorted(noise.rects_from_rows(rows, 55, 1000, 5000, 10)) == [
        (1010, 4980, 1030, 5000),
        (1020, 4970, 1030, 4980),
    ]
    # >=65 keeps only the two high cells in col 2, merged into one tall rect
    assert noise.rects_from_rows(rows, 65, 1000, 5000, 10) == [(1020, 4980, 1030, 5000)]
    assert noise.rects_from_rows(rows, 99, 1000, 5000, 10) == []


def test_rects_ignore_nodata_sentinel_above_range():
    assert noise.rects_from_rows([[1e38, 60.0]], 55, 0, 10, 10) == [(10, 0, 20, 10)]


def test_end_to_end_cells_to_percent(con):
    # 10 m cells over A (cols 0-9, rows 0-9 from y=100 down); left 3 columns loud
    rows = [[60.0] * 3 + [-1e38] * 7 for _ in range(10)]
    rects = noise.rects_from_rows(rows, 55, 0, 100, 10)
    assert noise.overlay_pct(con, rects)["A"] == 30.0


def test_read_geotiff_tiled_big_endian(tmp_path):
    # 3x2 raster in two 2x2 tiles (right tile padded), float32, big-endian, with a ModelTransformation tag
    vals = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    t1 = struct.pack(">4f", 1.0, 2.0, 4.0, 5.0)
    t2 = struct.pack(">4f", 3.0, 0.0, 6.0, 0.0)
    entries = [
        (256, 3, 1, 3),
        (257, 3, 1, 2),
        (258, 3, 1, 32),
        (259, 3, 1, 1),
        (322, 3, 1, 2),
        (323, 3, 1, 2),
        (324, 4, 2, None),
        (325, 4, 2, None),
        (339, 3, 1, 3),
        (34264, 12, 16, None),
    ]
    n = len(entries)
    ifd_len = 2 + 12 * n + 4
    extra = 8 + ifd_len
    offs_at, cnts_at, mt_at = extra, extra + 8, extra + 16
    data_at = mt_at + 16 * 8
    mt = struct.pack(">16d", 20, 0, 0, 1000, 0, -20, 0, 5000, 0, 0, 0, 0, 0, 0, 0, 1)
    body = b"".join(
        struct.pack(">HHI", t, ty, c)
        + (struct.pack(">HH", v, 0) if ty == 3 else struct.pack(">I", v))
        if v is not None
        else struct.pack(
            ">HHII", t, ty, c, {324: offs_at, 325: cnts_at, 34264: mt_at}[t]
        )
        for t, ty, c, v in entries
    )
    ifd = struct.pack(">H", n) + body + struct.pack(">I", 0)
    blob = (
        b"MM\x00\x2a"
        + struct.pack(">I", 8)
        + ifd
        + struct.pack(">2I", data_at, data_at + 16)
        + struct.pack(">2I", 16, 16)
        + mt
        + t1
        + t2
    )
    p = tmp_path / "t.tif"
    p.write_bytes(blob)
    rows, x0, y_top, px = noise.read_geotiff(p)
    assert [list(r) for r in rows] == vals
    assert (x0, y_top, px) == (1000, 5000, 20)
