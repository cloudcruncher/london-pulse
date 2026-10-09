"""What homes sell for and what they rent for: typical sale price per postcode sector and private rent per borough.

Written to site/api/v1/prices.json:
  sectors   per postcode sector (outward code + space + first inward digit, e.g. "E8 1"): median sale price of all homes,
            flats and houses, each with the number of sales behind it. A median is null below MIN_SALES sales.
  districts the same for the postcode district (outward code, e.g. "E8"), the fallback when a sector has too few sales.
  rents     ONS Price Index of Private Rents: the latest monthly average private rent per London borough, by bedrooms.
Prices are HM Land Registry Price Paid Data (individual sales, standard market sales only, deleted records dropped), the
12 months to the latest sale month in the data. Medians are of individual sale prices, never of area averages.

Usage: python -m london_pulse.prices
"""

import json
import re
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import duckdb

from .http import UA, download

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "site" / "api" / "v1"
WORK = ROOT / "work" / "reference"
PPD_URL = "https://price-paid-data.publicdata.landregistry.gov.uk/pp-{year}.csv"
PIPR_PAGE = "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/priceindexofprivaterentsukmonthlypricestatistics"
PIPR_FALLBACK = (
    "/file?uri=/economy/inflationandpriceindices/datasets/priceindexofprivaterentsukmonthlypricestatistics/"
    "16september2026/priceindexofprivaterentsukmonthlypricestatistics.xlsx"
)
PPD_APP = "https://landregistry.data.gov.uk/app/ppd/"
MIN_SALES = 10
MAX_AGE_DAYS = 7  # re-download a cached yearly file older than this: Land Registry amends it every month
PIPR_RANGE = "A3:AN200000"  # read_xlsx truncates silently without an explicit range; Table 1 is ~70k rows
PPD_COLUMNS = [
    "id",
    "price",
    "date",
    "postcode",
    "ptype",
    "newbuild",
    "tenure",
    "paon",
    "saon",
    "street",
    "locality",
    "town",
    "district",
    "county",
    "category",
    "status",
]
HOUSE_TYPES = ("D", "S", "T")
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
LR_ATTRIBUTION = (
    "Contains HM Land Registry data (c) Crown copyright and database right {year}. "
    "This data is licensed under the Open Government Licence v3.0."
)
CAVEATS = [
    "Sale prices are for homes that sold, not all homes; a few sales can swing a small area.",
    "Land Registry records are added with a lag, so the latest month holds fewer sales than earlier ones.",
    "Standard market sales only (Land Registry category A). Category B (repossessions, buy-to-lets where identified by a mortgage, "
    "transfers to companies and other non-private buyers) is left out.",
    "A sector's typical price mostly reflects its mix of flats and houses and of leasehold and freehold homes.",
    "Rents are the ONS Price Index of Private Rents: official statistics in development; local-authority estimates are best read as trends. "
    "A modelled borough average, not a specific street. Private rent only: shared and social rent are excluded.",
]

RENT_CAVEAT = 4


class CoverageError(RuntimeError):
    """The downloaded yearly files do not reach back to the start of the 12 month window."""

    def __init__(self, msg: str, year: int):
        super().__init__(msg)
        self.year = year


