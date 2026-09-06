# HANDOFF2.md — Phase 2 research loop, unattended run

Written 2026-08-23 for a ~1 week unattended stretch, updated 2026-09-06 when T4
closed. If you are reading this because the session died, this file plus the
checkpoints on disk are enough to restart without losing work.

## State: ingestion, data quality and the first EDA battery are done

Nothing is running. T2a, T2b, T3 and T4 are complete, gated and pushed; the
loop stopped there because T4's card says to. **Do not begin T5 without a
checkpoint.**

### T4, the first research card

`reports/T4_character.md`, experiment `experiments/T4-character/`, research gate
exit 0 (full). It ran in two parts.

**Step 0 re-issued T3's cross-check verdict under ruling R7**, which the M3
checkpoint pre-registered and which the card told this loop to write into
`SPEC2.md` before anything else. R7 amends pre-reg #7's flat 1.0 pip threshold
to a density-aware one and changes nothing about the consequence. On the same
stored sample, not re-drawn:

| | pinned #7 | under R7 |
|---|---|---|
| hours classified | 11,790 | 11,790 |
| `BLOCKED` | 1,327 | **478** |
| `UNVERIFIABLE` | — | **624** |
| `PASS` | — | 7,747 |
| `ROLL_EXEMPT` | 2,941 | 2,941 |

344 of the pinned blocks became `PASS`, 505 became `UNVERIFIABLE`, and nothing
R7 blocked was passed by the flat threshold. Agreement among verifiable hours is
94.2%, and the by-year table runs from 54.7% agreement with 46.6% unverifiable
in 2005 to 98.2% with none in 2024 — which is what the T4 appendix era-tags the
full history against. `config/crosscheck.toml` now carries the class of every
sampled hour, derived and re-compared on every T3 run exactly as
`config/calendar.toml` is, and `ResearchLoader.crosscheck_class` is how a later
card asks. It tags; it never filters.

**The battery** covers 12 pairs x 5 horizons over 2015-01-01 → 2025-02-28, with
a 1h/1d appendix back to 2005. Four things a next card should not have to
rediscover:

* **Directional memory lives at 5m and barely anywhere else.** 11 of 60
  pair-horizon cells have a q=4 variance ratio surviving Benjamini-Hochberg
  across a 300-test family: 9 at `5m`, 2 at `30m`, none at `1h`, `4h` or `1d`.
  All 11 are mean-reverting and all 11 hold their sign on rolling two-year
  windows. The effects are 5-10% departures from a random walk on returns whose
  standard deviation is 3-5 basis points — so T5's cost geometry, not T4's
  statistics, decides whether any of it is tradeable.
* **Volatility clustering is the one property that never changes its mind.**
  Positive |return| autocorrelation at lag 1 in every one of 60 cells, and zero
  sign flips between the two halves against 18 for the variance ratio. The
  report's hypothesis section argues this belongs in a *sizing* rule rather
  than an entry rule.
* **The roll window is a different market**: 1.7x to 3.0x the spread for 0.4x to
  0.6x the volatility, across all twelve pairs. Pre-reg #4 already excludes it;
  the evidence points the same way, which is the answer to the "revisable at a
  checkpoint with EDA evidence" clause.
* **Ruling R4's verdict: tick count is an activity proxy within a pair-year and
  not across years.** Bar-level log-ticks against log-|return| is positive for
  every pair (0.17 to 0.27); the annual version collapses to between -0.21 and
  0.73. 2007 moves ten of eleven pairs by 3x to 8x at once, which no market
  event does. The three conditions under which R4 could be lifted are stated in
  section 6; lifting it is a checkpoint decision.

**One finding is about the pipeline rather than the market, and a checkpoint
should look at it.** Of T3's 312 unexplained-empty dates, **236 have no readable
empty pair on them at all**: the only pair that went quiet was AUDUSD inside
ruling R1's window, so `calendar_build.classify` filtered the row's contents
away and left the row counted. Every one falls in 2007-2010. Fixing it would
change the committed holiday calendar, which T4's card does not cover, so it is
reported as an observation. The 76 real dates are 36 week-boundary artefacts, 21
partial holidays, 8 broader feed artefacts and 11 unknown.

