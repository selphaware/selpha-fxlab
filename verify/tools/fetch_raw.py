"""Harness-time fixture fetcher -- the ONLY part of the harness that uses the network.

Run this to re-freeze the raw bi5 fixtures, then run ``build_tick_fixtures.py``
to regenerate ``expected.json`` and the poison files. The gate itself never
imports this module and never touches the network.

    python verify/tools/fetch_raw.py            # fetch the standard fixture set
    python verify/tools/fetch_raw.py --dry-run  # print the URLs and stop

Operational notes learned the hard way against the live feed:

* The month component of the URL is **zero-based** (January = ``00``).
* A closed hour is served as **HTTP 200 with a zero-byte body**, not a 404.
* Sustained requests earn ``HTTP 503`` -- that is Dukascopy's HAProxy throttling,
  not an application error. Back off and retry.
* Connection resets are frequent; retry those too.
* A VPN or datacenter egress IP is refused outright by the datafeed front end
  even though ``www.dukascopy.com`` keeps working. If every request 503s from the
  first one, check the public IP before blaming the code.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time
import urllib.error
import urllib.request
from typing import Final

BASE: Final[str] = "https://datafeed.dukascopy.com/datafeed"
UA: Final[dict[str, str]] = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
}
OUT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "raw"

#: The frozen fixture set. Chosen to cover: three consecutive liquid hours (so
#: bars can be resampled), a second pair with JPY scaling, a Saturday (empty),
#: and both real week boundaries -- Friday close and Sunday open, each with the
#: empty hours either side of it.
TARGETS: Final[list[tuple[str, dt.date, int]]] = [
    ("EURUSD", dt.date(2026, 7, 14), 12),
    ("EURUSD", dt.date(2026, 7, 14), 13),
    ("EURUSD", dt.date(2026, 7, 14), 14),
    ("USDJPY", dt.date(2026, 7, 14), 13),
    ("EURUSD", dt.date(2026, 7, 11), 13),   # Saturday -> closed
    ("EURUSD", dt.date(2026, 7, 17), 20),   # Friday, last hour with data
    ("EURUSD", dt.date(2026, 7, 17), 21),   # Friday close boundary -> empty
    ("EURUSD", dt.date(2026, 7, 17), 22),
    ("EURUSD", dt.date(2026, 7, 19), 19),   # Sunday, still closed
    ("EURUSD", dt.date(2026, 7, 19), 20),
    ("EURUSD", dt.date(2026, 7, 19), 21),   # Sunday open boundary -> first ticks
    ("EURUSD", dt.date(2026, 7, 19), 22),
]


def url_for(pair: str, day: dt.date, hour: int) -> str:
    """Build the bi5 URL. The path month is ZERO-BASED: January is ``00``."""
    return (f"{BASE}/{pair}/{day.year:04d}/{day.month - 1:02d}/"
            f"{day.day:02d}/{hour:02d}h_ticks.bi5")


def fetch(url: str, attempts: int = 7) -> tuple[int | None, bytes, dict[str, str]]:
    """Fetch one URL with exponential backoff on throttling and transport errors."""
    delay = 5.0
    for i in range(attempts):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=40) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and i < attempts - 1:
                print(f"    HTTP {exc.code} (throttled), backing off {delay:.0f}s "
                      f"[{i + 1}/{attempts}]", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 90)
                continue
            return exc.code, b"", dict(exc.headers or {})
        except OSError as exc:
            if i < attempts - 1:
                print(f"    {type(exc).__name__}, retrying in {delay:.0f}s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 90)
                continue
            raise
    return None, b"", {}


def main() -> int:
    """Fetch every target hour and record provenance in ``_fetch_meta.json``."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args()

    if ns.dry_run:
        for pair, day, hour in TARGETS:
            print(f"{day} ({day:%a}) {hour:02d}h  {url_for(pair, day, hour)}")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    meta = []
    for pair, day, hour in TARGETS:
        url = url_for(pair, day, hour)
        print(f"GET {url}", flush=True)
        status, body, headers = fetch(url)
        name = f"{pair}_{day.isoformat()}_{hour:02d}h.bi5"
        if status == 200:
            (OUT / name).write_bytes(body)
        meta.append({
            "pair": pair, "date": day.isoformat(), "hour": hour, "url": url,
            "status": status, "compressed_bytes": len(body),
            "last_modified": headers.get("Last-Modified"),
            "file": name if status == 200 else None,
            "weekday": day.strftime("%a"),
        })
        print(f"    -> {status}  {len(body)} bytes  "
              f"LM={headers.get('Last-Modified')}", flush=True)
        time.sleep(2.5)

    (OUT / "_fetch_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf8")
    failed = [m for m in meta if m["status"] != 200]
    print(f"\nfetched {len(meta) - len(failed)}/{len(meta)} hours -> {OUT}")
    if failed:
        print("FAILED:", [f"{m['pair']} {m['date']} {m['hour']:02d}h" for m in failed])
        return 1
    print("Next: python verify/tools/build_tick_fixtures.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
