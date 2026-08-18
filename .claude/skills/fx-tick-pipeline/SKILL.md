---
name: fx-tick-pipeline
description: Build or debug a validated FX tick-data pipeline — Dukascopy bi5 download/decode, tick validation, Parquet storage, bar resampling, and a spread-crossing backtester with an IB-style cost model. Use when working on fxlab/ingestion, fxlab/costs, fxlab/backtest, when the gate reports a decoding/timestamp/cost failure, or when adding a new pair, timeframe, or data source.
---

# Building a validated FX tick pipeline

The order below is not arbitrary. Each step produces the ground truth the next
step is checked against. Skipping ahead is how you end up with a pipeline that
runs green and is quietly wrong.

## The order

1. **Decode before you design.** Fetch one real hour and decode it by hand
   before writing any abstraction. The feed is the truth; the docs are a
   rumour. Print the first and last three ticks and read them.
2. **Get ground truth from a second, independent source.** Compare the decoded
   hour against the same hour from another provider (here: OANDA H1 bid/ask
   OHLC). This single check simultaneously validates the binary layout, the
   price scaling and the hour alignment — three bugs that are hard to separate
   any other way. A wrong scale is off by 10×; a wrong hour is off by many pips;
   a wrong field order gives `ask < bid` everywhere.
3. **Freeze fixtures from real bytes.** Store the raw compressed hours plus the
   exact counts and boundary ticks. Never synthesise tick fixtures — synthetic
   data cannot catch a decoder that mis-reads real data.
4. **Write the validator before the storage layer.** Decide what "impossible"
   means and give each rule a named, machine-readable reason token. Then build a
   poisoned fixture per rule and confirm each one is actually rejected.
5. **Store with a pinned schema.** Write an explicit `pa.schema(...)`. Inferred
   dtypes drift silently between library versions.
6. **Resample from stored ticks, never re-download.** Fix the bar convention in
   writing — timestamp = bar OPEN, covering `[open, open+Δ)` — and state it in
   both code and docs.
7. **Build the cost model before the backtester,** and make the backtester
   unable to fill without it. A cost model bolted on afterwards ends up
   bypassable, and the bypass is invisible in the results.
8. **Prove the backtester on a known answer.** Construct a tiny bar series where
   correct next-bar bid/ask execution gives a P&L you computed by hand, and
   where lookahead and mid-fill give *different* hand-computed answers. Assert
   the correct one. Anything less does not test execution at all.

## Common failures and their real causes

| symptom | actual cause |
|---|---|
| Every tick has `ask < bid` | Record field order swapped. Dukascopy bi5 is `>IIIff` = ms, **ask**, **bid**, ask_vol, bid_vol. Ask comes first. |
| Prices 10× or 100× off | Per-pair scale is `10 ** -display_precision` — `1e-3` for JPY-quoted pairs, `1e-5` for the rest. One global constant cannot be right for both. |
| Everything shifted exactly 1h | The hour epoch came from a naive `datetime` that picked up local time or a DST offset. Build the hour start as explicit UTC and add the ms offset to that. |
| Timestamps drift by ms across a day | Millisecond offsets treated as seconds, or float accumulation. Use integer ms with `timedelta(milliseconds=...)`. |
| Weekend hours look like data loss | An empty body is **market closed**, not an error. Dukascopy returns HTTP 200 with 0 bytes. A 404 is a genuinely absent hour. Record the two differently. |
| Sporadic `503` mid-download | Rate limiting (an HAProxy page, not an application error). Back off exponentially; treat 503/429 and connection resets as retryable. |
| Persistent `503` on every request, but the marketing site works | The egress IP is a VPN/datacenter address and is being rejected on reputation. Not a firewall, not a `User-Agent` problem. Check the public IP before blaming the network. |
| Tick counts drift between runs | Over-aggressive dedup. Dedup on the *full* record, and always report the dropped count rather than swallowing it. |
| String columns change dtype between environments | pandas 3 defaults strings to Arrow-backed `large_string` and enables `future.infer_string`. Pin the Arrow schema explicitly. |
| `NaN` appears in resampled bars | A pandas join or `reindex` created empty bins. Decide explicitly whether an empty bin is a dropped row or a gap record — never let it default. |
| Backtest looks too good | Fills are using the signal bar's own price, or the mid. Assert against a known-answer fixture; both bugs are invisible to eyeballing an equity curve. |
| Costs are suspiciously round, or zero | Config keys never reached the cost model. Assert `total_costs > 0` *and* that the per-trade costs sum to the summary total. |
| P&L reconciles but the spread is missing | Gross was measured fill-to-fill, burying the spread in the price. Measure gross mid-to-mid so spread is an explicit line, then `net = gross - (spread + commission)`. |
| Commission is a flat rate everywhere | The per-order minimum (IB: $2.00) never binds because every test trade is large. Include a deliberately small trade. |

## Checks worth keeping permanently

* `ask >= bid > 0` on every stored tick.
* Timestamps tz-aware UTC, non-decreasing per pair, no exact duplicates.
* Exact tick counts per frozen hour — not `> 0`.
* Boundary ticks (first and last of each hour) compared to frozen values.
* Costs reconcile trade-by-trade against the summary.
* Nothing touches the network in fixture mode.

## Local reference

`CLAUDE.md` holds the confirmed Dukascopy URL/layout/scaling facts, the FX week
boundary (which moves with US DST — do not hardcode it), the pinned Parquet
schema and the full deliverable contract. `spec.md` holds the scope.
