"""London rail stations (Tube, DLR, Overground, Elizabeth line, Tram, National Rail) from the TfL Unified API.

Writes site/api/v1/stations.json. Run occasionally: python -m london_pulse.transport
"""
import json
from datetime import date
from pathlib import Path

from .http import get_json

TFL = "https://api.tfl.gov.uk"
BBOX = (51.28, 51.70, -0.52, 0.34)   # lat min/max, lon min/max (Greater London)
SUFFIX = (" Underground Station", " Rail Station", " DLR Station", " Tram Stop", " (London)")


def clean(name: str) -> str:
    for s in SUFFIX:
        name = name.removesuffix(s)
    return name.removeprefix("London ") if name.startswith("London ") and name.count(" ") == 1 else name


def build(api_dir: Path) -> int:
    lines = get_json(f"{TFL}/Line/Mode/tube,dlr,overground,elizabeth-line,tram,national-rail")
    st: dict[str, dict] = {}
    for ln in lines:
        try:
            stops = get_json(f"{TFL}/Line/{ln['id']}/StopPoints")
        except RuntimeError:
            continue   # one failing line should not lose the rest; the next run fills it in
        for s in stops:
            lat, lon = s.get("lat", 0), s.get("lon", 0)
            if not (BBOX[0] < lat < BBOX[1] and BBOX[2] < lon < BBOX[3]):
                continue
            e = st.setdefault(s.get("stationNaptan") or s["naptanId"],
                              {"name": clean(s["commonName"]), "lon": round(lon, 5), "lat": round(lat, 5), "lines": set(), "modes": set()})
            e["lines"].add(ln["name"])
            e["modes"].add(ln["modeName"])
    out = [{**v, "lines": sorted(v["lines"]), "modes": sorted(v["modes"])} for v in st.values()]
    out.sort(key=lambda s: s["name"])
    (api_dir / "stations.json").write_text(json.dumps(
        {"schema_version": 1, "as_of": date.today().isoformat(), "source": "Transport for London Unified API", "stations": out},
        ensure_ascii=False, separators=(",", ":")))
    return len(out)


if __name__ == "__main__":
    n = build(Path(__file__).resolve().parents[2] / "site" / "api" / "v1")
    print(f"{n} stations")
