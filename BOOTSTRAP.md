# BOOTSTRAP — generate a build-loop harness for this project

You are going to build a **harness**: the deterministic scaffolding that lets an
agent build this project autonomously and verifiably. You are NOT building the
application yet. You are building the thing that will judge the application.

Read this whole file before doing anything.

---

## The project

**What it is:**
> Phase 1 of `selpha-fxlab`: the data + research plumbing for a multi-pair FX
> trading research system. Pure Python 3.12 (typed, PEP8, docstrings). One
> importable package `fxlab` with subpackages:
> - `fxlab/ingestion/` — Dukascopy tick downloader/decoder + OANDA candle
>   fetcher, data validation, normalization to one schema, Parquet store
> - `fxlab/costs/` — IB-style cost model (raw spread + tiered commission)
> - `fxlab/backtest/` — event-ordered backtester skeleton using the cost model
> - `fxlab/recorder/` — IB live bid/ask tick recorder (built against a feed
>   interface with a replayable fake feed; live IB connection is validated
>   manually later, NOT by the gate)
> No web frontend. No server. This is a data-pipeline-shaped project (shape ③
> in the setup guide): the gate runs the pipeline on fixture input and judges
> the outputs.

**Data source:**
> Two sources, but the GATE MUST BE FULLY OFFLINE (see Phase 1 notes):
> 1. Dukascopy public historical feed — free, no account, no API key. Hourly
>    LZMA-compressed binary tick files ("bi5") fetched over plain HTTPS from
>    `datafeed.dukascopy.com`, decoded to bid/ask ticks. Verify the real URL
>    pattern and binary layout in Phase 0 by fetching ONE hour of EURUSD and
>    decoding it — the live feed is the truth, not this brief.
> 2. OANDA v20 REST API (fxpractice) — bid/ask/mid candles, token auth.
>    Token will be provided as environment variable `OANDA_API_TOKEN` (never
>    hardcoded, never committed). If the token is not set yet, build the
>    client anyway and skip live verification with a clear note.

**The deliverable:**
> The `fxlab` package, runnable end-to-end on fixture data:
> - `python -m fxlab.ingest --config <cfg>` → downloads (or, in fixture mode,
>   reads local raw bi5 files), decodes, validates, writes Parquet
> - a documented Parquet layout: ticks partitioned per pair/date, plus
>   resampled bars, with a `manifest` recording coverage and gap statistics
> - `python -m fxlab.backtest --config <cfg>` → runs a trivial built-in
>   reference strategy over fixture bars and writes a results file (trades,
>   equity, cost breakdown)
> - unit tests under `tests/` (pytest)
> The gate's argument is the package dir `fxlab` (it runs both entrypoints in
> fixture mode and judges their outputs).

**What "working" looks like to a user:**
> From a clean checkout: run ingest in fixture mode → Parquet files appear with
> the documented schema; every tick has ask ≥ bid > 0; timestamps are strictly
> UTC, monotonic per pair, deduplicated; weekend rows absent; the manifest
> reports coverage and any gaps honestly. Run the backtest entrypoint → a
> results file with trades whose fills are priced at bid/ask (not mid), a
> nonzero cost total that reconciles with the cost model applied trade-by-trade,
> and an equity curve consistent with the trade list. Then, pointing the same
> ingest code at the live network (manual, not the gate), a real day of EURUSD
> downloads and passes the identical validation.

**What "broken but passes tests" looks like:**
> The failure modes I actually fear — each would run green and look plausible:
> 1. **Silent timestamp corruption**: bi5 hour-offset decoded wrong or local-DST
>    handling shifts everything by 1h — session/spread analysis is then subtly
>    wrong forever. Gate must compare decoded fixture timestamps against known
>    ground-truth values captured at fixture-freeze time.
> 2. **Silent data loss**: decoder drops ticks (bad LZMA chunk skipped,
>    duplicate-drop too aggressive) but pipeline reports success. Gate must
>    assert exact expected tick counts per fixture hour, not just "> 0".
> 3. **Impossible quotes pass through**: bid ≥ ask, zero/negative prices, or
>    weekend ticks present in output. Gate asserts these are absent AND that the
>    validator provably rejects a poisoned fixture containing them.
> 4. **Lookahead in the backtester**: orders filled at the same bar's price that
>    generated the signal, or at mid instead of crossing the spread. Gate runs
>    the reference strategy on a constructed fixture where correct
>    next-bar/bid-ask execution produces a known P&L and lookahead/mid-fill
>    produces a detectably different one; assert the known value.
> 5. **Cost model silently zero**: backtest "profits" because commission/spread
>    return 0.0 on a config mismatch. Gate asserts total costs > 0 and equal the
>    independently recomputed sum over the trade list (including the $2/order
>    IB minimum kicking in on a deliberately small fixture trade).

