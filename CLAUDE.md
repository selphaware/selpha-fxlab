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

## Dukascopy and OANDA — confirmed feed facts

`SPEC.md` carries all of them, with the measurements they came from: the
zero-based-month URL, the `>IIIff` ask-before-bid record layout, LZMA1
`FORMAT_ALONE`, per-pair `10 ** -display_precision` scaling, empty-body-means-
closed, 503 throttling and VPN-egress rejection, and the FX week boundary
(which tracks 17:00 `America/New_York` — never hardcode 21:00 UTC).
OANDA practice host is `https://api-fxpractice.oanda.com`; `OANDA_ENV` selects
it. Read `SPEC.md` before touching `fxlab/ingestion/`.

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
  The OANDA token comes from `OANDA_API_TOKEN` only — never hardcode, log,
  print or commit it.
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

---
---

# CLAUDE.md — selpha-fxlab Phase 2 (research)

Everything above stands. Phase 1 is frozen: `verify/` is deny-edited, its gate
is now a **regression gate**, and it must exit 0 at every point in Phase 2.
This section adds what is different about research work.

`SPEC2.md` is the law of this phase. Its §Pre-registered decisions were fixed
before any result existed and are not yours to reinterpret. If a task seems to
require breaking one, stop and say so.

---

## Definition of done, Phase 2

Per task card, not per phase:

```
E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe -E -s verify2\research_gate.py experiments\<experiment-id>
```

Exit 0 and the card's deliverable report exists, and the task is done. The exit
codes mean exactly what they mean in Phase 1:

| exit | meaning | what to do |
|---|---|---|
| 0 | PASS | commit |
| 1 | **DELIVERABLE FAILURE** — the research or the record is wrong | read the named reason, fix `research/` or the experiment |
| 2 | **HARNESS ERROR** — the judge is broken | **do not touch `research/`**; tell the user |
| 3 | **ENVIRONMENT ERROR** — machine not judgable | **do not touch `research/`**; tell the user |

Other forms:

```
verify2\research_gate.py --fast        # Phase 1 gate + leakage + seal + ledger; what the hook runs
verify2\research_gate.py --selftest    # proves the gate by breaking it, ~3 min
```

The research gate **runs** the Phase 1 gate as its first check. It does not
reimplement it. A Phase 1 failure reports as `PHASE1_REGRESSION` and nothing
else matters until it is green.

## What the research gate judges, and the reasons it names

Named reasons go to stderr verbatim, exactly like the Phase 1 tokens:

| area | reasons |
|---|---|
| regression | `PHASE1_REGRESSION`, `RESEARCH_TESTS_FAILED` |
| leakage | `LEAKAGE_SELFCHECK_FAILED` |
| seal | `SEAL_LOADER_PERMISSIVE`, `SEAL_DATA_PRESENT`, `SEAL_CONFIG_DATE`, `SEAL_SCOPE_BREACH` |
| ledger | `LEDGER_MISSING_ENTRY`, `LEDGER_AFTER_RESULT`, `LEDGER_TASKCARD_MISMATCH`, `LEDGER_INCOMPLETE`, `LEDGER_MALFORMED`, `TASKCARD_MISSING` |
| costs | `COST_LADDER_INCOMPLETE`, `COST_LADDER_INCONSISTENT`, `COST_ARITHMETIC`, `COST_ZEROED`, `COST_MODEL_DRIFT`, `COST_CURRENCY`, `NON_USD_COST_UNFIXED`, `COST_FIX_MISDECLARED`, `SURVIVAL_VERDICT_MISMATCH` |
| reproducibility | `NOT_REPRODUCIBLE`, `UNSEEDED`, `CONFIG_DRIFT` |

## The task-card protocol

Each bounded loop task is defined by `taskcards/T<N>.md`, written by the user
with chat-Claude and **committed before the loop starts**. A card states scope,
allowed pairs and dates, the deliverable report path, and explicit non-goals.

* **The agent may not act outside its card.** Not a new pair, not a wider date
  range, not "a few more variants", not a new hypothesis. Pre-reg #3: the loop
  executes, chat decides.
* Every experiment's ledger entry names its card, and the gate checks that the
  card file exists and that the result agrees with the entry.
* If the card turns out to be impossible or wrong, **stop and report**. Do not
  reinterpret it.
* Observations worth chasing go in the report as observations. They become a
  next card only after a checkpoint.

## The seal rule

