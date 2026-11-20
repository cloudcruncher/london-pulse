"""Pubs insights on tiny fixtures (no network): shares, HHI, deprivation join, missing inputs, category coverage."""

import json
from datetime import date
from pathlib import Path

from london_pulse import pubs_insights as PI

CATS = {
    "pe": {"label": "PE"},
    "corp": {"label": "Corporate group"},
    "family": {"label": "Family brewer"},
    "indie": {"label": "Independent"},
    "unknown": {"label": "No group link found"},
}
FIELDS = [
    "name",
    "lon",
    "lat",
    "postcode",
    "borough",
    "fhrsid",
    "osm",
    "group",
    "category",
    "confidence",
    "evidence",
    "cask",
    "craft",
    "website",
]


def pub(i, borough, group, cat, cask=None, fhrsid=None, lon=-0.1, lat=51.5):
    return [
        f"Pub {i}",
        lon,
        lat,
        "",
        borough,
        fhrsid,
        f"n{i}",
        group,
        cat,
        None if cat == "unknown" else "likely",
        [],
        cask,
        None,
        None,
    ]


def doc(rows, fields=FIELDS):
    return {"as_of": "2026-11-20", "categories": CATS, "fields": fields, "pubs": rows}


# Camden: 2 bigco, 1 rival, 1 indie, 2 unknown. Hackney: 1 bigco, 1 unknown (so London known = 5, groups 4).
ROWS = [
    pub(1, "Camden", "bigco", "corp", cask=1, fhrsid=11),
    pub(2, "Camden", "bigco", "corp", cask=0, fhrsid=12),
    pub(3, "Camden", "rival", "pe", fhrsid=13),
    pub(4, "Camden", None, "indie", cask=1, fhrsid=14),
    pub(5, "Camden", None, "unknown"),
    pub(6, "Camden", None, "unknown", fhrsid=16),
    pub(7, "Richmond-Upon-Thames", "bigco", "corp", fhrsid=17),
    pub(8, "Richmond-Upon-Thames", None, "unknown"),
]
AREAS = {
    "fields": ["code", "name", "borough", "lon", "lat", "imd_decile", "net_income_ahc"],
    "areas": [
        ["E1", "a", "Camden", -0.1, 51.5, 2, 30000],
        ["E2", "b", "Camden", -0.2, 51.5, 9, 50000],
        ["E3", "c", "Richmond upon Thames", -0.3, 51.4, 10, 60000],
    ],
}
LSOA = ["E1", "E1", "E2", "E2", "E1", None, "E3", "E3"]
RATINGS = {11: "5", 12: "3", 13: "5", 14: "4", 16: "Exempt", 17: "AwaitingInspection"}


def full():
    return PI.build(
        doc(ROWS),
        ratings=RATINGS,
        pub_lsoa=LSOA,
        areas=AREAS,
        borough_km2={"camden": 2.0, "richmond upon thames": 4.0},
        today=date(2026, 11, 20),
    )


def test_london_shares_and_hhi():
    lo = full()["london"]
    assert lo["total"] == 8 and lo["known"] == 5
    assert lo["by_category"] == {
        "pe": 1,
        "corp": 3,
        "family": 0,
        "indie": 1,
        "unknown": 3,
    }
    assert (
        lo["group_share"] == 0.8
        and lo["group_share_of_total"] == 0.5
        and lo["indie_share_of_known"] == 0.2
    )
    # bigco 3/5, rival 1/5, one independent 1/5 as its own owner: 0.36 + 0.04 + 0.04
    assert lo["hhi"] == 0.44
    assert lo["top_groups"][0] == {
        "id": "bigco",
        "n": 3,
        "share": 0.6,
        "share_of_total": 0.375,
    }
    assert lo["top5_share"] == 0.8 and lo["cask"] == 2 and lo["cask_known"] == 3


