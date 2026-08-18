# spec.md — Phase 1: FX data & research plumbing (`fxlab`)

> Harness-builder: update the ⚠ marked facts with Phase 0 findings before this
> spec is used for a build run.

## Goal

An end-to-end, validated data pipeline and backtester skeleton that unblocks
multi-pair FX research. No strategies, no live trading — plumbing only, built
production-quality.

## Package layout

```
fxlab/
  ingestion/    # Dukascopy ticks, OANDA candles, validation, Parquet store
  costs/        # IB cost model
  backtest/     # event-ordered backtester skeleton + reference strategy
  recorder/     # tick recorder against a Feed interface (fake feed for tests)
  config/       # config loading (YAML/TOML; no hardcoded paths or secrets)
tests/
config/
```

Python 3.12, full type hints, PEP8, docstrings, logging (no prints), pytest.

## Universe & ranges (initial)

- Pairs (12): EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD,
  EURGBP, EURJPY, GBPJPY, EURCHF, AUDJPY
- Dukascopy ticks: 2015-01-01 → present (config-driven; must be extendable
  back to ~2005 later without code change)
- OANDA candles (cross-check only): H1 and D, same range
- All timestamps stored tz-aware UTC. FX week: Sunday ~21:00 UTC open to
  Friday ~21:00 UTC close (verify exact boundaries against real data ⚠).

## Ingestion — Dukascopy (primary)

- Fetch hourly bi5 files over HTTPS with retry/backoff, polite concurrency
  (≤ 4 in flight), resumable: already-downloaded hours are skipped via the
  manifest, never re-fetched.
- ⚠ URL pattern: `https://datafeed.dukascopy.com/datafeed/{PAIR}/{YYYY}/{MM}/{DD}/{HH}h_ticks.bi5`
  — CONFIRM in Phase 0, including the month-is-zero-indexed quirk if observed.
- ⚠ Binary layout per tick record (confirm by decoding real data):
  ms-offset-in-hour (uint32), ask (uint32), bid (uint32), ask-vol (float32),
  bid-vol (float32), big-endian; price scaling is per-pair point value
  (JPY pairs differ) — derive scaling from data, assert against sane ranges.
- Decode → validate → normalize → write Parquet. A failed/corrupt hour is
  recorded in the manifest as a gap, never silently skipped.

## Validation rules (hard failures unless noted)

- ask ≥ bid > 0; spread within a per-pair sanity ceiling (warn-level log above
  p99.9, hard-fail on negative)
- timestamps strictly UTC, non-decreasing per pair; exact duplicates dropped
  and counted in the manifest (count reported, not silent)
- no ticks during closed market; weekend boundary handled explicitly
- daily tick-count outliers vs trailing median flagged in manifest (warn)
- OANDA cross-check job: hourly mid from Dukascopy resample vs OANDA H1 mid;
  report distribution of differences; flag hours beyond a config threshold

## Storage

- Parquet, partitioned: `data/ticks/pair=X/date=YYYY-MM-DD/*.parquet`
- Resampled bars built FROM local ticks (never re-downloaded): 1m, 5m, 1h —
  bid/ask/mid OHLC + tick count + mean/max spread per bar. Bar timestamp =
  bar OPEN time; bar covers [open, open+Δ). State this convention in code and
  docs — off-by-one-bar conventions are a classic lookahead source.
- `data/manifest.duckdb` (or JSON) recording per pair/day: tick counts, gaps,
  duplicates dropped, validation flags, source checksums.

## Cost model (fxlab/costs)

- `CostModel` protocol: given (pair, side, size, quote at decision,
  session/time) → execution price and commission.
- `IBCostModel`: fill by crossing the quoted spread (buy at ask, sell at bid)
  + IB tiered commission: 0.20 bp of trade value, min $2.00 per order
  (tier 1 defaults; all parameters config-driven, with a `cost_multiplier`
  knob defaulting to 1.0 for stress runs at 1.5×/2×).
- Design so a future `RecordedSpreadCostModel` (from the IB tick recorder) can
  drop in without touching backtester code.

## Backtester skeleton (fxlab/backtest)

- Event-ordered bar loop, strict rule: signals computed on bar t may act no
  earlier than bar t+1's prices. Fills cross the spread via the cost model.
  No vectorized shortcuts that could peek.
- Multi-pair aware from day one: positions, P&L, and equity are per-pair and
  portfolio-level.
- Reference strategy (plumbing test only, NOT research): e.g. fixed-size MA
  cross on 1h bars. Its purpose is exercising order → fill → cost → P&L.
- Outputs: trades table (entry/exit time+price, size, side, spread cost,
  commission), equity curve, summary (net P&L, gross P&L, total costs, trade
  count, max drawdown). Costs must reconcile: sum of per-trade costs ==
  gross − net exactly.

## Tick recorder (fxlab/recorder)

- `Feed` protocol (subscribe(pairs) → async stream of bid/ask ticks) with:
  `FakeFeed` (replays fixture ticks; used in tests/gate) and `IBFeed` stub
  (ib_async-based; compiles, unit-tested against interface, but live
  connection NOT required for Phase 1 done — validated manually post
  IB approval).
- Recorder writes the same tick schema/Parquet layout as ingestion, tagged
  `source=ib_live`, with rotation and crash-safe flush.

## Definition of done

The gate (`verify/smoke_test.py fxlab`) exits 0, AND a manual live check
succeeds: one real week of EURUSD ticks downloads from Dukascopy, passes the
identical validation, and the OANDA cross-check runs against it (token
permitting). Produce `HANDOFF.md` summarizing: what was built, live-run
findings (coverage, gap stats, spread distributions by session), known issues,
and open questions — this feeds the Phase 2 bootstrap.

## Out of scope for Phase 1

Strategies and research signals; IB order execution; Gateway/IBC automation;
EDA beyond the validation stats; any live-capital anything.