Sealed: **2025-03-01 onward**, open-ended. Enforced by absence first — that data
is not downloaded in Phase 2 — and by refusal second:

* all research reads go through `research.loader.ResearchLoader`. Nothing opens
  a Parquet directly;
* `scoring` mode refuses any date at or after the cutoff with `HOLDOUT_SEALED`,
  before touching the filesystem;
* `mechanical` mode exists only for pipeline checks against `data/live_week/`
  and **can never produce a scorecard**;
* a scoring config may not so much as mention a sealed date, comments included;
* nothing dated on or after the cutoff may appear under `data/research/`.

The Phase 1 live week and the Phase 1 fixtures are inside the seal and are
deliberately out of scope of the on-disk check (`SPEC2.md` §Harness rulings,
ruling A). That carve-out is the quarantine, not a loophole: it is allowlisted
in one place, it is visible in every gate run, and it cannot score.

## The ledger rule

`experiments/ledger.jsonl`, append-only, JSON Lines.

* **The start entry is written before the experiment runs.** Not after, not
  alongside. An abandoned or crashed trial still leaves a mark, which is the
  only thing that makes a trial count honest.
* Every result file needs a start entry that precedes it. A result without one
  fails the gate.
* Reviews state the trial count next to any highlighted result (pre-reg #10).
  `research.ledger.trial_count` produces it.
* Re-run class is declared up front: `full` by default, and mandatory for any
  experiment deciding survival or kill unless a full re-run exceeds about two
  hours; `deterministic-subset` above that bound, with the subset hashed
  **before** results exist.

## Cost rules

* One cost model, one set of parameters, every candidate. Declared in the
  experiment config, carried in the scorecard, checked by the gate.
* Every scorecard reports the full ladder 1.0 / 1.2 / 1.5 / 2.0×.
* The survival bar is pinned: aggregate out-of-sample walk-forward net P&L in
  USD above zero at 1.5×, at the level the candidate is proposed to trade.
  Nothing else is thresholded. Do not add a threshold; do not soften this one.
* **Until SPEC2 prerequisite P0-A lands, no non-USD-quoted pair may be scored**
  — all 8 of them, not just the 4 JPY-quoted ones. The gate fails it.

## Autonomy in a research loop

Work through gate failures independently: read the named reason, fix, re-run.
The autonomy limits of Phase 1 stand, plus two more.

**Stop and ask if:**
* the same gate failure survives three genuine fix attempts;
* the gate reports exit 2 or 3 (harness or environment — not yours to fix);
* an external dependency is unreachable;
* **the task card does not cover what you have found yourself needing to do**;
* **a pre-registered decision appears to block the task.**

Never weaken a gate, never edit `verify/` or `verify2/`, never widen a card to
fit a result.

## Scope of writes, Phase 2

`research/`, `tests2/`, `experiments/`, `reports/`, `taskcards/` — plus the
Phase 1 trees already listed, `HANDOFF2.md` and `README.md`.

**Never edit `verify2/`.** It holds the research gate, the leakage known
answers, the USD-accounting known answers and the selftest. Settings deny it,
for the same reason they deny `verify/`.

`.claude/` is denied too, so harness changes the agent proposes land in
`verify2/proposed/` and the user copies them into place. `verify2/proposed/`
currently holds the Phase 2 hook and settings; both need a session restart to
take effect.

## Layout

```
research/      analysis code — the judged surface
  seal.py        the cutoff, one definition
  loader.py      the only way research reads data; scoring | mechanical
  ledger.py      append-only experiment record
  walkforward.py purged, embargoed splitting and execution
  experiment.py  config, hashing, result documents
  run.py         python -m research.run --config <cfg>
tests2/        research unit tests (kept out of tests/ so a research bug
               never reports itself as a Phase 1 regression)
experiments/   ledger.jsonl + one directory per experiment (config + result)
reports/       human-readable task deliverables
taskcards/     the cards, committed before their loop runs
data/research/ research data root — nothing dated 2025-03-01 or later
data/live_week/ quarantined Phase 1 live week, mechanical mode only
verify2/       the research gate. Deny-edited.
```

## Environment additions

`statsmodels`, `scipy`, `scikit-learn`, `matplotlib`, `duckdb` may be installed
into the pinned interpreter when a task card needs them; **none are installed
yet**. Justify each here when it lands. Nothing that phones home. No notebooks
in the loop — scripts and reports only, so everything is diffable and
reproducible.