def test_boroughs_groups_and_categories():
    d = full()
    b = {x["borough"]: x for x in d["boroughs"]}
    cam = b["Camden"]
    assert (
        cam["total"] == 6
        and cam["known"] == 4
        and cam["group_share"] == 0.75
        and cam["indie_n"] == 1
        and cam["cask_n"] == 2
    )
    assert (
        cam["top_group"] == "bigco"
        and cam["top_group_n"] == 2
        and cam["groups"] == {"bigco": 2, "rival": 1}
    )
    assert cam["hhi"] == round((2 / 4) ** 2 + (1 / 4) ** 2 + 1 / 16, 3)
    assert cam["rating_avg"] == 4.25 and cam["rating_n"] == 4  # Exempt is not a score
    assert cam["pubs_per_km2"] == 3.0
    ric = b["Richmond-Upon-Thames"]  # FSA spelling matched to the ONS area name
    assert (
        ric["pubs_per_km2"] == 0.5
        and ric["rating_n"] == 0
        and ric["rating_avg"] is None
    )
    g = {x["id"]: x for x in d["groups"]}
    assert [x["id"] for x in d["groups"]] == ["bigco", "rival"]
    assert (
        g["bigco"]["n"] == 3
        and g["bigco"]["boroughs"] == 2
        and g["bigco"]["top_boroughs"] == [["Camden", 2], ["Richmond-Upon-Thames", 1]]
    )
    assert g["bigco"]["cask_n"] == 1 and g["bigco"]["cask_known"] == 2
    assert (
        g["bigco"]["rating_dist"] == {"5": 1, "4": 0, "3": 1, "2": 0, "1": 0, "0": 0}
        and g["bigco"]["rating_avg"] == 4.0
    )


def test_deprivation_join():
    d = full()
    dep = {x["decile"]: x for x in d["deprivation"]}
    assert list(dep) == list(range(1, 11))
    assert dep[2]["total"] == 3 and dep[2]["by_category"] == {
        "pe": 0,
        "corp": 2,
        "family": 0,
        "indie": 0,
        "unknown": 1,
    }
    assert dep[9]["total"] == 2 and dep[10]["total"] == 2 and dep[5]["total"] == 0
    assert sum(x["total"] for x in d["deprivation"]) == 7  # pub 6 sits in no LSOA
    assert d["by_category"]["corp"]["imd_decile_avg"] == round((2 + 2 + 10) / 3, 2)
    cam = next(x for x in d["boroughs"] if x["borough"] == "Camden")
    assert (
        cam["imd_decile_avg"] == round((2 + 2 + 9 + 9 + 2) / 5, 2)
        and cam["income_avg"] == 38000
    )
    assert d["inputs"]["pubs_located"] == 7


def test_missing_inputs_give_null():
    d = PI.build(doc(ROWS))
    assert d["deprivation"] is None and d["churn"] is None
    assert all(
        b["imd_decile_avg"] is None
        and b["income_avg"] is None
        and b["rating_avg"] is None
        and b["pubs_per_km2"] is None
        for b in d["boroughs"]
    )
    assert all(
        c["rating_avg"] is None
        and c["imd_decile_avg"] is None
        and c["rating_dist"] is None
        for c in [
            *(d["by_category"][k] for k in CATS),
            *d["by_category"]["ex_camden"].values(),
        ]
    )
    assert d["inputs"] == {
        "ratings": False,
        "deprivation": False,
        "borough_areas": False,
        "pubs_located": 0,
    }
    assert d["london"]["hhi"] == 0.44  # counts still work


def test_no_known_pubs_gives_null_shares():
    d = PI.build(doc([pub(1, "Camden", None, "unknown")]))
    lo = d["london"]
    assert (
        lo["group_share"] is None
        and lo["hhi"] is None
        and lo["top5_share"] is None
        and lo["top_groups"] == []
    )
    assert (
        d["boroughs"][0]["top_group"] is None
        and d["boroughs"][0]["group_share"] is None
    )


def test_categories_always_present():
    d = PI.build(doc([pub(1, "Camden", "bigco", "corp")]), pub_lsoa=["E1"], areas=AREAS)
    keys = list(CATS)
    assert list(d["london"]["by_category"]) == keys and list(d["by_category"]) == [
        *keys,
        "ex_camden",
    ]
    assert list(d["by_category"]["ex_camden"]) == keys
    assert all(list(b["by_category"]) == keys for b in d["boroughs"])
    assert all(list(x["by_category"]) == keys for x in d["deprivation"])
    assert d["by_category"]["family"]["n"] == 0 and d["categories"] == CATS


def test_rating_field_in_pubs_json_wins_and_fields_are_by_name():
    fields = [
        "category",
        "fhrsid",
        "borough",
        "name",
        "lon",
        "lat",
        "postcode",
        "osm",
        "group",
        "confidence",
        "evidence",
        "cask",
        "craft",
        "website",
        "rating",
        "rating_date",
    ]
    rows = [
        [
            dict(zip(FIELDS, r))[f] if f in FIELDS else ("2" if f == "rating" else None)
            for f in fields
        ]
        for r in ROWS[:2]
    ]
    d = PI.build(doc(rows, fields), ratings={11: "5", 12: "5"})
    assert (
        d["by_category"]["corp"]["rating_avg"] == 2.0
        and d["by_category"]["corp"]["rating_n"] == 2
    )


