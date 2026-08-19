# spec2.md — Phase 2: Research

> The law of Phase 2. The pre-registrations below were fixed in discussion
> BEFORE any research result existed, and are not revisable in response to
> results. Harness-builder: update ⚠ facts from Phase 0; change nothing in
> §Pre-registered decisions without the user saying so in chat.

## Goal

Rank the universe, find where edge lives after costs, develop and validate
strategy candidates (single-pair and cross-pair), and deliver at most a few
strategies that survive walk-forward validation and the sealed holdout exam —
or an honest report that none do.

## Pre-registered decisions (fixed 2026-08-19)

1. **Cost survival bar: 1.5×.** A candidate survives only if net-profitable
   with all costs multiplied by 1.5. Every scorecard reports the full ladder
   1.0× / 1.2× / 1.5× / 2.0×. Candidates clearing 1.2× but not 1.5× are
   PARKED (visible, not deleted), revisitable only if recorder-measured IB
   costs later prove the model overestimates — evidence, not preference.
2. **Holdout: sealed from 2025-03-01.** Research window ends 2025-02-28.
   Data from 2025-03-01 onward is NOT DOWNLOADED during Phase 2 — the seal is
   absence. It is ingested once, at final-exam time, and each surviving
   strategy gets exactly ONE evaluation on it, with the exact configuration
   frozen beforehand. No second attempts, no post-exam tuning. (The live-week
   data from Phase 1 predates nothing — it is 2026 data and therefore inside
   the sealed period: it may be used for pipeline/mechanical checks but NEVER
   for strategy scoring or selection.)
3. **Checkpointed autonomy.** The loop executes committed task cards
   (`taskcards/T<N>.md`) unattended; everything decisional — hypothesis
   selection, universe membership, advancing/killing candidates, interpreting
   results — happens in chat review between tasks. The agent must not
   originate new hypotheses or extend scope mid-task.
4. **Roll hour: refuse-to-trade (default).** The daily roll (16:00–18:00
   America/New_York, derived not hardcoded) is excluded from strategy
   execution and scored as its own regime in EDA. Revisable only at a
   checkpoint, with EDA evidence, before any affected strategy is scored.
5. **Holiday calendar**: built as an early task from observed empty trading
   hours across the full research window + a static major-holiday list;
   thereafter EMPTY_TRADING_HOUR on a calendar holiday is `closed`, not a
   warning.
6. **Bar timeframes built**: 1m, 5m, 30m, 1h, 4h, 1d. **Horizon ladder
   ranked in EDA**: 5m, 30m, 1h, 4h, 1d. (Sub-minute is out of scope for
   Phase 2 — gated behind recorder-measured venue costs, per PLAN.md.)
7. **OANDA cross-check threshold: 1.0 pip on hourly mids, roll hour exempt.**
   Any other hour beyond threshold blocks the affected data from research use
   until resolved.
8. **Validation scheme**: walk-forward with purge and embargo (embargo ≥ 1
   holding period of the strategy under test; ⚠ exact purge windows set per
   timeframe in the harness and recorded here). Never random K-fold. Tuning
   only inside training windows.
9. **Universe (12)**: EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD
   EURGBP EURJPY GBPJPY EURCHF AUDJPY. Membership changes are a checkpoint
   decision.
10. **Multiple-testing honesty**: every experiment ledgered (what, when,
    config hash, seed, outcome). Reviews state the trial count next to any
    highlighted result. Preference for parsimony is explicit review policy.

## Prerequisite fixes (block everything downstream of them)

* **P0-A — JPY commission notional** (HANDOFF known issue 1): commission
  minimum and rate must apply to USD-equivalent notional via a cross rate at
  fill time. Known-answer test required; research gate fails any JPY-scored
  experiment until this lands.
* **P0-B — Incremental bar building** (HANDOFF known issue 7): rebuilding all
  bars per run will not survive a decade of 12 pairs.

## Task roadmap (each = one task card, one bounded loop, one review)

* **T1 — Coverage survey.** Measure Dukascopy coverage per pair per year,
  2005-01-01 → 2025-02-28: available hours vs expected, gap structure,
  earliest reliable date per pair. Deliverable: coverage report + recommended
  per-pair research start dates. No strategy content.
* **T2 — Bulk ingestion.** Ingest the research window for all 12 pairs per
  T1's recommendation (≤2 concurrent connections; resumable; expect days of
  wall-clock — patience is budgeted). Deliverable: manifest summary, gap
  report, storage/bar-build timings.
* **T3 — Data quality + holiday calendar.** Full-window validation pass;
  build the calendar (pre-reg #5); OANDA cross-check on sampled hours per
  pair per year. Deliverable: data-quality report; any blocked data listed.
* **T4 — EDA battery I (per-pair character).** Return distributions,
  stationarity, autocorrelation by horizon, volatility clustering/regimes,
  spread by session/regime, session structure — per pair, compared across
  pairs, stability over time explicitly tested (split-half and rolling).
  Deliverable: ranked universe character report.
* **T5 — EDA battery II (cost geometry).** Horizon-vs-cost ranking: for each
  pair × horizon, realised move distributions vs round-trip cost at the
  ladder; minimum viable holding period per pair; roll-hour regime
  quantification (pre-reg #4 evidence). Deliverable: the "where can edge
  even exist" map.
* **T6 — EDA battery III (cross-pair structure).** Correlation structure and
  stability, cointegration scans WITH multiple-testing correction and
  out-of-window confirmation, currency-strength decomposition, lead-lag
  scans at ranked horizons. Deliverable: cross-pair opportunity report with
  trial counts stated.
* **T7+ — Hypothesis batches.** Strategy candidates specified as task cards
  (entry/exit logic, horizon, pairs, parameter ranges) drafted in chat from
  T4–T6 evidence. Each batch: implement → walk-forward per pre-reg #8 →
  scorecard per §Scorecard. Kill/advance decided in review, never in-loop.
* **T-final — Holdout exam.** For survivors only: ingest sealed period, one
  frozen-config evaluation each, portfolio-level assessment, HANDOFF2.md.

## Scorecard (every scored candidate, no exceptions)

Net P&L at cost ladder 1.0/1.2/1.5/2.0×; Sharpe (net, 1.5×); max drawdown;
hit rate; turnover; avg holding period; per-pair and portfolio views;
walk-forward window-by-window results (not just aggregate); regime split
(vol terciles, sessions); trial count from ledger; verdict vs survival bar.
In-sample and out-of-sample numbers never mixed in one figure.

## Definition of done

Either: ≥1 candidate survives walk-forward across regimes at 1.5×, passes
its single holdout exam, and is documented in HANDOFF2.md with everything
Phase 3 needs (data requirements, order types, frequency, sizing) — or:
HANDOFF2.md honestly documents that no candidate survived, with the ledger
as evidence of what was tried. Both are valid completions; only one of them
is common.