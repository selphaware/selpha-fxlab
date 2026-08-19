# HANDOFF — Phase 1 (`fxlab`)

Phase 1 is complete. The offline gate passes, and one real week of EURUSD ticks
has been pulled from the live Dukascopy feed through the identical decode,
validate and store path, then cross-checked against OANDA.

```
E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe -E -s verify\smoke_test.py fxlab
GATE PASS   (exit 0)   202 unit tests pass
```

The live week in one line: **205,088 EURUSD ticks across 120 open hours, zero
gaps, zero duplicates, mids agreeing with OANDA to a median of 0.000 pips.**

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

## 3. The live run

One complete FX week of EURUSD, pulled from the live Dukascopy feed on
2026-08-19 through the same decode, validate and store path the offline gate
exercises:

```
python -m fxlab.ingest     --config config/ingest_live_week.toml
python -m fxlab.crosscheck --config config/ingest_live_week.toml
python -m fxlab.report     --config config/ingest_live_week.toml
python -m fxlab.backtest   --config config/backtest_example.toml
```

### Coverage

| | |
|---|---|
| Hours requested | 144 (all 24 hours of the six calendar days the week touches) |
| Hours with ticks | **120** |
| Hours the feed served empty | **24** |
| Gaps after resume | **0** |
| Ticks stored | **205,088** |
| Exact duplicates dropped | **0** |
| First tick | `2026-08-09T21:00:17.798Z` |
| Last tick | `2026-08-14T20:59:59.802Z` |

| date | requested | with ticks | closed | ticks |
|---|---|---|---|---|
| 2026-08-09 (Sun) | 24 | 3 | 21 | 1,951 |
| 2026-08-10 (Mon) | 24 | 24 | 0 | 38,391 |
| 2026-08-11 (Tue) | 24 | 24 | 0 | 36,420 |
| 2026-08-12 (Wed) | 24 | 24 | 0 | 45,491 |
| 2026-08-13 (Thu) | 24 | 24 | 0 | 40,639 |
| 2026-08-14 (Fri) | 24 | 21 | 3 | 42,196 |

### The week boundary, confirmed against the feed rather than against a comment

The 24 hours the feed served as empty bodies were **exactly** Sunday
00:00–20:00 UTC and Friday 21:00–23:00 UTC. The 120 hours carrying ticks were
**exactly** Sunday 21:00 UTC through Friday 20:00 UTC.

That is precisely the window `fxlab.ingestion.sessions` derives from 17:00
`America/New_York`, and the boundary ticks sit inside it by seconds: the first
tick of the week arrived 17.8 seconds after the derived open and the last one
0.2 seconds before the derived close.

A hardcoded 22:00 UTC rule -- the winter boundary, and a perfectly reasonable
thing to write down in January -- would have rejected all 293 ticks of the
Sunday 21:00 hour as closed-market and failed the run, while expecting data in
the Friday 21:00 hour the feed served empty. The derivation is not decoration.

### Spread by session (pips)

| session | ticks | p50 | p75 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| all | 205,088 | 0.30 | 0.40 | 0.50 | 1.30 | 13.70 |
| tokyo | 44,052 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 |
| london | 46,643 | 0.30 | 0.40 | 0.40 | 0.50 | 1.20 |
| london_ny_overlap | 60,627 | 0.30 | 0.40 | 0.40 | 0.60 | 3.60 |
| new_york | 46,265 | 0.30 | 0.40 | 0.40 | 0.80 | 3.00 |
| sydney | 7,501 | 0.50 | 1.40 | 3.80 | 6.40 | 13.70 |

The median is 0.30 pip almost everywhere, matching the ECN spread `SPEC.md`
records. The interesting column is the tail. All four liquid sessions stay
inside 1 pip at the 99th percentile, while the thin hours around the daily roll
and the weekly reopen (`sydney` here) run **1.7x wider at the median and eight
to nine times wider at p90**, on 4% of the tick volume. Any strategy that trades
in those hours pays a completely different cost, and a single average spread
would hide that entirely.

Tick counts by UTC hour show the same shape from the other side. Summed across
the week they peak at 12:00–14:00 (21,352 / 20,977 / 18,298) and bottom at
20:00–21:00 (3,839 / 1,817), and the individual 21:00 hours are thinner still:
293, 298, 448, 478 and 300 ticks on the five days the feed served them.

### OANDA cross-check

All 120 stored hourly bars were compared against 120 OANDA H1 candles.

| statistic | pips |
|---|---|
| mid difference, mean | **-0.010** |
| mid difference, median | **0.000** |
| mid difference, p95 abs | 0.305 |
| mid difference, max abs | 1.450 |
| Dukascopy bid minus OANDA bid, median | **+0.600** |
| Dukascopy ask minus OANDA ask, median | **-0.600** |

Two independent feeds agreeing on the mid to a median of **zero pips** across a
week is the strongest available evidence that the record layout, the price
scale and the hour alignment are all correct — three bugs that are hard to
separate any other way. The bid and ask offsets are the ECN-versus-retail
spread difference `SPEC.md` predicted (+0.7 / -0.6 measured previously), and
the cross-check deliberately does not threshold them.

