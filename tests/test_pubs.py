"""Pubs ownership build on tiny fixtures (no network): linking, London filter, classification, indie rule, cask parsing."""
import json
import re

from london_pulse import pubs as P

CATS = {"corp": {"label": "Corporate group", "short": "CORP"}, "indie": {"label": "Independent", "short": "INDIE"},
        "unknown": {"label": "No group link found", "short": "?"}}
GROUPS = [
    {"id": "bigco", "name": "BigCo", "category": "corp", "owner": "BigCo plc", "model": "managed", "note": "n", "src": "https://x.test"},
    {"id": "rival", "name": "Rival", "category": "corp", "owner": "Rival plc", "model": "tenanted", "note": "n", "src": "https://y.test"},
    {"id": "microchain", "name": "Micro", "category": "indie", "owner": "Micro Ltd", "model": "managed", "note": "n", "src": "https://z.test"},
]
RULES = [("bigco", r"bigco|big co", "Q1"), ("rival", r"rival", "Q2"), ("microchain", r"micro pubs", None)]


def match_group(operator, brand, licensee, name, wikidata):
    out = []
    for gid, rx, qid in RULES:
        for text, ev in ((operator, "osm:operator"), (brand, "osm:brand"), (licensee, "camden:licensee")):
            if text and re.search(rx, P.norm(text)):
                out.append((gid, ev))
        if qid and qid in wikidata:
            out.append((gid, "osm:wikidata"))
    return out


def fsa(i, name, lon, lat, typ="Pub/bar/nightclub", borough="Camden", pc="NW1 1AA"):
    return {"fhrsid": i, "name": name, "business_type": typ, "address": "", "postcode": pc, "rating": "5", "rating_date": None,
            "authority": borough, "lon": lon, "lat": lat}


def el(i, name, lon, lat, **tags):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": {"amenity": "pub", "name": name, **tags}}


def run(elements, fsa_rows, camden=None, overrides=None):
    doc, pubs = P.build({"elements": elements}, fsa_rows, camden, GROUPS, match_group, overrides, "2026-11-20", CATS)
    f = doc["fields"]
    return doc, {r[f.index("osm")]: dict(zip(f, r)) for r in doc["pubs"]}, {p["osm"]: p for p in pubs}


def test_cask_parsing():
    assert [P.cask_flag(v) for v in ("yes", "no", "4", "3 beers", "0", "Usual Wetherspoons variety", "", None, "unknown")] == \
        [1, 0, 1, 1, 0, 1, None, None, None]
    assert P.craft_flag({"craft_beer": "yes"}) == 1 and P.craft_flag({"microbrewery": "yes"}) == 1 and P.craft_flag({}) is None


def test_names_match_normalises_and_rejects_lookalikes():
    assert P.names_match("The Red Lion", "Red Lion Pub") and P.names_match("The King's Arms", "Kings Arms")
    assert not P.names_match("Prince of Wales", "Prince Albert")


def test_fhrs_id_link_then_nearby_name_link_and_london_filter():
    rows = [fsa(1, "The Crown", -0.1, 51.5), fsa(2, "Red Lion", -0.12, 51.51, borough="Islington"),
            fsa(3, "Shop", -0.2, 51.52, typ="Retailers - other", borough="Barnet")]
    els = [el(10, "Crown & Anchor", -0.1, 51.5, **{"fhrs:id": "1"}),       # linked by id despite different name
           el(11, "Red Lion", -0.12003, 51.51003),                          # ~4 m away, name matches
           el(12, "Far Away Arms", -0.2, 51.52),                            # only a shop within 400 m: kept, not on FSA, Barnet
           el(13, "Nowhere", 0.3, 51.3)]                                    # no FSA premises near: outside London
    doc, by, _ = run(els, rows)
    assert set(by) == {"n10", "n11", "n12"}
    assert by["n10"]["fhrsid"] == 1 and by["n11"]["fhrsid"] == 2 and by["n12"]["fhrsid"] is None
    assert by["n11"]["borough"] == "Islington" and by["n12"]["borough"] == "Barnet"
    assert doc["counts"]["total"] == 3 and doc["counts"]["on_fsa"] == 2


def test_fsa_record_linked_once_and_wrong_name_not_linked():
    rows = [fsa(1, "The Crown", -0.1, 51.5)]
    els = [el(10, "The Crown", -0.1, 51.5), el(11, "The Crown", -0.10001, 51.50001), el(12, "Zebra Bar", -0.1, 51.50002)]
    _, by, _ = run(els, rows)
    assert sorted(v["fhrsid"] for v in by.values() if v["fhrsid"]) == [1]
    assert by["n12"]["fhrsid"] is None


