# T2b — Backfill ingestion, 2005-01-03 → 2014-12-31, all 12 pairs

Bounded loop task, pre-committed so it MAY start unattended — but only
behind its start condition. Scope is this card; the agent may not extend it.

## Start condition (hard)

T2b may begin only when ALL of the following hold:
1. T2a's closing sequence is fully complete per its card: gap sweep run,
   report written, research gate exit 0 on experiments/T2a-ingestion,
   ledger committed and pushed.
2. Nothing is parked or blocked: no unresolved FEED_UNREACHABLE, no
   harness/environment error outstanding.
If T2a ends in any other state, T2b does NOT start — park and wait for
the user per the standing away instruction.

## Goal

Extend the research store back to the start of usable history: every open
hour of tick data for the 12-pair universe across
[2005-01-03, 2014-12-31], validated, in Parquet under data/research/,
bars appended incrementally, manifest complete. No analysis.

## Scope and method

Identical discipline to T2a, differences only where dated:
* Range: 2005-01-03 → 2014-12-31 inclusive (T1 established coverage and
  quality from 2005-01-03 on all pairs; the two known JPY partial holes,
  2009-06-15 → 2009-06-19, are expected — record as gaps/empty per what
  the feed serves, do not treat as errors).
* Order: reverse-chronological by month (2014-12 first), all pairs per
  month.
* Throughput: carry the calibration state and its baselines from T2a;
  same probe-up/back-off policy, hard ceiling 4, ≤2 on any sign the
  early-history endpoints behave differently. Early-history hours are far
  smaller (T1: 450–1,000 ticks/hour) — expect request-bound pacing, and
  record the measured early-era rate in the report.
* Validation: identical Phase 1 rules and reason tokens. NOTE: per-pair
  spread sanity ceilings were tuned on the modern era; T1 measured 2005
  median spreads 1.5–3.6× wider. If warn-level spread flags cluster
  broadly in early years, that is a finding to characterise in the
  report — widening any ceiling is NOT permitted without the user (it is
  a validation-rule change, out of scope for an unattended run). Hard
  failures (CROSSED_QUOTE etc.) remain hard.
* Checkpoint/resume, park-through-outages, seal discipline: all as T2a.
  (The seal is trivially satisfied — every date here predates the cutoff
  by a decade — but the config is still seal-scanned per protocol.)

## Deliverable

reports/T2b_backfill.md: per-pair per-year coverage vs T1's survey
expectations, gap table, early-era throughput and its comparison to
T2a's measured rate, spread-flag characterisation by year (the regime
question for T5), storage added, bar-build timings. Ledgered experiment
under experiments/T2b-backfill/ (survey pattern: scoring-mode config,
scored=false).

## Non-goals

No EDA, no statistics beyond coverage/validation counts, no strategy
content, no holiday calendar, no OANDA cross-checks, no validation-rule
changes, no touching T2a's stored data, no T3.

## Done

Store complete for the range (or gaps honestly documented), report
written, research gate exit 0 on experiments/T2b-backfill, ledger
committed and pushed. Stop. Do not begin T3 or any other task.