One hour of 120 exceeded the 1.0 pip mid threshold: `2026-08-12T21:00Z`, at
-1.45 pips. That is 17:00 New York, the daily roll, where the hour opens on
thin books and the two venues genuinely print different first ticks. The job
exits 1 on it, which is correct behaviour for the configured threshold and a
calibration question for Phase 2 rather than a defect.

### What the feed did to us

Across 145 hour requests the client absorbed **116 retries**:

| failure | count | meaning |
|---|---|---|
| HTTP 503 | 57 | throttling, served as an HAProxy page |
| connect timeout (`WinError 10060`) | 37 | transport, unrelated to throttling |
| read timeout | 11 | transport |
| connection reset (`WinError 10054`) | 11 | transport |

Four connections in flight provoked sustained 503s within about two minutes; at
two it completed. One hour (`2026-08-11T12:00Z`) still exhausted all nine
attempts and was recorded as a gap, with `FETCH_ERROR` on stderr and in the
manifest — not skipped. Re-running the same config re-fetched **that one hour
and no other**, leaving the other 143 untouched, and the week closed with zero
gaps. That is the resume path working under the conditions it exists for.

### Reproducibility

The run archived every compressed payload it fetched. Re-ingesting those 144
files in fixture mode, offline, produced an **identical** manifest: same status,
tick count, duplicate count, sha256, compressed size and boundary timestamps for
every one of the 144 hours, and the same 205,088 ticks. The live path and the
offline path are the same path.

### Reference backtest on the live week

Not a research result — it exists to show the chain ends somewhere real. A 6/24
MA cross on the 120 hourly bars, 1,000,000 units, IB costs:

| | |
|---|---|
| trades | 5 (one closed at the final bar) |
| gross P&L (mid to mid) | -2,535.00 |
| spread cost | 405.00 |
| commission | 230.83 |
| net P&L | **-3,170.83** |
| max drawdown | 6,864.61 |

Costs are 25% of the size of the gross move over one week at this trade rate,
which is the entire reason the cost model is explicit and the spread is a
separate line.


---

## 4. Feed semantics worth not rediscovering

Three things about the datafeed that are cheap to get wrong and expensive to
notice, all confirmed by observation rather than documentation.

* **The month in the URL is zero-based.** `/2026/07/12/` is 12 **August** 2026.
  This is confirmable without decoding a single byte: every response carries a
  `Last-Modified` header naming the true date.
* **An empty body with HTTP 200 means the market was closed. A 404 means the
  hour is genuinely absent.** Conflating them either manufactures gaps across
  every weekend or hides real holes, so they are recorded as different statuses
  (`closed` and `gap`) and only one of them fails a run.
* **A datacenter or VPN egress IP is rejected outright** by the datafeed front
  end with 403, while `www.dukascopy.com` keeps working. That failure looks
  exactly like a routing fault and is not one, so the client says so in the
  error text rather than leaving the next person to find out.

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
6. **The tick-count outlier check is coarse**, flagging a whole trading day
   outside a quarter to four times the trailing median of at least three
   previous whole days. It exists to catch a truncated download, not to model
   volume seasonality. The partial first and last days of every FX week are
   excluded from both sides of the comparison, because including them flagged
   the live week twice for nothing at all.
7. **Bars are rebuilt from the whole stored history** for a pair each time
   `bar_timeframes` is set. That is fine for a week and will not be fine for a
   decade; incremental bar building is a Phase 2 job.
8. **Only TOML configuration is supported.** `spec.md` allowed YAML or TOML;
   PyYAML is not in the pinned environment and one config format is one fewer
   thing to get wrong.
9. **The OANDA mid threshold is set to 1.0 pip and is not yet calibrated.** On
   the live week it flagged exactly one hour of 120, the 21:00 UTC daily roll,
   where thin books make the two venues print genuinely different first ticks.
   That is the threshold doing its job on a real difference, but nobody has
   decided yet whether the roll hour should be exempt or the threshold widened.
10. **The spread sanity ceiling is deliberately blunt** — a p99.9 above 20 pips
   for majors, 40 for crosses. It exists to catch a wrong price scale or a
   mangled field order, both of which are off by orders of magnitude, not to
   comment on a wide market. The weekly reopen alone reaches a p99.9 near 8.5
   pips on EURUSD, so a tighter ceiling would warn every week and be ignored by
   the second one.

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
   trading week is currently a warning (`EMPTY_TRADING_HOUR`). None fired in the
   live week, but some certainly will over a multi-year window, and some of
   those will be real holidays. Research over that range needs a calendar rather
   than a warning.
5. **Should the daily roll be treated as its own regime?** The live week shows
   the thin hours around 21:00 UTC running five times the median spread and ten
   times the p90 of the liquid sessions, on a tenth of the tick volume, and it
   is the one hour where the two feeds disagreed beyond threshold. Whether
   Phase 2 excludes it, models it separately, or simply refuses to trade it is a
   decision worth making before any strategy is scored, not after.
6. **Which bar timeframe is the research unit?** The reference strategy runs on
   1h bars because that was enough to exercise the plumbing. Nothing about the
   universe selection has been done yet.
7. **Does the IB paper account clear IDEALPRO cash FX, or only CFDs?** The whole
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
