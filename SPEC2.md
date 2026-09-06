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

1. **Cost survival bar: 1.5×.** **Pinned, approved in chat 2026-08-19:** a
   candidate survives if and only if its **aggregate out-of-sample walk-forward
   net P&L, denominated in USD, is greater than zero with all costs multiplied
   by 1.5, at the level the candidate is proposed to trade** — per-pair for a
   single-pair candidate, portfolio for one proposed as a portfolio. That one
   inequality is the entire quantitative bar. Everything else — window-by-window
   consistency, regime stability, Sharpe, drawdown, turnover, parsimony — is
   checkpoint review judgement: the scorecard displays it, nothing thresholds
   it, and no threshold may be added to it after a result exists.
   Every scorecard reports the full ladder 1.0× / 1.2× / 1.5× / 2.0×.
   Candidates clearing 1.2× but not 1.5× are PARKED (visible, not deleted),
   revisitable only if recorder-measured IB costs later prove the model
   overestimates — evidence, not preference. Below 1.2× is dead.
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
   Pinned 2026-08-19: “roll hour” here means exactly the window of #4 — the
   derived 16:00–18:00 `America/New_York` window, not a single hardcoded UTC
   hour. Any hour outside that window beyond threshold blocks the affected data
   from research use until resolved.
8. **Validation scheme**: walk-forward with purge and embargo. Never random
   K-fold. Tuning only inside training windows. The floor stands: embargo ≥ 1
   holding period of the strategy under test. Set in the harness and recorded
   here 2026-08-19 (`research/walkforward.py::PURGE_EMBARGO_BARS`):

   | timeframe | purge (bars) | embargo (bars) | wall-clock each |
   |---|---|---|---|
   | 5m  | 288 | 288 | 1 trading day |
   | 30m | 48  | 48  | 1 trading day |
   | 1h  | 24  | 24  | 1 trading day |
   | 4h  | 30  | 30  | 1 trading week |
   | 1d  | 5   | 5   | 1 trading week |

   Effective values are `max(table value, holding period in bars)` for both,
   so a strategy holding longer than the floor widens its own purge and
   embargo rather than under-purging. Train/test window lengths are not
   pre-registered globally: each task card declares them and the ledger
   records them.
9. **Universe (12)**: EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD
   EURGBP EURJPY GBPJPY EURCHF AUDJPY. Membership changes are a checkpoint
   decision.
10. **Multiple-testing honesty**: every experiment ledgered (what, when,
    config hash, seed, outcome). Reviews state the trial count next to any
    highlighted result. Preference for parsimony is explicit review policy.

## Prerequisite fixes (block everything downstream of them)

* **P0-A — USD accounting** (HANDOFF known issue 1, widened by ruling B,
  2026-08-19). The Phase 1 model computes notional as `units * fill_price`,
  which is the **quote** currency; 8 of the 12 universe pairs are not
  USD-quoted (USDJPY, USDCHF, USDCAD, EURGBP, EURJPY, GBPJPY, EURCHF, AUDJPY),
  and `BacktestResult.gross_pnl` sums per-trade P&L across pairs without
  converting, so a portfolio figure can add JPY to CHF to USD. The fix is
  therefore not a JPY commission patch but full USD accounting:

  * every per-trade quantity — gross P&L, spread cost, commission, and the
    notional the `commission_min` floor is tested against — is converted to
    USD before it is reported or summed;
  * conversion uses the **fill-time mid of a universe pair** (JPY via USDJPY,
    CHF via USDCHF, CAD via USDCAD, GBP via GBPUSD, and so on), never a period
    average and never a constant;
  * conversion is **lookahead-safe**: the rate is the last mid at or before the
    fill timestamp. A rate from a later bar is leakage and is treated as such;
  * portfolio aggregation happens only in USD.

  Known-answer test required, covering both the floor applied to a
  USD-equivalent notional and a portfolio sum mixing three quote currencies.
  The research gate fails any scored experiment touching a **non-USD-quoted**
  pair (all 8, not only the 4 JPY-quoted ones) until this lands. The gate
  detects landing by the capability flag `fxlab.costs.USD_ACCOUNTING`; setting
  that flag without the conversion is a lie the known-answer test exists to
  catch.
* **P0-B — Incremental bar building** (HANDOFF known issue 7): rebuilding all
  bars per run will not survive a decade of 12 pairs.

## Harness rulings and Phase 0 facts (approved in chat 2026-08-19)

Approved by the user in chat during the Phase 2 bootstrap. The pre-registration
note at the top of this file still stands for everything above it: nothing in
§Pre-registered decisions changes without the same approval.

