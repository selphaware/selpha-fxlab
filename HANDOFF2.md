# HANDOFF2.md — Phase 2 research loop, unattended run

Written 2026-08-23 for a ~1 week unattended stretch. If you are reading this
because the session died, this file plus the checkpoints on disk are enough to
restart without losing work.

## State: both ingestion cards are done

Nothing is running. T2a and T2b are complete, gated and pushed; the loop stopped
there because T2b's card says to. **Do not begin T3 without a checkpoint.**

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
5.35 billion ticks, zero duplicates, 61.96 GiB.**

T2b's 13,015 gaps are almost entirely one pair: 12,998 CROSSED_QUOTE in AUDUSD
across 2007-04..2008-09 and 2009-04..2010-10. The feed served those hours and
the pipeline refused them. `reports/T2b_backfill.md` has the analysis, including
why the decoder was ruled out first.

**Anyone using AUDUSD before 2011 must read that section.** Its per-year
completeness is 51%, 50%, 56% and 36% for 2007-2010 while every other pair is
100.00% in every year.

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