**Environment (fill in what you already know):**
> - Python interpreter: `E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe`
>   **— this venv already exists; do not create a new one.** Verify it reports
>   Python 3.12.x and has pytest, pandas, pyarrow importable.
> - Project root: `E:\CODE\selpha-fxlab` (local disk). NOTE: the venv lives
>   INSIDE the repo directory. In Phase 0, verify `.gitignore` covers
>   `env_fxlab/` (fail loudly if not) and treat `env_fxlab/` as strictly
>   read-only except for pip installs via the interpreter — never edit, list
>   into reports, or commit anything under it.
> - Node/npm needed? **No.** Dukascopy fetch/decode is implemented directly in
>   Python (requests/httpx + stdlib `lzma`) — we do NOT depend on
>   dukascopy-node.
> - Already installed (gate dependencies): pytest, pandas, pyarrow
>   (no Playwright — nothing to screenshot in this project)
> - You may install (app runtime): httpx/requests, numpy, duckdb, and similar —
>   install into the interpreter above, never into a new venv. Justify anything
>   non-obvious in CLAUDE.md.
> - Anything else: no DB server, no ports. Network access exists on this
>   machine but the gate must not use it (fixture-only). Secrets only via env
>   vars (`OANDA_API_TOKEN` now; IB credentials NEVER go in this repo at all —
>   they live in the IBC config outside the repo, Phase 3 concern).

> **Note on the venv:** it is created by the user *before* this session, because
> the gate self-tests using that interpreter. An agent that builds its own
> verification environment has nothing trustworthy to verify with. App runtime
> packages are different — install those as you discover you need them.

---

The "broken but passes tests" line is the most important thing on this page.
**It defines the gate, and the gate is the whole system.**

---

## Phase 0 — Discover the environment. Do not skip. Do not assume.

Assumptions about the environment are the single biggest source of wasted
iterations. Establish facts first, and **write them down** in the harness.

1. **Find the real Python interpreter.**
   - On Windows, bare `python` may resolve to the Microsoft Store alias stub at
     `...\WindowsApps\python.exe`, which is a 0-byte shim that cannot execute.
     Detect and reject it explicitly.
   - Report the absolute path you will use. Verify with `<path> --version`
     (must be 3.12.x).
2. **Check the project location.** Resolve the working directory to an absolute
   path. If it is a mapped network drive or UNC path, say so.
3. **Inspect the data source once — this is the fixture-freeze step.**
   - Fetch ONE hour of EURUSD bi5 from the live Dukascopy feed. Print the URL
     used, the HTTP status, the compressed size. (A 403 is as likely to be a
     missing `User-Agent` header as a firewall — diagnose before blaming the
     network.)
   - Decode it. Print the first and last 3 ticks (timestamp, bid, ask, sizes)
     and the tick count. Sanity-check the prices against the known EURUSD range
     for that hour — this catches wrong binary layout or wrong point-scaling
     immediately.
   - Repeat for enough hours to build the fixture set (see Phase 1), then
     **freeze**: store the raw bi5 bytes under `verify/fixtures/raw/` and write
     `verify/fixtures/expected.json` with per-hour tick counts and first/last
     tick values. From this moment the gate never touches the network.
   - If `OANDA_API_TOKEN` is set, fetch one day of EURUSD H1 candles from
     fxpractice and record the response shape; if unset, note it and move on.
4. **Report all of the above back to me before writing any files.**

---

## Phase 1 — Design the gate FIRST

The gate is the harness. Everything else is support. Build it before anything
else.

**The gate must catch the five failure modes named above**, not merely confirm
the code runs.

For this project the gate must (all offline, all against `verify/fixtures/`):

1. **Run the ingest entrypoint in fixture mode** on the frozen raw bi5 files.
2. **Judge the Parquet output:**
   - exact tick counts per hour match `expected.json` (failure mode 2)
   - first/last tick timestamps and prices match `expected.json` to the tick
     (failure mode 1 — a 1h DST shift or ms/offset bug cannot survive this)
   - all timestamps UTC, strictly non-decreasing per pair, no duplicates
   - ask ≥ bid > 0 everywhere; no Saturday/closed-market rows (failure mode 3)
   - schema matches the documented layout exactly (names, dtypes, tz)
3. **Prove the validator rejects poison**: run ingest on the deliberately
   corrupted fixture (crossed quotes, weekend rows, duplicated block — built at
   harness time) and assert it FAILS with the right named reason.
4. **Run the backtest entrypoint** on the constructed backtest fixture and
   assert the known-answer P&L, cost total (with the $2 minimum case), and
   trade-by-trade cost reconciliation (failure modes 4 and 5).
5. **Run pytest** as a final step — but the output checks above are the gate's
   core; unit tests alone are exactly the "passes tests but broken" trap.

Requirements for the gate:

- **Binary.** Exit 0 = pass, non-zero = fail. No warnings tier, no partial
  credit.
- **Specific failure messages.** Not "validation failed" but
  `"EURUSD 2026-07-14T13:00 fixture hour: expected 4,217 ticks, got 4,105 —
  decoder is dropping ticks"`. The message is fed back to the agent; a vague
  message costs an iteration.