**How T4 reads "every test run is a ledgered trial".** The ledger records
experiments — one entry per run of `research.run`, written before the run — and
filling it with three thousand individual z-statistics would destroy what it is
for. So T4 registers every hypothesis test inside the *hashed result*
(`payload.test_register`, 1,164 tests across 9 families) and applies
Benjamini-Hochberg within each family. A test cannot be dropped from its family
after its p-value has been seen, and the report states the family size next to
every claim. If a checkpoint prefers a different reading, this is the place to
say so.

| | T2a | T2b |
|---|---|---|
| window | 2015-01-01 … 2025-02-28 | 2005-01-03 … 2014-12-31 |
| pair-months | 1,464 / 1,464 | 1,440 / 1,440 |
| hours stored | 760,195 | 735,545 |
| ticks | 3,298,569,754 | 2,049,194,460 |
| duplicates | 0 | 0 |
| surviving gaps | 0 | 13,015 |
| store added | 37.65 GiB | 24.31 GiB |
| research gate | exit 0 (full) | exit 0 (full) |

Together: **2005-01-03 to 2025-02-28, twelve pairs, 1,495,740 hours,
5.35 billion ticks, zero duplicates, 61.96 GiB.** T3 re-opened every one of
those hours offline and found **zero** disagreements with the manifests, and
reconciled manifests against results against the file listing across all 252
pair-years with zero mismatches. The store is what it says it is.

T2b's 13,015 gaps are almost entirely one pair: 12,996 CROSSED_QUOTE in AUDUSD
across 2007-04..2008-09 and 2009-04..2010-10, plus 2 in USDJPY, 16
CLOSED_MARKET_TICK and 1 FETCH_ERROR. The feed served those hours and the
pipeline refused them. `reports/T2b_backfill.md` has the analysis, including
why the decoder was ruled out first.

**AUDUSD before 2011-01-01 is now excluded from research** by ruling R1. The
loader refuses it with `PAIR_EXCLUDED_WINDOW`; `research/exclusions.py` is the
one definition. Cross-pair work spanning that window runs on eleven pairs and
must say so.

## What T3 established, and what it left

`reports/T3_data_quality.md` is the full record. The four things worth carrying
forward:

* **The store validates clean.** Schema, row counts, `ask >= bid > 0`, UTC
  monotonicity, hour boundaries and the derived FX week: 1,495,740 hours, zero
  failures. Bars match stored hours as sets for all twelve pairs.
* **The holiday calendar exists and is thin before 2013, for a reason that
  matters more than the calendar.** `config/calendar.toml` carries 19 full and
  3 partial holidays, all derived from hour statuses per ruling R5. It is
  near-empty in the early years because the feed *quoted straight through* days
  the whole market was shut — so those bars carry prices nobody traded at, and
  no emptiness exists to derive a holiday from. Read the year-by-year table
  before trusting an early-era holiday bar. `research.calendar_build.Calendar`
  is how to query it.
* **1,327 sampled hours are blocked by pre-reg #7** — beyond the pinned 1.0 pip
  threshold against OANDA, outside the roll window. They are blocked per hour,
  not per pair-year. The diagnosis is quote density, not feed accuracy: hours
  under 500 ticks disagree 81% of the time, hours holding 3k-10k disagree 5.7%,
  and the by-year median falls from 2.7 pip in 2005 to 0.15 in 2024. A
  checkpoint decides what to do with them; the threshold is pinned and was
  applied as pinned.
* **312 dates of unexplained empty hours** are handed to T4 as data facts, not
  holidays. They concentrate in 2007-2010 and include the known JPY hole.

## Open questions left for a checkpoint

* Hour-level rejection is expensive against tick-level corruption: one crossed
  tick discards an hour of good quotes, which is what cost AUDUSD those years.
  Dropping and counting the bad ticks, as duplicates already are, would have
  kept them. That is a validation-rule change and was out of scope unattended.
* The week boundary and the feed disagree on 16 JPY hours at 21:00Z on Sundays,
  2011-03-06 and 2012-01-01..02-26. The derivation was verified correct. Only
  detectable in northern winter, so its true extent is unknown.
* The spread ceilings fired zero times in 2005 and 2006, against the card's
  expectation. "The flag did not fire" is not "the spreads were not wide" --
  p99.9 over a thousand-tick hour is a weak instrument. T5's regime question
  inherits this unanswered.