def test_group_confidence_and_evidence():
    rows = [fsa(1, "A", -0.1, 51.5)]
    els = [el(1, "Pub One", -0.1, 51.5, operator="BigCo Pubs Ltd", brand="BigCo"),
           el(2, "Pub Two", -0.1, 51.5002, brand="Rival Taverns"),
           el(3, "Pub Three", -0.1, 51.5004, **{"brand:wikidata": "Q1"})]
    doc, by, _ = run(els, rows)
    assert (by["n1"]["group"], by["n1"]["confidence"], by["n1"]["evidence"]) == ("bigco", "likely", ["osm:operator", "osm:brand"])
    assert (by["n2"]["group"], by["n2"]["category"], by["n2"]["confidence"]) == ("rival", "corp", "likely")
    assert by["n3"]["group"] == "bigco" and by["n3"]["evidence"] == ["osm:wikidata"]
    assert doc["counts"]["by_group"] == {"bigco": 2, "rival": 1}


def test_conflict_prefers_licensee_then_operator_then_brand():
    rows = [fsa(1, "Lamb", -0.1, 51.5, pc="NW1 2BB")]
    els = [el(1, "The Lamb", -0.1, 51.5, operator="Rival Taverns", brand="BigCo", **{"addr:postcode": "NW1 2BB"}),
           el(2, "The Flag", -0.1, 51.51, operator="Rival Taverns", brand="BigCo")]
    camden = [{"premises_name": "Lamb", "premises_address": "1 High St NW1 2BB", "licensee": "BigCo Retail Ltd",
               "licence_type": "Premises Licence", "status": "In Force"}]
    _, by, _ = run(els, rows + [fsa(2, "x", -0.1, 51.51)], camden)
    assert by["n1"]["group"] == "bigco" and "camden:licensee" in by["n1"]["evidence"]   # licensee beats operator
    assert by["n2"]["group"] == "rival"                                                 # operator beats brand


def test_camden_link_by_postcode_and_by_distance_needs_matching_name():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "The Plough", -0.1, 51.5, **{"addr:postcode": "NW1 9ZZ"}), el(2, "The Anchor", -0.1, 51.5002)]
    camden = [{"premises_name": "Plough", "premises_address": "5 Rd NW1 9ZZ", "licensee": "Rival Ltd"},
              {"premises_name": "Anchor Inn", "premises_address": "6 Rd NW3 1AA", "licensee": "Someone Ltd",
               "latitude": "51.50021", "longitude": "-0.1"},
              {"premises_name": "Plough", "premises_address": "7 Rd NW1 9ZZ", "licensee": ""}]
    _, _, raw = run(els, rows, camden)
    assert raw["n1"]["licensee"] == "Rival Ltd" and raw["n2"]["licensee"] == "Someone Ltd"


def test_indie_rule():
    rows = [fsa(1, "x", -0.1, 51.5), fsa(2, "y", -0.1, 51.52)]
    els = [el(1, "Solo", -0.1, 51.5, operator="Jane Smith"),                      # operator on 1 pub: indie
           el(2, "Free One", -0.1, 51.5002, operator="Free House"),               # free house counts regardless of count
           el(3, "Free Two", -0.1, 51.5004, operator="Free House"),
           el(4, "Free Three", -0.1, 51.5006, operator="Free House"),
           el(5, "Free Four", -0.1, 51.5008, operator="Free House"),
           *[el(10 + i, f"Chainy {i}", -0.1, 51.501 + i * 0.0002, operator="Mystery Pubs Ltd") for i in range(4)],  # 4 > 3: not indie
           el(20, "Plain", -0.1, 51.52),                                          # no signal
           el(21, "Micro One", -0.1, 51.5202, operator="Micro Pubs Ltd"),         # curated indie group
           el(22, "Wells Arms", -0.1, 51.5204, operator="Charles Wells")]         # single London pub of a big pub company
    _, by, _ = run(els, rows)
    assert (by["n1"]["category"], by["n1"]["confidence"], by["n1"]["group"]) == ("indie", "likely", None)
    assert all(by[f"n{i}"]["category"] == "indie" for i in (2, 3, 4, 5))
    assert all(by[f"n{10 + i}"]["category"] == "unknown" and by[f"n{10 + i}"]["confidence"] is None for i in range(4))
    assert by["n20"]["category"] == "unknown"
    assert (by["n21"]["group"], by["n21"]["category"]) == ("microchain", "indie")
    assert by["n22"]["category"] == "unknown"


