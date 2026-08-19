# HANDOFF — Phase 1 (`fxlab`)

Phase 1 is complete: the offline gate passes, and one real week of EURUSD ticks
has been pulled from the live Dukascopy feed through the identical decode,
validate and store path and cross-checked against OANDA.

```
E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe -E -s verify\smoke_test.py fxlab
GATE PASS   (exit 0)
```

This document is written for whoever bootstraps Phase 2. It says what exists,
what the live feed actually does, what is deliberately unfinished, and which
questions Phase 2 has to answer before building research on top.

---

## 1. What was built

| package | what it does |
|---|---|
| `fxlab.ingestion` | Dukascopy bi5 fetch and decode, tick validation, Parquet store, bar resampling, read-only OANDA client, and the pipeline that runs them in order |
| `fxlab.costs` | `CostModel` protocol, `IBCostModel`, and a `RecordedSpreadCostModel` that proves the protocol takes a second implementation |
| `fxlab.backtest` | event-ordered multi-pair engine, reference MA-cross strategy, results serialisation |
| `fxlab.recorder` | `Feed` protocol, replayable `FakeFeed`, `IBFeed` stub, and a rotating crash-safe tick recorder |
| `fxlab.config` | typed TOML configuration; no path, host or secret is hardcoded anywhere |

Entrypoints:

```
python -m fxlab.ingest      --config <cfg.toml>     # fetch/decode/validate/store
python -m fxlab.crosscheck  --config <cfg.toml>     # OANDA H1 vs stored hourly bars
python -m fxlab.report      --config <cfg.toml>     # coverage, gaps, spread by session
python -m fxlab.backtest    --config <cfg.toml>     # reference strategy over stored bars
```

### Decisions that will matter downstream

* **The FX week is derived, never hardcoded.** It runs Sunday 17:00 to Friday
  17:00 `America/New_York` — 21:00 UTC in northern summer, 22:00 UTC in winter.
  Everything that asks "was the market open" goes through
  `fxlab.ingestion.sessions`. A fixed UTC hour is wrong for roughly half of
  every year and fails silently.
* **Gross P&L is measured mid to mid**, and the spread crossed is booked as its
  own cost line, so `net = gross - (spread + commission)` holds exactly and the
  spread is auditable rather than buried in a fill price.
* **A signal computed on bar `t` cannot fill before bar `t+1` open**, and the
  engine has no way to construct a price without going through the cost model.
  Bar 0 can therefore never trade, whatever a strategy asks for.
* **Bar timestamps are bar OPEN times**, each bar covering `[open, open + delta)`.
* **Arrow schemas are pinned field by field**, because pandas 3 changed the
  default string dtype and an inferred schema drifts between versions silently.
* **Duplicates are dropped and counted; failures are recorded as gaps.** Nothing
  is ever silently skipped, so a hole in the data is visible in the manifest
  rather than inferred later from a suspicious backtest.

---

## 2. Contracts Phase 2 will consume

### Tick Parquet

`<out_dir>/ticks/pair=<PAIR>/date=<YYYY-MM-DD>/<PAIR>_<DATE>_<HH>h.parquet`

| column | Arrow type |
|---|---|
| `pair` | `large_string` |
| `ts` | `timestamp[us, tz=UTC]` |
| `bid` | `double` |
| `ask` | `double` |
| `bid_volume` | `double` |
| `ask_volume` | `double` |
| `source` | `large_string` |

`source` is `dukascopy` for research ticks and `ib_live` for recorded ones; the
layout and schema are identical so both are read by the same code.

### Bar Parquet

`<out_dir>/bars/timeframe=<TF>/pair=<PAIR>/<PAIR>_<TF>.parquet`, carrying
`pair`, `ts`, bid/ask/mid OHLC, `tick_count`, `spread_mean` and `spread_max`.
Bins with no ticks are dropped from the table and reported separately as gaps;
they are never emitted as rows of `NaN`.

### Manifest

`<out_dir>/manifest.json` — one entry per requested hour including the empty
ones, with `status` (`ok` / `empty` / `closed` / `gap`), `decoded_ticks`,
`written_ticks`, `duplicates_dropped`, `sha256`, `compressed_bytes`, the
per-hour spread percentiles, and any issues found; plus a `coverage` block per
pair-day and a `validation` block holding errors and warnings.

Named rejection reasons, emitted verbatim on stderr and recorded in the
manifest: `CROSSED_QUOTE`, `NON_POSITIVE_PRICE`, `CLOSED_MARKET_TICK`,
`TICK_OUTSIDE_HOUR`, `DECODE_ERROR`, `FETCH_ERROR`. Warning-level:
`SPREAD_OUTLIER`, `TICK_COUNT_OUTLIER`, `EMPTY_TRADING_HOUR`.

### Backtest results

`{"summary": {...}, "trades": [...], "equity": [{"ts", "equity"}]}`, where
`summary` carries `trade_count`, `gross_pnl`, `spread_cost`, `commission`,
`total_costs`, `net_pnl`, `max_drawdown` and a `by_pair` breakdown, and every
trade carries its own `spread_cost` and `commission`.

---

