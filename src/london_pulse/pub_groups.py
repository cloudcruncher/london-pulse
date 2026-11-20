"""Curated table of pub groups and brand families operating in London.

Each entry is a short factual statement with a public source URL. Facts were
checked against trade press, company pages and Wikipedia in November 2026;
ownership changes often, so re-check before relying on a row.

Matching is done on normalised text (lower-case, curly quotes straightened).
Regexes are applied case-insensitively. Generic pub names are never matched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

CATEGORIES = {  # order = display order
    "pe": {"label": "PE / investor-owned", "short": "PE"},
    "corp": {"label": "Corporate group", "short": "CORP"},
    "family": {"label": "Family brewer", "short": "FAMILY"},
    "indie": {"label": "Independent", "short": "INDIE"},
    "unknown": {"label": "No group link found", "short": "?"},
}


def _g(
    id,
    name,
    category,
    owner,
    model,
    note,
    src,
    operator=None,
    brand=None,
    licensee=None,
    name_re=None,
    wikidata=(),
):
    return {
        "id": id,
        "name": name,
        "category": category,
        "owner": owner,
        "model": model,
        "note": note,
        "src": src,
        "match": {
            "operator": operator,
            "brand": brand,
            "licensee": licensee,
            "name": name_re,
            "wikidata": list(wikidata),
        },
    }


_STONEGATE_BRANDS = (
    r"slug (&|and) lettuce|be at one|walkabout|yates|popworld|"
    r"pitcher (&|and) piano|craft union"
)

GROUPS = [
    _g(
        "stonegate",
        "Stonegate Group",
        "pe",
        "TDR Capital (private equity)",
        "mixed",
        "Owned by private equity firm TDR Capital; runs managed brands such as Slug & Lettuce, Be At One "
        "and Walkabout, and bought Ei Group and its leased pubs for £1.27bn in 2020.",
        "https://en.wikipedia.org/wiki/Stonegate_Pub_Company",
        operator=r"stonegate|enterprise inns?|ei publican|\bei group\b",
        brand=_STONEGATE_BRANDS,
        licensee=r"stonegate|enterprise inns?",
        name_re=r"^(slug (&|and) lettuce|be at one|walkabout|popworld|pitcher (&|and) piano)\b",
        wikidata=["Q7619176"],
    ),
    _g(
        "star-pubs",
        "Star Pubs & Bars (Heineken)",
        "corp",
        "Heineken (global brewer)",
        "tenanted",
        "Heineken UK's pub arm, which added 1,900 pubs bought from Punch in 2017; its pubs are run by "
        "licensees who buy their beer from Heineken.",
        "https://en.wikipedia.org/wiki/Star_Pubs_%26_Bars",
        operator=r"star pubs",
        brand=r"star pubs",
        licensee=r"star pubs",
        wikidata=["Q124504786"],
    ),
    _g(
        "greene-king",
        "Greene King",
        "corp",
        "CK Asset Holdings (Hong Kong property group)",
        "mixed",
        "Bury St Edmunds pub company bought by CK Asset in 2019; runs managed brands (Hungry Horse, "
        "Chef & Brewer, Farmhouse Inns, Flaming Grill, Metropolitan Pub Company) and ex-Spirit pubs such "
        "as Taylor Walker, plus tenanted pubs.",
        "https://en.wikipedia.org/wiki/Greene_King",
        operator=r"greene king|spirit pub|taylor walker|hungry horse|chef (&|and) brewer|metropolitan pub",
        brand=(
            r"greene king|hungry horse|chef (&|and) brewer|farmhouse inns|flaming grill|"
            r"metropolitan pub|taylor walker|wacky warehouse|belhaven"
        ),
        licensee=r"greene king|spirit pub",
        name_re=r"^(hungry horse|flaming grill)\b",
        wikidata=["Q5564162"],
    ),
    _g(
        "mitchells-butlers",
        "Mitchells & Butlers",
        "corp",
        "Listed company (Mitchells & Butlers plc)",
        "managed",
        "London-listed (FTSE 250) operator of managed pubs and restaurants including Nicholson's, All Bar One, "
        "O'Neill's, Ember Inns, Vintage Inns, Browns, Toby Carvery, Harvester, Miller & Carter, Castle, "
        "Sizzling Pubs and Stonehouse.",
        "https://en.wikipedia.org/wiki/Mitchells_%26_Butlers",
        operator=r"mitchells (&|and) butlers|vintage inns|sizzling pubs",
        brand=(
            r"nicholson'?s|ember inns|vintage inns|o'neill'?s|all bar one|^browns$|toby carvery|"
            r"harvester|sizzling pubs|stonehouse|miller (&|and) carter|^castle$|mitchells (&|and) butlers"
        ),
        licensee=r"mitchells (&|and) butlers",
        name_re=r"^(all bar one|o'neill'?s|nicholson'?s|toby carvery|harvester)\b",
        wikidata=["Q1939537"],
    ),
    _g(
        "wetherspoon",
        "J D Wetherspoon",
        "corp",
        "Listed company; founder Tim Martin is the largest shareholder",
        "managed",
        "London-listed managed pub chain including Lloyds No.1 bars; founder Tim Martin owned about 24% "
        "of the shares in 2023.",
        "https://en.wikipedia.org/wiki/Wetherspoons",
        operator=r"wetherspoon|weatherspoon",
        brand=r"wetherspoon|lloyds no\.? ?1",
        licensee=r"wetherspoon",
        name_re=r"wetherspoon|^lloyds no\.? ?1",
        wikidata=["Q6109362"],
    ),
    _g(
        "punch",
        "Punch Pubs & Co",
        "pe",
        "Fortress Investment Group (investment manager)",
        "tenanted",
        "Leases about 1,300 pubs to self-employed tenants; Fortress bought 100% of it from Patron "
        "Capital Partners.",
        "https://www.punchpubs.com/fortress-acquires-punch-pubs-co-from-patron-capital",
        operator=r"punch pubs|punch taverns",
        brand=r"punch pubs|punch taverns",
        licensee=r"punch pubs|punch taverns",
        wikidata=["Q2118336"],
    ),
    _g(
        "admiral",
        "Admiral Taverns",
        "pe",
        "Proprium Capital Partners (private equity)",
        "tenanted",
        "Tenanted pub company bought by US investment fund Proprium Capital Partners in 2017; bought 37 "
        "tenanted pubs from Fuller's in 2024.",
        "https://en.wikipedia.org/wiki/Admiral_Taverns",
        operator=r"admiral taverns",
        brand=r"admiral taverns",
        licensee=r"admiral taverns",
        wikidata=["Q124759102"],
    ),
    _g(
        "marstons",
        "Marston's",
        "corp",
        "Listed company (Marston's PLC)",
        "tenanted",
        "London-listed pub company with over 1,350 pubs; sold its 40% stake in the Carlsberg Marston's "
        "brewing business to Carlsberg in 2024.",
        "https://en.wikipedia.org/wiki/Marston%27s_plc",
        operator=r"marston'?s",
        brand=r"marston'?s",
        licensee=r"marston'?s",
        wikidata=["Q6773982"],
    ),
    _g(
        "youngs",
        "Young's (incl. Geronimo, Cubitt House)",
        "corp",
        "Young & Co's Brewery PLC (AIM-listed London pub company)",
        "mixed",
        "Stock-market-listed London pub company (including Geronimo and Ram Pub Company pubs) that agreed "
        "to buy the Cubitt House pubs in April 2026.",
        "https://www.morningadvertiser.co.uk/Article/2026/04/08/youngs-to-acquire-cubitt-house-pubs-in-london-expansion-push",
        operator=r"\byoung'?s\b|young (&|and) co|geronimo|ram pub|cubitt house",
        brand=r"young'?s|geronimo|cubitt house",
        licensee=r"young (&|and) co",
        name_re=None,
    ),
    _g(
        "fullers",
        "Fuller's (Fuller, Smith & Turner)",
        "corp",
        "Fuller, Smith & Turner P.L.C. (London-listed company)",
        "mixed",
        "London-listed pub operator; sold its brewing arm to Asahi for £250m in 2019, so Fuller's beer "
        "is Asahi-owned but the pubs are not.",
        "https://en.wikipedia.org/wiki/Fuller,_Smith_%26_Turner",
        operator=r"fuller'?s|fuller,? smith",
        brand=r"fuller'?s",
        licensee=r"fuller,? smith",
        wikidata=["Q90901784"],
    ),
    _g(
        "shepherd-neame",
        "Shepherd Neame",
        "family",
        "Shepherd Neame Ltd (family-controlled brewer)",
        "mixed",
        "Faversham brewer whose ownership has passed through five families; a private company whose "
        "shares are quoted on the AQSE exchange.",
        "https://en.wikipedia.org/wiki/Shepherd_Neame",
        operator=r"shepherd neame",
        brand=r"shepherd neame",
        licensee=r"shepherd neame",
    ),
    _g(
        "samuel-smith",
        "Samuel Smith Old Brewery",
        "family",
        "Smith family (private brewer)",
        "managed",
        "Tadcaster brewer with more than 20 central London pubs; its pubs only stock its own products.",
        "https://en.wikipedia.org/wiki/Samuel_Smith_Old_Brewery",
        operator=r"samuel smith|sam smith",
        brand=r"samuel smith|sam smith",
        licensee=r"samuel smith (old brewery|southern)|^samuel smith$",
    ),
    _g(
        "hall-woodhouse",
        "Hall & Woodhouse (Badger)",
        "family",
        "Hall & Woodhouse (Dorset family brewer)",
        "mixed",
        "Dorset family brewer behind Badger beers, with pubs in the south of England.",
        "https://www.hall-woodhouse.co.uk/",
        operator=r"hall (&|and) woodhouse",
        brand=r"hall (&|and) woodhouse|^badger$",
        licensee=r"hall (&|and) woodhouse",
    ),
    _g(
        "mcmullen",
        "McMullen & Sons",
        "family",
        "McMullen & Sons (Hertford family brewer)",
        "mixed",
        "Hertford brewer run on traditional family-business lines, with almost 100 pubs across "
        "Hertfordshire and London.",
        "https://www.mcmullens.co.uk/about-us/macs-history/",
        operator=r"mcmullen",
        brand=r"mcmullen",
        licensee=r"mcmullen (&|and) sons",
    ),
    _g(
        "amber-taverns",
        "Amber Taverns",
        "pe",
        "Epiris (private equity)",
        "mixed",
        "Community pub group whose 178-pub estate private equity firm Epiris took on in October 2024.",
        "https://www.restaurantonline.co.uk/Article/2024/10/07/big-table-restaurant-group-owner-acquires-wet-led-pubco-amber-taverns/",
        operator=r"amber taverns",
        brand=r"amber taverns",
        licensee=r"amber taverns",
    ),
    _g(
        "brewdog",
        "BrewDog",
        "corp",
        "Tilray Brands (reported, 2026)",
        "managed",
        "Scottish craft brewer and bar chain; trade press in 2026 names Tilray Brands as its new owner.",
        "https://www.morningadvertiser.co.uk/Article/2026/07/30/brewdog-adds-511m-to-tilrays-q4-revenue/",
        operator=r"brewdog",
        brand=r"brewdog",
        name_re=r"^brewdog\b",
    ),
    _g(
        "camden-town-brewery",
        "Camden Town Brewery",
        "corp",
        "AB InBev (global brewer)",
        "managed",
        "London brewery founded in 2010 and owned by Anheuser-Busch InBev since 2015.",
        "https://en.wikipedia.org/wiki/Camden_Town_Brewery",
        operator=r"camden town brew",
        brand=r"camden town brew",
    ),
    _g(
        "beavertown",
        "Beavertown Brewery (Heineken)",
        "corp",
        "Heineken (global brewer)",
        "managed",
        "Tottenham brewery founded in 2011 and acquired by Heineken in 2022.",
        "https://en.wikipedia.org/wiki/Beavertown_Brewery",
        operator=r"beavertown",
        brand=r"^beavertown",
    ),
    _g(
        "meantime",
        "Meantime (Asahi)",
        "corp",
        "Asahi (global brewer)",
        "managed",
        "Greenwich brewery owned by Asahi Breweries since 2016.",
        "https://en.wikipedia.org/wiki/Meantime_Brewing_Company",
        operator=r"meantime",
        brand=r"^meantime",
    ),
    _g(
        "urban-pubs-bars",
        "Urban Pubs & Bars",
        "indie",
        "Independent London group (founded 2014)",
        "managed",
        "Described by trade press as London's largest independent pub group, with about 65 sites; founded "
        "in 2014 by Nick Pring and Malcolm Heap.",
        "https://www.morningadvertiser.co.uk/Article/2026/01/30/urban-pubs-bars-multi-million-pound-investment-into-london-pubs",
        operator=r"urban pubs (&|and) bars",
        brand=r"urban pubs (&|and) bars",
    ),
    _g(
        "remarkable-pubs",
        "Remarkable Pubs",
        "indie",
        "Family-run London group",
        "managed",
        "Family business of about 17 pubs, mainly in east and north London.",
        "https://londondrinker.camra.org.uk/wordpress/index.php/2025/11/26/a-remarkable-story/",
        operator=r"remarkable pubs",
        brand=r"remarkable pubs",
        licensee=r"remarkable pubs",
    ),
]

_FLAGS = re.IGNORECASE
_COMPILED = {
    g["id"]: {
        k: (re.compile(v, _FLAGS) if isinstance(v, str) else None)
        for k, v in g["match"].items()
        if k != "wikidata"
    }
    for g in GROUPS
}
_WIKIDATA = {g["id"]: set(g["match"]["wikidata"]) for g in GROUPS}

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _norm(text):
    if not text:
        return ""
    return " ".join(str(text).translate(_QUOTES).lower().split())


def match_group(
    operator=None, brand=None, licensee=None, name=None, wikidata: Iterable[str] = ()
):
    """Return [(group_id, evidence_code)] for every matching group and signal."""
    fields = (
        ("operator", _norm(operator), "osm:operator"),
        ("brand", _norm(brand), "osm:brand"),
        ("licensee", _norm(licensee), "camden:licensee"),
        ("name", _norm(name), "name"),
    )
    wd = {w.strip().upper() for w in (wikidata or ()) if w}
    out = []
    for g in GROUPS:
        gid = g["id"]
        for key, text, code in fields:
            rx = _COMPILED[gid].get(key)
            if text and rx and rx.search(text):
                out.append((gid, code))
        if wd & _WIKIDATA[gid]:
            out.append((gid, "osm:wikidata"))
    return out