### The seal is enforced by scope, not by a blanket file ban (ruling A)

A literal “no Parquet on disk carries a sealed date” rule was unbuildable. The
Phase 1 live week (`data/live_week/`, EURUSD 2026-08-09 → 2026-08-14, 120 tick
files) and the frozen Phase 1 fixture
`verify/fixtures/backtest/bars_EURUSD_1h.parquet` (timestamps 2026-07-14) both
sit after the cutoff, and `verify/` is deny-edited. So:

* the on-disk seal assertion is scoped to the **research data root**,
  `data/research/**` — no Parquet under it may carry a date ≥ 2025-03-01;
* the research loader has two modes. **`scoring`** refuses any date ≥ the
  cutoff with the named reason `HOLDOUT_SEALED`. **`mechanical`** is permitted
  only against an explicit allowlist containing exactly `data/live_week/`, and
  any experiment declaring it is **barred from emitting a scorecard**;
* every experiment records the dates its loader actually served, and the gate
  asserts that a scored result ran in `scoring` mode and touched no sealed
  date.

Mechanical mode exists for pipeline checks — pre-reg #2 already allows the live
week for exactly that — and for nothing else. It cannot produce a number a
strategy decision rests on.

### Data layout (ruling D3)

Research data lives under `data/research/`, mirroring the Phase 1 store exactly:
`ticks/pair=<PAIR>/date=<YYYY-MM-DD>/*.parquet` and
`bars/timeframe=<TF>/pair=<PAIR>/<PAIR>_<TF>.parquet`, same pinned Arrow
schemas. `data/live_week/` stays where it is as the quarantined mechanical area.
`data/holdout/` does not exist and is not created until T-final.

### Reproducibility re-run classes (ruling D5)

The gate re-executes an experiment from its recorded config and seed and
requires the result hash to match exactly. Ingestion is **never** re-run. Every
ledger entry declares a re-run class:

* **`full`** — the default, and mandatory for any experiment that decides
  survival or kill, unless a full re-run exceeds roughly two hours;
* **`deterministic-subset`** — permitted only above that bound. The subset is
  declared and hashed **before results exist**, and must contain the best
  window, the worst window, and a seeded random selection of the rest.
  Choosing a subset after seeing results is the failure this rule prevents.

### Timeframes (ruling D1)

`fxlab.ingestion.bars` shipped Phase 1 without a 30m alias, which pre-reg #6
requires. Added under the Phase 1 gate.

### Phase 0 verification, 2026-08-19

| check | result |
|---|---|
| `verify\smoke_test.py fxlab` | exit 0, 202 tests, all four stages green |
| `python -m fxlab.report --config config/ingest_live_week.toml` | exit 0; 144 hours → 120 ok / 24 closed, 205,088 ticks, 0 duplicates, median spread 0.30 pips |
| interpreter | 3.12.13, `sys.base_prefix = a:\envs\py312` (mapped drive up) |
| pinned libs | pandas 3.0.5, pyarrow 25.0.1, numpy 2.5.2, pytest 9.1.1 |
| Phase 2 libs | scipy, statsmodels, scikit-learn, matplotlib, duckdb — none installed yet |
| `data/` | git-ignored, nothing tracked; only `data/live_week/` exists |

### The harness as built (2026-08-19)

```
verify2\research_gate.py <experiment_dir>   # full judgement of one experiment
verify2\research_gate.py --fast             # Phase 1 gate + leakage + seal + ledger
verify2\research_gate.py --selftest         # 28 assertions, one per failure mode
```

Judged surface: `research/` and `tests2/`. Judge: `verify2/`, deny-edited like
`verify/`. Research unit tests live in `tests2/`, not `tests/`, so that a
research bug can never report itself as a Phase 1 regression.

Selftest coverage, all green: leaky walk-forward (three distinct leaks), sealed
date in a config, sealed Parquet on disk, permissive loader, scored result over
sealed data, scored result from mechanical mode, unseeded experiment,
non-integer seed, result with no ledger entry, ledger written after the result,
wrong task card, zero-cost scorecard, missing ladder rung, per-rung cost drift,
unsupported survival verdict, JPY scored before P0-A, and the Phase 1 exit codes
1/2/3 translating to regression / harness / environment.

The walk-forward known answer: a twenty-bar series whose training mean is
exactly zero, giving an honest out-of-sample P&L of **-11**. Test-window
peeking gives **+17** (a losing rule turned profitable, which is what leakage
looks like), unpurged overlap **-15**, full-sample normalisation **-13**. Four
distinct hand-computed numbers, so the fixture is known to discriminate rather
than merely to agree.

