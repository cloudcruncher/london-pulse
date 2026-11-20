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
    assert (by["n1"]["group"], by["n1"]["category"], by["n1"]["confidence"], by["n1"]["evidence"]) == (None, "indie", "confirmed", ["override"])
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
