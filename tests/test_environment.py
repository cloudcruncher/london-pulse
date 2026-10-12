import json

from london_pulse import environment
from london_pulse.environment import merge


def test_merge_joins_topics_by_code_and_leaves_gaps_as_none():
    fields, rows = merge(
        {
            "flood": {"A": {"z3": 5.0}, "B": {"z3": 0.0}},
            "air": {"A": {"no2": 30.0}, "C": {"no2": 22.0}},
        }
    )
    assert fields == ["z3", "no2"]
    assert rows == [["A", 5.0, 30.0], ["B", 0.0, None], ["C", None, 22.0]]


def test_build_skips_a_failing_topic_and_records_why(tmp_path, monkeypatch):
    class Good:
        GUIDELINES = {"no2": 10}
        compute = staticmethod(
            lambda g, r: ({"A": {"no2": 30.0}, "B": {"no2": 20.0}}, [{"id": "air"}])
        )

    class Bad:
        @staticmethod
        def compute(g, r):
            raise RuntimeError("download failed")

    mods = {"london_pulse.air": Good, "london_pulse.flood": Bad}
    monkeypatch.setattr(environment.importlib, "import_module", lambda n: mods[n])
    environment.build(
        tmp_path / "x.geojson", tmp_path, tmp_path, topics=["flood", "air"]
    )
    out = json.loads((tmp_path / "environment.json").read_text())
    assert out["topics"] == ["air"] and out["skipped"][0]["topic"] == "flood"
    assert out["fields"] == ["code", "no2"] and out["medians"] == {"no2": 25.0}
    assert out["guidelines"] == {"no2": 10}