### The interface P0-A must expose

Fixed here so the fix is written against a target rather than against whatever
the test happens to call. `verify2/fixtures/cost_known_answers.py` holds the
arithmetic and runs the moment the flag appears:

* `fxlab.costs.USD_ACCOUNTING` — `True` once conversion is implemented;
* `fxlab.costs.quote_to_usd(pair, rates)` — the factor converting one unit of
  the pair's quote currency into USD, given a mapping of conversion pair to its
  **fill-time** mid;
* `IBCostModel.commission_for(units, fill_price, *, quote_to_usd=1.0)` and
  `IBCostModel.spread_cost_for(units, fill_price, mid, *, quote_to_usd=1.0)`,
  both returning USD.

Known answers, at `USDJPY = 150.00` and `USDCHF = 0.90`: 50,000 USDJPY is a
50,000 USD notional whose 0.20bp is 1.00, so commission is the **2.00** floor,
not the 1,500 JPY the quote-currency path produces; 1,000,000 USDJPY costs
**20.00**; a 0.015 half-spread on 100,000 USDJPY costs **10.00**; and a
portfolio of +300.00 USD, +3,000 JPY and -180.00 CHF is **+120.00 USD**, where
the blind sum is 3,120 in no currency at all.

### T0, the protocol shakedown

`taskcards/T0.md` -> `experiments/T0-spread-by-session/` ->
`reports/T0_spread_by_session.md`, run end to end through card, ledger, result,
report, gate exit 0 and a reproducibility re-run. Mechanical mode on the
quarantined live week; not scorable, not a research finding. It reproduced the
Phase 1 coverage report session by session, which is the only available
evidence that the research loader does not reshape what it serves.

### M2 checkpoint rulings, R1-R6 (approved in chat 2026-09-05)

Fixed at the M2 checkpoint that closed T2a/T2b and opened T3, before any T3
result existed. Recorded here by `taskcards/T3.md`, which states them verbatim.
They bind every card downstream in the same way §Pre-registered decisions does.

**R1 — AUDUSD before 2011-01-01 is EXCLUDED from research.** The crossed-quote
corruption of the two episodes 2007-04→2008-09 and 2009-04→2010-10 is **not**
recovered, and the hour-level `CROSSED_QUOTE` rejection rule is unchanged. The
research loader refuses an AUDUSD request dated before 2011-01-01 with the named
reason `PAIR_EXCLUDED_WINDOW`. The exclusion is stated on every report and
scorecard that touches AUDUSD. A cross-pair analysis spanning pre-2011 runs on
**eleven** pairs there, and says so.

**R2 — JPY pre-open hours stay rejected.** The 16 USDJPY/EURJPY Sunday 21:00Z
northern-winter hours keep `CLOSED_MARKET_TICK`. They gain the sub-label
`PRE_OPEN_FEED_DATA` in the gap table so the class is nameable. The derived week
boundary rule itself is untouched.

**R3 — Spread-regime comparisons across eras must control for ticks per hour.**
Medians, p90 and fixed-sample statistics only; never a raw `SPREAD_OUTLIER`
count. A p99.9 taken over a thousand-tick hour and one taken over six thousand
are not the same instrument, so their counts are not comparable across eras. A
T5 requirement, recorded here because T3's spread tables must already be framed
that way.

**R4 — Tick counts are not a volume or activity proxy** until a T4 card has
characterised the density series. No report and no scorecard may use them as one
before then.

**R5 — The holiday calendar derives from manifest `status == "empty"`,** never
from the `EMPTY_TRADING_HOUR` warning list. The T3 audit measured the warning
list 1,183 hours short of the empty-status hours across the store (6,117 empty,
4,934 warned) — see the canonical-reading ruling below for why.

**R6 — No hand-written numbers in reports.** Every figure in every report is
derived at render time from the manifests, the ledger or `result.json`. A number
typed into prose is a number that stops being true the moment the store changes,
and the T3 audit found three that had.

### The canonical manifest reading (recorded by T3 Step 0.4)

Not a checkpoint ruling — a declaration the T3 card instructed Step 0.4 to make
after root-causing the disagreement its audit found. It settles which of the two
copies of a validation flag a report may read.

A manifest shard carries validation flags in **two** places, and they disagree:

* `hours[*].issues[]` — filed on the hour record itself, and replaced wholesale
  whenever `Manifest.upsert` replaces that record;
* `validation.warnings[]` — a flat list appended to as a session observes each
  flag, pruned by `strip_aborted`, and reloaded across sessions.

