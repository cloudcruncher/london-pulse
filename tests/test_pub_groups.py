import re

import pytest

from london_pulse.pub_groups import CATEGORIES, GROUPS, match_group

FIELDS = {"id", "name", "category", "owner", "model", "note", "src", "match"}


def test_schema():
    ids = [g["id"] for g in GROUPS]
    assert len(ids) == len(set(ids))
    for g in GROUPS:
        assert FIELDS <= set(g), g["id"]
        assert g["category"] in CATEGORIES and g["category"] != "unknown"
        assert g["model"] in {"managed", "tenanted", "mixed"}
        assert g["src"].startswith("https://")
        assert g["owner"] and g["note"].strip()
        m = g["match"]
        assert set(m) == {"operator", "brand", "licensee", "name", "wikidata", "domain", "brewery"}
        for d in m["domain"]:
            assert re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", d), d
        for k in ("operator", "brand", "licensee", "name", "brewery"):
            if m[k]:
                re.compile(m[k], re.IGNORECASE)
        assert any(
            m[k] for k in ("operator", "brand", "licensee", "name", "wikidata")
        ), g["id"]
        for q in m["wikidata"]:
            assert re.fullmatch(r"Q\d+", q)


def ids(**kw):
    return {g for g, _ in match_group(**kw)}


@pytest.mark.parametrize(
    "brand,gid",
    [
        ("Greene King", "greene-king"),
        ("Fuller's", "fullers"),
        ("Wetherspoon", "wetherspoon"),
        ("Young's", "youngs"),
        ("Nicholson's", "mitchells-butlers"),
        ("Ember Inns", "mitchells-butlers"),
        ("Shepherd Neame", "shepherd-neame"),
        ("Samuel Smith", "samuel-smith"),
        ("Vintage Inns", "mitchells-butlers"),
        ("O'Neill's", "mitchells-butlers"),
        ("O’Neill’s", "mitchells-butlers"),
        ("Hall & Woodhouse", "hall-woodhouse"),
        ("BrewDog", "brewdog"),
        ("Chef & Brewer", "greene-king"),
        ("Craft Union", "stonegate"),
        ("Hungry Horse", "greene-king"),
        ("Sizzling Pubs", "mitchells-butlers"),
        ("Slug & Lettuce", "stonegate"),
        ("Stonehouse Pizza & Carvery", "mitchells-butlers"),
        ("Castle", "mitchells-butlers"),
        ("Toby Carvery", "mitchells-butlers"),
        ("Metropolitan Pub Company", "greene-king"),
        ("Pitcher & Piano", "stonegate"),
        ("Camden Town Brewery", "camden-town-brewery"),
        ("Walkabout", "stonegate"),
        ("Marston's", "marstons"),
    ],
)
def test_brands(brand, gid):
    assert gid in ids(brand=brand)
    assert (gid, "osm:brand") in match_group(brand=brand)


@pytest.mark.parametrize(
    "op,gid",
    [
        ("Stonegate", "stonegate"),
        ("Fullers", "fullers"),
        ("Fuller's", "fullers"),
        ("Youngs", "youngs"),
        ("Young & Co", "youngs"),
        ("Greene King", "greene-king"),
        ("Mitchells & Butlers plc", "mitchells-butlers"),
        ("Taylor Walker", "greene-king"),
        ("Spirit Pub Company", "greene-king"),
        ("JD Weatherspoon", "wetherspoon"),
        ("Weatherspoons", "wetherspoon"),
        ("McMullens", "mcmullen"),
        ("McMullen", "mcmullen"),
        ("Geronimo Inns", "youngs"),
        ("Ram Pub Company", "youngs"),
        ("Vintage Inns", "mitchells-butlers"),
        ("Camden Town Brewery", "camden-town-brewery"),
        ("Urban Pubs & Bars", "urban-pubs-bars"),
        ("Remarkable Pubs", "remarkable-pubs"),
        ("Samuel Smith", "samuel-smith"),
        ("Shepherd Neame", "shepherd-neame"),
        ("Sizzling Pubs", "mitchells-butlers"),
    ],
)
def test_operators(op, gid):
    assert (gid, "osm:operator") in match_group(operator=op)


@pytest.mark.parametrize(
    "lic,gid",
    [
        ("Mitchells & Butlers Leisure Retail Ltd", "mitchells-butlers"),
        ("Mitchells and Butlers Leisure Retail Ltd", "mitchells-butlers"),
        ("Spirit Pub Company (Services) Limited", "greene-king"),
        ("Fuller, Smith & Turner Plc", "fullers"),
        ("Star Pubs Trading Limited", "star-pubs"),
        ("Young & Co's Brewery PLC", "youngs"),
        ("Samuel Smith Southern", "samuel-smith"),
        ("Greene King Retailing", "greene-king"),
        ("Stonegate Pub Company", "stonegate"),
        ("J D Wetherspoon plc", "wetherspoon"),
    ],
)
def test_licensees(lic, gid):
    assert (gid, "camden:licensee") in match_group(licensee=lic)


def test_names_wikidata():
    assert ("stonegate", "name") in match_group(name="Slug and Lettuce Clapham")
    assert ("mitchells-butlers", "name") in match_group(name="All Bar One Soho")
    assert ("wetherspoon", "name") in match_group(name="Lloyds No.1 Bar")
    assert ("wetherspoon", "osm:wikidata") in match_group(wikidata=["Q6109362"])
    assert ("greene-king", "osm:wikidata") in match_group(wikidata=("Q5564162",))


@pytest.mark.parametrize(
    "text",
    [
        "Free House",
        "The Red Lion",
        "The Crown",
        "The Royal Oak",
        "Romantic Pub",
        "Browns Hill Tavern",
        "Badger Arms",
        "",
    ],
)
def test_no_match(text):
    assert match_group(operator=text, brand=text, licensee=text, name=text) == []
    assert match_group() == []


def test_brewery_rules_only_for_family_and_regional_brewers():
    # family/regional brewers whose beer is (almost) only sold in their own pubs; never global lager brands, and not
    # Young's or Fuller's, whose beers are widely sold in the free trade
    with_brewery = {g["id"] for g in GROUPS if g["match"]["brewery"]}
    assert with_brewery == {"shepherd-neame", "samuel-smith", "mcmullen"}


def test_no_group_claims_a_generic_platform_domain():
    from london_pulse.pubs import GENERIC_DOMAINS

    for g in GROUPS:
        for d in g["match"]["domain"]:
            assert not GENERIC_DOMAINS.search(d), (g["id"], d)


def test_new_groups_match():
    assert ("craft-beer-co", "osm:brand") in match_group(brand="The Craft Beer Co.")
    assert ("craft-beer-co", "name") in match_group(name="Craft Beer Co. Brixton")
    assert ("bohem", "osm:operator") in match_group(operator="Bohem Brewery")
    assert ("stonegate", "osm:operator") in match_group(operator="Heritage Pubs")
    assert ("stonegate", "osm:brand") in match_group(brand="Social Pub & Kitchen")
    assert ("greene-king", "osm:wikidata") in match_group(wikidata=["Q7577905"])  # Spirit Pub Company
    assert not [m for m in match_group(name="Crafty Beer Company House") if m[0] == "craft-beer-co"]