def test_overrides_win_and_can_set_flags():
    rows = [fsa(7, "Lamb", -0.1, 51.5)]
    els = [el(1, "The Lamb", -0.1, 51.5, operator="BigCo", real_ale="no", **{"fhrs:id": "7"}),
           el(2, "The Flag", -0.1, 51.5002, brand="BigCo")]
    ov = [{"osm": "", "fhrsid": "7", "group": "", "category": "indie", "cask": "1", "craft": "", "source_url": "u", "note": ""},
          {"osm": "n2", "fhrsid": "", "group": "rival", "category": "", "cask": "", "craft": "1", "source_url": "u", "note": ""}]
    _, by, _ = run(els, rows, overrides=ov)
    assert (by["n1"]["group"], by["n1"]["category"], by["n1"]["confidence"], by["n1"]["evidence"]) == (None, "indie", "likely", ["override"])  # Independent is always likely
    assert by["n1"]["cask"] == 1
    assert (by["n2"]["group"], by["n2"]["category"], by["n2"]["craft"]) == ("rival", "corp", 1)


def test_output_schema_is_compact_and_serialisable():
    rows = [fsa(1, "Crown", -0.1234567, 51.5234567)]
    els = [el(1, "Crown", -0.1234567, 51.5234567, website="https://a.test", real_ale="yes", **{"addr:postcode": "nw11aa"})]
    doc, by, _ = run(els, rows)
    assert doc["fields"] == P.FIELDS and doc["schema_version"] == 1 and doc["as_of"] == "2026-11-20"
    row = doc["pubs"][0]
    assert len(row) == len(P.FIELDS) and row[1:3] == [-0.12346, 51.52346] and row[3] == "NW1 1AA"
    assert by["n1"]["cask"] == 1 and by["n1"]["website"] == "https://a.test"
    assert {"total", "by_category", "by_group", "cask", "on_fsa"} <= set(doc["counts"])
    assert all(set(g) == {"id", "name", "category", "owner", "model", "note", "src"} for g in doc["groups"])
    json.dumps(doc, separators=(",", ":"))


# ---- review fixes

def test_osm_only_agreement_is_likely_but_osm_plus_camden_is_confirmed():
    rows = [fsa(1, "Lamb", -0.1, 51.5, pc="NW1 2BB")]
    els = [el(1, "The Lamb", -0.1, 51.5, operator="BigCo", brand="BigCo", **{"addr:postcode": "NW1 2BB"}),
           el(2, "The Flag", -0.1, 51.5004, operator="BigCo", brand="BigCo")]
    camden = [{"premises_name": "Lamb", "premises_address": "1 High St NW1 2BB", "licensee": "BigCo Retail Ltd"}]
    _, by, _ = run(els, rows, camden)
    assert by["n2"]["evidence"] == ["osm:operator", "osm:brand"] and by["n2"]["confidence"] == "likely"
    assert by["n1"]["confidence"] == "confirmed"


def test_holding_company_shared_with_a_group_pub_is_not_indie():
    # "Bermondsey Ltd" is the licensee of one BigCo pub and of a lone unmatched pub: it must not read as independent
    rows = [fsa(1, "x", -0.1, 51.5), fsa(2, "y", -0.1, 51.51)]
    els = [el(1, "Rifleman", -0.1, 51.5, brand="BigCo", operator="Bermondsey Ltd"),
           el(2, "Lone Star", -0.1, 51.51, operator="Bermondsey Ltd")]
    _, by, _ = run(els, rows)
    assert by["n1"]["group"] == "bigco" and by["n2"]["category"] == "unknown"


def test_not_indie_names_and_students_unions_stay_unknown():
    rows = [fsa(1, "x", -0.1, 51.5)]
    ops = ["Brunning & Price", "Holiday Inn London", "SSP UK", "Heritage Pubs Ltd", "Company Secretary", "Freehold",
           "Students' Union UCL", "King's College SU"]
    els = [el(i, f"Pub {i}", -0.1, 51.5 + i * 0.0002, operator=o) for i, o in enumerate(ops, 1)]
    _, by, _ = run(els, rows)
    assert all(v["category"] == "unknown" for v in by.values()), {k: v["category"] for k, v in by.items()}


