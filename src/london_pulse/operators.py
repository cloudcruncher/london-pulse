"""Who is opening, where momentum is building and how the local business mix is shifting, from Companies House.

Source: Companies House "Free Company Data Product" (Open Government Licence v3.0), monthly, London postcode districts.
The free file has no directors, so "first-time founder" and "serial operator" are inferred from the company itself
(its age, a name or registered address shared with other food and drink companies) and every such label says "likely".
Companies on the register today are survivors, so the older half of any comparison is slightly depleted.
"""

import argparse
import json
import re
from datetime import date
from pathlib import Path

import duckdb

SECTORS = {
    "food_drink": ("Food and drink", ("5610", "5621", "5629", "5630")),
    "craft": (
        "Craft makers (bakers, roasters, brewers, distillers)",
        ("10710", "10832", "11010", "11020", "11050", "10520"),
    ),
    "creative": ("Creative and culture (includes SIC 74, all other professional, scientific and technical services)", ("59", "74", "90")),
    "wellness": ("Wellness and personal care", ("9313", "9604", "9602")),
    "retail": ("Retail (all)", ("47",)),
    "tech": ("Software and tech", ("62",)),
}
SHIFT_SECTORS = (
    "craft",
    "creative",
    "wellness",
    "tech",
)  # sectors whose share of new registrations is compared with their share of the existing stock (no causal claim)
HUB_MIN = 150  # registered addresses with this many companies are formation agents or virtual offices
MIN_DISTRICT_RECENT = (
    12  # formations in the last 12 months before a district can be ranked
)
WINDING_STATUSES = (
    "Active - Proposal to Strike off",
    "Liquidation",
    "In Administration",
    "Live but Receiver Manager on at least one charge",
    "Voluntary Arrangement",
)
CH_URL = "https://find-and-update.company-information.service.gov.uk/company/"
GENERIC = {
    "the",
    "ltd",
    "limited",
    "llp",
    "plc",
    "uk",
    "london",
    "group",
    "holdings",
    "restaurant",
    "restaurants",
    "cafe",
    "caf",
    "food",
    "foods",
    "kitchen",
    "catering",
    "bar",
    "pub",
    "fish",
    "chicken",
    "pizza",
    "grill",
    "charcoal",
    "golden",
    "fresh",
    "royal",
    "new",
    "best",
    "la",
    "el",
    "al",
    "and",
    "of",
}
PAT = "^([A-Za-z]{1,2}[0-9][A-Za-z0-9]?)"


def norm_name(name: str) -> str:
    """Comparable form of a trading or company name: no punctuation, company suffixes or leading 'the'."""
    s = re.sub(r"[^a-z0-9 ]", "", (name or "").lower().replace("&", " and "))
    s = re.sub(r"\b(ltd|limited|llp|plc|uk)\b", " ", s)
    s = re.sub(r"^the\s+", "", re.sub(r"\s+", " ", s).strip())
    return s.strip()


def key(name: str) -> str:
    """Every distinctive word of a name, so 'Sufi Grill And Pizza' and 'SUFI GRILL & PIZZA LTD' meet."""
    return " ".join(t for t in norm_name(name).replace(" and ", " ").split() if t not in GENERIC)


def stem(name: str) -> str:
    """First two distinctive words of a company name, or '' when the name is too generic to link companies."""
    toks = [t for t in norm_name(name).split() if t not in GENERIC and len(t) > 1]
    s = " ".join(toks[:2])
    return s if len(s) >= 6 else ""


def _month_back(on: date, months: int) -> date:
    y, m = divmod(on.year * 12 + on.month - 1 - months, 12)
    return date(y, m + 1, 1)


def _verdict(recent: int, prior: int) -> str | None:
    if recent + prior < 40:
        return None
    return (
        "accelerating"
        if recent > prior * 1.15
        else "slowing"
        if recent < prior * 0.85
        else "steady"
    )


