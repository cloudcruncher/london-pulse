"""Build the versioned static JSON API (site/api/v1) from the latest snapshot, events and history."""
import json
from datetime import date
from pathlib import Path

import duckdb

EAT_DRINK = ("Restaurant/Cafe/Canteen", "Takeaway/sandwich shop", "Pub/bar/nightclub")
COFFEE_RE = "coffee|espresso|roastery|barista|caffe|caffè"
RECENT_DAYS = 30


SCHEMA_VERSION = 1


def build(curr: Path, events_dir: Path, history_csv: Path, api_dir: Path, on: date) -> None:
    """Write the versioned static JSON API (consumed by the website and any future app)."""
    con = duckdb.connect()
    con.execute(f"CREATE VIEW s AS SELECT * FROM '{curr}'")
    types = ",".join(f"'{t}'" for t in EAT_DRINK)
    con.execute(f"CREATE VIEW fd AS SELECT * FROM s WHERE business_type IN ({types})")

    def rows(sql: str) -> list[dict]:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    totals = rows("""SELECT count(*) premises,
        count(*) FILTER (business_type IN (%s)) eating_drinking,
        count(*) FILTER (rating = '5') five_star,
        count(*) FILTER (rating IN ('0','1','2')) low_rated,
        count(*) FILTER (rating = 'AwaitingInspection') awaiting FROM s""" % types)[0]

    boroughs = rows("""SELECT authority AS name, count(*) premises,
        round(100.0 * count(*) FILTER (rating = '5') / nullif(count(*) FILTER (rating ~ '^[0-5]$'), 0), 1) five_star_pct,
        count(*) FILTER (rating IN ('0','1','2')) low_rated,
        count(*) FILTER (rating = 'AwaitingInspection') awaiting,
        round(100.0 * count(*) FILTER (rating = 'AwaitingInspection') / count(*), 1) awaiting_pct
        FROM fd GROUP BY 1 ORDER BY premises DESC""")
    coffee = {r["authority"]: r["n"] for r in rows(f"""SELECT authority, count(*) n FROM fd
            WHERE regexp_matches(lower(name), '{COFFEE_RE}') GROUP BY 1""")}
    for b in boroughs:
        b["coffee_named"] = coffee.get(b["name"], 0)

    rating_dist = rows("""SELECT rating, count(*) n FROM fd GROUP BY 1 ORDER BY
        CASE WHEN rating ~ '^[0-5]$' THEN rating::INT ELSE 9 END""")
    by_type = rows("SELECT business_type AS type, count(*) n FROM s GROUP BY 1 ORDER BY n DESC LIMIT 10")
    # FHRS ids are issued per authority, so "newest" is only meaningful within a borough: newest awaiting venue in each.
    newest_unrated = rows("""SELECT name, business_type AS type, authority, postcode FROM (
        SELECT *, row_number() OVER (PARTITION BY authority ORDER BY fhrsid DESC) rn FROM fd
        WHERE rating = 'AwaitingInspection') WHERE rn = 1 ORDER BY authority""")
    stories = build_stories(con, rows)

    events: dict = {"days": 0, "by_day": [], "recent": {"new": [], "removed": [], "rating_changed": []}}
    csvs = sorted(events_dir.glob("*.csv"))[-RECENT_DAYS:]
    if csvs:
        glob = ",".join(f"'{p}'" for p in csvs)
        con.execute(f"CREATE VIEW ev AS SELECT * FROM read_csv([{glob}], header=true, all_varchar=true, union_by_name=true)")
        events["days"] = len(csvs)
        events["by_day"] = rows("""SELECT event_date, event, count(*) n FROM ev GROUP BY 1, 2 ORDER BY 1, 2""")
        events["by_borough"] = rows(f"""SELECT authority AS name, event, count(*) n FROM ev
            WHERE business_type IN ({types}) GROUP BY 1, 2 ORDER BY n DESC""")
        latest = con.execute("SELECT max(event_date) FROM ev").fetchone()[0]
        events["latest_date"] = latest
        events["latest"] = rows(f"""SELECT event, name, business_type AS type, authority, postcode, old_rating, new_rating,
            try_cast(lon AS DOUBLE) AS lon, try_cast(lat AS DOUBLE) AS lat FROM ev
            WHERE event_date = '{latest}' AND lon IS NOT NULL AND lon <> '' LIMIT 3000""")
        for kind in events["recent"]:
            events["recent"][kind] = rows(f"""SELECT event_date, name, business_type AS type, authority, postcode,
                old_rating, new_rating FROM ev WHERE event = '{kind}' AND business_type IN ({types})
                ORDER BY event_date DESC, authority LIMIT 40""")

    history = []
    if history_csv.exists():
        history = rows(f"SELECT * FROM read_csv('{history_csv}', header=true) ORDER BY snapshot_date")

    api_dir.mkdir(parents=True, exist_ok=True)
    meta = {"schema_version": SCHEMA_VERSION, "as_of": on.isoformat()}

    def write(name: str, payload: dict) -> None:
        (api_dir / name).write_text(json.dumps({**meta, **payload}, default=str, ensure_ascii=False, separators=(",", ":")))

    write("summary.json", {"totals": totals, "rating_distribution": rating_dist, "business_types": by_type,
                           "newest_unrated": newest_unrated, "stories": stories})
    write("boroughs.json", {"boroughs": boroughs})
    write("events.json", {"events": events})
    write("history.json", {"history": history})
    export_venues(con, api_dir / "venues.json", meta)
    export_parquet(con, events_dir, history_csv, api_dir)