def test_real_group_regexes_do_not_match_surnames_but_match_enterprise_inns():
    from london_pulse.pub_groups import match_group as real

    assert ("stonegate", "osm:operator") in real("Enterprise Inns", "", "", "x", [])
    assert ("stonegate", "camden:licensee") in real("", "", "Enterprise Inn plc", "x", [])
    assert not [m for m in real("", "", "John McMullen", "x", []) if m[0] == "mcmullen"]
    assert ("mcmullen", "camden:licensee") in real("", "", "McMullen & Sons Ltd", "x", [])
    assert not [m for m in real("", "", "Mr Peter Young", "x", []) if m[0] == "youngs"]
    assert not [m for m in real("", "", "Mr Samuel Smith", "x", []) if m[0] == "samuel-smith"]
    assert ("youngs", "camden:licensee") in real("", "", "Young & Co's Brewery PLC", "x", [])
    assert not [m for m in real("Hollingyoungs", "", "", "x", []) if m[0] == "youngs"]
    assert ("greene-king", "osm:operator") in real("Metropolitan Pub Company", "", "", "x", [])


def test_invalid_overrides_are_skipped_and_unknown_category_has_no_confidence(capsys):
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "One", -0.1, 51.5, brand="BigCo"), el(2, "Two", -0.1, 51.5002), el(3, "Three", -0.1, 51.5004)]
    ov = [{"osm": "n1", "fhrsid": "", "group": "nope", "category": "", "cask": "1", "craft": "", "source_url": "", "note": ""},
          {"osm": "n2", "fhrsid": "", "group": "", "category": "bogus", "cask": "", "craft": "", "source_url": "", "note": ""},
          {"osm": "n3", "fhrsid": "", "group": "", "category": "unknown", "cask": "", "craft": "", "source_url": "", "note": ""}]
    _, by, _ = run(els, rows, overrides=ov)
    assert by["n1"]["group"] == "bigco" and by["n1"]["cask"] is None          # skipped: normal matching applies
    assert by["n2"]["evidence"] == [] and by["n2"]["category"] == "unknown"
    assert (by["n3"]["category"], by["n3"]["confidence"]) == ("unknown", None)
    assert "skipping override" in capsys.readouterr().err


def test_postcode_only_camden_match_needs_a_close_name():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "The Red Lion Hotel", -0.1, 51.5, **{"addr:postcode": "NW1 9ZZ"})]
    far = [{"premises_name": "Red Lion", "premises_address": "5 Rd NW1 9ZZ", "licensee": "Weak Ltd"}]          # Jaccard 0.67
    close = [{"premises_name": "The Red Lion Hotel", "premises_address": "5 Rd NW1 9ZZ", "licensee": "Good Ltd"}]
    assert run(els, rows, far)[2]["n1"]["licensee"] == ""
    assert run(els, rows, close)[2]["n1"]["licensee"] == "Good Ltd"


def test_unmatched_listing_hides_people():
    pubs = [{"operator": "", "licensee": "Mr John Evans", "group": None}, {"operator": "", "licensee": "Dive Bar Ltd", "group": None}]
    _, lics, hidden = P.unmatched(pubs)
    assert lics == [("Dive Bar Ltd", 1)] and hidden == 1


# ---- v2 coverage signals

GROUPS2 = [
    {**GROUPS[0], "match": {"domain": ["bigco.co.uk"], "brewery": None, "wikidata": ["Q1"]}},
    {**GROUPS[1], "match": {"domain": [], "brewery": r"rival'?s", "wikidata": ["Q2"]}},
    {**GROUPS[2], "match": {"domain": [], "brewery": None, "wikidata": ["Q3"]}},
]


def match_group2(operator, brand, licensee, name, wikidata):
    out = match_group(operator, brand, licensee, name, wikidata)
    if name and re.search(r"^bigco tap\b", P.norm(name)):
        out.append(("bigco", "name"))
    if name and re.search(r"micro pubs", P.norm(name)):
        out.append(("microchain", "name"))
    return out


def run2(elements, fsa_rows, camden=None, overrides=None, wd=None):
    # a shop next to every fixture pub keeps it inside the London filter without linking to it
    fsa_rows = fsa_rows + [fsa(9000 + i, "Corner Shop", e["lon"], e["lat"], typ="Retailers - other")
                           for i, e in enumerate(elements)]
    doc, _pubs = P.build({"elements": elements}, fsa_rows, camden, GROUPS2, match_group2, overrides, "2026-11-20", CATS,
                        wd=wd)
    f = doc["fields"]
    return doc, {r[f.index("osm")]: dict(zip(f, r)) for r in doc["pubs"]}


