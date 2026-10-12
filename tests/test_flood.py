"""Flood overlay maths on tiny synthetic geometries (no network)."""

import struct

import pytest

from london_pulse import flood


def square(x, y, s):
    return f"POLYGON(({x} {y},{x + s} {y},{x + s} {y + s},{x} {y + s},{x} {y}))"


@pytest.fixture
def con():
    c = flood._con()
    # two 100 m x 100 m LSOAs side by side (EPSG:27700 metres)
    c.execute("CREATE TABLE lsoa(code VARCHAR, g GEOMETRY)")
    c.execute(
        f"INSERT INTO lsoa VALUES ('A', ST_GeomFromText('{square(0, 0, 100)}')), "
        f"('B', ST_GeomFromText('{square(100, 0, 100)}'))"
    )
    c.execute("CREATE TABLE lsoa_area AS SELECT code, ST_Area(g) AS area FROM lsoa")
    return c


def flood_table(c, wkts):
    c.execute("CREATE OR REPLACE TABLE f(g GEOMETRY)")
    for w in wkts:
        c.execute(f"INSERT INTO f VALUES (ST_GeomFromText('{w}'))")


def test_pct_rounds_and_caps():
    assert flood._pct(1, 3) == 33.3
    assert flood._pct(150, 100) == 100.0
    assert flood._pct(-1, 100) == 0.0
    assert flood._pct(5, 0) == 0.0


def test_overlay_partial_cover_and_untouched_lsoa_absent(con):
    flood_table(con, [square(0, 0, 50)])  # quarter of A, nothing in B
    assert flood.overlay_pct(con, "f") == {"A": 25.0}


def test_overlay_spans_two_lsoas(con):
    flood_table(con, [square(50, 0, 100)])  # half of A and half of B
    assert flood.overlay_pct(con, "f") == {"A": 50.0, "B": 50.0}


def test_overlay_overlapping_polygons_not_double_counted(con):
    flood_table(
        con, [square(0, 0, 60), square(40, 0, 60)]
    )  # overlap 40..60 -> union 0..100 x 0..60
    assert flood.overlay_pct(con, "f") == {"A": 60.0, "B": 0.0}   # B only touched along an edge


def test_overlay_duplicate_polygons_capped_at_full(con):
    flood_table(con, [square(0, 0, 100)] * 3)
    assert flood.overlay_pct(con, "f") == {"A": 100.0, "B": 0.0}


def test_overlay_flood_larger_than_lsoa_capped(con):
    flood_table(con, [square(-500, -500, 2000)])
    assert flood.overlay_pct(con, "f") == {"A": 100.0, "B": 100.0}


def test_overlay_tolerates_invalid_bowtie(con):
    # self-intersecting polygon is made valid by the loader; here we do it explicitly as compute() does
    con.execute("CREATE OR REPLACE TABLE f(g GEOMETRY)")
    con.execute(
        "INSERT INTO f VALUES (ST_MakeValid(ST_GeomFromText('POLYGON((0 0,100 100,100 0,0 100,0 0))')))"
    )
    pct = flood.overlay_pct(con, "f")["A"]
    assert 0 < pct <= 50.0


def rgba_tiff(pixels, w, h):
    """Minimal big-endian uncompressed RGBA TIFF, one strip per row, like the EA WMS returns."""
    data = b"".join(bytes(p) for p in pixels)
    ifd_off = 8
    n = 9
    tag_area = ifd_off + 2 + 12 * n + 4
    bps_off = tag_area
    so_off = bps_off + 8
    sc_off = so_off + 4 * h
    data_off = sc_off + 4 * h
    ents = [
        (256, 3, 1, struct.pack(">HH", w, 0)),
        (257, 3, 1, struct.pack(">HH", h, 0)),
        (258, 3, 4, struct.pack(">I", bps_off)),
        (259, 3, 1, struct.pack(">HH", 1, 0)),
        (262, 3, 1, struct.pack(">HH", 2, 0)),
        (273, 4, h, struct.pack(">I", so_off)),
        (277, 3, 1, struct.pack(">HH", 4, 0)),
        (278, 3, 1, struct.pack(">HH", 1, 0)),
        (279, 4, h, struct.pack(">I", sc_off)),
    ]
    out = b"MM\x00\x2a" + struct.pack(">I", ifd_off) + struct.pack(">H", n)
    for tag, typ, cnt, val in ents:
        out += struct.pack(">HHI", tag, typ, cnt) + val
    out += struct.pack(">I", 0) + struct.pack(">4H", 8, 8, 8, 8)
    out += b"".join(struct.pack(">I", data_off + r * w * 4) for r in range(h))
    out += b"".join(struct.pack(">I", w * 4) for _ in range(h))
    return out + data


def test_classify_tile_colours_and_alpha(monkeypatch):
    monkeypatch.setattr(flood, "SW_PX", 6)
    clear = (255, 255, 255, 0)
    high = (85, 91, 157, 255)
    med = (154, 159, 222, 255)
    low = (195, 224, 255, 255)
    faint_high = (85, 91, 157, 10)  # anti-aliased edge, mostly transparent: ignored
    grey = (166, 166, 166, 255)  # "unavailable": ignored
    px = [clear, high, med, low, faint_high, grey]
    hi, md = flood.classify_tile(rgba_tiff(px * 6, 6, 6))
    assert list(hi) == [0, 1, 0, 0, 0, 0] * 6
    assert list(md) == [0, 1, 1, 0, 0, 0] * 6  # medium-or-worse includes high, excludes low


def test_blank_or_wrong_sized_tile_is_rejected_not_read_as_no_risk(monkeypatch):
    monkeypatch.setattr(flood, "SW_PX", 4)
    with pytest.raises(ValueError, match="fully transparent"):
        flood.classify_tile(rgba_tiff([(255, 255, 255, 0)] * 16, 4, 4))
    with pytest.raises(ValueError, match="expected 4x4"):
        flood.classify_tile(rgba_tiff([(85, 91, 157, 255)] * 6, 3, 2))


def test_runs_pixel_centres(monkeypatch):
    monkeypatch.setattr(flood, "SW_PX", 8)
    monkeypatch.setattr(flood, "SW_TILE_M", 80)
    monkeypatch.setattr(flood, "SW_PIXEL_M", 10)
    plane = bytearray(64)
    plane[0:2] = b"\x01\x01"  # top row (northernmost), pixels 0-1
    plane[8 * 7 + 3] = 1  # bottom row, pixel 3
    runs = flood._runs(bytes(plane), 1000, 2000)
    assert runs == [(1005, 2075, 2), (1035, 2005, 1)]


def test_pixel_pct_counts_and_caps(con, tmp_path):
    # 10 m pixels: A is 10x10 = 100 pixels. Flag its bottom 3 rows (30 px) plus a duplicate row to prove the cap.
    rows = ["x,y,n"] + [f"5,{5 + 10 * r},10" for r in range(3)]
    f = tmp_path / "r.csv"
    f.write_text("\n".join(rows) + "\n")
    assert flood.pixel_pct(con, f) == {"A": 30.0}
    rows = ["x,y,n"] + [f"5,{5 + 10 * r},10" for r in range(10)] * 2
    f.write_text("\n".join(rows) + "\n")
    assert flood.pixel_pct(con, f) == {"A": 100.0}