* Tick density follows neither age nor volatility: 2022 (6,200/h) and 2016
  (5,946) top the store, 2008 the crisis year is fourth, and 2005-2006 are the
  sparsest at 976 and 1,070. **T4 answered this one**: the annual series is
  dominated by feed changes, and 2007 alone moves ten of eleven pairs by 3x to
  8x.

Left by T4, for the M4 checkpoint:

* **236 of the 312 unexplained-empty dates are an artefact of R1's filter**, as
  above. The fix is a change to `calendar_build.classify` and would change the
  committed calendar, so it is proposed rather than made.
* **The strongest reversion in every pair sits in the Sydney session, where the
  spread is roughly twice that pair's own median.** Returns here are mid-to-mid
  so this is not bid-ask bounce in the textbook sense, but quote noise in a thin
  book has the same signature and is equally untradeable. Whether the effect
  survives outside that session is the question T7 has to answer, and the report
  says so beside every hypothesis rather than in a footnote.
* **The volatility-regime terciles are estimated on the whole decade.** Any T7
  card conditioning on them must re-estimate the boundary inside each training
  window, or it has fitted the regime to its own test set.
* **EURCHF and USDCHF carry the 2015 SNB de-peg inside the primary window** —
  a 15% five-minute move, 403 standard deviations, which is where their
  five-figure kurtosis comes from. Every statistic for those two pairs in the
  first half of the split is that afternoon. The report tabulates the largest
  move in every cell so no reader has to guess.

## What T4 added to the machinery

| module | what it is |
|---|---|
| `research/stats.py` | every EDA estimator, in numpy alone: moments, tails, Jarque-Bera, ADF, Lo-MacKinlay variance ratio, span-aware autocorrelation, forward continuation, Benjamini-Hochberg, trailing volatility. No scipy, no statsmodels — each is checked in `tests2/test_stats.py` against a printed table or a hand-computed series, and the first such test caught a spurious `sqrt(n)` in the variance-ratio statistic that would have rejected every horizon for every pair |
| `research/character.py` | the T4 experiment. Its four load-bearing decisions are documented at the top of the file and tested in `tests2/test_character.py` |
| `research/character_report.py` | the report, including a *generated* hypothesis section — which pair-horizons appear in it comes from the FDR correction, not from a paragraph |
| `research/character_figures.py` | 19 figures, each written beside the CSV it was drawn from |
| `research/svgplot.py` | a deterministic SVG plotter. matplotlib stamps a timestamp into its SVG output, so two renders of an unchanged result would differ and every figure would show as changed on every diff |
| `research/crosscheck_class.py` | ruling R7 as code, plus `config/crosscheck.toml` and its reader |
| `research/crosscheck_spreads.py` | the checkpointed pass measuring each sampled hour's median spread, which R7's middle band thresholds against |

**Nothing was installed.** `SPEC2.md` permits scipy, statsmodels, scikit-learn,
matplotlib and duckdb when a card needs them; T4 did not need them, and the
interpreter is unchanged from Phase 0.

Two traps worth carrying forward:

* **`asi8` on a stored timestamp returns microseconds, not nanoseconds.** The
  bar tables are `timestamp[us]` and pandas 3 preserves the unit, so a gap rule
  written against nanosecond steps rejects every pair in the store. `load_series`
  calls `.as_unit("ns")` and says why.
* **`write_result` serialises with `sort_keys=True`**, which is what makes the
  hash reproducible and what destroys every meaningful key order in the payload.
  Weekdays, priority classes and density bands are re-ordered at render time
  from lists the payload does preserve.

## What was running

`python -m research.bulk_ingest --config experiments/<card>/config.toml` under
the pinned interpreter. Both cards are finished, but the machinery below is what
a future ingestion card would reuse.

Everything it does is checkpointed. Nothing is held only in memory:

| file | what it is | write discipline |
|---|---|---|
| `experiments/T2a-ingestion/chunks.jsonl` | one record per completed pair-month | appended and flushed per record |
| `experiments/T2a-ingestion/sessions.jsonl` | one record per **finished** session | appended at session end |
| `experiments/ledger.jsonl` | start/end per run, append-only | start written *before* the run |
| `data/research/manifests/pair=<P>/<YYYY-MM>/manifest.json` | per pair-month hour records | rewritten atomically every 50 hours |
| `data/research/ticks/…`, `data/research/bars/…` | the store | every file written via tmp + `os.replace` |
| `experiments/T3-quality/validation.jsonl` | one record per re-validated pair-month | appended and fsynced per record |
| `experiments/T3-quality/oanda.jsonl` | one record per cross-checked pair-date | appended and fsynced per record |
| `experiments/T3-quality/oanda_availability.jsonl` | OANDA's history reach, one record per pair | written once |