<!-- LIVE-FINDINGS -->

---

## 4. What the live feed actually does

Everything in this section was observed during the week pull above, not read in
documentation.

* **The month in the URL is zero-based.** `/2026/07/12/` is 12 **August** 2026.
  Confirmed independently of any decoding: the response carries a
  `Last-Modified` header naming the true date.
* **An empty body with HTTP 200 means the market was closed**, and a 404 means
  the hour is genuinely absent. Conflating them either manufactures gaps across
  every weekend or hides real holes, so they are recorded as different statuses.
* **Sustained fetching earns HTTP 503.** It is throttling served as an HAProxy
  page, not an application error. Four connections in flight provoked it within
  a couple of minutes on this connection; two did not.
* **The transport itself is unreliable here.** Connect timeouts (`WinError
  10060`) and resets (`WinError 10054`) arrived in bursts throughout, unrelated
  to the 503s. Retries with exponential backoff absorbed them.
* **A datacenter or VPN egress IP is rejected outright** by the datafeed front
  end with 403 while `www.dukascopy.com` keeps working. That failure looks
  exactly like a routing fault and is not one; the client says so in the error.

---

## 5. Known issues and deliberate limitations

1. **Commission is charged in the quote currency.** IB tier 1 is 0.20 bp with a
   USD 2.00 per-order minimum. `IBCostModel` computes notional as
   `units * fill_price`, which is USD only for USD-quoted pairs. For JPY-quoted
   pairs the floor is applied to a JPY notional and therefore overstated. Doing
   this properly needs a cross rate at fill time — a Phase 2 concern, but one
   that must be fixed before any JPY-cross result is believed.
2. **A position reversal is executed as two orders**, so it attracts two
   commissions where a single netting order would attract one. That errs towards
   charging too much, which is the safe direction, but it is a modelling choice
   rather than a fact about IB.
3. **A position still open on the final bar is closed at that bar close**, so
   that every cost paid belongs to a trade and the summary reconciles exactly.
   Strategies that would hold through the end of the sample will show a trade
   they did not ask for.
4. **`RecordedSpreadCostModel` is a shape, not a calibration.** It implements
   the protocol and is tested, but no venue-true spreads exist yet to feed it.
   That is what the tick recorder is for, and it needs IB market data.
5. **`IBFeed` has never held a live connection.** It compiles and is tested
   against the `Feed` protocol; `ib_async` is not installed, and Phase 1 was
   explicitly not gated on it. Validate it by hand once IB approves the account.
6. **The tick-count outlier check is coarse**, flagging a day outside a quarter
   to four times the trailing median. It exists to catch a truncated download,
   not to model volume seasonality.
7. **Bars are rebuilt from the whole stored history** for a pair each time
   `bar_timeframes` is set. That is fine for a week and will not be fine for a
   decade; incremental bar building is a Phase 2 job.
8. **Only TOML configuration is supported.** `spec.md` allowed YAML or TOML;
   PyYAML is not in the pinned environment and one config format is one fewer
   thing to get wrong.

---

## 6. Open questions for Phase 2

1. **How far back does the universe actually go?** The pipeline is
   date-range-driven and extends to 2005 without a code change, but coverage
   before ~2015 has not been measured pair by pair. Doing that is a cheap first
   Phase 2 task and it bounds every backtest that follows.
2. **How stable is the ECN-versus-retail spread relationship across pairs and
   regimes?** It was measured on EURUSD over one week. If the offset moves with
   volatility, a cost model calibrated on calm weeks will flatter every
   strategy tested on stressed ones.
3. **What is the right cost stress ladder?** `cost_multiplier` exists and
   defaults to 1.0. Phase 2 should decide up front which multiples a candidate
   has to survive, before seeing any results, or the choice becomes a way to
   rescue a strategy that failed.
4. **How are holidays distinguished from gaps?** An empty body inside the
   trading week is currently a warning (`EMPTY_TRADING_HOUR`). Some of those are
   real holidays, and research over a multi-year window needs a calendar rather
   than a warning.
5. **Which bar timeframe is the research unit?** The reference strategy runs on
   1h bars because that was enough to exercise the plumbing. Nothing about the
   universe selection has been done yet.
6. **Does the IB paper account clear IDEALPRO cash FX, or only CFDs?** The whole
   cost model assumes IDEALPRO. This is a UK-retail permissions question and it
   invalidates the cost basis if the answer is CFDs.

---

## 7. Running it

```
env_fxlab\Scripts\python.exe -m fxlab.ingest     --config config\ingest_fixture.toml
env_fxlab\Scripts\python.exe -m fxlab.ingest     --config config\ingest_live_week.toml
env_fxlab\Scripts\python.exe -m fxlab.crosscheck --config config\ingest_live_week.toml
env_fxlab\Scripts\python.exe -m fxlab.report     --config config\ingest_live_week.toml
env_fxlab\Scripts\python.exe -m fxlab.backtest   --config config\backtest_example.toml
```

`OANDA_API_TOKEN` is required for the cross-check and nothing else;
`OANDA_ENV` selects `practice` (default) or `live`. No other credential is
read anywhere in this repository.
