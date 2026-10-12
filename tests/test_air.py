import duckdb
import pytest

from london_pulse import air


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("INSTALL spatial; LOAD spatial")
    c.execute("""CREATE TABLE lsoa AS SELECT * FROM (VALUES
        ('A', ST_GeomFromText('POLYGON((0 0, 100 0, 100 100, 0 100, 0 0))')),
        ('B', ST_GeomFromText('POLYGON((200 0, 205 0, 205 5, 200 5, 200 0))'))) t(code, geom)""")
    c.execute("""CREATE TABLE cells AS SELECT * FROM (VALUES
        (10, 10, 20.0, 10.0, 15.0), (50, 50, 30.0, 12.0, 17.0), (90, 90, 40.0, 14.0, 19.0),
        (500, 500, 99.0, 99.0, 99.0),      -- outside everything
        (190, 2, 60.0, 20.0, 30.0),        -- nearest to B (10 m away), not inside it
        (150, 2, 70.0, 25.0, 35.0)) t(x, y, no2, pm25, pm10)""")
    return c


def test_mean_of_cells_inside(con):
    assert air.aggregate(con)["A"] == {"no2": 30.0, "pm25": 12.0, "pm10": 17.0}


def test_nearest_cell_fallback(con):
    assert air.aggregate(con)["B"] == {"no2": 60.0, "pm25": 20.0, "pm10": 30.0}


def test_no_cell_within_radius_is_omitted(con):
    con.execute("INSERT INTO lsoa VALUES ('C', ST_GeomFromText('POLYGON((9000 9000, 9010 9000, 9010 9010, 9000 9000))'))")
    assert "C" not in air.aggregate(con)


def test_rounding_one_dp(con):
    con.execute("DELETE FROM cells; INSERT INTO cells VALUES (10, 10, 17.48829, 7.892006, 13.23266)")
    assert air.aggregate(con)["A"] == {"no2": 17.5, "pm25": 7.9, "pm10": 13.2}


def test_guidelines_and_source_shape():
    assert air.GUIDELINES["no2"]["who"] == 10 and air.GUIDELINES["pm25"]["uk_target_2040"] == 10
    assert {"id", "name", "publisher", "url", "licence", "vintage", "caveat"} <= set(air.SOURCES[0])