def test_site_domain_is_the_registrable_domain():
    assert P.site_domain("https://www.pubs.bigco.co.uk/the-crown?x=1") == "bigco.co.uk"
    assert P.site_domain("www.thecrown.com/") == "thecrown.com"
    assert P.site_domain("https://a.org.uk;https://b.com") == "a.org.uk"
    assert P.site_domain("") == "" and P.site_domain(None) == "" and P.site_domain("localhost") == ""


def test_brewery_values():
    assert P.brewery_values("Sam_Smith's;Various") == ["sam smith's", "various"]
    assert P.brewery_values(None) == []


def test_website_domain_links_to_a_group_including_subdomains():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "The Crown", -0.1, 51.5, website="https://www.bigco.co.uk/crown"),
           el(2, "The Bell", -0.1, 51.502, website="https://bell.bigco.co.uk"),
           el(3, "The Lamb", -0.1, 51.504, website="https://notbigco.co.uk")]  # look-alike domain: no link
    _, by = run2(els, rows)
    assert (by["n1"]["group"], by["n1"]["confidence"], by["n1"]["evidence"]) == ("bigco", "likely", ["osm:website"])
    assert by["n2"]["group"] == "bigco"
    assert by["n3"]["group"] is None


def test_brewery_tag_counts_only_as_a_single_family_brewer_value():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "A", -0.1, 51.5, brewery="Rival's"),               # tied house of a family brewer
           el(2, "B", -0.1, 51.502, brewery="Rival's;Guinness"),     # mixed: just beers sold
           el(3, "C", -0.1, 51.504, brewery="Guinness"),             # lager/stout brands never mean ownership
           el(4, "D", -0.1, 51.506, brewery="various")]              # 'various' is not an indie signal
    _, by = run2(els, rows)
    assert (by["n1"]["group"], by["n1"]["evidence"]) == ("rival", ["osm:brewery"])
    assert all(by[f"n{i}"]["category"] == "unknown" for i in (2, 3, 4))


def test_brewery_tag_is_never_sole_evidence_when_the_pubs_own_website_is_not_the_groups():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "Free House", -0.1, 51.5, brewery="Rival's", website="https://www.freehousepub.com"),  # sells their beer
           el(2, "Tied", -0.1, 51.502, brewery="Rival's", website="https://rivals.co.uk/tied"),
           el(3, "Tied Two", -0.1, 51.504, brewery="Rival's", operator="Rival Brewery")]
    groups = [GROUPS2[0], {**GROUPS2[1], "match": {**GROUPS2[1]["match"], "domain": ["rivals.co.uk"]}}, GROUPS2[2]]
    rows = rows + [fsa(9000 + i, "Corner Shop", e["lon"], e["lat"], typ="Retailers - other") for i, e in enumerate(els)]
    doc, _ = P.build({"elements": els}, rows, None, groups, match_group2, None, "2026-11-20", CATS)
    f = doc["fields"]
    by = {r[f.index("osm")]: dict(zip(f, r)) for r in doc["pubs"]}
    assert by["n1"]["category"] == "unknown"
    assert by["n2"]["group"] == "rival" and "osm:brewery" in by["n2"]["evidence"]
    assert by["n3"]["group"] == "rival"


def test_young_s_and_fuller_s_beer_tags_do_not_mean_ownership():
    from london_pulse.pub_groups import GROUPS as REAL

    for beer in ("Young's", "Fuller's", "Youngs", "Fullers"):
        p = {"tags": {"brewery": beer}, "domain": "", "fsa": None, "name": "x", "qids": []}
        assert P.extra_matches(p, REAL, lambda *a: [], None) == [], beer


def test_real_groups_ignore_guinness_and_heineken_brewery_tags():
    from london_pulse.pub_groups import GROUPS as REAL

    p = {"tags": {"brewery": "Heineken"}, "domain": "", "fsa": None, "name": "x", "qids": []}
    assert P.extra_matches(p, REAL, lambda *a: [], None) == []
    p["tags"]["brewery"] = "Guinness"
    assert P.extra_matches(p, REAL, lambda *a: [], None) == []
    p["tags"]["brewery"] = "Shepherd Neame"
    assert P.extra_matches(p, REAL, lambda *a: [], None) == [("shepherd-neame", "osm:brewery")]


