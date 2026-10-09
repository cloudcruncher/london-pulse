"""London craft-drinks company formation from the Companies House monthly bulk snapshot.

Source: Companies House "Free Company Data Product" (Open Government Licence v3.0).
London is approximated by postcode district: any outward code that appears in the FSA London snapshot.
Companies on the snapshot are those still on the register, so older cohorts are survivors.
"""
import argparse
import json
import re
from datetime import date
from pathlib import Path

import duckdb

GROUPS = {
    "coffee_roasting": ("10832", "Coffee roasting"),
    "brewing": ("11050", "Breweries"),
    "distilling": ("11010", "Distilleries"),
    "pubs_bars": ("56302", "Pubs and bars"),
}
CH_URL = "https://find-and-update.company-information.service.gov.uk/company/"


def build(csv: Path, fsa_parquet: Path, out: Path, on: date) -> None:
    con = duckdb.connect()
    con.execute(f"""CREATE TABLE co AS SELECT CompanyNumber AS num, CompanyName AS name,
        "RegAddress.PostCode" AS pc, CompanyStatus AS status,
        try_strptime(IncorporationDate, '%d/%m/%Y')::DATE AS inc,
        "SICCode.SicText_1" AS s1, "SICCode.SicText_2" AS s2, "SICCode.SicText_3" AS s3, "SICCode.SicText_4" AS s4
        FROM read_csv('{csv}', header=true, all_varchar=true, ignore_errors=true, strict_mode=false)""")
    pat = "^([A-Za-z]{1,2}[0-9][A-Za-z0-9]?)"
    con.execute(f"""CREATE TABLE lon AS SELECT DISTINCT upper(regexp_extract(postcode, '{pat}', 1)) AS od
        FROM '{fsa_parquet}' WHERE regexp_extract(postcode, '{pat}', 1) <> ''""")
    con.execute(f"""CREATE TABLE lc AS SELECT *, upper(regexp_extract(pc, '{pat}', 1)) AS od FROM co
        WHERE status = 'Active' AND upper(regexp_extract(pc, '{pat}', 1)) IN (SELECT od FROM lon)""")

    # Mass-registration addresses (formation agents, virtual offices) distort district rankings.
    con.execute("""CREATE TABLE hubs AS SELECT replace(upper(pc), ' ', '') AS pc FROM lc
        GROUP BY 1 HAVING count(*) >= 150""")

    def rows(sql):
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    result = {}
    for key, (code, label) in GROUPS.items():
        has = " OR ".join(f"{c} LIKE '{code}%'" for c in ("s1", "s2", "s3", "s4"))
        con.execute(f"CREATE OR REPLACE VIEW g AS SELECT * FROM lc WHERE {has}")
        result[key] = {
            "label": label, "sic": code,
            "active": con.execute("SELECT count(*) FROM g").fetchone()[0],
            "formed_last_12m": con.execute(f"SELECT count(*) FROM g WHERE inc >= DATE '{on.isoformat()}' - INTERVAL 12 MONTH").fetchone()[0],
            "by_month": rows(f"""SELECT strftime(date_trunc('month', inc), '%Y-%m') AS month, count(*) AS n FROM g
                WHERE inc >= date_trunc('month', DATE '{on.isoformat()}') - INTERVAL 23 MONTH
                GROUP BY 1 ORDER BY 1"""),
            "by_year": rows("SELECT year(inc)::INT AS year, count(*) AS n FROM g WHERE inc >= DATE '2015-01-01' GROUP BY 1 ORDER BY 1"),
            "top_districts": rows("SELECT od AS district, count(*) AS n FROM g WHERE replace(upper(pc), ' ', '') NOT IN (SELECT pc FROM hubs) "
                "GROUP BY 1 ORDER BY n DESC, od LIMIT 8"),
            "recent": [{**r, "url": CH_URL + r["num"]} for r in rows(
                "SELECT num, name, pc AS postcode, strftime(inc, '%Y-%m-%d') AS incorporated FROM g "
                "WHERE inc IS NOT NULL ORDER BY inc DESC LIMIT 15")],
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema_version": 1, "as_of": on.isoformat(), "generated": date.today().isoformat(),
        "source": "Companies House free company data product", "licence": "Open Government Licence v3.0",
        "note": "Active companies with a London postcode district. Registered offices are not always trading premises; district rankings exclude mass-registration addresses.",
        "groups": result,
    }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--fsa", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("site/api/v1/companies.json"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    a = ap.parse_args()
    build(a.csv, a.fsa, a.out, a.as_of)
    print("wrote", a.out)
