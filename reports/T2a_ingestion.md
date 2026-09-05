# T2a — Bulk ingestion, 2015-01-01 → 2025-02-28, 12 pairs

**Task card:** `taskcards/T2a.md` · **Experiment:** `T2a-ingestion` · **Seed:** 20260820 · **Result hash:** `8a1f1ad775dcb7f3`

**Trials ledgered under T2a:** 18 (SPEC2 pre-reg #10; the count includes the bulk-ingest sessions, which are data collection rather than analysis).

This is an **ingestion**, not an analysis. Every number below is read back off disk — from the sharded manifests, from the tick store's own directory listings, and from the bar tables through the research loader. No strategy content appears anywhere in it, the experiment is not scorable and it carries no scorecard.

Two things are worth stating before the numbers, because they decide what the numbers mean.

* **Every hour went through the identical Phase 1 pipeline.** The driver decides the order, the rate and what to do about an outage; it decodes, validates and stores nothing. Crossed quotes, non-positive prices, Saturday ticks and out-of-hour ticks reject an hour here exactly as they do in the Phase 1 gate, and duplicates are dropped and counted rather than tolerated silently.
* **Closed hours are derived, not assumed.** The FX week tracks 17:00 `America/New_York`, so it sits at 21:00 UTC in northern summer and 22:00 UTC in winter. Hours the derived boundary calls shut are recorded as `closed` without being fetched — with one deliberate exception: the shut hour on either side of every boundary **is** fetched, so the derivation is checked against the feed every week rather than trusted. The result of that check is in *Validation anomalies*.

## What is in the store

| measure | value |
| --- | --- |
| window | 2015-01-01 → 2025-02-28 (3,712 days) |
| pairs | 12 |
| hours in the range, per pair | 89,088 |
| of which the derived week calls open, per pair | 63,646 |
| **open hours expected, all pairs** | **763,752** |
| open hours accounted for (`ok` + `empty`) | 763,752 (100.00%) |
| hours stored with ticks (`ok`) | 760,195 |
| open hours the feed served empty (`empty`) | 3,557 |
| hours recorded closed (`closed`) | 305,304 |
| **gaps** | **0** |
| manifest entries written | 1,069,056 |
| ticks stored | 3,298,569,754 |
| duplicate ticks dropped | 0 |
| tick Parquet files | 760,195 |
| tick store on disk | 37.65 GiB |
| bar rows inside this window | 57,040,344 |
| compressed bytes served by the feed | 14.23 GiB |

An hour is `ok` when it decoded, validated and stored; `empty` when the feed served a zero-byte body during an hour the derived week calls open; `closed` when the week was shut; and a `gap` when it could not be had at all. Every requested hour has exactly one entry, closed ones included — a pipeline whose failures are invisible produces a dataset whose holes are invisible too.

## Data excluded from research (ruling R1)

These hours were ingested, validated and stored. A ruling then put them out of reach of research, and `research.loader` refuses them with `PAIR_EXCLUDED_WINDOW`. The counts are what the ruling costs this window; every other table in this report describes the **ingestion**, which did happen, and so still includes them.

| pair | ruling | window | ok hours | empty | gap | ticks | bar rows |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDUSD` | R1 | before 2011-01-01 | 0 | 0 | 0 | 0 | 0 |

* `AUDUSD` before 2011-01-01 (ruling R1): crossed-quote corruption in two bounded episodes, 2007-04 to 2008-09 and 2009-04 to 2010-10, rejected most of four years and left what survived a biased sample of the window rather than merely a thin one.

## Per-pair coverage

| pair | ok | empty | closed | gap | open-hour completeness | T1 data %, same years | days with data | ticks | dupes dropped |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 63,350 | 296 | 25,442 | 0 | 100.00% | 99.43% | 3,176 | 321,203,453 | 0 |
| `AUDUSD` | 63,351 | 295 | 25,442 | 0 | 100.00% | 99.43% | 3,177 | 208,614,799 | 0 |
| `EURCHF` | 63,346 | 300 | 25,442 | 0 | 100.00% | 99.43% | 3,177 | 200,414,099 | 0 |
| `EURGBP` | 63,350 | 296 | 25,442 | 0 | 100.00% | 99.43% | 3,177 | 245,404,562 | 0 |
| `EURJPY` | 63,352 | 294 | 25,442 | 0 | 100.00% | 99.43% | 3,178 | 453,723,211 | 0 |
| `EURUSD` | 63,354 | 292 | 25,442 | 0 | 100.00% | 99.43% | 3,178 | 285,075,993 | 0 |
| `GBPJPY` | 63,350 | 296 | 25,442 | 0 | 100.00% | 99.43% | 3,176 | 404,142,816 | 0 |
| `GBPUSD` | 63,349 | 297 | 25,442 | 0 | 100.00% | 99.43% | 3,176 | 293,307,301 | 0 |
| `NZDUSD` | 63,345 | 301 | 25,442 | 0 | 100.00% | 99.43% | 3,174 | 176,469,424 | 0 |
| `USDCAD` | 63,350 | 296 | 25,442 | 0 | 100.00% | 99.43% | 3,177 | 239,385,424 | 0 |
| `USDCHF` | 63,346 | 300 | 25,442 | 0 | 100.00% | 99.43% | 3,177 | 180,391,062 | 0 |
| `USDJPY` | 63,352 | 294 | 25,442 | 0 | 100.00% | 99.43% | 3,178 | 290,437,610 | 0 |

**Open-hour completeness** is `(ok + empty) / open hours the derived week contains`. It reaches 100% when every open hour of the range is accounted for — including the ones the feed answered empty, which are an answer rather than a hole.

**T1 data %** is the comparison the card asks for, quoted from the coverage survey and re-totalled over exactly the years this card covers: the share of trading days whose 13:00 UTC probe returned data. It is still a *different* measurement — one hour a day against every hour of every day — so the two columns are not expected to be equal. T1's number is depressed by closed trading days its single probe could not tell apart from absent ones; this column separates them.

## Completeness by year

Open-hour completeness per pair per year. This is the table a missing region would show up in.

| pair | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `AUDUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURCHF` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURGBP` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `GBPJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `GBPUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `NZDUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `USDCAD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `USDCHF` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `USDJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |

Years carrying at least one gap:

_none_

## Gaps

**0** hour(s) could not be had. A gap is an hour that exhausted its retries *while the feed was answering everything else*, or that arrived and would not decode or validate. An hour that failed while the feed was answering nothing at all is not a gap: the session parked and asked again, and an hour nobody finished asking about was left unsettled rather than recorded as a hole.

No hour of the range is missing.

### What the pull recorded, and what the sweep recovered

The count above is the **end** state, and on its own it flatters the run. During the pull itself **85** hour(s) across 76 pair-month(s) were recorded as gaps. The card's closing sweep re-asked every one of them, and **85** came back.

That is the difference between a gap meaning *absent history* and a gap meaning *a feed in a bad mood on the Tuesday it was asked*. Every gap this run recorded was the second kind. None of them was a hole in Dukascopy's history; all of them were hours the feed declined at the moment it was first asked and served without complaint when asked again.

Re-asking every gap is what makes the distinction available at all: a transient refusal clears on the second ask and a deterministic one does not. A run that reported only its final gap count would have hidden which kind it had.

## Validation anomalies

### Hard rejections

A hard validation failure rejects the hour, which is recorded as a gap carrying its reason token. These are the Phase 1 tokens, unchanged.

_none_

Counted from the rejected hours' own records, so a fetch failure appears here with the validation rejections rather than among the warnings — it is a reason an hour is missing, whatever filed it.

### Warnings on stored data

A warning records something worth knowing that is not a reason to reject data. Every count here is a flag carried by an hour that **is** in the store. `EMPTY_TRADING_HOUR` — the feed serving nothing during an hour the derived week calls open — is derived from the hour's status per ruling R5, so this row and the boundary audit below cannot disagree. It is the holiday-calendar input of pre-reg #5, counted here and interpreted nowhere.

| reason | hours |
| --- | --- |
| `EMPTY_TRADING_HOUR` | 3,557 |
| `SPREAD_OUTLIER` | 2,412 |
| `TICK_COUNT_OUTLIER` | 27 |

#### Where the spread flags fall

`SPREAD_OUTLIER` fired on 2,412 hour(s). 53% fall on 21:00Z alone and **79% on 21:00Z and 22:00Z together**. Those two hours are not two phenomena: they are the same one, the 17:00 `America/New_York` roll, which sits at 21:00Z in northern summer and 22:00Z in winter. The flags track the boundary as it moves with daylight saving, which is the same derivation the closed-hour logic uses and an independent check on it. At the roll liquidity is handed between sessions and the spread on a thin cross legitimately blows out; a flag that concentrates there is describing the market, where one scattered evenly across the clock would have been describing a ceiling set too low.

| hour (UTC) | hours flagged | share |
| --- | --- | --- |
| 21:00Z | 1,269 | 52.6% |
| 22:00Z | 630 | 26.1% |
| 23:00Z | 84 | 3.5% |
| 20:00Z | 83 | 3.4% |
| 00:00Z | 43 | 1.8% |
| 07:00Z | 36 | 1.5% |

By year:

| year | hours flagged |
| --- | --- |
| 2015 | 128 |
| 2016 | 118 |
| 2017 | 102 |
| 2018 | 101 |
| 2019 | 98 |
| 2020 | 236 |
| 2021 | 189 |
| 2022 | 526 |
| 2023 | 460 |
| 2024 | 377 |
| 2025 | 77 |

**Ruling R3 applies to this table and forbids the obvious reading of it.** A flag fires when an hour's p99.9 spread clears a fixed ceiling, and p99.9 over an hour holding a thousand ticks is not the same instrument as p99.9 over one holding six thousand. So a year with more flags may have had wider spreads, or more ticks, and this column cannot tell you which. Comparing spread regimes across eras requires a statistic that controls for ticks per hour — medians, p90, fixed-sample — and that is T5's work, not a conclusion available here.

### The derived week boundary, checked against the feed

The shut hour either side of every week boundary was fetched rather than assumed, at about 1.7% more requests than skipping them. What came back:

| pair | shut hours fetched | shut but carried ticks | open but served empty |
| --- | --- | --- | --- |
| `AUDJPY` | 1,061 | 0 | 296 |
| `AUDUSD` | 1,061 | 0 | 295 |
| `EURCHF` | 1,061 | 0 | 300 |
| `EURGBP` | 1,061 | 0 | 296 |
| `EURJPY` | 1,061 | 0 | 294 |
| `EURUSD` | 1,061 | 0 | 292 |
| `GBPJPY` | 1,061 | 0 | 296 |
| `GBPUSD` | 1,061 | 0 | 297 |
| `NZDUSD` | 1,061 | 0 | 301 |
| `USDCAD` | 1,061 | 0 | 296 |
| `USDCHF` | 1,061 | 0 | 300 |
| `USDJPY` | 1,061 | 0 | 294 |

The derivation and the feed agree: no hour the derived week called shut came back carrying ticks.

Across the universe, 3,557 hour(s) the derived week calls open were served empty. That is the same number as the `EMPTY_TRADING_HOUR` row above, and necessarily so: both are the count of hours whose status is `empty` inside the trading week. Most of them are holidays, and T3's calendar is what decides which.

## Throughput, and what it cost

Recorded because T2b ingests the same feed for the years before this range and should budget from a measurement rather than from optimism.

| measure | value | source |
| --- | --- | --- |
| sessions that finished | 8 | `sessions.jsonl` |
| pair-months completed | 1,464 | `chunks.jsonl` |
| requests attributable to the stored chunks | 735,237 | `chunks.jsonl` |
| throttled responses on those | 16,832 (2.29%) | `chunks.jsonl` |
| time inside the ingest pipeline | 133.5 h | `chunks.jsonl` |
| rate over pipeline time | 1.53 requests/s | `chunks.jsonl` |
| requests the sessions actually issued | 662,598 | `sessions.jsonl` |
| throttled responses on those | 19,946 (3.01%) | `sessions.jsonl` |
| wall clock across sessions | 148.4 h | `sessions.jsonl` |
| of which parked waiting out the feed | 34.5 h (23.22%) | `sessions.jsonl` |
| rate over wall clock | 1.24 requests/s | `sessions.jsonl` |
| time building bars | 8.0 h | `chunks.jsonl` |

**Two request counts, and they are different numbers.** The chunk log is keyed by pair-month and rewritten whenever a chunk is re-worked, so it reports the requests attributable to the store as it now stands. The session log is append-only, so it reports every request the process ever issued, including ones for chunks later re-done. Neither is wrong and each is the right answer to a different question — but a rate that divided one log's numerator by the other log's denominator would be the answer to neither, so each rate above stays inside one source and the column says which.

### Concurrency calibration

The rule was fixed before the run: start at level 2 — T1's proven-safe setting — and after an unbroken clean hour probe the next level, to the card's ceiling of 4. A level is judged against the measured throttle rate of the level below it, and two consecutive ten-minute windows above 1.5× that rate (or two percentage points above it) back the level off and block it for six hours.

A level here is both a connection count and a paced rate: level *n* means *n* connections and a gap of `0.8/n` seconds. Raising the connection count alone changes nothing measurable — a fetch costs about a second, so the worker count is what binds — and probing a concurrency that cannot offer more load would not be a probe.

| level | pair-months | requests | throttles | throttle rate | requests/s | ingest time |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 387 | 164,889 | 3,932 | 2.38% | 1.20 | 38.1 h |
| 3 | 613 | 324,403 | 7,382 | 2.28% | 1.56 | 57.8 h |
| 4 | 464 | 245,945 | 5,518 | 2.24% | 1.82 | 37.5 h |

Transitions, with the measurement that caused each:

| session | at | to level | why |
| --- | --- | --- | --- |
| 1 | 0 min | 2 | start; T1's proven-safe level |
| 2 | 0 min | 2 | start; T1's proven-safe level |
| 3 | 0 min | 2 | start; T1's proven-safe level |
| 4 | 0 min | 2 | start; T1's proven-safe level |
| 4 | 267 min | 3 | clean for 3600s at level 2 (throttle rate 2.326%) |
| 5 | 0 min | 3 | start; T1's proven-safe level |
| 5 | 410 min | 4 | clean for 3600s at level 3 (throttle rate 1.090%) |
| 5 | 492 min | 3 | level 4 blocked: 4.673% throttled against a tolerance of 3.345% for 2 consecutive windows |
| 6 | 0 min | 3 | start; T1's proven-safe level |
| 6 | 651 min | 4 | clean for 3600s at level 3 (throttle rate 0.936%) |
| 6 | 813 min | 3 | level 4 blocked: 10.036% throttled against a tolerance of 4.527% for 2 consecutive windows |
| 6 | 1184 min | 4 | clean for 3600s at level 3 (throttle rate 0.623%) |
| 6 | 1304 min | 3 | level 4 blocked: 3.824% throttled against a tolerance of 2.765% for 2 consecutive windows |
| 6 | 1671 min | 4 | clean for 3600s at level 3 (throttle rate 8.929%) |
| 7 | 0 min | 4 | resumed at level 4, earned earlier in the run; baselines carried forward {2: 0.0244, 3: 0.0535, 4: 0.0166} |
| 7 | 629 min | 3 | level 4 blocked: 9.626% throttled against a tolerance of 8.021% for 2 consecutive windows |
| 7 | 994 min | 4 | clean for 3600s at level 3 (throttle rate 2.307%) |
| 7 | 1056 min | 3 | level 4 blocked: 10.386% throttled against a tolerance of 3.573% for 2 consecutive windows |
| 7 | 1312 min | 2 | level 3 blocked: 20.336% throttled against a tolerance of 4.445% for 2 consecutive windows |
| 7 | 1689 min | 3 | clean for 3600s at level 2 (throttle rate 7.172%) |
| 7 | 1757 min | 4 | clean for 3600s at level 3 (throttle rate 1.230%) |
| 7 | 1815 min | 3 | level 4 blocked: 5.426% throttled against a tolerance of 3.162% for 2 consecutive windows |
| 7 | 2186 min | 4 | clean for 3600s at level 3 (throttle rate 2.185%) |
| 7 | 2300 min | 3 | level 4 blocked: 9.959% throttled against a tolerance of 4.122% for 2 consecutive windows |
| 7 | 2663 min | 4 | clean for 3600s at level 3 (throttle rate 0.751%) |
| 7 | 2749 min | 3 | level 4 blocked: 5.119% throttled against a tolerance of 3.623% for 2 consecutive windows |
| 7 | 3067 min | 2 | level 3 blocked: 12.143% throttled against a tolerance of 7.272% for 2 consecutive windows |
| 7 | 3437 min | 3 | clean for 3600s at level 2 (throttle rate 6.481%) |
| 7 | 3507 min | 4 | clean for 3600s at level 3 (throttle rate 0.980%) |
| 7 | 3554 min | 3 | level 4 blocked: 10.000% throttled against a tolerance of 4.010% for 2 consecutive windows |
| 7 | 3930 min | 4 | clean for 3600s at level 3 (throttle rate 2.186%) |
| 7 | 4141 min | 3 | level 4 blocked: 10.280% throttled against a tolerance of 3.911% for 2 consecutive windows |
| 7 | 4516 min | 2 | level 3 blocked: 10.271% throttled against a tolerance of 8.752% for 2 consecutive windows |
| 7 | 4881 min | 3 | clean for 3600s at level 2 (throttle rate 1.829%) |
| 7 | 4944 min | 4 | clean for 3600s at level 3 (throttle rate 2.952%) |
| 7 | 4991 min | 3 | level 4 blocked: 5.098% throttled against a tolerance of 4.668% for 2 consecutive windows |
| 7 | 5051 min | 2 | level 3 blocked: 10.041% throttled against a tolerance of 4.232% for 2 consecutive windows |
| 7 | 5421 min | 3 | clean for 3600s at level 2 (throttle rate 1.028%) |
| 7 | 5481 min | 2 | level 3 blocked: 13.760% throttled against a tolerance of 3.060% for 2 consecutive windows |
| 7 | 5849 min | 3 | clean for 3600s at level 2 (throttle rate 0.840%) |
| 7 | 5911 min | 4 | clean for 3600s at level 3 (throttle rate 1.766%) |
| 7 | 5973 min | 3 | level 4 blocked: 5.123% throttled against a tolerance of 3.369% for 2 consecutive windows |
| 7 | 6064 min | 2 | level 3 blocked: 3.399% throttled against a tolerance of 3.229% for 2 consecutive windows |
| 7 | 6437 min | 3 | clean for 3600s at level 2 (throttle rate 1.553%) |
| 7 | 6508 min | 4 | clean for 3600s at level 3 (throttle rate 1.561%) |
| 7 | 6536 min | 3 | level 4 blocked: 5.243% throttled against a tolerance of 3.428% for 2 consecutive windows |
| 7 | 6751 min | 2 | level 3 blocked: 4.122% throttled against a tolerance of 3.381% for 2 consecutive windows |
| 7 | 7116 min | 3 | clean for 3600s at level 2 (throttle rate 0.648%) |
| 7 | 7184 min | 4 | clean for 3600s at level 3 (throttle rate 1.298%) |
| 7 | 7230 min | 3 | level 4 blocked: 14.652% throttled against a tolerance of 3.291% for 2 consecutive windows |
| 7 | 7300 min | 2 | level 3 blocked: 14.766% throttled against a tolerance of 3.713% for 2 consecutive windows |
| 8 | 0 min | 2 | start; T1's proven-safe level; baselines carried forward {2: 0.0489, 3: 0.017, 4: 0.019} |
| 8 | 7406 min | 2 | level 3 blocked: 46.667% throttled against a tolerance of 10.000% for 2 consecutive windows |

### Sessions

| # | status | pair-months | hours ok | wall | requests/s | outages ridden out | parked |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | ok | 1 | 433 | 0.2 h | 0.65 | 0 | 0.1 h |
| 2 | ok | 2 | 606 | 0.2 h | 0.29 | 0 | 0.0 h |
| 3 | ok | 4 | 1,541 | 0.4 h | 1.12 | 0 | 0.1 h |
| 4 | ok | 23 | 11,260 | 2.5 h | 1.29 | 1 | 0.5 h |
| 5 | ok | 36 | 18,760 | 3.8 h | 1.39 | 1 | 1.1 h |
| 6 | ok | 206 | 106,826 | 18.3 h | 1.65 | 0 | 3.9 h |
| 7 | ok | 983 | 510,060 | 122.2 h | 1.19 | 24 | 28.7 h |
| 8 | ok | 76 | 39,394 | 0.9 h | 0.03 | 0 | 0.1 h |

A session that was interrupted leaves a ledger start record and no end record, which is what the ledger is for. Only sessions that finished and reported their own counters appear here.

## Storage footprint

| pair | tick files | day partitions | on disk | ticks | bytes/tick |
| --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 63,350 | 3,176 | 3.59 GiB | 321,203,453 | 12.0 |
| `AUDUSD` | 63,351 | 3,177 | 2.43 GiB | 208,614,799 | 12.5 |
| `EURCHF` | 63,346 | 3,177 | 2.22 GiB | 200,414,099 | 11.9 |
| `EURGBP` | 63,350 | 3,177 | 2.75 GiB | 245,404,562 | 12.0 |
| `EURJPY` | 63,352 | 3,178 | 5.05 GiB | 453,723,211 | 12.0 |
| `EURUSD` | 63,354 | 3,178 | 3.39 GiB | 285,075,993 | 12.8 |
| `GBPJPY` | 63,350 | 3,176 | 4.56 GiB | 404,142,816 | 12.1 |
| `GBPUSD` | 63,349 | 3,176 | 3.39 GiB | 293,307,301 | 12.4 |
| `NZDUSD` | 63,345 | 3,174 | 2.02 GiB | 176,469,424 | 12.3 |
| `USDCAD` | 63,350 | 3,177 | 2.74 GiB | 239,385,424 | 12.3 |
| `USDCHF` | 63,346 | 3,177 | 2.11 GiB | 180,391,062 | 12.6 |
| `USDJPY` | 63,352 | 3,178 | 3.41 GiB | 290,437,610 | 12.6 |

Total tick store: **37.65 GiB** across 760,195 files — one Parquet per ingested hour, so an hour can be re-ingested without rewriting a day and a partial day is still readable.

## Bar tables

Bars are built incrementally (SPEC2 prerequisite P0-B, landed for this card). Only the days whose stored ticks changed since the last build are resampled, and the coarser timeframes are rolled up from the 1m bars rather than re-read from ticks — which is exact, because every timeframe in the research set tiles UTC days and the bins nest.

Rows per pair and timeframe, **counted inside this card's window**. A bar table is one file per pair covering the whole store, so an unbounded count would hand this card every row every other card ever built; where ruling R1 excludes part of the window the count stops at the exclusion and the *Data excluded* table above carries the remainder (0 row(s)).

| pair | `1min` | `5min` | `30min` | `1h` | `4h` | `1D` |
| --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 3,792,694 | 759,906 | 126,690 | 63,350 | 16,373 | 3,176 |
| `AUDUSD` | 3,784,661 | 759,950 | 126,694 | 63,351 | 16,374 | 3,177 |
| `EURCHF` | 3,771,823 | 759,310 | 126,675 | 63,346 | 16,374 | 3,177 |
| `EURGBP` | 3,785,985 | 759,814 | 126,694 | 63,350 | 16,374 | 3,177 |
| `EURJPY` | 3,791,028 | 759,554 | 126,691 | 63,352 | 16,375 | 3,178 |
| `EURUSD` | 3,787,603 | 760,051 | 126,701 | 63,354 | 16,375 | 3,178 |
| `GBPJPY` | 3,790,450 | 759,460 | 126,688 | 63,350 | 16,373 | 3,176 |
| `GBPUSD` | 3,789,760 | 759,924 | 126,692 | 63,349 | 16,373 | 3,176 |
| `NZDUSD` | 3,778,192 | 759,635 | 126,678 | 63,345 | 16,371 | 3,174 |
| `USDCAD` | 3,783,426 | 759,808 | 126,691 | 63,350 | 16,374 | 3,177 |
| `USDCHF` | 3,765,977 | 759,492 | 126,675 | 63,346 | 16,374 | 3,177 |
| `USDJPY` | 3,786,755 | 760,019 | 126,697 | 63,352 | 16,375 | 3,178 |

Build cost, one build per pair-month:

| timeframe | builds | days folded in | rows spliced | total time | per build |
| --- | --- | --- | --- | --- | --- |
| `1min` | 1,463 | 36,180 | 43,117,435 | 22470 s | 15359 ms |
| `5min` | 1,463 | 36,180 | 8,657,241 | 4711 s | 3220 ms |
| `30min` | 1,463 | 36,180 | 1,443,621 | 855 s | 584 ms |
| `1h` | 1,463 | 36,180 | 721,870 | 459 s | 314 ms |
| `4h` | 1,463 | 36,180 | 186,570 | 156 s | 106 ms |
| `1D` | 1,463 | 36,180 | 36,180 | 70 s | 48 ms |

On disk:

| timeframe | size |
| --- | --- |
| `1min` | 3355.4 MiB |
| `5min` | 792.1 MiB |
| `30min` | 176.9 MiB |
| `1h` | 106.8 MiB |
| `4h` | 37.1 MiB |
| `1D` | 8.6 MiB |

## Observations

Recorded for the checkpoint review. Per the card, an observation worth chasing becomes a next card only after a checkpoint; nothing here proposes work.

* The least complete pair is `AUDJPY` at 100.00% of the open hours the derived week contains. T1 found no missing region in this range and predicted near-complete coverage; that prediction is what this column tests.
* **No duplicate ticks at all.** De-duplication is on the whole record, so two ticks sharing a millisecond but differing in price or volume are both kept; the feed served none that were identical.
* 3,557 hour(s) the derived week calls open were served empty. Those are candidate holidays and are pre-reg #5's raw material; T3 turns them into a calendar, and until it does an empty open hour stays a warning rather than a `closed`.
* The tick store averages 12.3 bytes per stored tick after Snappy, which is what a later card should size a comparable pull with.
* **The wall clock understates the run.** `sessions.jsonl` is written only by a session that finishes. On 2026-08-22 the host lost power mid-chunk and that session died without writing its record, so its pair-months are in the store and in `chunks.jsonl` — every coverage number counts them, and the pipeline-time row includes them — while the session wall clock misses them entirely. A later pull should budget from the chunk-sourced rows, not the session-sourced ones.
* **Level 4 was reachable and never holdable.** The calibrator probed the card ceiling and backed off from it every time. Several back-offs and several level-3 failures landed at much the same throttle rate, which is what a feed expressing its limit as a fixed refusal *share* rather than a fixed rate would look like. The aggregate still favoured concurrency — the level table above ranks them — so the ceiling earned its place; it simply could not be held.
* **The spread flags are an independent check on the week boundary.** The hours they concentrate on are the 17:00 `America/New_York` roll in its summer and winter positions, which is the boundary the closed-hour logic derives. Two unrelated parts of the pipeline agree about where the FX day ends, and neither was told the answer.
* **Storage is the window, not the store.** `data/research/` is shared between the ingestion cards. The footprint walk and the bar-row count are both bounded by this experiment window, so the figures above are what this card added rather than what is on disk.

## Provenance

* Config: `experiments/T2a-ingestion/config.toml` (sha256 `322897c574d51acc`)
* Manifests: `data/research/manifests/pair=<PAIR>/<YYYY-MM>/manifest.json` — one shard per pair-month, one entry per requested hour. Read the canonical way (SPEC2 §The canonical manifest reading): hour records and the derived coverage block, never the session warning log.
* Progress records: `experiments/T2a-ingestion/chunks.jsonl` and `experiments/T2a-ingestion/sessions.jsonl`
* Result: `experiments/T2a-ingestion/result.json`, hash `8a1f1ad775dcb7f3e8d25adfe94cf86f8107c40f36a36107e8675fd4066a8200`
* Loader mode `scoring`, scored `False`, re-run class `full`. The loader served 72 bar file(s) across 12 pair(s) and 3178 date(s); sealed dates served: none; dates withheld by an exclusion window: 0.
* Research gate: exit 0 (full, 2026-09-05)

