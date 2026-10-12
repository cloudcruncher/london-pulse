"""Environmental conditions around an area: flood risk, noise and green space per LSOA.

Each topic lives in its own module (flood, noise, green) exposing compute(lsoa_geojson, ref_dir) ->
(metrics by LSOA code, source dicts). This merges them into site/api/v1/environment.json, one row per LSOA in
the same order as the 'fields' list, joined to areas.json by LSOA code in the browser.

A topic that fails to download or compute is left out (and listed under 'skipped') rather than failing the run:
the other topics are still useful, and the page only shows what is present.
These are modelled or mapped conditions for a neighbourhood, not a survey of any address or a prediction.

Run: python -m london_pulse.environment
"""

import importlib
import json
import traceback
from datetime import date
from pathlib import Path

TOPICS = ["flood", "noise", "green"]   # air quality (LAEI) is left out: its licence on the London Datastore is "Not Specified"
# wording the data owners ask for when their data is republished; shown beside each source
ATTRIBUTION = {
    "flood": "Contains public sector information licensed under the Open Government Licence v3.0. © Environment Agency copyright and/or database right 2026. All rights reserved.",
    "noise": "Contains public sector information licensed under the Open Government Licence v3.0. © Crown copyright and database rights, Defra.",
    "greenspace": "Contains OS data © Crown copyright and database right 2026. Contains public sector information licensed under the Open Government Licence v3.0.",
}


def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def merge(results: dict[str, dict[str, dict]]) -> tuple[list[str], list[list]]:
    """Join per-topic {code: {metric: value}} into columnar rows [code, v1, v2, ...], None where a topic has no value."""
    fields = [
        k for topic in results.values() for row in list(topic.values())[:1] for k in row
    ]
    codes = sorted({c for topic in results.values() for c in topic})
    rows = []
    for code in codes:
        merged = {}
        for topic in results.values():
            merged.update(topic.get(code, {}))
        rows.append([code] + [merged.get(k) for k in fields])
    return fields, rows


def build(
    lsoa_geojson: Path,
    ref_dir: Path,
    api_dir: Path,
    topics: list[str] = TOPICS,
    only: set[str] | None = None,
) -> None:
    """only: LSOA codes to keep (London areas in areas.json; the boundary file is fetched by bbox and spills over)."""
    results, sources, skipped = {}, [], []
    guidelines = {}
    for name in topics:
        try:
            mod = importlib.import_module(f"london_pulse.{name}")
            metrics, src = mod.compute(lsoa_geojson, ref_dir)
            if not metrics:
                raise RuntimeError("no values computed")
            results[name] = metrics
            sources += [{**x, "attribution": next(v for k, v in ATTRIBUTION.items() if x["id"].startswith(k))} for x in src]
            for note in getattr(mod, "SKIPPED", []):
                skipped.append({"topic": name, "error": note})
            guidelines.update(getattr(mod, "GUIDELINES", {}))
            print(f"{name}: {len(metrics)} LSOAs")
        except Exception as e:  # noqa: BLE001 - one broken source must not take the others down
            traceback.print_exc()
            skipped.append({"topic": name, "error": f"{type(e).__name__}: {e}"[:300]})
    if not results:
        raise SystemExit("no environment topic could be computed")
    if only is not None:
        results = {
            t: {c: v for c, v in m.items() if c in only} for t, m in results.items()
        }
    fields, rows = merge(results)
    medians = {}
    for i, k in enumerate(fields, start=1):
        vals = [r[i] for r in rows if r[i] is not None]
        if vals:
            medians[k] = round(median(vals), 1)
    (api_dir / "environment.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated": date.today().isoformat(),
                "licence": "Mixed: see the licence and attribution of each entry in sources",
                "method": "Per-LSOA overlay of public flood, noise and green-space datasets; see sources.",
                "topics": list(results),
                "skipped": skipped,
                "fields": ["code"] + fields,
                "areas": rows,
                "medians": medians,
                "guidelines": guidelines,
                "sources": sources,
            },
            separators=(",", ":"),
        )
    )
    print(
        f"environment.json: {len(rows)} LSOAs, topics {list(results)}, skipped {[s['topic'] for s in skipped]}"
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    ref = root / "work" / "reference"
    api = root / "site" / "api" / "v1"
    london = {r[0] for r in json.loads((api / "areas.json").read_text())["areas"]}
    build(ref / "lsoa.geojson", ref, api, only=london)