def build(csv: Path, fsa_parquet: Path, out: Path, on: date) -> None:
    con = duckdb.connect()
    con.execute(f"""CREATE TABLE lon AS SELECT DISTINCT upper(regexp_extract(postcode, '{PAT}', 1)) AS od
                    FROM '{fsa_parquet}' WHERE regexp_extract(postcode, '{PAT}', 1) <> ''""")
    con.execute(f"""CREATE TABLE co AS SELECT CompanyNumber AS num, CompanyName AS name,
        "RegAddress.AddressLine1" AS a1, "RegAddress.PostCode" AS pc,
        upper(regexp_extract("RegAddress.PostCode", '{PAT}', 1)) AS od, CompanyStatus AS status,
        try_strptime(IncorporationDate, '%d/%m/%Y')::DATE AS inc, "Accounts.AccountCategory" AS acc,
        try_cast("Mortgages.NumMortCharges" AS INT) AS charges, "PreviousName_1.CompanyName" AS prev1,
        "SICCode.SicText_1" AS s1, "SICCode.SicText_2" AS s2, "SICCode.SicText_3" AS s3, "SICCode.SicText_4" AS s4
        FROM read_csv('{csv}', header=true, all_varchar=true, ignore_errors=true, strict_mode=false)
        WHERE upper(regexp_extract("RegAddress.PostCode", '{PAT}', 1)) IN (SELECT od FROM lon)
          AND (CompanyStatus LIKE 'Active%' OR CompanyStatus IN ({",".join(f"'{s}'" for s in WINDING_STATUSES)}))""")
    con.execute(
        f"""CREATE TABLE hubs AS SELECT replace(upper(pc), ' ', '') AS pc FROM co GROUP BY 1 HAVING count(*) >= {HUB_MIN}"""
    )
    con.execute(
        "DELETE FROM co WHERE replace(upper(pc), ' ', '') IN (SELECT pc FROM hubs) OR inc IS NULL"
    )
    unions = " UNION ALL ".join(
        f"""SELECT num, '{k}' AS sector FROM co WHERE {" OR ".join(f"{c} LIKE '{p}%'" for c in ("s1", "s2", "s3", "s4") for p in prefixes)}"""
        for k, (_, prefixes) in SECTORS.items()
    )
    con.execute(f"CREATE TABLE sec AS {unions}")
    con.execute(
        "CREATE TABLE cs AS SELECT co.*, sec.sector FROM co JOIN sec USING (num)"
    )

    t0, t6, t12 = on, _month_back(on, 6), _month_back(on, 12)
    t24 = _month_back(on, 24)

    def rows(sql: str) -> list[dict]:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---- momentum: formations by sector, last 6 months against the 6 before -----------------------------------
    momentum = {}
    for k, (label, _) in SECTORS.items():
        r = rows(f"""SELECT count(*) FILTER (inc >= DATE '{t6}' AND inc < DATE '{t0}') AS last6,
                            count(*) FILTER (inc >= DATE '{t12}' AND inc < DATE '{t6}') AS prior6,
                            count(*) FILTER (inc >= DATE '{t12}' AND inc < DATE '{t0}') AS last12,
                            count(*) FILTER (inc >= DATE '{t24}' AND inc < DATE '{t12}') AS prior12,
                            count(*) AS stock FROM cs WHERE sector = '{k}'""")[0]
        r["change_pct"] = (
            round(100.0 * (r["last6"] - r["prior6"]) / r["prior6"], 1)
            if r["prior6"]
            else None
        )
        r["yoy_pct"] = (
            round(100.0 * (r["last12"] - r["prior12"]) / r["prior12"], 1)
            if r["prior12"]
            else None
        )
        r["verdict"] = _verdict(r["last6"], r["prior6"])
        r["label"] = label
        w = rows(f"""SELECT count(*) AS n, count(*) FILTER (status IN ({",".join(f"'{x}'" for x in WINDING_STATUSES)})) AS wind
                     FROM cs WHERE sector = '{k}' AND inc >= DATE '{t24}' AND inc < DATE '{t12}'""")[0]
        r["prior_cohort_winding_pct"] = round(100.0 * w["wind"] / w["n"], 1) if w["n"] else None
        r["by_month"] = (
            rows(f"""SELECT strftime(date_trunc('month', inc), '%Y-%m') AS month, count(*) AS n FROM cs
            WHERE sector = '{k}' AND inc >= DATE '{t24}' AND inc < DATE '{t0}' GROUP BY 1 ORDER BY 1""")
        )
        momentum[k] = r

    # ---- seasonality: which calendar months do new companies appear in (2016-2025 full years) ------------------
    seasonality = {}
    for k in ("food_drink", "craft", "creative"):
        m = rows(f"""SELECT month(inc)::INT AS month, count(*) AS n FROM cs WHERE sector = '{k}'
                     AND inc >= DATE '2016-01-01' AND inc < DATE '{on.year}-01-01' GROUP BY 1 ORDER BY 1""")
        tot = sum(x["n"] for x in m) or 1
        for x in m:
            x["index"] = round(100.0 * x["n"] / (tot / 12), 0)  # 100 = an average month
        seasonality[k] = {
            "label": SECTORS[k][0],
            "months": m,
            "peak": max(m, key=lambda x: x["n"])["month"] if m else None,
            "trough": min(m, key=lambda x: x["n"])["month"] if m else None,
        }

    # ---- districts: momentum, mix shift and winding down ---------------------------------------------------------
    sh = ",".join(f"'{s}'" for s in SHIFT_SECTORS)
    stock = {
        r["od"]: r
        for r in rows(f"""SELECT od, count(*) AS stock,
        count(*) FILTER (sector IN ({sh})) AS shift_stock,
        count(*) FILTER (sector = 'food_drink') AS fd_stock,
        count(*) FILTER (sector = 'food_drink' AND status IN ({",".join(f"'{s}'" for s in WINDING_STATUSES)})) AS fd_winding
        FROM cs GROUP BY 1""")
    }
    recent = {
        r["od"]: r
        for r in rows(f"""SELECT od,
        count(*) FILTER (inc >= DATE '{t6}') AS last6, count(*) FILTER (inc < DATE '{t6}') AS prior6,
        count(*) AS last12, count(*) FILTER (sector IN ({sh})) AS shift_last12,
        count(*) FILTER (sector = 'food_drink') AS fd_last12
        FROM cs WHERE inc >= DATE '{t12}' AND inc < DATE '{t0}' GROUP BY 1""")
    }
    districts = []
    for od, rc in recent.items():
        st = stock.get(od)
        if not st or rc["last12"] < MIN_DISTRICT_RECENT:
            continue
        shift_now = 100.0 * rc["shift_last12"] / rc["last12"]
        shift_then = 100.0 * st["shift_stock"] / st["stock"] if st["stock"] else 0
        districts.append(
            {
                "district": od,
                "formed_12m": rc["last12"],
                "last6": rc["last6"],
                "prior6": rc["prior6"],
                "change_pct": round(
                    100.0 * (rc["last6"] - rc["prior6"]) / rc["prior6"], 1
                )
                if rc["prior6"]
                else None,
                "verdict": _verdict(rc["last6"], rc["prior6"])
                if rc["last12"] >= 40
                else None,
                "mix_new_pct": round(shift_now, 1),
                "mix_stock_pct": round(shift_then, 1),
                "mix_shift_pts": round(shift_now - shift_then, 1),
                "food_drink_12m": rc["fd_last12"],
                "winding_pct": round(100.0 * st["fd_winding"] / st["fd_stock"], 1)
                if st["fd_stock"] >= 40
                else None,
            }
        )
    districts.sort(key=lambda d: -d["formed_12m"])

    # ---- who is opening: profile of food and drink companies and their match to new premises ---------------------
    # names are normalised in Python (a DuckDB UDF would pull in numpy); only the food and drink rows need it
    con.execute("CREATE TABLE nm (num VARCHAR, nn VARCHAR, st VARCHAR, nk VARCHAR)")
    con.executemany("INSERT INTO nm VALUES (?, ?, ?, ?)", [
        (n, norm_name(nme), stem(nme), key(nme)) for n, nme in
        con.execute("SELECT DISTINCT num, name FROM cs WHERE sector = 'food_drink'").fetchall()])
    con.execute(f"""CREATE TABLE fd AS SELECT cs.*, nm.nn, nm.st, nm.nk,
                    date_diff('month', inc, DATE '{on}') AS age_m FROM cs JOIN nm USING (num) WHERE sector = 'food_drink'""")
    con.execute(
        """CREATE TABLE st_n AS SELECT st, count(*) AS n FROM fd WHERE st <> '' GROUP BY 1"""
    )
    con.execute(
        """CREATE TABLE ad_n AS SELECT upper(pc) AS pc, upper(a1) AS a1, count(*) AS n FROM fd GROUP BY 1, 2"""
    )
    con.execute("""CREATE TABLE fdx AS SELECT fd.*, coalesce(st_n.n, 1) AS stem_n, coalesce(ad_n.n, 1) AS addr_n,
        CASE WHEN age_m < 12 THEN 'New company (under a year)' WHEN age_m < 36 THEN 'Young (1 to 3 years)'
             WHEN age_m < 120 THEN 'Established (3 to 10 years)' ELSE 'Long-standing (10+ years)' END AS age_band
        FROM fd LEFT JOIN st_n USING (st) LEFT JOIN ad_n ON upper(fd.pc) = ad_n.pc AND upper(fd.a1) = ad_n.a1""")
    con.execute(
        """CREATE VIEW fdx2 AS SELECT *, (stem_n >= 2 AND stem_n <= 60) AS named_group, (addr_n >= 2 AND addr_n <= 6) AS shared_addr FROM fdx"""
    )
    newco = f"inc >= DATE '{t12}' AND inc < DATE '{t0}'"
    profile = rows(f"""SELECT count(*) AS companies,
        round(100.0 * count(*) FILTER (named_group OR shared_addr) / count(*), 1) AS linked_pct,
        round(100.0 * count(*) FILTER (named_group) / count(*), 1) AS named_group_pct,
        round(100.0 * count(*) FILTER (shared_addr) / count(*), 1) AS shared_addr_pct,
        round(100.0 * count(*) FILTER (NOT named_group AND NOT shared_addr) / count(*), 1) AS standalone_pct,
        round(100.0 * count(*) FILTER (prev1 IS NOT NULL AND prev1 <> '') / count(*), 1) AS renamed_pct
        FROM fdx2 WHERE {newco}""")[0]
    profile["by_status"] = (
        rows(f"""SELECT CASE WHEN status LIKE 'Active%' AND status = 'Active' THEN 'Active' ELSE 'Winding down' END AS status,
        count(*) AS n FROM fdx2 WHERE inc >= DATE '{t24}' AND inc < DATE '{t12}' GROUP BY 1""")
    )
    prior = rows(
        f"""SELECT count(*) AS n FROM fdx2 WHERE inc >= DATE '{t24}' AND inc < DATE '{t12}'"""
    )[0]["n"]
    wind = next(
        (x["n"] for x in profile["by_status"] if x["status"] == "Winding down"), 0
    )
    profile["prior_cohort_winding_pct"] = (
        round(100.0 * wind / prior, 1) if prior else None
    )
    stock_age = rows("""SELECT age_band, count(*) AS n FROM fdx2 GROUP BY 1""")
    order = [
        "New company (under a year)",
        "Young (1 to 3 years)",
        "Established (3 to 10 years)",
        "Long-standing (10+ years)",
    ]
    profile["stock_by_age"] = sorted(
        stock_age, key=lambda x: order.index(x["age_band"])
    )
    profile["scale_of_older"] = (
        rows(f"""SELECT CASE WHEN acc = 'MICRO ENTITY' THEN 'Micro' WHEN acc IN ('SMALL', 'TOTAL EXEMPTION SMALL') THEN 'Small'
        WHEN acc IN ('MEDIUM', 'FULL', 'GROUP', 'AUDITED ABRIDGED') THEN 'Medium or larger' WHEN acc = 'DORMANT' THEN 'Dormant'
        WHEN acc = 'NO ACCOUNTS FILED' THEN 'No accounts yet' ELSE 'Other (abridged or exempt)' END AS scale, count(*) AS n
        FROM fdx2 WHERE age_m >= 24 GROUP BY 1 ORDER BY n DESC""")
    )
    profile["borrowing_pct_older"] = rows(
        """SELECT round(100.0 * count(*) FILTER (charges > 0) / count(*), 1) AS p FROM fdx2 WHERE age_m >= 24"""
    )[0]["p"]

    # New premises (awaiting first inspection) matched to a company by name and postcode district.
    con.execute(f"""CREATE TABLE newv AS SELECT fhrsid, name, business_type, authority, postcode,
        upper(regexp_extract(postcode, '{PAT}', 1)) AS od FROM '{fsa_parquet}'
        WHERE rating = 'AwaitingInspection' AND business_type IN ('Restaurant/Cafe/Canteen', 'Takeaway/sandwich shop',
              'Pub/bar/nightclub', 'Other catering premises')""")
    con.execute("CREATE TABLE newn (fhrsid BIGINT, nn VARCHAR, nk VARCHAR)")
    con.executemany("INSERT INTO newn VALUES (?, ?, ?)", [
        (i, norm_name(nme), key(nme)) for i, nme in con.execute("SELECT fhrsid, name FROM newv").fetchall()])
    con.execute("ALTER TABLE newv ADD COLUMN nn VARCHAR")
    con.execute("ALTER TABLE newv ADD COLUMN nk VARCHAR")
    con.execute("UPDATE newv SET nn = newn.nn, nk = newn.nk FROM newn WHERE newv.fhrsid = newn.fhrsid")
    con.execute("""CREATE TABLE uniq AS SELECT nk, od, any_value(num) AS num, any_value(name) AS cname, any_value(age_band) AS age_band,
        any_value(age_m) AS age_m, any_value(named_group) AS named_group, any_value(shared_addr) AS shared_addr, any_value(status) AS status
        FROM fdx2 WHERE length(nk) >= 5 GROUP BY nk, od HAVING count(*) = 1""")
    con.execute("""CREATE TABLE matched AS SELECT newv.*, uniq.num, uniq.cname, uniq.age_band, uniq.age_m, uniq.named_group, uniq.shared_addr
        FROM newv JOIN uniq ON newv.nk = uniq.nk AND newv.od = uniq.od""")
    total_new = con.execute("SELECT count(*) FROM newv").fetchone()[0]
    mt = rows("""SELECT count(*) AS n, count(*) FILTER (age_m < 12) AS new_entrant, count(*) FILTER (age_m >= 12 AND age_m < 36) AS young,
        count(*) FILTER (age_m >= 36) AS established, count(*) FILTER (named_group OR shared_addr) AS linked FROM matched""")[
        0
    ]
    examples = rows("""SELECT name, cname AS company, num, postcode, authority, age_band, (named_group OR shared_addr) AS linked FROM matched
        ORDER BY age_m, name LIMIT 14""")
    for e in examples:
        e["url"] = CH_URL + e["num"]
        e["read"] = (
            "likely first venue of a new company"
            if e["age_band"].startswith("New") and not e["linked"]
            else "new company linked to other food or drink companies"
            if e["age_band"].startswith("New")
            else "established operator, new site or new owner"
            if not e["age_band"].startswith("Young")
            else "young company" + (", linked to others" if e["linked"] else "")
        )
    opening = {
        "new_premises_awaiting": total_new,
        "matched": mt["n"],
        "match_rate_pct": round(100.0 * mt["n"] / total_new, 1) if total_new else None,
        "of_matched": {
            "new_entrant": mt["new_entrant"],
            "young": mt["young"],
            "established": mt["established"],
            "linked": mt["linked"],
        },
        "examples": examples,
    }

    result = {
        "schema_version": 1,
        "as_of": on.isoformat(),
        "generated": date.today().isoformat(),
        "source": "Companies House free company data product",
        "licence": "Open Government Licence v3.0",
        "sectors": {k: v[0] for k, v in SECTORS.items()},
        "momentum": momentum,
        "seasonality": seasonality,
        "districts": districts,
        "new_company_profile": profile,
        "who_is_opening": opening,
    }
    result["headlines"] = _headlines(result)
    result["method"] = (
        "Companies registered to a London postcode district in each sector, excluding mass-registration addresses. "
        "'Linked' means a shared distinctive name stem or registered address with another food and drink company; the free "
        "file has no directors, so first-time founder is a likely, not a certain, label. Today's register is survivors, which "
        "slightly flatters the older half of any comparison. Premises are matched to companies by name within a postcode "
        "district, only when exactly one company fits."
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
    )