def test_fsa_business_name_is_a_signal_in_the_osm_family():
    rows = [fsa(1, "BigCo Tap Room", -0.1, 51.5)]
    els = [el(1, "The Crown", -0.1, 51.5, **{"fhrs:id": "1", "operator": "BigCo"}),
           el(2, "The Bell", -0.1, 51.51)]
    rows.append(fsa(2, "BigCo Tap Bell", -0.1, 51.51))
    els[1]["tags"]["fhrs:id"] = "2"
    _, by = run2(els, rows)
    assert by["n1"]["group"] == "bigco" and set(by["n1"]["evidence"]) == {"osm:operator", "fsa:name"}
    assert by["n1"]["confidence"] == "likely"  # the FSA trading name is the same claim as OSM's: one family
    assert (by["n2"]["group"], by["n2"]["evidence"]) == ("bigco", ["fsa:name"])


def test_wikidata_owner_hops_nearest_wins_and_ambiguous_is_skipped():
    gq = {"bigco": {"Q1"}, "rival": {"Q2"}}
    edges = {"Q10": [["Q11", True]], "Q11": [["Q1", True]], "Q1": [["Q2", True]]}
    assert P.wikidata_owner_groups(["Q10"], edges, gq) == {"bigco": True}   # 2 hops, stops at the nearest group
    assert P.wikidata_owner_groups(["Q1"], edges, gq) == {"rival": True}    # hop 0 is the plain osm:wikidata match
    amb = "Q2118336"  # Punch Pubs item: ambiguous after the 2017 split
    assert P.wikidata_owner_groups(["Q20"], {"Q20": [[amb, True]], amb: [["Q1", True]]}, gq) == {}
    assert P.wikidata_owner_groups(["Q30"], {}, gq) == {}


def test_wikidata_path_is_weak_if_any_statement_on_it_is_weak():
    gq = {"bigco": {"Q1"}}
    assert P.wikidata_owner_groups(["Q10"], {"Q10": [["Q11", False]], "Q11": [["Q1", True]]}, gq) == {"bigco": False}
    assert P.wikidata_owner_groups(["Q10"], {"Q10": [["Q11", True]], "Q11": [["Q1", False]]}, gq) == {"bigco": False}
    # a strong path wins over a weak one at the same hop
    both = {"Q10": [["Q11", False], ["Q12", True]], "Q11": [["Q1", True]], "Q12": [["Q1", True]]}
    assert P.wikidata_owner_groups(["Q10"], both, gq) == {"bigco": True}


def wd(edges, labels=None):
    return {"edges": {k: [[o, st] for o, st in v] for k, v in edges.items()}, "labels": labels or {}}


def test_wikidata_owner_is_its_own_family_only_from_the_pubs_own_item():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "The Anchor", -0.1, 51.5, wikidata="Q10"),
           el(2, "The Bell", -0.1, 51.502, wikidata="Q11", operator="BigCo"),           # own item + OSM operator: two families
           el(3, "C", -0.1, 51.504, **{"brand:wikidata": "Q12", "brand": "BigCo"}),   # brand QID chain: still the OSM claim
           el(4, "D", -0.1, 51.506, **{"operator:wikidata": "Q12"})]
    w = wd({"Q10": [("Q1", True)], "Q11": [("Q1", True)], "Q12": [("Q1", True)]}, {"Q10": "Anchor", "Q11": "The Bell Inn"})
    _, by = run2(els, rows, wd=w)
    assert (by["n1"]["group"], by["n1"]["confidence"], by["n1"]["evidence"]) == ("bigco", "likely", ["wikidata:owner"])
    assert by["n2"]["confidence"] == "confirmed"
    assert by["n3"]["group"] == "bigco" and by["n3"]["confidence"] == "likely"
    assert "osm:wikidata-owner" in by["n3"]["evidence"] and "wikidata:owner" not in by["n3"]["evidence"]
    assert (by["n4"]["group"], by["n4"]["evidence"]) == ("bigco", ["osm:wikidata-owner"])


def test_weak_wikidata_statement_is_never_sole_evidence_nor_its_own_family():
    # e.g. a P127 whose only reference is a Pubs Galore ID (P7411), from the 2019 bulk import
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "The Anchor", -0.1, 51.5, wikidata="Q10"),
           el(2, "The Bell", -0.1, 51.502, wikidata="Q11", operator="BigCo"),
           el(3, "C", -0.1, 51.504, **{"brand:wikidata": "Q12"})]
    w = wd({"Q10": [("Q1", False)], "Q11": [("Q1", False)], "Q12": [("Q1", False)]}, {"Q10": "The Anchor", "Q11": "The Bell"})
    _, by = run2(els, rows, wd=w)
    assert (by["n1"]["group"], by["n1"]["category"]) == (None, "unknown")
    assert by["n2"]["group"] == "bigco" and by["n2"]["confidence"] == "likely"
    assert set(by["n2"]["evidence"]) == {"osm:operator", "wikidata:weak"}
    assert by["n3"]["category"] == "unknown"