def test_shares_bounded_and_rounded():
    d = full()
    vals = [
        d["london"][k]
        for k in (
            "group_share",
            "group_share_of_total",
            "indie_share_of_known",
            "hhi",
            "top5_share",
        )
    ]
    vals += [b["group_share"] for b in d["boroughs"] if b["group_share"] is not None]
    assert all(0 <= v <= 1 and round(v, 3) == v for v in vals)


def test_locate_falls_back_to_nearest_centroid(tmp_path: Path):
    codes, km2 = PI.locate(
        doc([pub(1, "Camden", None, "unknown", lon=-0.21, lat=51.5)]),
        AREAS,
        tmp_path / "missing.geojson",
    )
    assert codes == ["E2"] and km2 is None


def test_main_writes_file(tmp_path: Path):
    api = tmp_path / "site" / "api" / "v1"
    api.mkdir(parents=True)
    (api / "pubs.json").write_text(json.dumps(doc(ROWS)))
    out = PI.main(tmp_path)  # no areas.json, no latest.parquet: runs with nulls
    on_disk = json.loads((api / "pubs-insights.json").read_text())
    assert (
        on_disk["london"]["total"] == 8
        and out["deprivation"] is None
        and on_disk["inputs"]["ratings"] is False
    )


FSA_AUTHORITIES = [
    "Barking and Dagenham",
    "Barnet",
    "Bexley",
    "Brent",
    "Bromley",
    "Camden",
    "City of London Corporation",
    "Croydon",
    "Ealing",
    "Enfield",
    "Greenwich",
    "Hackney",
    "Hammersmith and Fulham",
    "Haringey",
    "Harrow",
    "Havering",
    "Hillingdon",
    "Hounslow",
    "Islington",
    "Kensington and Chelsea",
    "Kingston-Upon-Thames",
    "Lambeth",
    "Lewisham",
    "Merton",
    "Newham",
    "Redbridge",
    "Richmond-Upon-Thames",
    "Southwark",
    "Sutton",
    "Tower Hamlets",
    "Waltham Forest",
    "Wandsworth",
    "Westminster",
]
ONS_NAMES = [
    "Barking and Dagenham",
    "Barnet",
    "Bexley",
    "Brent",
    "Bromley",
    "Camden",
    "City of London",
    "Croydon",
    "Ealing",
    "Enfield",
    "Greenwich",
    "Hackney",
    "Hammersmith and Fulham",
    "Haringey",
    "Harrow",
    "Havering",
    "Hillingdon",
    "Hounslow",
    "Islington",
    "Kensington and Chelsea",
    "Kingston upon Thames",
    "Lambeth",
    "Lewisham",
    "Merton",
    "Newham",
    "Redbridge",
    "Richmond upon Thames",
    "Southwark",
    "Sutton",
    "Tower Hamlets",
    "Waltham Forest",
    "Wandsworth",
    "Westminster",
]


def test_norm_borough_matches_all_33_fsa_names_to_ons():
    assert len(FSA_AUTHORITIES) == len(ONS_NAMES) == 33
    assert [PI.norm_borough(a) for a in FSA_AUTHORITIES] == [
        PI.norm_borough(o) for o in ONS_NAMES
    ]
    assert len({PI.norm_borough(a) for a in FSA_AUTHORITIES}) == 33


def test_ex_camden():
    d = full()
    x = d["london"]["ex_camden"]
    # outside Camden: Richmond's bigco (grouped) and one unknown; no independents
    assert x == {"total": 2, "known": 1, "group_share": 1.0, "indie_n": 0}
    ex = d["by_category"]["ex_camden"]
    assert (
        ex["corp"]["n"] == 1
        and ex["indie"]["n"] == 0
        and ex["unknown"]["n"] == 1
        and ex["pe"]["n"] == 0
    )
    assert (
        ex["corp"]["rating_n"] == 0 and ex["corp"]["rating_avg"] is None
    )  # AwaitingInspection is not a score
    assert ex["corp"]["imd_decile_avg"] == 10 and ex["indie"]["imd_decile_avg"] is None
    assert d["by_category"]["indie"]["n"] == 1
    assert any(
        n.startswith("1 of the 1 likely independents are in Camden") for n in d["notes"]
    )


def test_no_camden_note_without_independents():
    d = PI.build(doc([pub(1, "Camden", "bigco", "corp")]))
    assert not any("are in Camden" in n for n in d["notes"])
