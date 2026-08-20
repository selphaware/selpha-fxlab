# T2a — Bulk ingestion, 2015-01-01 → 2025-02-28, all 12 pairs

Bounded loop task. Scope is this card; the agent may not extend it.

## Goal

The research store: every open hour of tick data for the 12-pair universe
across [2015-01-01, 2025-02-28], validated, in Parquet under
`data/research/`, with bars built and a complete manifest. No analysis.

## Scope and order

* Pairs: the full universe (SPEC2 pre-reg #9), all twelve.
* Range: 2015-01-01 → 2025-02-28 inclusive. The seal forbids any request
  ≥ 2025-03-01; the gate checks configs.
* Order: reverse-chronological by month (2025-02 first, then 2025-01, …),
  all pairs per month before moving to the previous month — so if the run
  is cut short, the most recent (most-researched) history is complete.
* T1's coverage survey found no missing regions in this range; the two
  JPY partial holes (2009) are outside it. Expect near-complete coverage;
  every failed/corrupt hour is a manifest gap, never silently skipped.

## Throughput (self-calibrate, politely)

* Start at 2 concurrent connections (T1's proven-safe level). After a
  sustained clean hour, the loop MAY probe higher concurrency step by step
  (3, then 4 — hard ceiling 4), backing off to the last safe level on any
  sustained rise in 503 rate. Record the calibration in the report.
* Full Phase 1 retry/backoff discipline; the T1 EndpointPool behaviour
  applies. During feed outages, park and resume rather than hammer.
* Checkpoint the manifest continuously (Phase 1 lesson); the run must be
  resumable at hour granularity from a cold restart.

## Validation and storage

* Every hour passes the identical Phase 1 validation (named reason
  tokens; duplicates dropped and counted). Weekend/closed hours recorded
  as closed via the derived week boundary — never hardcoded UTC.
* Tick Parquet per the pinned schema; bars built incrementally per P0-B
  (1m, 5m, 30m, 1h, 4h, 1d) — building bars must not require re-reading
  the whole store per run.
* If P0-B (incremental bars) is not yet implemented, implementing it is
  IN scope for this card (it lives in fxlab/, gated as usual). P0-A (USD
  accounting) is NOT in scope — no scoring happens here.

## Deliverable

`reports/T2a_ingestion.md`: per-pair coverage summary (hours ok / empty /
gap, ticks stored, date completeness vs T1 expectation), gap table with
dates, dedup counts, calibrated throughput achieved and time taken (T2b's
budget), storage footprint per pair, bar-build timings, and any validation
anomalies. Plus the ledgered experiment record under
`experiments/T2a-ingestion/` (survey pattern: scoring-mode config,
scored=false).

## Non-goals

No EDA, no statistics beyond coverage/validation counts, no strategy
content, no holiday calendar (T3), no OANDA cross-checks (T3), no
touching data before 2015 (T2b), no requests past the seal.

## Done

Store complete for the range (or gaps honestly documented), report
written, research gate exit 0 on experiments/T2a-ingestion, ledger
committed and pushed. Stop; do not begin T3 or T2b.