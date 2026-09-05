# HANDOFF2.md — Phase 2 research loop, unattended run

Written 2026-08-23 for a ~1 week unattended stretch, updated 2026-09-06 when T3
closed. If you are reading this because the session died, this file plus the
checkpoints on disk are enough to restart without losing work.

## State: ingestion and data quality are done

Nothing is running. T2a, T2b and T3 are complete, gated and pushed; the loop
stopped there because T3's card says to. **Do not begin T4 without a
checkpoint.**

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
  sparsest at 976 and 1,070.

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
