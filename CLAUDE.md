# CLAUDE.md — selpha-fxlab Phase 1

Conventions and hard constraints for building `fxlab`. Read `spec.md` for what to
build; this file is how it will be judged and what you may touch.

---

## Definition of done

```
E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe -E -s verify\smoke_test.py fxlab
```

Exit `0` and you are done. Nothing else counts as done — not "tests pass", not
"it runs".

| gate exit | meaning | what to do |
|---|---|---|
| 0 | PASS | commit |
| 1 | **DELIVERABLE FAILURE** — your code is wrong | read the message, fix `fxlab/` |
| 2 | **HARNESS ERROR** — the judge is broken | **do not touch `fxlab/`**; tell the user |
| 3 | **ENVIRONMENT ERROR** — machine not judgable | **do not touch `fxlab/`**; tell the user |

Exit 2 and 3 are never your code's fault. The most common cause of 3 on this
machine is the `A:` drive mapping disappearing (see *Environment* below).

Run `verify\smoke_test.py --selftest` only if you suspect the gate itself is
wrong. It takes ~3 minutes and requires no deliverable.

---

## Environment — established facts, not assumptions

| fact | value |
|---|---|
| Interpreter (**use no other**) | `E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe` |
| Version | Python 3.12.13 (Anaconda build) |
| Project root | `E:\CODE\selpha-fxlab` — local physical disk |
| Installed | pytest 9.1.1, pandas **3.0.5**, pyarrow 25.0.1, numpy 2.5.2 |
| Not installed | httpx, requests, duckdb, pyyaml, ib_async |

Three traps, all verified by running commands rather than assumed:

1. **Bare `python` is a trap.** The first `python` on `PATH` is the Microsoft
   Store alias stub at `C:\Users\uahma\AppData\Local\Microsoft\WindowsApps\`.
   Always use the absolute path above. Never create a venv — this one already
   exists and the gate runs on it.
2. **`PYTHONPATH=i:\xstar` is set globally** and lands *ahead of the standard
   library* on `sys.path`. It shadows the stdlib `test` package and exposes
   `generic`, `ship`, `space`, `xmath`, `xobject`, `xanimation` as importable.
   Always run Python with `-E -s`. The gate does this and asserts it.
3. **The venv's stdlib lives on a network-mapped drive.** `sys.base_prefix` is
   `a:\envs\py312`, which is `\\localhost\C$\users\uahma\anaconda3` (DriveType 4).
   If that mapping is gone the interpreter cannot start — that is an
   ENVIRONMENT ERROR, not a bug in `fxlab/`. It also makes every `import pandas`
   cost ~3s, which is why the gate parallelises its subprocess runs.

To install a runtime package:
`E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe -m pip install <pkg>`
Justify anything non-obvious here. Never write files into `env_fxlab/`.

---

## The contract the deliverable must satisfy

### Entrypoints

```
python -m fxlab.ingest    --config <cfg.toml>
python -m fxlab.backtest  --config <cfg.toml>
```

`fxlab/ingest.py` and `fxlab/backtest.py` are thin CLIs over `fxlab/ingestion/`,
`fxlab/costs/`, `fxlab/backtest/`. Both must exit non-zero on failure.

### Ingest config (TOML — the gate generates these)

```toml
[ingest]
mode = "fixture"          # "fixture" reads local bi5; "live" fetches. Gate only ever uses fixture.
raw_dir = "…"             # holds <PAIR>_<YYYY-MM-DD>_<HH>h.bi5
out_dir = "…"