Measured across the whole store: 3,009 `SPREAD_OUTLIER` hours appear in both,
**27 in the warning list only**, **160 in the records only**. The two causes are
distinct, and both are structural rather than corruption:

* the **27** are hours whose *hard* rejection discarded the data. In
  `pipeline._process_hour`, a rejected hour's record carries only the hard
  reasons while its soft flags are still appended to the shard warning list. So
  the warning list counts spread flags on hours that are **not in the store**;
* the **160** are hours carried forward on resume. The record travels with its
  `issues[]` through `_already_stored`; the shard warning list is only appended
  to by the session that actually fetched the hour, and `strip_aborted` can
  prune entries from it. So the warning list under-counts hours that **are** in
  the store. The same mechanism is why 1,183 empty hours carry no
  `EMPTY_TRADING_HOUR` warning, which is what R5 responds to.

`EMPTY_TRADING_HOUR` and `TICK_COUNT_OUTLIER` never appear in `issues[]` at all:
the first is appended straight to the warning list for an hour that stores no
issues, the second is a whole-day derived flag with no single hour to attach to.

**The ruling.** The canonical statement about the store is the hour records —
`hours[]` — together with the derived `coverage` block, which
`Manifest.to_dict` recomputes from `hours[]` on every write and is therefore a
pure function of them. Concretely:

| quantity | canonical source |
|---|---|
| any hour status count | `hours[*].status` |
| `SPREAD_OUTLIER` on stored data | `hours[*].issues[]` where `status == "ok"` |
| hard rejection reasons | `hours[*].issues[]` where `status == "gap"` |
| empty trading hours / the holiday calendar | `hours[*].status == "empty"` (R5) |
| `TICK_COUNT_OUTLIER` | `coverage.by_day[*].tick_count_outlier` |

`validation.warnings[]` is **an ingestion-session log**, kept for provenance —
it is the only record that a flag was ever *observed*, including on hours whose
data was then discarded, and that is the one question it may be asked. No report
figure derives from it. Reports state flag counts on stored data separately from
flags observed on rejected hours, because those are different claims.

Nothing above changes the manifest format. Annotating the shards themselves — so
a shard states its own exclusions and reconciles its own two lists — would be a
format change, and per the T3 card that is a checkpoint decision. It is
proposed, not made.

### M3 checkpoint rulings, R7-R8 (approved in chat 2026-09-06)

Fixed at the M3 checkpoint that closed T3 and opened T4, before any T4 result
existed. Recorded here by `taskcards/T4.md`, which states them verbatim. They
bind every card downstream in the same way §Pre-registered decisions does, and
R7 is the first amendment any pre-registration has taken: it changes *how* the
pre-reg #7 threshold is applied, and changes nothing about what happens to an
hour that fails it.

**R7 — Cross-check threshold (amends pre-reg #7) is density-aware.** For a
sampled hour with ≥ 3,000 ticks the threshold is 1.0 pip. For an hour with
500–2,999 ticks it is 1.0 pip + that hour's own median spread. Hours under 500
ticks are classified `UNVERIFIABLE` (the check cannot see them) — usable,
tagged, and their usage before 2013 is a T5 decision on the by-year agreement
evidence. Hours failing the applicable threshold remain `BLOCKED`. The roll
window stays exempt.

The amendment is to the instrument, not to the verdict. T3 measured the fixed
1.0 pip threshold rejecting 81% of hours under 500 ticks and 5.7% of hours
holding 3k–10k, against a by-year median difference falling from 2.7 pip in
2005 to 0.15 in 2024 — so the same pip threshold was answering a different
question in each era, and most of what it rejected it rejected for being thin
rather than for being wrong. R7 gives the middle band a threshold that scales
with the instrument's own resolution and gives the thinnest band no verdict at
all, because a check that cannot see an hour should say so rather than fail it.
`BLOCKED` still means what pre-reg #7 said it means: out of research use until a
checkpoint says otherwise.

**R8 — Holiday eligibility.** The static major-holiday list in
`config/calendar.toml` marks hours ineligible for execution in every backtest,
in every year, regardless of whether the feed served data (pre-2013 the feed
quoted through closed markets; those prices were not tradeable). The
empties-derived calendar component is informational. This is a backtester rule
to be implemented before T7; stated here.

R8 exists because T3 found the derived calendar near-empty before 2013 for a
reason that is about the feed rather than the market: through the early era the
feed quoted straight across days the whole market was shut, so there is no
emptiness there to derive a holiday from and the bars carry prices nobody
traded at. A calendar that can only find the holidays the feed left empty
cannot be the eligibility rule; the static list can, and is.

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