def test_pub_item_for_a_renamed_pub_is_dropped():
    # an item labelled "Baxter's Court" (an old Wetherspoon) on a pub now called The DC Lion says nothing about it
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "The DC Lion", -0.1, 51.5, wikidata="Q10"), el(2, "Unlabelled", -0.1, 51.502, wikidata="Q11")]
    w = wd({"Q10": [("Q1", True)], "Q11": [("Q1", True)]}, {"Q10": "Baxter's Court"})
    _, by = run2(els, rows, wd=w)
    assert by["n1"]["category"] == "unknown" and by["n2"]["category"] == "unknown"


def _row(i, o, rp=None):
    b = {"i": {"value": f"http://www.wikidata.org/entity/{i}"}, "o": {"value": f"http://www.wikidata.org/entity/{o}"}}
    if rp:
        b["rp"] = {"value": f"http://www.wikidata.org/prop/reference/{rp}"}
    return b


def test_fetch_wikidata_marks_bulk_import_and_unreferenced_statements_weak(tmp_path, monkeypatch):
    queries = []

    def fake(q):
        queries.append(q)
        if "rdfs:label" in q:
            return [{"i": {"value": "http://www.wikidata.org/entity/Q10"}, "l": {"value": "The Anchor"}}] if "wd:Q10" in q else []
        if "wd:Q10" in q:
            return [_row("Q10", "Q1", "P7411"), _row("Q10", "Q1", "P813"),   # Pubs Galore ID + retrieved: weak
                    _row("Q10", "Q2"),                                         # no reference: weak
                    _row("Q10", "Q3", "P7411"), _row("Q10", "Q3", "P854")]     # a reference URL: strong
        return []

    monkeypatch.setattr(P, "_sparql", fake)
    out = P.fetch_wikidata(tmp_path, ["Q10"])
    assert out["edges"]["Q10"] == [["Q1", False], ["Q2", False], ["Q3", True]]
    assert out["labels"] == {"Q10": "The Anchor"}
    # ended (P582) and deprecated statements are filtered in the query itself
    q = next(x for x in queries if "wd:Q10" in x and "rdfs:label" not in x)
    assert "pq:P582" in q and "wikibase:DeprecatedRank" in q and "p:P127" in q and "ps:P127" in q


def test_fetch_wikidata_uses_fresh_cache_without_network_and_refetches_old_format(tmp_path, monkeypatch):
    cache = {"version": P.WIKIDATA_CACHE_VERSION, "edges": {"Q10": [["Q1", True]]}, "labels": {"Q10": "A"},
             "queried": ["Q10", "Q1"]}
    (tmp_path / P.WIKIDATA_CACHE).write_text(json.dumps(cache))

    def boom(q):
        raise AssertionError("network used")

    monkeypatch.setattr(P, "_sparql", boom)
    assert P.fetch_wikidata(tmp_path, ["Q10"]) == {"edges": {"Q10": [["Q1", True]]}, "labels": {"Q10": "A"}}
    (tmp_path / P.WIKIDATA_CACHE).write_text(json.dumps({"edges": {"Q10": ["Q1"]}, "queried": ["Q10", "Q1"]}))  # v1
    monkeypatch.setattr(P, "_sparql", lambda q: [])
    assert P.fetch_wikidata(tmp_path, ["Q10"]) == {"edges": {}, "labels": {}}


def test_small_shared_domain_is_indie_but_duplicates_and_generic_domains_are_not():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "Duo One", -0.1, 51.5, website="https://duopubs.co.uk/one"),
           el(2, "Duo Two", -0.1, 51.51, website="https://duopubs.co.uk/two"),           # ~1.1 km apart: a small group
           el(3, "Twin", -0.12, 51.5, website="https://twin.com"),
           el(4, "Twin Bar", -0.12, 51.5003, website="https://twin.com"),                 # same place, <150 m: duplicate
           el(5, "Face One", -0.13, 51.5, website="https://facebook.com/a"),
           el(6, "Face Two", -0.13, 51.51, website="https://facebook.com/b"),
           el(7, "Ed One", -0.14, 51.5, website="https://one.edan.io"),
           el(8, "Ed Two", -0.14, 51.51, website="https://two.edan.io"),
           el(9, "Link", -0.15, 51.5, website="https://linktr.ee/a"),
           el(10, "Link Two", -0.15, 51.51, website="https://linktr.ee/b")]
    _, by = run2(els, rows)
    assert (by["n1"]["category"], by["n1"]["confidence"]) == ("indie", "likely")
    assert by["n2"]["category"] == "indie"
    assert all(by[f"n{i}"]["category"] == "unknown" for i in range(3, 11))