def export_venues(con, out: Path, meta: dict) -> None:
    """Compact point list for the map. Dictionary-encoded to keep it small (generated, not committed)."""
    types = list(EAT_DRINK)
    ratings = ["5", "4", "3", "2", "1", "0", "AwaitingInspection", "Exempt", "AwaitingPublication"]
    auths = [r[0] for r in con.execute("SELECT DISTINCT authority FROM fd ORDER BY 1").fetchall()]
    rows = con.execute("""SELECT round(lon, 5), round(lat, 5), business_type, rating, authority, name, postcode
                          FROM fd WHERE lon IS NOT NULL AND lat IS NOT NULL AND lon BETWEEN -0.6 AND 0.4
                          AND lat BETWEEN 51.2 AND 51.75""").fetchall()
    ti = {t: i for i, t in enumerate(types)}
    ri = {r: i for i, r in enumerate(ratings)}
    ai = {a: i for i, a in enumerate(auths)}
    v = [[lo, la, ti[t], ri.get(r, 7), ai[a], n, pc] for lo, la, t, r, a, n, pc in rows]
    out.write_text(json.dumps({**meta, "types": types, "ratings": ratings, "boroughs": auths, "venues": v},
                              ensure_ascii=False, separators=(",", ":")))


def append_history(curr: Path, history_csv: Path, on: date) -> None:
    con = duckdb.connect()
    types = ",".join(f"'{t}'" for t in EAT_DRINK)
    con.execute(f"""CREATE TABLE h AS SELECT DATE '{on.isoformat()}' AS snapshot_date, authority,
        count(*) premises, count(*) FILTER (business_type IN ({types})) eating_drinking,
        count(*) FILTER (rating = '5') five_star, count(*) FILTER (rating = 'AwaitingInspection') awaiting
        FROM '{curr}' GROUP BY 2""")
    if history_csv.exists():
        con.execute(f"""CREATE TABLE old AS SELECT * FROM read_csv('{history_csv}', header=true)
            WHERE snapshot_date <> DATE '{on.isoformat()}'""")
        con.execute("INSERT INTO old SELECT * FROM h")
        src = "old"
    else:
        src = "h"
    history_csv.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM {src} ORDER BY snapshot_date, authority) TO '{history_csv}' (HEADER)")



