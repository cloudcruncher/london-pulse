"""Build site/data/insights.json from the latest snapshot, change events and history."""
import json
from datetime import date
from pathlib import Path

import duckdb

EAT_DRINK = ("Restaurant/Cafe/Canteen", "Takeaway/sandwich shop", "Pub/bar/nightclub")
COFFEE_RE = "coffee|espresso|roastery|barista|caffe|caffè"
RECENT_DAYS = 30


def build(curr: Path, events_dir: Path, history_csv: Path, out_json: Path, on: date) -> None:
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
    newest_unrated = rows("""SELECT name, business_type AS type, authority, postcode FROM fd
        WHERE rating = 'AwaitingInspection' ORDER BY fhrsid DESC LIMIT 25""")

    events: dict = {"days": 0, "by_day": [], "recent": {"new": [], "removed": [], "rating_changed": []}}
    csvs = sorted(events_dir.glob("*.csv"))[-RECENT_DAYS:]
    if csvs:
        glob = ",".join(f"'{p}'" for p in csvs)
        con.execute(f"CREATE VIEW ev AS SELECT * FROM read_csv([{glob}], header=true, all_varchar=true)")
        events["days"] = len(csvs)
        events["by_day"] = rows("""SELECT event_date, event, count(*) n FROM ev GROUP BY 1, 2 ORDER BY 1, 2""")
        events["by_borough"] = rows(f"""SELECT authority AS name, event, count(*) n FROM ev
            WHERE business_type IN ({types}) GROUP BY 1, 2 ORDER BY n DESC""")
        for kind in events["recent"]:
            events["recent"][kind] = rows(f"""SELECT event_date, name, business_type AS type, authority, postcode,
                old_rating, new_rating FROM ev WHERE event = '{kind}' AND business_type IN ({types})
                ORDER BY event_date DESC, authority LIMIT 40""")

    history = []
    if history_csv.exists():
        history = rows(f"SELECT * FROM read_csv('{history_csv}', header=true) ORDER BY snapshot_date")

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({
        "as_of": on.isoformat(), "totals": totals, "boroughs": boroughs, "rating_distribution": rating_dist,
        "business_types": by_type, "newest_unrated": newest_unrated, "events": events, "history": history,
    }, default=str, ensure_ascii=False))


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