[[ingest.hours]]
pair = "EURUSD"
date = "2026-07-14"
hour = 13
```

### Ingest outputs

* `<out_dir>/ticks/pair=<PAIR>/date=<YYYY-MM-DD>/*.parquet`
* `<out_dir>/manifest.json`

**Tick Parquet schema — exact Arrow types, asserted column by column:**

| column | Arrow type |
|---|---|
| `pair` | `large_string` |
| `ts` | `timestamp[us, tz=UTC]` |
| `bid` | `double` |
| `ask` | `double` |
| `bid_volume` | `double` |
| `ask_volume` | `double` |
| `source` | `large_string` |

No extra columns. Pin these explicitly with a `pa.schema(...)` — do **not** let
pandas infer them. pandas 3 changed the default string dtype to `str`
(Arrow-backed `large_string`) and `future.infer_string` is already `True`; code
written against pandas 2's `object` dtype will drift.

**Manifest** — one entry per requested hour, *including* empty ones:

```json
{"hours": [{"pair": "...", "date": "...", "hour": 13,
            "status": "ok|empty|gap",
            "decoded_ticks": 0, "written_ticks": 0, "duplicates_dropped": 0,
            "sha256": "...", "compressed_bytes": 0}],
 "validation": {"ok": true, "errors": [{"reason": "...", "detail": "..."}]}}
```

A failed or corrupt hour is recorded as a gap, never silently skipped.

### Validation — named reasons

On rejection, exit non-zero and print the reason token to **stderr** (and record
it in the manifest). The gate greps for these exact tokens:

| token | when |
|---|---|
| `CROSSED_QUOTE` | any tick with `bid > ask` |
| `NON_POSITIVE_PRICE` | any tick with `bid <= 0` or `ask <= 0` |
| `CLOSED_MARKET_TICK` | any tick on a Saturday (UTC) |

Duplicates are **not** a hard failure: drop exact duplicate rows and report the
count in `duplicates_dropped`. Dropping them silently fails the gate.

### Backtest config and output

```toml
[backtest]
bars_path = "…parquet"
pair = "EURUSD"
units = 1000000
fast = 2
slow = 4
out_path = "…json"

[backtest.costs]
commission_rate = 2e-05   # 0.20 bp of notional
commission_min = 2.0      # USD per order
cost_multiplier = 1.0
```

Results JSON: `{"summary": {...}, "trades": [...], "equity": [{"ts", "equity"}]}`
with `summary` carrying `trade_count`, `gross_pnl`, `spread_cost`, `commission`,
`total_costs`, `net_pnl`, `max_drawdown`, and each trade carrying its own
`spread_cost` and `commission`.

**Accounting convention — this is load-bearing:**

```
gross_pnl   = (mid_exit - mid_entry) * units * direction   # measured MID to MID
spread_cost = (ask_entry - mid_entry)*units + (mid_exit - bid_exit)*units
commission  = per order: max(rate * notional, minimum)
total_costs = spread_cost + commission
net_pnl     = gross_pnl - total_costs                      # asserted exactly
```

Gross is measured mid-to-mid so the spread paid is an explicit, auditable cost
line instead of being buried in the fill price. A backtester that reports
`gross == net` fails.

### Execution rules

* A signal computed on bar `t` may not fill before bar `t+1`'s **open**.
* Buy at the **ask**, sell at the **bid**. Never at the mid.
* Bar timestamp = bar **OPEN** time; the bar covers `[open, open+Δ)`.

---

## Dukascopy — facts confirmed against the live feed

```
https://datafeed.dukascopy.com/datafeed/{PAIR}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5
```

* **`MM0` is ZERO-BASED**: January = `00` … December = `11`. Confirmed six ways —
  `/2026/06/11/` and `/2026/06/18/` return empty bodies (they are Saturdays
  11 and 18 **July**), and every response's `Last-Modified` header names the real
  date directly.
* **Empty body = market closed**, not an error. Dukascopy returns **HTTP 200 with
  0 bytes** for closed hours. A 404 means the hour genuinely does not exist.
* **Compression**: raw LZMA1 *alone* format — `lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)`.
  The header is `5d 00 00 40 00` + 8-byte little-endian uncompressed size.
* **Record layout**: 20 bytes, `struct` format `>IIIff`, big-endian:
  `(ms_offset_in_hour, ask_int, bid_int, ask_volume_f32, bid_volume_f32)`.
  **ASK comes before BID.** Getting this backwards produces `ask < bid` on every
  tick, which the gate rejects immediately.
* **Price scale = 10 ^ -display_precision**: `1e-3` for JPY-quoted pairs,
  `1e-5` for all others. Verified against OANDA H1 for all 12 pairs in scope —
  worst disagreement 1.2 pip (GBPJPY), which is the genuine ECN-vs-retail spread
  difference, not a scaling error.
* **Rate limiting is real.** Sustained requests earn `HTTP 503` (an HAProxy page,
  not a Dukascopy application error). Back off exponentially and treat 503/429 as
  retryable. Connection resets are frequent — retry those too.
* **VPN egress is blocked.** Datacenter/VPN IPs get a hard 503 from the
  datafeed front end. `www.dukascopy.com` still works, which makes this look like
  a routing problem when it is an IP-reputation problem.

### FX week boundary — measured, and it moves with US DST

| period | opens | closes |
|---|---|---|
| Northern **summer** (observed July 2026) | Sun **21:00 UTC** | Fri **21:00 UTC** |
| Northern **winter** (observed January 2026) | Sun **22:00 UTC** | Fri **22:00 UTC** |

Evidence: Fri 2026-07-17 20:00Z has 1,163 ticks and 21:00Z is empty; Sun
2026-07-19 20:00Z is empty and 21:00Z has 222 ticks. In January the same probe
shows Fri 2026-01-09 21:00Z still carrying 868 ticks and 22:00Z empty.

**Do not hardcode 21:00 UTC.** The boundary tracks 17:00 `America/New_York`.
Derive it with `zoneinfo`, or it will be wrong for half of every year — silently,
in a way that corrupts every session and spread statistic downstream.

---

## OANDA

* Host is **config-driven**: `OANDA_ENV` selects `practice` (default) or `live`.
  Practice is `https://api-fxpractice.oanda.com`.
* Token comes from `OANDA_API_TOKEN` only. **Never** hardcode it, log it, print
  it, or commit it.
* The client is **read-only**: `/v3/instruments/{inst}/candles` and
  `/v3/accounts/{id}/instruments`. Never `/orders`, `/trades`, or `/positions`.
* Candle prices arrive as **strings** and timestamps as RFC3339 with **nanosecond**
  precision (9 fractional digits). Parse both deliberately.
* `displayPrecision` / `pipLocation` from the instruments endpoint is the
  authoritative per-pair scaling reference.

---

## Guardrails

* **Scope of writes: `fxlab/`, `tests/`, `config/`** (plus `HANDOFF.md`, `README.md`).
* **Never edit `verify/`.** It holds the gate, the frozen fixtures and the
  reference implementation. Settings deny it. An agent that can edit its own
  judge will eventually edit its own judge.
* **Never edit `env_fxlab/`.** Install via `python -m pip`, never by writing files.
* **Do not read `verify/reference/`** for hints. It is a minimal harness stub, not
  a model answer, and copying it will not satisfy `spec.md`.
* **No network in fixture mode.** The gate installs a socket guard and fails if
  anything reaches for a non-loopback address. Guard your HTTP layer so `mode =
  "fixture"` cannot fetch.
* **No secrets in code or commits.** Env vars only. IB credentials never enter
  this repo at all — they live in IBC config outside it (a Phase 3 concern).
* **Logging, not printing.** Use the `logging` module. The reason tokens above are
  the one deliberate exception: they go to stderr verbatim.

---

## Git

* Commit after each gate-green milestone, with a message describing what now
  works. Do not commit red.
* `git push origin main` over SSH is fine — an ssh-agent session exists, so do
  not prompt for credentials.
* Never force-push.

---

## Autonomy

Work through gate failures independently — read the message, fix, re-run. The
gate's messages name the specific bug and often the specific line of reasoning.

**Stop and ask only if:**
* the same gate failure survives three genuine fix attempts;
* the gate reports exit 2 or 3 (harness or environment — not yours to fix);
* an external dependency is unreachable.

Do not work around the gate. Do not weaken a test to make it pass. If you believe
the gate is wrong, say so and stop — do not edit it.