T3's two long passes resume the same way the ingestion does — re-run the
command and it skips what is already checkpointed:

```
python -m research.validate_store  --config experiments\T3-quality\config.toml
python -m research.crosscheck_oanda --config experiments\T3-quality\config.toml
```

Neither is re-run by the gate (ruling D5): the experiment entry point reads
both back. A judge that needed a third-party API to be reachable would be
judging the API. **Nothing in an experiment directory may be a bare `.json`
except `result.json`** — the gate treats every one as a result document to be
ledgered and re-hashed, which is how the availability checkpoint failed the
gate before it became `.jsonl`.

## How to restart it

Just run it again. It reads `chunks.jsonl` to skip completed pair-months and
each manifest shard to skip settled hours, and `resume_calibration` recovers the
concurrency level and its baselines from `sessions.jsonl`.

```
E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe -E -s -m research.bulk_ingest \
    --config experiments\T2a-ingestion\config.toml
```

**After an unclean stop, verify before resuming.** A crash can leave tick files
written after a manifest's last checkpoint. They are unsettled but present, and
the tick reader globs a day directory rather than consulting the manifest, so
they would read as settled data. Delete any `*.parquet` under the in-flight
pair-month that no manifest entry references, then resume; the run re-asks for
those hours. This happened once already — see commit `9406719`, which also
records the checks worth repeating (no leftover `*.tmp`, every manifest shard
parses, settled hours re-read with matching row counts and timestamps).

## The closing sequence, for the next ingestion card

Both cards ran the same one and it is worth repeating verbatim:

1. work the plan, milestone commit + push at each completed calendar year;
2. `--retry-gaps` sweep, which re-asks every recorded gap. This is not a
   formality: it is the only thing that separates a transient refusal from a
   deterministic one, because the first clears on the second ask and the second
   does not. T2a recovered 85 of 85; T2b recovered 111 of 112 fetch errors and
   none of its 13,014 validation rejections, which is exactly the information
   the gap table needs to carry;
3. `python -m research.run --config <cfg>` to regenerate the result;
4. `python -m research.ingest_report ...` for the report. Facts the result
   cannot carry go in via repeatable `--note` flags so the report stays
   regenerable rather than hand-edited;
5. research gate exit 0 on the experiment directory;
6. commit and push.

## Park conditions

Stop cleanly, commit, push, and wait for the user rather than improvising:

* `FEED_UNREACHABLE` — the driver raises it after 3 hours of the feed answering
  nothing. An unreachable external dependency is a stop-and-report.
* Research gate exit **2** (harness) or **3** (environment). Do not touch
  `research/` or `fxlab/` for either.
* The same gate failure surviving three genuine fix attempts.
* Anything the task card does not cover.

To stop the driver cleanly, create the file named by its `--stop-file` argument;
it finishes the hour in flight and writes its session record.

## Feed behaviour measured on this run

Worth knowing before diagnosing a slow run as broken:

* The feed refuses service in phases, answering HTTP 503 to valid requests from
  every address DNS offers. Five such outages so far, all self-clearing:
  24, 3, 5, 8 and 16 minutes, none near the 3-hour budget.
* The concurrency ceiling of 4 is a ceiling, not a target. Level 4 has twice run
  ~10% throttled and been backed off; on one occasion level 3 ran 20% throttled
  and dropping to level 2 produced *ten times* the completed work. Let the
  calibrator find the level — it is a measured quantity, not a configured one.
* ~131 hours have warned that p99.9 spread exceeds the 40-pip sanity ceiling.
  81% sit on 21:00Z — 17:00 America/New_York, the daily rollover. Warnings, not
  rejections; the hours store normally.

## Rules that are not mine to bend

`verify/` and `verify2/` are the judges and are deny-edited. The seal is
2025-03-01 onward. The ledger is append-only — corrections are appended, never
edited in place. A task card is executed, not reinterpreted; if it does not
cover the situation, that is a park condition.
