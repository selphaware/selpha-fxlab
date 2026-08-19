# selpha-fxlab

FX data and research plumbing for a multi-pair strategy search.

**Phase 1 (complete)** is deliberately narrow: ingest and validate tick data,
store it with a pinned schema, model execution costs, and run an event-ordered
backtester over the result. No strategies, no live trading.

**Phase 2 (in progress)** is the research phase: EDA across a twelve-pair
universe, walk-forward validation with purge and embargo, and a sealed holdout.
It runs as bounded task cards judged by a second gate, `verify2/`, whose job is
not to decide whether a finding is true -- nothing binary can -- but to make
leakage, holdout breach, non-reproducibility, unledgered trials and cost-model
drift mechanically impossible to hide.

The roadmap lives in `PLAN.md`, the Phase 1 scope in `SPEC.md`, the Phase 2 law
in `SPEC2.md`, and the build conventions and deliverable contracts for both in
`CLAUDE.md`.

## Layout

```
fxlab/
  ingest.py       python -m fxlab.ingest      --config <cfg.toml>
  backtest/       python -m fxlab.backtest    --config <cfg.toml>
  crosscheck.py   python -m fxlab.crosscheck  --config <ingest_cfg.toml>
  report.py       python -m fxlab.report      --config <ingest_cfg.toml>
  ingestion/      Dukascopy bi5 fetch/decode, validation, Parquet store,
                  bar resampling, read-only OANDA client
  costs/          CostModel protocol and the IB-calibrated implementation
  backtest/       event-ordered multi-pair engine and the reference strategy
  recorder/       Feed protocol, replayable fake feed, IB stub, tick recorder
  config/         TOML config loading; no path or secret is hardcoded
tests/            pytest unit tests
config/           example configurations
verify/           the Phase 1 offline gate, frozen fixtures (do not edit)

research/         Phase 2 analysis code -- the judged surface
  seal.py         the holdout cutoff, one definition
  loader.py       the only way research reads data; scoring | mechanical modes
  ledger.py       append-only experiment record, written before results exist
  walkforward.py  purged and embargoed walk-forward splitting and execution
  run.py          python -m research.run --config <experiment.toml>
tests2/           research unit tests, kept out of tests/ on purpose
experiments/      ledger.jsonl and one directory per experiment
reports/          human-readable task deliverables
taskcards/        the bounded task definitions, committed before their loop runs
verify2/          the research gate and its known answers (do not edit)
```

## Running it

```
env_fxlab\Scripts\python.exe -m fxlab.ingest     --config config\ingest_fixture.toml
env_fxlab\Scripts\python.exe -m fxlab.ingest     --config config\ingest_live_week.toml
env_fxlab\Scripts\python.exe -m fxlab.crosscheck --config config\ingest_live_week.toml
env_fxlab\Scripts\python.exe -m fxlab.report     --config config\ingest_live_week.toml
env_fxlab\Scripts\python.exe -m fxlab.backtest   --config config\backtest_example.toml
```

`mode = "fixture"` reads local bi5 files and never touches the network;
`mode = "live"` fetches from the Dukascopy datafeed with bounded concurrency
and exponential backoff. Both run the identical decode, validate and store
path.

The gate that decides whether the deliverable is correct:

```
env_fxlab\Scripts\python.exe -E -s verify\smoke_test.py fxlab
```

Exit 0 means pass. Exit 2 means the harness is broken and exit 3 means the
machine is, neither of which is a problem with `fxlab/`.

The Phase 2 gate, which judges one completed experiment and runs the Phase 1
gate as its regression check:

```
env_fxlab\Scripts\python.exe -E -s verify2\research_gate.py experiments\<id>
env_fxlab\Scripts\python.exe -E -s verify2\research_gate.py --selftest
```

The selftest is worth watching once: it builds a good experiment and then one
deliberately broken variant per failure mode -- a leaky walk-forward, a
sealed-date read, an unseeded run, a result with no ledger entry, a zero-cost
scorecard, a JPY score before the USD-accounting fix -- and asserts the gate
names the right reason for each.

## Conventions that are load-bearing

* **Timestamps are tz-aware UTC everywhere.** A naive datetime shifts by the
  local offset at every session boundary and nothing complains.
* **The FX week is derived, never hardcoded.** It runs Sunday 17:00 to Friday
  17:00 `America/New_York`, which is 21:00 UTC in northern summer and 22:00 UTC
  in winter. A fixed UTC hour is wrong for half of every year.
* **A bar timestamp is the bar OPEN**, and the bar covers `[open, open + delta)`.
* **Gross P&L is measured mid to mid**, so the spread crossed is an explicit,
  auditable cost line rather than a haircut hidden in the fill price:
  `net = gross - (spread + commission)`.
* **A signal computed on bar t cannot fill before bar t+1 open**, and every
  fill crosses the spread through the cost model. The engine has no way to
  construct a price of its own.
* **The Parquet schemas are pinned field by field.** pandas 3 changed the
  default string dtype; inferred schemas drift silently between versions.
* **Duplicates are dropped and counted**, never swallowed. A failed hour is
  recorded in the manifest as a gap, never skipped.

## Secrets

Nothing here reads a credential from a file or a constant. The OANDA token
comes from `OANDA_API_TOKEN` and `OANDA_ENV` selects practice (the default) or
live; the client is restricted to read-only instruments and candles endpoints.
IB credentials never enter this repository at all.
