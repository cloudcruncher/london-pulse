"""The service worker must never ship empty or name a shell file that does not exist."""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def test_service_worker_is_complete():
    src = (SITE / "sw.js").read_text()
    assert src.strip(), "site/sw.js is empty"
    assert re.search(r"const VERSION = 'lp-v\d+'", src)
    m = re.search(r"const SHELL = \[(.*?)\];", src, re.S)
    assert m, "no SHELL list"
    files = re.findall(r"'([^']+)'", m.group(1))
    assert len(files) >= 5
    for f in files:
        assert (SITE / f).exists(), f"SHELL lists a missing file: {f}"
