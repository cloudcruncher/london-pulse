"""Diff two snapshots into change events (new / removed / rating_changed)."""
from datetime import date
from pathlib import Path

import duckdb


def diff_snapshots(prev: Path, curr: Path, out_csv: Path, on: date) -> dict:
    con = duckdb.connect()
    con.execute(f"CREATE VIEW p AS SELECT * FROM '{prev}'")
    con.execute(f"CREATE VIEW c AS SELECT * FROM '{curr}'")
    con.execute(f"""
        CREATE TABLE ev AS
        SELECT DATE '{on.isoformat()}' AS event_date, 'new' AS event, c.fhrsid, c.name, c.business_type,
               c.authority, c.postcode, NULL AS old_rating, c.rating AS new_rating, c.lon, c.lat
        FROM c ANTI JOIN p USING (fhrsid)
        UNION ALL
        SELECT DATE '{on.isoformat()}', 'removed', p.fhrsid, p.name, p.business_type,
               p.authority, p.postcode, p.rating, NULL, p.lon, p.lat
        FROM p ANTI JOIN c USING (fhrsid)
        UNION ALL
        SELECT DATE '{on.isoformat()}', 'rating_changed', c.fhrsid, c.name, c.business_type,
               c.authority, c.postcode, p.rating, c.rating, c.lon, c.lat
        FROM c JOIN p USING (fhrsid) WHERE c.rating IS DISTINCT FROM p.rating""")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM ev ORDER BY event, authority, name) TO '{out_csv}' (HEADER)")
    return dict(con.execute("SELECT event, count(*) FROM ev GROUP BY 1").fetchall())