def month_back(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    i = y * 12 + (m - 1) - n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def fresh(path: Path) -> bool:
    return (
        path.exists()
        and path.stat().st_size > 0
        and time.time() - path.stat().st_mtime < MAX_AGE_DAYS * 86400
    )


def fetch_ppd(work: Path, today: date | None = None, years: tuple[int, ...] | None = None) -> list[Path]:
    """Yearly Price Paid files for the previous and current calendar year (the current one may not exist in January)."""
    today = today or date.today()
    work.mkdir(parents=True, exist_ok=True)
    files = []
    for year in years or (today.year - 1, today.year):
        dest = work / f"pp-{year}.csv"
        if not fresh(dest):
            try:
                download(PPD_URL.format(year=year), dest)
            except RuntimeError:
                if year == today.year and files:
                    continue
                raise
        files.append(dest)
    return files


def aggregate_sales(files: list[Path], min_sales: int = MIN_SALES) -> dict:
    """Median sale prices per postcode sector and district for the 12 months to the latest sale month."""
    con = duckdb.connect()
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in PPD_COLUMNS)
    paths = ", ".join(f"'{p}'" for p in files)
    con.execute(f"""
        CREATE TABLE raw AS
        SELECT id, price::BIGINT AS price, substr(date, 1, 7) AS month, upper(trim(postcode)) AS postcode, ptype, status
        FROM read_csv([{paths}], header=false, columns={{{cols}}}, quote='"')
        WHERE county = 'GREATER LONDON' AND category = 'A'""")
    # a deletion removes the sale; a change replaces its earlier add
    con.execute("""
        CREATE TABLE sales AS
        SELECT price, month, postcode, ptype FROM raw
        WHERE id NOT IN (SELECT id FROM raw WHERE status = 'D')
        QUALIFY row_number() OVER (PARTITION BY id ORDER BY (status = 'C') DESC) = 1""")
    as_of = con.execute("SELECT max(month) FROM sales").fetchone()[0]
    if not as_of:
        raise RuntimeError("no Greater London sales found in the Price Paid files")
    start = month_back(as_of, 11)
    first = con.execute("SELECT min(month) FROM sales").fetchone()[0]
    if first > start:
        raise CoverageError(
            f"the Price Paid files start at {first} but the 12 month window starts at {start}; refusing to describe fewer months as 12",
            int(start[:4]),
        )
    con.execute(f"""
        CREATE TABLE win AS
        SELECT price, ptype,
               regexp_extract(postcode, '^([A-Z0-9]{{2,4}}) ([0-9])[A-Z]{{2}}$', 1) AS outward,
               regexp_extract(postcode, '^([A-Z0-9]{{2,4}}) ([0-9])[A-Z]{{2}}$', 2) AS d
        FROM sales WHERE month >= '{start}' AND regexp_matches(postcode, '^[A-Z0-9]{{2,4}} [0-9][A-Z]{{2}}$')""")

    def med(group: str, flat_house: bool):
        extra = (
            (
                ", count(*) FILTER (ptype = 'F'), median(price) FILTER (ptype = 'F'), "
                "count(*) FILTER (ptype IN ('D','S','T')), median(price) FILTER (ptype IN ('D','S','T'))"
            )
            if flat_house
            else ""
        )
        return con.execute(
            f"SELECT {group}, count(*), median(price){extra} FROM win GROUP BY ALL ORDER BY 1"
        ).fetchall()

    def val(m, n):
        return round(m) if n >= min_sales and m is not None else None

    sectors, districts = {}, {}
    for key, n, m, nf, mf, nh, mh in med("outward || ' ' || d", True):
        sectors[key] = [val(m, n), n, val(mf, nf), nf, val(mh, nh), nh]
    for key, n, m in med("outward", False):
        districts[key] = [val(m, n), n]
    return {
        "as_of": as_of,
        "window": {"from": start, "to": as_of},
        "sectors": sectors,
        "districts": districts,
        "n_sales": sum(v[1] for v in sectors.values()),
    }