def export_parquet(con, events_dir: Path, history_csv: Path, api_dir: Path) -> None:
    """Parquet files the in-browser SQL lab loads (DuckDB-WASM). Generated on each run, not committed."""
    con.execute(f"""COPY (SELECT fhrsid, name, business_type, address, postcode, rating, rating_date, authority, lon, lat
                     FROM s ORDER BY authority, name) TO '{api_dir / "venues.parquet"}' (FORMAT parquet, COMPRESSION zstd)""")
    csvs = sorted(events_dir.glob("*.csv"))
    if csvs:
        glob = ",".join(f"'{p}'" for p in csvs)
        con.execute(f"""COPY (SELECT try_cast(event_date AS DATE) AS event_date, event, try_cast(fhrsid AS BIGINT) AS fhrsid, name,
            business_type, authority, postcode, old_rating, new_rating, try_cast(lon AS DOUBLE) AS lon, try_cast(lat AS DOUBLE) AS lat
            FROM read_csv([{glob}], header=true, all_varchar=true, union_by_name=true))
            TO '{api_dir / "events.parquet"}' (FORMAT parquet, COMPRESSION zstd)""")
    else:   # empty file with the right schema so the SQL lab's tables always exist
        con.execute(f"""COPY (SELECT NULL::DATE AS event_date, NULL::VARCHAR AS event, NULL::BIGINT AS fhrsid, NULL::VARCHAR AS name,
            NULL::VARCHAR AS business_type, NULL::VARCHAR AS authority, NULL::VARCHAR AS postcode, NULL::VARCHAR AS old_rating,
            NULL::VARCHAR AS new_rating, NULL::DOUBLE AS lon, NULL::DOUBLE AS lat WHERE false)
            TO '{api_dir / "events.parquet"}' (FORMAT parquet)""")
    if history_csv.exists():
        con.execute(f"""COPY (SELECT * FROM read_csv('{history_csv}', header=true)) TO '{api_dir / "history.parquet"}' (FORMAT parquet)""")


def build_stories(con, rows) -> dict:
    """Headline findings for the home page. Every number is computed here so the page can state it plainly."""
    rated = "rating ~ '^[0-5]$'"
    stale = rows(f"""SELECT authority AS name, count(*) rated,
        round(100.0 * count(*) FILTER (rating_date < current_date - INTERVAL 2 YEAR) / count(*), 1) pct_stale
        FROM fd WHERE {rated} AND rating_date IS NOT NULL GROUP BY 1 HAVING count(*) >= 300 ORDER BY pct_stale DESC""")
    london_stale = rows(f"""SELECT round(100.0 * count(*) FILTER (rating_date < current_date - INTERVAL 2 YEAR) / count(*), 1) p
        FROM fd WHERE {rated} AND rating_date IS NOT NULL""")[0]["p"]
    by_type = rows(f"""SELECT business_type AS type, count(*) rated,
        round(100.0 * count(*) FILTER (rating = '5') / count(*), 1) five_star_pct,
        round(100.0 * count(*) FILTER (rating IN ('0','1','2')) / count(*), 1) low_pct
        FROM fd WHERE {rated} GROUP BY 1 ORDER BY five_star_pct DESC""")
    names = rows("""SELECT upper(trim(name)) AS name, count(*) n FROM fd GROUP BY 1 ORDER BY n DESC LIMIT 5""")
    total_fd = rows("SELECT count(*) n FROM fd")[0]["n"]
    takeaway = rows("""SELECT authority AS name, count(*) venues,
        round(100.0 * count(*) FILTER (business_type = 'Takeaway/sandwich shop') / count(*), 1) takeaway_pct
        FROM fd GROUP BY 1 HAVING count(*) >= 300 ORDER BY takeaway_pct DESC""")
    hot = rows("""SELECT split_part(postcode, ' ', 1) AS district, count(*) awaiting FROM fd
        WHERE rating = 'AwaitingInspection' AND postcode <> '' GROUP BY 1 ORDER BY awaiting DESC LIMIT 5""")
    weak = rows(f"""SELECT split_part(postcode, ' ', 1) AS district, count(*) rated,
        round(100.0 * count(*) FILTER (rating IN ('0','1','2')) / count(*), 1) low_pct
        FROM fd WHERE {rated} AND postcode <> '' GROUP BY 1 HAVING count(*) >= 150 ORDER BY low_pct DESC LIMIT 5""")
    return {
        "stale": {"london_pct": london_stale, "worst": stale[:3], "best": stale[-1]},
        "by_type": by_type,
        "top_names": names, "top_names_share": round(100.0 * sum(n["n"] for n in names) / total_fd, 1),
        "takeaway": {"highest": takeaway[0], "lowest": takeaway[-1]},
        "awaiting_hotspots": hot,
        "weak_districts": weak,
    }
