"""Copy lint: the report card must not use words that read as a verdict on a place."""

import json
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
JS = SITE / "assets" / "report.js"
BANNED = re.compile(r"\b(safe|unsafe|dangerous|rough|sink|score|deprived area)\b", re.I)
ALLOWED = ("No overall score.",)  # the footer states there is no score


# exact upstream phrases in source metadata: a dataset's official title and a negated caveat
JSON_ALLOWED = ALLOWED + ("income score,", "how safe a street feels")


def _bad(text, allowed=ALLOWED):
    for ok in allowed:
        text = text.replace(ok, "")
    return [
        m.group(0)
        for line in text.splitlines()
        if not line.lstrip().startswith("//")
        for m in BANNED.finditer(line)
    ]


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def test_report_copy_has_no_banned_words():
    assert not _bad(JS.read_text())


def test_report_json_strings_have_no_banned_words():
    rep = json.loads((SITE / "api" / "v1" / "report.json").read_text())
    assert not _bad(
        "\n".join(_strings({k: v for k, v in rep.items() if k != "checks"})),
        JSON_ALLOWED,
    )


def test_report_section_of_index_has_no_banned_words():
    html = (SITE / "index.html").read_text()
    m = re.search(r'<section class="view" id="v-report".*?</section>', html, re.S)
    assert m
    assert not _bad(re.sub(r"<[^>]+>", " ", m.group(0)))
