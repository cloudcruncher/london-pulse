"""Green space metrics on tiny synthetic polygons (no network). Coordinates are EPSG:27700 metres."""
import duckdb

from london_pulse.green import PARK_MIN_M2, SOURCES, metrics_from_tables


def box(x0, y0, x1, y1):
    return f"ST_GeomFromText('POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))')"


def run(lsoas, sites):
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute("CREATE TABLE lsoa (code VARCHAR, geom GEOMETRY)")
    con.execute("CREATE TABLE site (function VARCHAR, geom GEOMETRY)")
    for code, g in lsoas:
        con.execute(f"INSERT INTO lsoa VALUES ('{code}', {g})")
    for fn, g in sites:
        con.execute(f"INSERT INTO site VALUES ('{fn}', {g})")
    return metrics_from_tables(con)


def test_area_share_unions_overlaps_and_ignores_non_green():
    m = run(
        [("A", box(0, 0, 1000, 1000)), ("B", box(5000, 0, 6000, 1000)), ("C", box(0, 5000, 1000, 6000))],
        [("Public Park Or Garden", box(0, 0, 500, 1000)),     # half of A
         ("Playing Field", box(250, 0, 750, 1000)),           # overlaps the park: union is 75%
         ("Golf Course", box(5000, 0, 6000, 1000)),           # not counted
         ("Public Park Or Garden", box(-500, 5000, 1500, 7000))])  # covers C entirely and spills over: capped
    assert m["A"]["green_pct"] == 75.0
    assert m["B"]["green_pct"] == 0.0
    assert m["C"]["green_pct"] == 100.0


def test_nearest_park_needs_two_hectares_and_public_park_function():
    big = box(2000, 400, 2200, 600)        # 4 ha, centroid of LSOA is 1500 m from its edge
    small = box(1100, 450, 1150, 500)          # 0.25 ha park, closer but too small
    assert 200 * 200 >= PARK_MIN_M2 > 50 * 50
    m = run([("A", box(0, 0, 1000, 1000))],
            [("Public Park Or Garden", big), ("Public Park Or Garden", small), ("Playing Field", box(600, 400, 900, 700))])
    # centroid (500, 500) -> nearest edge of the 4 ha park at x=2000
    assert m["A"]["park_m"] == 1500


def test_centroid_inside_park_is_zero():
    m = run([("A", box(0, 0, 1000, 1000))], [("Public Park Or Garden", box(0, 0, 1000, 1000))])
    assert m["A"]["park_m"] == 0 and m["A"]["green_pct"] == 100.0


def test_source_shape():
    assert set(SOURCES[0]) == {"id", "name", "publisher", "url", "licence", "vintage", "caveat"}