MONTHS = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _headlines(r: dict) -> list[dict]:
    out = []
    for k in ("food_drink", "craft", "creative", "tech"):
        m = r["momentum"][k]
        if m["verdict"] and m["change_pct"] is not None:
            word = "up" if m["change_pct"] >= 0 else "down"
            out.append(
                {
                    "kind": "momentum",
                    "sector": k,
                    "text": f"{m['label']}: {m['last6']:,} new companies in the last 6 months, {word} {abs(m['change_pct'])}% on the 6 months before"
                    + (
                        f" and {m['yoy_pct']:+}% on the year."
                        if m["yoy_pct"] is not None
                        else "."
                    ),
                }
            )
    d = [
        x
        for x in r["districts"]
        if x["change_pct"] is not None and x["formed_12m"] >= 40
    ]
    if d:
        up = max(d, key=lambda x: x["change_pct"])
        down = min(d, key=lambda x: x["change_pct"])
        out.append(
            {
                "kind": "district",
                "area": up["district"],
                "text": f"{up['district']} is picking up fastest: {up['last6']} new companies in 6 months against {up['prior6']} before.",
            }
        )
        out.append(
            {
                "kind": "district",
                "area": down["district"],
                "text": f"{down['district']} is cooling fastest: {down['last6']} new companies in 6 months against {down['prior6']} before.",
            }
        )
    sh = sorted(
        (x for x in r["districts"] if x["formed_12m"] >= 40),
        key=lambda x: -x["mix_shift_pts"],
    )[:3]
    if sh and sh[0]["mix_shift_pts"] > 0:
        out.append(
            {
                "kind": "mix",
                "area": sh[0]["district"],
                "text": "Craft, creative, wellness and tech companies make up a higher share of new registrations than of existing ones in "
                + ", ".join(x["district"] for x in sh)
                + f": {sh[0]['district']}'s new companies are {sh[0]['mix_new_pct']}% in those sectors against {sh[0]['mix_stock_pct']}% of what is there already.",
            }
        )
    s = r["seasonality"].get("food_drink")
    if s and s["peak"]:
        idx = {x["month"]: int(x["index"]) for x in s["months"]}
        out.append(
            {
                "kind": "season",
                "text": f"New food and drink companies are registered most often in {MONTHS[s['peak']]} "
                f"(index {idx[s['peak']]}, 100 = an average month) and least in {MONTHS[s['trough']]} ({idx[s['trough']]}).",
            }
        )
    wd = {k: v["prior_cohort_winding_pct"] for k, v in r["momentum"].items() if v["prior_cohort_winding_pct"] is not None}
    if "food_drink" in wd and len(wd) > 2:
        lo, hi = min(wd, key=wd.get), max(wd, key=wd.get)
        short = lambda k: r["momentum"][k]["label"].split(" (")[0].lower()  # noqa: E731
        out.append({"kind": "survival", "text": f"{wd['food_drink']}% of food and drink companies formed 12 to 24 months ago are already "
                                                f"being struck off or wound up, against {wd[lo]}% for {short(lo)} and {wd[hi]}% for {short(hi)}."})
    p = r["new_company_profile"]
    out.append(
        {
            "kind": "who",
            "text": f"Of {p['companies']:,} food and drink companies formed in the last year, {p['standalone_pct']}% stand alone and "
            f"{p['linked_pct']}% look linked to other food and drink companies (a shared name or address), a sign of groups and serial operators.",
        }
    )
    o = r["who_is_opening"]
    if o["matched"] >= 20:
        m = o["of_matched"]
        out.append(
            {
                "kind": "who",
                "text": f"Matching {o['matched']:,} new premises to their companies: {round(100 * m['new_entrant'] / o['matched'])}% belong to "
                f"companies under a year old (new entrants) and {round(100 * m['established'] / o['matched'])}% to companies over 3 years old (established operators adding a site or taking over).",
            }
        )
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--fsa", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("site/api/v1/operators.json"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    a = ap.parse_args()
    build(a.csv, a.fsa, a.out, a.as_of)
    print("wrote", a.out)