def test_big_unmapped_domain_blocks_indie_even_with_an_operator_signal():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(i, f"Chain {i}", -0.1, 51.5 + i * 0.005, website=f"https://mysterychain.com/{i}",
              operator=f"Solo Owner {i}") for i in range(1, 5)]
    _, by = run2(els, rows)
    assert all(by[f"n{i}"]["category"] == "unknown" for i in range(1, 5))


def test_shared_domain_with_a_group_pub_or_not_indie_name_is_not_indie():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "G", -0.1, 51.5, website="https://mixed.com", operator="BigCo"),
           el(2, "H", -0.1, 51.51, website="https://mixed.com"),
           el(3, "B1", -0.12, 51.5, website="https://brunningandprice.co.uk/b1"),
           el(4, "B2", -0.12, 51.51, website="https://brunningandprice.co.uk/b2")]
    _, by = run2(els, rows)
    assert by["n1"]["group"] == "bigco" and by["n2"]["category"] == "unknown"
    assert by["n3"]["category"] == by["n4"]["category"] == "unknown"


def test_not_indie_covers_publove_and_whelans():
    assert P.NOT_INDIE.search("publove") and P.NOT_INDIE.search("whelan's pubs") and P.NOT_INDIE.search("brunningandprice.co.uk")


def test_rating_fields_are_emitted():
    r = fsa(1, "Crown", -0.1, 51.5)
    r["rating_date"] = "2025-03-04T00:00:00"
    _, by = run2([el(1, "Crown", -0.1, 51.5)], [r])
    assert P.FIELDS[-2:] == ["rating", "rating_date"]
    assert (by["n1"]["rating"], by["n1"]["rating_date"]) == ("5", "2025-03-04")


def test_independent_is_never_confirmed_even_with_two_families_or_an_override():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "Micro Bar", -0.1, 51.5, wikidata="Q30", operator="Micro Pubs Ltd"), el(2, "Solo", -0.1, 51.51)]
    ov = [{"osm": "n2", "fhrsid": "", "group": "", "category": "indie", "cask": "", "craft": "", "source_url": "u", "note": ""}]
    _, by = run2(els, rows, None, ov, wd=wd({"Q30": [("Q3", True)]}, {"Q30": "Micro Bar"}))
    assert set(by["n1"]["evidence"]) == {"osm:operator", "wikidata:owner"}  # two families, still only likely
    assert (by["n1"]["group"], by["n1"]["category"], by["n1"]["confidence"]) == ("microchain", "indie", "likely")
    assert (by["n2"]["category"], by["n2"]["confidence"], by["n2"]["evidence"]) == ("indie", "likely", ["override"])


def test_sample_fixture_never_has_a_confirmed_independent():
    from pathlib import Path

    doc = json.loads((Path(__file__).parent / "fixtures" / "pubs_sample.json").read_text())
    f = doc["fields"]
    rows = [dict(zip(f, r)) for r in doc["pubs"]]
    assert rows and not [r["name"] for r in rows if r["category"] == "indie" and r["confidence"] == "confirmed"]


def test_wikidata_only_owner_yields_to_a_current_operator_that_names_someone_else():
    rows = [fsa(1, "x", -0.1, 51.5)]
    els = [el(1, "Sold On", -0.1, 51.5, wikidata="Q10", operator="New Owner Inns Ltd"),
           el(2, "Still Theirs", -0.1, 51.51, wikidata="Q11"),
           el(3, "Sold Too", -0.1, 51.52, **{"brand:wikidata": "Q12"}, operator="Other New Owner Ltd")]
    w = wd({"Q10": [("Q1", True)], "Q11": [("Q1", True)], "Q12": [("Q1", True)]}, {"Q10": "Sold On", "Q11": "Still Theirs"})
    _, by = run2(els, rows, wd=w)
    assert (by["n1"]["group"], by["n1"]["category"]) == (None, "indie")
    assert by["n2"]["group"] == "bigco"
    assert by["n3"]["group"] is None