- **Fast.** Seconds, not minutes. Size the fixture accordingly (a few hours ×
  2 pairs of ticks, not days). If it creeps past ~30s, shrink the fixture.
- **Fully offline.** Any network attempt during the gate is itself a failure —
  monkeypatch/guard the HTTP layer in fixture mode and assert no calls escape.
- **Thresholds derived from the frozen fixture**, not invented.
- **Distinguishes harness failure from deliverable failure.** Missing fixture
  files, wrong interpreter, missing pytest → "HARNESS ERROR — this is not a
  deliverable problem", and do NOT blame the code.

**Standard layout — do not deviate:** the gate lives at `verify/smoke_test.py`,
takes the deliverable path (`fxlab`) as its argument, supports a `--selftest`
flag, and writes any artifacts to `verify/artifacts/`.

**Then prove the gate works.** Add a `--selftest` mode that:
- runs a known-GOOD reference implementation stub against the fixtures and
  asserts the gate passes it
- runs known-BROKEN variants — one per feared failure mode above (timestamp
  shifted +1h; 5% of ticks dropped; crossed quotes passed through; lookahead
  fill; zeroed cost model) — and asserts the gate fails each one **naming the
  right reason**
- runs from a temp dir and from the project dir, and reports if they disagree

Run the self-test. **A gate you have not watched fail is not a gate.** Do not
proceed until it discriminates all five.

---

## Phase 2 — Build the rest of the harness

### `CLAUDE.md` — always-loaded conventions
- The definition of done, stated as the literal gate command.
- The contract the deliverable must satisfy, as a table (schema, entrypoints,
  invariants).
- The environment facts from Phase 0 (absolute interpreter path, fixture
  locations, the live Dukascopy URL pattern discovered).
- Guardrails: no secrets in code or commits (env vars only); no editing the
  harness; no network in gate/fixture mode; scope of writes is `fxlab/`,
  `tests/`, `config/`.
- Git conventions: the agent commits after each gate-green milestone with a
  descriptive message; pushes via SSH (`git push origin main`) are allowed —
  the user has an ssh-agent session, do not prompt for credentials.
- An autonomy clause: work through gate failures independently; only stop if
  the same failure repeats 3 times or an external dependency is unreachable.

### The verification hook — the inner loop
- A `PostToolUse` hook matching `Write|Edit` that runs the gate.
- Exit 2 on failure, gate output on stderr; exit 0 for harness errors.
- **Pin the interpreter with an absolute path in the hook command.**
- Python stdlib only for the hook script itself.

### A skill — the reusable procedure
The steps to build a validated market-data pipeline of this kind, in order,
with a "common failures and their real causes" table (bi5 decoding traps,
tz/DST, Parquet dtype drift, pandas silent NaN propagation).

### `settings.json`
- Register the hook with the absolute interpreter path.
- Allowlist the tools needed to run unattended (including git commit/push).
- **Deny edits to `verify/`** — a self-verifying agent that can edit its own
  judge will eventually edit its own judge. `verify/fixtures/` included.
- **Deny edits to `env_fxlab/`** — the venv is part of the judge's machinery
  (the gate runs on that interpreter). Pip installs happen via the interpreter
  command, not by writing files there.

### The task spec (`spec.md`)
A draft `spec.md` is provided alongside this bootstrap. During harness build,
update it with the real facts discovered in Phase 0 (true bi5 URL pattern and
binary layout, real fixture hours chosen, OANDA response shape if the token was
present) — do not leave placeholders in it.

### An outer loop script (optional)
Only after the interactive loop is proven.

---

## Phase 3 — Prove the whole thing

Before you tell me it is done:

1. `--selftest` passes: the gate discriminates good from broken for **all five**
   named failure modes, naming the right reason each time.
2. The hook logic is proven **by direct invocation** — pipe a simulated
   tool-use JSON payload into the hook script on stdin and confirm: exit 2 with
   a useful message against a broken variant, exit 0 against the good stub, and
   exit 0 with "HARNESS ERROR" for a missing fixture/dependency.
   **Important:** the live hook CANNOT fire in this session — hooks register at
   session start. Say so in the final report; the user restarts Claude Code and
   runs the live-fire test themselves.
3. Every absolute path in the harness has been verified to exist and run.
4. The gate has been demonstrated to run with networking blocked (fixture-only
   guarantee holds).
5. Report: the exact gate command for the kickoff prompt, the interpreter path,
   the hook status (incl. restart requirement), the live Dukascopy facts
   discovered in Phase 0 (URL pattern, binary layout, point scaling per pair),
   and anything that contradicted this brief.

Then stop. Do not build the application. I will start that in a fresh session.

---

## Rules

- **Verify, don't assert.** Every claim about the environment must come from a
  command you ran, not from what is usually true.
- **Tell me when this brief is wrong.** If Phase 0 contradicts something above
  (URL pattern, binary layout, scaling), say so plainly and update `spec.md`
  rather than working around it silently.
- Prefer fewer moving parts. Every dependency is something that can drift.
