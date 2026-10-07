"""Tiny stdlib JSON GET with retries (public APIs throw the occasional 429/5xx/504)."""
import json
import time
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
