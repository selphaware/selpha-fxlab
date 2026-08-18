# spec.md — Phase 1: FX data & research plumbing (`fxlab`)

> Phase 0 complete (2026-08-18). Every ⚠ in the original draft has been replaced
> with a fact measured against the live Dukascopy feed and cross-checked against
> OANDA. Where a measurement contradicted the draft it is called out inline as
> **CORRECTION**. Machine-specific environment facts and the full deliverable
> contract live in `CLAUDE.md`.

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
- All timestamps stored tz-aware UTC.
- **CORRECTION — the FX week boundary is not a fixed UTC hour.** Measured from
  the live feed:

  | period | opens | closes |
  |---|---|---|
  | Northern summer (July 2026, observed) | Sun **21:00 UTC** | Fri **21:00 UTC** |
  | Northern winter (January 2026, observed) | Sun **22:00 UTC** | Fri **22:00 UTC** |

  Evidence: EURUSD Fri 2026-07-17 20:00Z = 1,163 ticks, 21:00Z = empty; Sun
  2026-07-19 20:00Z = empty, 21:00Z = 222 ticks. The same probe in January shows
  Fri 2026-01-09 21:00Z still carrying 868 ticks with 22:00Z empty. The boundary
  tracks 17:00 `America/New_York`, so it must be derived with `zoneinfo`, never
  hardcoded — a fixed 21:00 UTC rule is wrong for roughly half of every year and
  fails silently, corrupting session and spread statistics downstream.

## Ingestion — Dukascopy (primary)

- Fetch hourly bi5 files over HTTPS with retry/backoff, polite concurrency
  (≤ 4 in flight), resumable: already-downloaded hours are skipped via the
  manifest, never re-fetched.
- **CONFIRMED** URL pattern:
  `https://datafeed.dukascopy.com/datafeed/{PAIR}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5`
  where **`MM0` is zero-based** (January = `00` … December = `11`). The
  zero-indexed-month quirk is real. Proven independently of any decoding: paths
  `/2026/06/11/` and `/2026/06/18/` return empty bodies because they are
  Saturdays 11 and 18 **July**, and every response carries a `Last-Modified`
  header naming the true date (`Sat, 11 Jul 2026 14:00:52 GMT`).
- **CONFIRMED** binary layout, exactly as drafted: 20-byte records, `struct`
  format `>IIIff` (big-endian) =
  `(ms_offset_in_hour, ask_uint32, bid_uint32, ask_vol_f32, bid_vol_f32)`.
  **Ask precedes bid.** Compression is raw LZMA1 *alone* format
  (`lzma.FORMAT_ALONE`), header `5d 00 00 40 00` + 8-byte LE uncompressed size.
- **CONFIRMED** price scaling = `10 ** -display_precision`: `1e-3` for
  JPY-quoted pairs, `1e-5` for all others. Derived from data and cross-validated
  against OANDA H1 bid opens for all 12 pairs at 2026-07-14 13:00Z; worst
  disagreement 1.2 pip (GBPJPY), which is the genuine ECN-vs-retail spread
  difference rather than a scaling error.
- **NEW — empty body means market closed.** Dukascopy answers a closed hour with
  **HTTP 200 and a zero-byte body**, not 404. Treating that as an error would
  manufacture gaps across every weekend; treating a real 404 as "closed" would
  hide genuine holes. They are different cases.
- **NEW — 503 is throttling, not a firewall.** Sustained fetching earns an
  HAProxy `503 Service Unavailable` page. Back off exponentially; treat 503/429
  and connection resets as retryable. Separately, **VPN/datacenter egress IPs are
  rejected outright** by the datafeed front end while `www.dukascopy.com` keeps
  working — which makes an IP-reputation block look like a routing fault.
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
  report distribution of differences; flag hours beyond a config threshold.
  Expect Dukascopy's bid to sit *above* OANDA's and its ask *below* — measured
  at +0.7 / -0.6 pip on EURUSD, because Dukascopy's ECN spread (median 0.3 pip)
  is tighter than OANDA's retail spread. Mids agree to ~0.15 pip. A cross-check
  that flags this as an error is miscalibrated.
- **CONFIRMED OANDA response shape** (`price=BAM&granularity=H1`): top-level
  `instrument` / `granularity` / `candles`; each candle has `time`, `complete`,
  `volume` and `bid`/`ask`/`mid` OHLC objects. Prices are **strings** and `time`
  is RFC3339 with **nanosecond** precision — both need deliberate parsing.
  `displayPrecision` / `pipLocation` from `/v3/accounts/{id}/instruments` is the
  authoritative per-pair scaling reference.
- **CORRECTION — environment selection must be config-driven.** `OANDA_ENV`
  selects `practice` (default) or `live`; the client is restricted to the
  read-only `instruments/candles` endpoints and must never call `/orders`,
  `/trades` or `/positions`.

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
