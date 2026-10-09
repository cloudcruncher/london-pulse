"""Tiny stdlib JSON GET with retries (public APIs throw the occasional 429/5xx/504)."""
import json
import time
from pathlib import Path
import urllib.error
import urllib.request

UA = {"User-Agent": "london-pulse (https://github.com/cloudcruncher/london-pulse)"}


def get_json(url: str, tries: int = 5, timeout: int = 60):
    err: Exception | None = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 503 or e.code == 400:   # police API: too many results / bad poly; caller decides
                raise
            err = e
        except Exception as e:  # noqa: BLE001 - network flakiness
            err = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET failed after {tries} tries: {url}") from err


def download(url: str, dest, tries: int = 5, timeout: int = 180) -> None:
    """Stream a file to disk with the same retry policy as get_json.

    Written to a .part file and only renamed once it is non-empty and not an HTML error page served with status 200,
    so an interrupted or bad download is never mistaken for a cached good one.
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    err: Exception | None = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            with open(part, "rb") as f:
                head = f.read(64).lstrip().lower()
            if not head or head.startswith((b"<!doctype", b"<html")):
                raise ValueError("empty or HTML response")
            part.replace(dest)
            return
        except Exception as e:  # noqa: BLE001 - network flakiness
            err = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"download failed after {tries} tries: {url}") from err