def latest_pipr_url() -> str:
    """Newest edition listed on the ONS dataset page, so the monthly job needs no manual URL change."""
    href = PIPR_FALLBACK
    try:
        req = urllib.request.Request(PIPR_PAGE, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", "replace")
        best = None
        for m in re.finditer(
            r'href="(/file\?uri=[^"]*?/(\d{1,2})([a-z]+)(\d{4})/[^"]*?\.xlsx)"', html
        ):
            if m.group(3) in MONTHS:
                key = (int(m.group(4)), MONTHS.index(m.group(3)), int(m.group(2)))
                if best is None or key > best[0]:
                    best = (key, m.group(1))
        if best:
            href = best[1]
    except Exception as e:  # noqa: BLE001 - fall back to the known edition
        print(
            f"  warning: could not scrape the PIPR page ({e}); using the fallback edition"
        )
    return "https://www.ons.gov.uk" + href


def fetch_pipr(work: Path) -> Path:
    url = latest_pipr_url()
    edition = re.search(r"/(\d{1,2}[a-z]+\d{4})/", url)
    dest = work / f"pipr-{edition.group(1) if edition else 'latest'}.xlsx"
    work.mkdir(parents=True, exist_ok=True)
    if not (dest.exists() and dest.stat().st_size > 0):
        download(url, dest)
    return dest


def parse_pipr(xlsx: Path) -> dict:
    """Latest month's private rent per London borough (area codes E09000001 to E09000033), by bedrooms."""
    con = duckdb.connect()
    con.execute("INSTALL excel; LOAD excel")
    con.execute(
        f"CREATE TABLE t AS SELECT * FROM read_xlsx('{xlsx}', sheet='Table 1', range='{PIPR_RANGE}', header=true, all_varchar=true)"
    )
    n = con.execute("SELECT count(*) FROM t WHERE \"Area code\" IS NOT NULL").fetchone()[0]
    if n >= 190_000:
        raise RuntimeError("PIPR sheet is longer than the read range; widen PIPR_RANGE")
    num = lambda c: f'try_cast("{c}" AS DOUBLE)'
    rows = con.execute(f"""
        SELECT {num("Time period")} AS serial, "Area code", "Area name", {num("Rental price")}, {num("Rental price one bed")},
               {num("Rental price two bed")}, {num("Rental price three bed")}, {num("Annual change")}
        FROM t WHERE "Area code" LIKE 'E09%'""").fetchall()
    return rents_from_rows(rows)


def rents_from_rows(rows) -> dict:
    """rows: (excel serial date, code, name, all, one, two, three, annual change %) for London boroughs; newest month wins."""
    codes = {f"E090000{i:02d}" for i in range(1, 34)}
    rows = [r for r in rows if r[0] is not None and r[1] in codes]
    if not rows:
        raise RuntimeError("no London boroughs in the PIPR sheet")
    latest = max(r[0] for r in rows)
    d = date(1899, 12, 30) + timedelta(days=int(latest))
    money = lambda x: None if x is None else round(x)
    boroughs = {}
    for ser, code, name, a, one, two, three, chg in sorted(rows, key=lambda r: r[1]):
        if ser != latest:
            continue
        boroughs[code] = {
            "name": name,
            "all": money(a),
            "one_bed": money(one),
            "two_bed": money(two),
            "three_bed": money(three),
            "annual_change_pct": None if chg is None else round(chg, 1),
        }
    return {"as_of": f"{d.year:04d}-{d.month:02d}", "boroughs": boroughs}


def build(work: Path = WORK) -> dict:
    files = fetch_ppd(work)
    try:
        agg = aggregate_sales(files)
    except CoverageError as e:  # e.g. the latest data is older than last year: also fetch the year the window starts in
        agg = aggregate_sales(fetch_ppd(work, years=(e.year, *sorted({int(f.stem[3:]) for f in files}))))
    rents = parse_pipr(fetch_pipr(work))
    year = date.today().year
    sectors = agg["sectors"]
    if (
        len(json.dumps(sectors)) > 250_000
    ):  # keep prices.json small: sectors with a handful of sales add little
        sectors = {k: v for k, v in sectors.items() if v[1] >= 5}
    return {
        "schema_version": 1,
        "as_of": agg["as_of"],
        "generated": date.today().isoformat(),
        "source": "HM Land Registry Price Paid Data (sales) and ONS Price Index of Private Rents (rents)",
        "licence": LR_ATTRIBUTION.format(year=year)
        + " ONS data under the Open Government Licence v3.0.",
        "method": (
            f"Sales: the median of individual sale prices (not an average of averages) for Greater London standard market sales "
            f"(category A, deleted records dropped) in the 12 months {agg['window']['from']} to {agg['window']['to']}. "
            f"Postcode sector is the outward code plus the first digit of the inward code. A median is null below "
            f"{MIN_SALES} sales. Flats are type F; houses are detached, semi-detached and terraced. Rents: the latest month of "
            "the ONS Price Index of Private Rents for each London borough."
        ),
        "caveats": CAVEATS,
        "window": agg["window"],
        "min_sales": MIN_SALES,
        "columns": [
            "median_all",
            "n_all",
            "median_flat",
            "n_flat",
            "median_house",
            "n_house",
        ],
        "sectors": sectors,
        "districts": agg["districts"],
        "proof": {
            "ppd": PPD_APP,
            "note": "Search the Land Registry price paid data for a postcode to see the individual sales behind a median.",
        },
        "rents": {
            "as_of": rents["as_of"],
            "source": "ONS Price Index of Private Rents, UK: monthly price statistics",
            "url": PIPR_PAGE,
            "licence": "Open Government Licence v3.0",
            "caveat": CAVEATS[RENT_CAVEAT],
            "boroughs": rents["boroughs"],
        },
        "sources": [
            {
                "id": "lr_ppd",
                "name": "HM Land Registry Price Paid Data",
                "publisher": "HM Land Registry",
                "url": "https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads",
                "licence": LR_ATTRIBUTION.format(year=year),
                "vintage": f"Sales to {agg['as_of']}",
                "caveat": CAVEATS[0],
            },
            {
                "id": "ons_pipr",
                "name": "Price Index of Private Rents, UK: monthly price statistics",
                "publisher": "Office for National Statistics",
                "url": PIPR_PAGE,
                "licence": "Open Government Licence v3.0",
                "vintage": f"Rents for {rents['as_of']}",
                "caveat": CAVEATS[RENT_CAVEAT],
            },
        ],
    }


def write(api: Path = API, work: Path = WORK) -> Path:
    out = build(work)  # any failure raises before the file is touched
    p = api / "prices.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(p)
    return p


if __name__ == "__main__":
    p = write()
    print(f"prices -> {p} ({p.stat().st_size // 1024} KB)")
