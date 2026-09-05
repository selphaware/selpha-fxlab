# T2a — Bulk ingestion, 2005-01-03 → 2014-12-31, 12 pairs

**Task card:** `taskcards/T2a.md` · **Experiment:** `T2b-backfill` · **Seed:** 20260827 · **Result hash:** `725d008ef1d698d3`

**Trials ledgered under T2a:** 5 (SPEC2 pre-reg #10; the count includes the bulk-ingest sessions, which are data collection rather than analysis).

This is an **ingestion**, not an analysis. Every number below is read back off disk — from the sharded manifests, from the tick store's own directory listings, and from the bar tables through the research loader. No strategy content appears anywhere in it, the experiment is not scorable and it carries no scorecard.

Two things are worth stating before the numbers, because they decide what the numbers mean.

* **Every hour went through the identical Phase 1 pipeline.** The driver decides the order, the rate and what to do about an outage; it decodes, validates and stores nothing. Crossed quotes, non-positive prices, Saturday ticks and out-of-hour ticks reject an hour here exactly as they do in the Phase 1 gate, and duplicates are dropped and counted rather than tolerated silently.
* **Closed hours are derived, not assumed.** The FX week tracks 17:00 `America/New_York`, so it sits at 21:00 UTC in northern summer and 22:00 UTC in winter. Hours the derived boundary calls shut are recorded as `closed` without being fetched — with one deliberate exception: the shut hour on either side of every boundary **is** fetched, so the derivation is checked against the feed every week rather than trusted. The result of that check is in *Validation anomalies*.

## What is in the store

| measure | value |
| --- | --- |
| window | 2005-01-03 → 2014-12-31 (3,650 days) |
| pairs | 12 |
| hours in the range, per pair | 87,600 |
| of which the derived week calls open, per pair | 62,592 |
| **open hours expected, all pairs** | **751,104** |
| open hours accounted for (`ok` + `empty`) | 738,105 (98.27%) |
| hours stored with ticks (`ok`) | 735,545 |
| open hours the feed served empty (`empty`) | 2,560 |
| hours recorded closed (`closed`) | 300,080 |
| **gaps** | **13,015** |
| manifest entries written | 1,051,200 |
| ticks stored | 2,049,194,460 |
| duplicate ticks dropped | 0 |
| tick Parquet files | 735,545 |
| tick store on disk | 24.31 GiB |
| bar rows built | 112,321,350 |
| compressed bytes served by the feed | 9.37 GiB |

An hour is `ok` when it decoded, validated and stored; `empty` when the feed served a zero-byte body during an hour the derived week calls open; `closed` when the week was shut; and a `gap` when it could not be had at all. Every requested hour has exactly one entry, closed ones included — a pipeline whose failures are invisible produces a dataset whose holes are invisible too.

## Per-pair coverage

| pair | ok | empty | closed | gap | open-hour completeness | T1 data %, same years | days with data | ticks | dupes dropped |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 62,513 | 79 | 25,008 | 0 | 100.00% | 99.85% | 3,129 | 210,098,578 | 0 |
| `AUDUSD` | 48,623 | 972 | 25,008 | 12,997 | 79.24% | 99.31% | 3,024 | 117,396,382 | 0 |
| `EURCHF` | 62,516 | 76 | 25,008 | 0 | 100.00% | 99.85% | 3,129 | 155,353,423 | 0 |
| `EURGBP` | 62,510 | 82 | 25,008 | 0 | 100.00% | 99.81% | 3,129 | 169,556,250 | 0 |
| `EURJPY` | 62,173 | 419 | 24,998 | 10 | 100.00% | 99.39% | 3,120 | 233,564,035 | 0 |
| `EURUSD` | 62,514 | 78 | 25,008 | 0 | 100.00% | 99.85% | 3,129 | 179,770,072 | 0 |
| `GBPJPY` | 62,511 | 81 | 25,008 | 0 | 100.00% | 99.81% | 3,129 | 229,008,354 | 0 |
| `GBPUSD` | 62,510 | 82 | 25,008 | 0 | 100.00% | 99.81% | 3,129 | 183,785,649 | 0 |
| `NZDUSD` | 62,479 | 113 | 25,008 | 0 | 100.00% | 99.81% | 3,128 | 115,865,319 | 0 |
| `USDCAD` | 62,508 | 84 | 25,008 | 0 | 100.00% | 99.81% | 3,129 | 119,755,096 | 0 |
| `USDCHF` | 62,515 | 77 | 25,008 | 0 | 100.00% | 99.85% | 3,129 | 168,403,733 | 0 |
| `USDJPY` | 62,173 | 417 | 25,002 | 8 | 100.00% | 99.39% | 3,120 | 166,637,569 | 0 |

**Open-hour completeness** is `(ok + empty) / open hours the derived week contains`. It reaches 100% when every open hour of the range is accounted for — including the ones the feed answered empty, which are an answer rather than a hole.

**T1 data %** is the comparison the card asks for, quoted from the coverage survey and re-totalled over exactly the years this card covers: the share of trading days whose 13:00 UTC probe returned data. It is still a *different* measurement — one hour a day against every hour of every day — so the two columns are not expected to be equal. T1's number is depressed by closed trading days its single probe could not tell apart from absent ones; this column separates them.

## Completeness by year

Open-hour completeness per pair per year. This is the table a missing region would show up in.

| pair | 2005 | 2006 | 2007 | 2008 | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `AUDUSD` | 100.00% | 100.00% | 51.10% | 49.67% | 56.26% | 35.66% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURCHF` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURGBP` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `EURUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `GBPJPY` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `GBPUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `NZDUSD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `USDCAD` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `USDCHF` | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `USDJPY` | 100.00% | 100.00% | 100.00% | 99.98% | 100.00% | 99.98% | 100.00% | 100.00% | 100.00% | 100.00% |

Years carrying at least one gap:

| pair | year | gap hours | ok hours |
| --- | --- | --- | --- |
| `AUDUSD` | 2007 | 3,063 | 2,827 |
| `AUDUSD` | 2008 | 3,165 | 2,856 |
| `AUDUSD` | 2009 | 2,740 | 3,335 |
| `AUDUSD` | 2010 | 4,029 | 2,193 |
| `EURJPY` | 2011 | 1 | 6,238 |
| `EURJPY` | 2012 | 9 | 6,237 |
| `USDJPY` | 2008 | 1 | 6,232 |
| `USDJPY` | 2010 | 1 | 6,231 |
| `USDJPY` | 2011 | 1 | 6,238 |
| `USDJPY` | 2012 | 5 | 6,241 |

## Gaps

**13,015** hour(s) could not be had. A gap is an hour that exhausted its retries *while the feed was answering everything else*, or that arrived and would not decode or validate. An hour that failed while the feed was answering nothing at all is not a gap: the session parked and asked again, and an hour nobody finished asking about was left unsettled rather than recorded as a hole.

| pair | date | hour | reason | detail |
| --- | --- | --- | --- | --- |
| `AUDUSD` | 2007-04-02 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T02:00Z: 3 crossed quote(s) (bid > ask); first at index 60 ts=2007-04-02T02:17:33.479000Z bid=np.float64(0.81357) ask=np.float64(0.81353) |
| `AUDUSD` | 2007-04-02 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T03:00Z: 1 crossed quote(s) (bid > ask); first at index 244 ts=2007-04-02T03:59:27.175000Z bid=np.float64(0.81367) ask=np.float64(0.81363) |
| `AUDUSD` | 2007-04-02 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T04:00Z: 2 crossed quote(s) (bid > ask); first at index 10 ts=2007-04-02T04:02:13.373000Z bid=np.float64(0.81407) ask=np.float64(0.81403) |
| `AUDUSD` | 2007-04-02 | 05:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T05:00Z: 1 crossed quote(s) (bid > ask); first at index 2 ts=2007-04-02T05:00:19.029000Z bid=np.float64(0.81417) ask=np.float64(0.81413) |
| `AUDUSD` | 2007-04-02 | 07:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T07:00Z: 2 crossed quote(s) (bid > ask); first at index 159 ts=2007-04-02T07:48:19.233000Z bid=np.float64(0.81547) ask=np.float64(0.81543) |
| `AUDUSD` | 2007-04-02 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T08:00Z: 4 crossed quote(s) (bid > ask); first at index 181 ts=2007-04-02T08:26:02.939000Z bid=np.float64(0.81687) ask=np.float64(0.81683) |
| `AUDUSD` | 2007-04-02 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T09:00Z: 5 crossed quote(s) (bid > ask); first at index 105 ts=2007-04-02T09:16:00.426000Z bid=np.float64(0.81797) ask=np.float64(0.81793) |
| `AUDUSD` | 2007-04-02 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T10:00Z: 3 crossed quote(s) (bid > ask); first at index 112 ts=2007-04-02T10:13:13.365000Z bid=np.float64(0.81807) ask=np.float64(0.81803) |
| `AUDUSD` | 2007-04-02 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T11:00Z: 7 crossed quote(s) (bid > ask); first at index 3 ts=2007-04-02T11:00:20.545000Z bid=np.float64(0.81687) ask=np.float64(0.81683) |
| `AUDUSD` | 2007-04-02 | 12:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T12:00Z: 1 crossed quote(s) (bid > ask); first at index 57 ts=2007-04-02T12:30:55.803000Z bid=np.float64(0.81667) ask=np.float64(0.81663) |
| `AUDUSD` | 2007-04-02 | 17:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T17:00Z: 2 crossed quote(s) (bid > ask); first at index 71 ts=2007-04-02T17:17:30.056000Z bid=np.float64(0.81667) ask=np.float64(0.81663) |
| `AUDUSD` | 2007-04-02 | 18:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T18:00Z: 1 crossed quote(s) (bid > ask); first at index 6 ts=2007-04-02T18:02:03.851000Z bid=np.float64(0.81707) ask=np.float64(0.81703) |
| `AUDUSD` | 2007-04-02 | 21:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T21:00Z: 1 crossed quote(s) (bid > ask); first at index 52 ts=2007-04-02T21:30:06.128000Z bid=np.float64(0.81657) ask=np.float64(0.81623) |
| `AUDUSD` | 2007-04-02 | 23:00Z | CROSSED_QUOTE | AUDUSD 2007-04-02T23:00Z: 2 crossed quote(s) (bid > ask); first at index 57 ts=2007-04-02T23:25:33.382000Z bid=np.float64(0.81547) ask=np.float64(0.81543) |
| `AUDUSD` | 2007-04-03 | 00:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T00:00Z: 9 crossed quote(s) (bid > ask); first at index 10 ts=2007-04-03T00:04:44.997000Z bid=np.float64(0.81437) ask=np.float64(0.81433) |
| `AUDUSD` | 2007-04-03 | 01:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T01:00Z: 1 crossed quote(s) (bid > ask); first at index 5 ts=2007-04-03T01:01:50.504000Z bid=np.float64(0.81537) ask=np.float64(0.81533) |
| `AUDUSD` | 2007-04-03 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T02:00Z: 1 crossed quote(s) (bid > ask); first at index 143 ts=2007-04-03T02:36:06.317000Z bid=np.float64(0.81487) ask=np.float64(0.81483) |
| `AUDUSD` | 2007-04-03 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T03:00Z: 6 crossed quote(s) (bid > ask); first at index 16 ts=2007-04-03T03:03:52.240000Z bid=np.float64(0.81487) ask=np.float64(0.81483) |
| `AUDUSD` | 2007-04-03 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T04:00Z: 2 crossed quote(s) (bid > ask); first at index 55 ts=2007-04-03T04:19:08.461000Z bid=np.float64(0.81327) ask=np.float64(0.81323) |
| `AUDUSD` | 2007-04-03 | 05:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T05:00Z: 9 crossed quote(s) (bid > ask); first at index 8 ts=2007-04-03T05:00:46.783000Z bid=np.float64(0.81357) ask=np.float64(0.81353) |
| `AUDUSD` | 2007-04-03 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T06:00Z: 3 crossed quote(s) (bid > ask); first at index 122 ts=2007-04-03T06:33:34.967000Z bid=np.float64(0.81267) ask=np.float64(0.81263) |
| `AUDUSD` | 2007-04-03 | 07:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T07:00Z: 8 crossed quote(s) (bid > ask); first at index 28 ts=2007-04-03T07:04:57.061000Z bid=np.float64(0.81357) ask=np.float64(0.81353) |
| `AUDUSD` | 2007-04-03 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T08:00Z: 8 crossed quote(s) (bid > ask); first at index 10 ts=2007-04-03T08:01:30.697000Z bid=np.float64(0.81407) ask=np.float64(0.81403) |
| `AUDUSD` | 2007-04-03 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T09:00Z: 2 crossed quote(s) (bid > ask); first at index 48 ts=2007-04-03T09:06:40.040000Z bid=np.float64(0.81397) ask=np.float64(0.81393) |
| `AUDUSD` | 2007-04-03 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T10:00Z: 4 crossed quote(s) (bid > ask); first at index 145 ts=2007-04-03T10:22:18.122000Z bid=np.float64(0.81417) ask=np.float64(0.81413) |
| `AUDUSD` | 2007-04-03 | 19:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T19:00Z: 6 crossed quote(s) (bid > ask); first at index 24 ts=2007-04-03T19:08:19.501000Z bid=np.float64(0.81237) ask=np.float64(0.81233) |
| `AUDUSD` | 2007-04-03 | 23:00Z | CROSSED_QUOTE | AUDUSD 2007-04-03T23:00Z: 1 crossed quote(s) (bid > ask); first at index 67 ts=2007-04-03T23:38:03.927000Z bid=np.float64(0.80977) ask=np.float64(0.80973) |
| `AUDUSD` | 2007-04-04 | 00:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T00:00Z: 4 crossed quote(s) (bid > ask); first at index 67 ts=2007-04-04T00:07:54.099000Z bid=np.float64(0.81177) ask=np.float64(0.81173) |
| `AUDUSD` | 2007-04-04 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T02:00Z: 1 crossed quote(s) (bid > ask); first at index 45 ts=2007-04-04T02:12:40.675000Z bid=np.float64(0.81217) ask=np.float64(0.81213) |
| `AUDUSD` | 2007-04-04 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T03:00Z: 4 crossed quote(s) (bid > ask); first at index 77 ts=2007-04-04T03:16:32.388000Z bid=np.float64(0.81277) ask=np.float64(0.81273) |
| `AUDUSD` | 2007-04-04 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T04:00Z: 15 crossed quote(s) (bid > ask); first at index 22 ts=2007-04-04T04:01:28.766000Z bid=np.float64(0.81287) ask=np.float64(0.81283) |
| `AUDUSD` | 2007-04-04 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T06:00Z: 2 crossed quote(s) (bid > ask); first at index 31 ts=2007-04-04T06:14:54.254000Z bid=np.float64(0.81337) ask=np.float64(0.81333) |
| `AUDUSD` | 2007-04-04 | 07:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T07:00Z: 1 crossed quote(s) (bid > ask); first at index 21 ts=2007-04-04T07:03:31.712000Z bid=np.float64(0.81237) ask=np.float64(0.81233) |
| `AUDUSD` | 2007-04-04 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T08:00Z: 2 crossed quote(s) (bid > ask); first at index 11 ts=2007-04-04T08:01:20.491000Z bid=np.float64(0.81337) ask=np.float64(0.81333) |
| `AUDUSD` | 2007-04-04 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T09:00Z: 3 crossed quote(s) (bid > ask); first at index 150 ts=2007-04-04T09:22:39.738000Z bid=np.float64(0.81397) ask=np.float64(0.81393) |
| `AUDUSD` | 2007-04-04 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T10:00Z: 10 crossed quote(s) (bid > ask); first at index 45 ts=2007-04-04T10:04:32.147000Z bid=np.float64(0.81527) ask=np.float64(0.81523) |
| `AUDUSD` | 2007-04-04 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T11:00Z: 14 crossed quote(s) (bid > ask); first at index 67 ts=2007-04-04T11:07:24.252000Z bid=np.float64(0.81807) ask=np.float64(0.81793) |
| `AUDUSD` | 2007-04-04 | 14:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T14:00Z: 3 crossed quote(s) (bid > ask); first at index 56 ts=2007-04-04T14:24:14.956000Z bid=np.float64(0.81847) ask=np.float64(0.81843) |
| `AUDUSD` | 2007-04-04 | 17:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T17:00Z: 2 crossed quote(s) (bid > ask); first at index 11 ts=2007-04-04T17:00:27.779000Z bid=np.float64(0.81867) ask=np.float64(0.81853) |
| `AUDUSD` | 2007-04-04 | 19:00Z | CROSSED_QUOTE | AUDUSD 2007-04-04T19:00Z: 10 crossed quote(s) (bid > ask); first at index 134 ts=2007-04-04T19:41:48.134000Z bid=np.float64(0.82027) ask=np.float64(0.82013) |
| `AUDUSD` | 2007-04-05 | 00:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T00:00Z: 1 crossed quote(s) (bid > ask); first at index 93 ts=2007-04-05T00:50:14.764000Z bid=np.float64(0.81747) ask=np.float64(0.81743) |
| `AUDUSD` | 2007-04-05 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T02:00Z: 2 crossed quote(s) (bid > ask); first at index 81 ts=2007-04-05T02:20:13.070000Z bid=np.float64(0.81677) ask=np.float64(0.81673) |
| `AUDUSD` | 2007-04-05 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T03:00Z: 4 crossed quote(s) (bid > ask); first at index 33 ts=2007-04-05T03:04:01.619000Z bid=np.float64(0.81757) ask=np.float64(0.81753) |
| `AUDUSD` | 2007-04-05 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T04:00Z: 3 crossed quote(s) (bid > ask); first at index 117 ts=2007-04-05T04:32:22.946000Z bid=np.float64(0.81847) ask=np.float64(0.81843) |
| `AUDUSD` | 2007-04-05 | 05:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T05:00Z: 4 crossed quote(s) (bid > ask); first at index 20 ts=2007-04-05T05:09:28.243000Z bid=np.float64(0.81807) ask=np.float64(0.81803) |
| `AUDUSD` | 2007-04-05 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T06:00Z: 2 crossed quote(s) (bid > ask); first at index 24 ts=2007-04-05T06:09:03.588000Z bid=np.float64(0.81747) ask=np.float64(0.81743) |
| `AUDUSD` | 2007-04-05 | 07:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T07:00Z: 7 crossed quote(s) (bid > ask); first at index 202 ts=2007-04-05T07:28:59.877000Z bid=np.float64(0.81967) ask=np.float64(0.81963) |
| `AUDUSD` | 2007-04-05 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T09:00Z: 6 crossed quote(s) (bid > ask); first at index 72 ts=2007-04-05T09:13:50.421000Z bid=np.float64(0.81857) ask=np.float64(0.81853) |
| `AUDUSD` | 2007-04-05 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T10:00Z: 4 crossed quote(s) (bid > ask); first at index 9 ts=2007-04-05T10:00:29.751000Z bid=np.float64(0.81987) ask=np.float64(0.81983) |
| `AUDUSD` | 2007-04-05 | 21:00Z | CROSSED_QUOTE | AUDUSD 2007-04-05T21:00Z: 1 crossed quote(s) (bid > ask); first at index 58 ts=2007-04-05T21:31:25.782000Z bid=np.float64(0.81897) ask=np.float64(0.81893) |
| `AUDUSD` | 2007-04-06 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-06T08:00Z: 3 crossed quote(s) (bid > ask); first at index 519 ts=2007-04-06T08:46:28.696000Z bid=np.float64(0.81767) ask=np.float64(0.81763) |
| `AUDUSD` | 2007-04-06 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-06T09:00Z: 2 crossed quote(s) (bid > ask); first at index 131 ts=2007-04-06T09:23:45.215000Z bid=np.float64(0.81767) ask=np.float64(0.81763) |
| `AUDUSD` | 2007-04-06 | 13:00Z | CROSSED_QUOTE | AUDUSD 2007-04-06T13:00Z: 1 crossed quote(s) (bid > ask); first at index 1 ts=2007-04-06T13:00:31.093000Z bid=np.float64(0.81607) ask=np.float64(0.81603) |
| `AUDUSD` | 2007-04-09 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T02:00Z: 1 crossed quote(s) (bid > ask); first at index 49 ts=2007-04-09T02:30:13.944000Z bid=np.float64(0.81637) ask=np.float64(0.81613) |
| `AUDUSD` | 2007-04-09 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T11:00Z: 1 crossed quote(s) (bid > ask); first at index 59 ts=2007-04-09T11:30:19.196000Z bid=np.float64(0.81667) ask=np.float64(0.81613) |
| `AUDUSD` | 2007-04-09 | 16:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T16:00Z: 1 crossed quote(s) (bid > ask); first at index 2 ts=2007-04-09T16:03:07.187000Z bid=np.float64(0.81707) ask=np.float64(0.81613) |
| `AUDUSD` | 2007-04-09 | 17:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T17:00Z: 2 crossed quote(s) (bid > ask); first at index 1 ts=2007-04-09T17:03:32.781000Z bid=np.float64(0.81687) ask=np.float64(0.81683) |
| `AUDUSD` | 2007-04-09 | 18:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T18:00Z: 1 crossed quote(s) (bid > ask); first at index 9 ts=2007-04-09T18:03:37.754000Z bid=np.float64(0.81677) ask=np.float64(0.81673) |
| `AUDUSD` | 2007-04-09 | 20:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T20:00Z: 2 crossed quote(s) (bid > ask); first at index 222 ts=2007-04-09T20:54:08.878000Z bid=np.float64(0.81947) ask=np.float64(0.81943) |
| `AUDUSD` | 2007-04-09 | 21:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T21:00Z: 5 crossed quote(s) (bid > ask); first at index 232 ts=2007-04-09T21:32:54.310000Z bid=np.float64(0.82187) ask=np.float64(0.82153) |
| `AUDUSD` | 2007-04-09 | 22:00Z | CROSSED_QUOTE | AUDUSD 2007-04-09T22:00Z: 1 crossed quote(s) (bid > ask); first at index 263 ts=2007-04-09T22:42:06.756000Z bid=np.float64(0.82397) ask=np.float64(0.82393) |
| `AUDUSD` | 2007-04-10 | 00:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T00:00Z: 6 crossed quote(s) (bid > ask); first at index 3 ts=2007-04-10T00:04:36.138000Z bid=np.float64(0.82307) ask=np.float64(0.82293) |
| `AUDUSD` | 2007-04-10 | 01:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T01:00Z: 1 crossed quote(s) (bid > ask); first at index 53 ts=2007-04-10T01:18:52.204000Z bid=np.float64(0.82367) ask=np.float64(0.82363) |
| `AUDUSD` | 2007-04-10 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T02:00Z: 5 crossed quote(s) (bid > ask); first at index 78 ts=2007-04-10T02:18:10.806000Z bid=np.float64(0.82327) ask=np.float64(0.82323) |
| `AUDUSD` | 2007-04-10 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T03:00Z: 5 crossed quote(s) (bid > ask); first at index 172 ts=2007-04-10T03:39:02.300000Z bid=np.float64(0.82357) ask=np.float64(0.82353) |
| `AUDUSD` | 2007-04-10 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T04:00Z: 3 crossed quote(s) (bid > ask); first at index 150 ts=2007-04-10T04:41:19.353000Z bid=np.float64(0.82357) ask=np.float64(0.82353) |
| `AUDUSD` | 2007-04-10 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T06:00Z: 2 crossed quote(s) (bid > ask); first at index 85 ts=2007-04-10T06:20:30.298000Z bid=np.float64(0.82387) ask=np.float64(0.82383) |
| `AUDUSD` | 2007-04-10 | 07:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T07:00Z: 1 crossed quote(s) (bid > ask); first at index 8 ts=2007-04-10T07:03:16.349000Z bid=np.float64(0.82377) ask=np.float64(0.82373) |
| `AUDUSD` | 2007-04-10 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T08:00Z: 2 crossed quote(s) (bid > ask); first at index 115 ts=2007-04-10T08:32:24.020000Z bid=np.float64(0.82457) ask=np.float64(0.82453) |
| `AUDUSD` | 2007-04-10 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T09:00Z: 5 crossed quote(s) (bid > ask); first at index 88 ts=2007-04-10T09:20:35.108000Z bid=np.float64(0.82347) ask=np.float64(0.82343) |
| `AUDUSD` | 2007-04-10 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T10:00Z: 10 crossed quote(s) (bid > ask); first at index 13 ts=2007-04-10T10:01:02.984000Z bid=np.float64(0.82437) ask=np.float64(0.82433) |
| `AUDUSD` | 2007-04-10 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T11:00Z: 1 crossed quote(s) (bid > ask); first at index 18 ts=2007-04-10T11:05:36.244000Z bid=np.float64(0.82527) ask=np.float64(0.82523) |
| `AUDUSD` | 2007-04-10 | 15:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T15:00Z: 1 crossed quote(s) (bid > ask); first at index 53 ts=2007-04-10T15:36:20.425000Z bid=np.float64(0.82657) ask=np.float64(0.82653) |
| `AUDUSD` | 2007-04-10 | 17:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T17:00Z: 1 crossed quote(s) (bid > ask); first at index 56 ts=2007-04-10T17:39:49.142000Z bid=np.float64(0.82567) ask=np.float64(0.82563) |
| `AUDUSD` | 2007-04-10 | 20:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T20:00Z: 2 crossed quote(s) (bid > ask); first at index 64 ts=2007-04-10T20:21:49.863000Z bid=np.float64(0.82477) ask=np.float64(0.82473) |
| `AUDUSD` | 2007-04-10 | 21:00Z | CROSSED_QUOTE | AUDUSD 2007-04-10T21:00Z: 1 crossed quote(s) (bid > ask); first at index 61 ts=2007-04-10T21:13:25.906000Z bid=np.float64(0.82367) ask=np.float64(0.82363) |
| `AUDUSD` | 2007-04-11 | 05:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T05:00Z: 4 crossed quote(s) (bid > ask); first at index 57 ts=2007-04-11T05:22:39.222000Z bid=np.float64(0.82537) ask=np.float64(0.82533) |
| `AUDUSD` | 2007-04-11 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T06:00Z: 9 crossed quote(s) (bid > ask); first at index 12 ts=2007-04-11T06:02:31.763000Z bid=np.float64(0.82537) ask=np.float64(0.82533) |
| `AUDUSD` | 2007-04-11 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T10:00Z: 1 crossed quote(s) (bid > ask); first at index 52 ts=2007-04-11T10:09:25.846000Z bid=np.float64(0.82477) ask=np.float64(0.82473) |
| `AUDUSD` | 2007-04-11 | 13:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T13:00Z: 1 crossed quote(s) (bid > ask); first at index 31 ts=2007-04-11T13:29:01.913000Z bid=np.float64(0.82537) ask=np.float64(0.82533) |
| `AUDUSD` | 2007-04-11 | 14:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T14:00Z: 1 crossed quote(s) (bid > ask); first at index 197 ts=2007-04-11T14:28:12.483000Z bid=np.float64(0.82457) ask=np.float64(0.82453) |
| `AUDUSD` | 2007-04-11 | 18:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T18:00Z: 1 crossed quote(s) (bid > ask); first at index 10 ts=2007-04-11T18:04:05.965000Z bid=np.float64(0.82487) ask=np.float64(0.82483) |
| `AUDUSD` | 2007-04-11 | 19:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T19:00Z: 2 crossed quote(s) (bid > ask); first at index 22 ts=2007-04-11T19:08:43.294000Z bid=np.float64(0.82617) ask=np.float64(0.82613) |
| `AUDUSD` | 2007-04-11 | 21:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T21:00Z: 1 crossed quote(s) (bid > ask); first at index 186 ts=2007-04-11T21:36:06.301000Z bid=np.float64(0.82597) ask=np.float64(0.82593) |
| `AUDUSD` | 2007-04-11 | 22:00Z | CROSSED_QUOTE | AUDUSD 2007-04-11T22:00Z: 2 crossed quote(s) (bid > ask); first at index 50 ts=2007-04-11T22:08:01.570000Z bid=np.float64(0.82547) ask=np.float64(0.82543) |
| `AUDUSD` | 2007-04-12 | 01:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T01:00Z: 1 crossed quote(s) (bid > ask); first at index 109 ts=2007-04-12T01:31:50.558000Z bid=np.float64(0.82517) ask=np.float64(0.82513) |
| `AUDUSD` | 2007-04-12 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T02:00Z: 1 crossed quote(s) (bid > ask); first at index 10 ts=2007-04-12T02:02:01.251000Z bid=np.float64(0.82487) ask=np.float64(0.82483) |
| `AUDUSD` | 2007-04-12 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T04:00Z: 3 crossed quote(s) (bid > ask); first at index 114 ts=2007-04-12T04:34:37.317000Z bid=np.float64(0.82627) ask=np.float64(0.82623) |
| `AUDUSD` | 2007-04-12 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T06:00Z: 3 crossed quote(s) (bid > ask); first at index 162 ts=2007-04-12T06:46:00.417000Z bid=np.float64(0.82597) ask=np.float64(0.82593) |
| `AUDUSD` | 2007-04-12 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T08:00Z: 3 crossed quote(s) (bid > ask); first at index 66 ts=2007-04-12T08:16:58.648000Z bid=np.float64(0.82607) ask=np.float64(0.82603) |
| `AUDUSD` | 2007-04-12 | 09:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T09:00Z: 1 crossed quote(s) (bid > ask); first at index 209 ts=2007-04-12T09:48:00.616000Z bid=np.float64(0.82637) ask=np.float64(0.82633) |
| `AUDUSD` | 2007-04-12 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T10:00Z: 7 crossed quote(s) (bid > ask); first at index 51 ts=2007-04-12T10:07:24.017000Z bid=np.float64(0.82607) ask=np.float64(0.82603) |
| `AUDUSD` | 2007-04-12 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T11:00Z: 1 crossed quote(s) (bid > ask); first at index 11 ts=2007-04-12T11:01:44.203000Z bid=np.float64(0.82757) ask=np.float64(0.82753) |
| `AUDUSD` | 2007-04-12 | 12:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T12:00Z: 2 crossed quote(s) (bid > ask); first at index 147 ts=2007-04-12T12:48:28.635000Z bid=np.float64(0.82787) ask=np.float64(0.82783) |
| `AUDUSD` | 2007-04-12 | 15:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T15:00Z: 1 crossed quote(s) (bid > ask); first at index 49 ts=2007-04-12T15:17:53.443000Z bid=np.float64(0.82917) ask=np.float64(0.82913) |
| `AUDUSD` | 2007-04-12 | 17:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T17:00Z: 1 crossed quote(s) (bid > ask); first at index 7 ts=2007-04-12T17:03:07.138000Z bid=np.float64(0.82947) ask=np.float64(0.82943) |
| `AUDUSD` | 2007-04-12 | 18:00Z | CROSSED_QUOTE | AUDUSD 2007-04-12T18:00Z: 8 crossed quote(s) (bid > ask); first at index 107 ts=2007-04-12T18:41:11.048000Z bid=np.float64(0.83027) ask=np.float64(0.83013) |
| `AUDUSD` | 2007-04-13 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T02:00Z: 5 crossed quote(s) (bid > ask); first at index 138 ts=2007-04-13T02:26:54.786000Z bid=np.float64(0.83267) ask=np.float64(0.83263) |
| `AUDUSD` | 2007-04-13 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T04:00Z: 2 crossed quote(s) (bid > ask); first at index 54 ts=2007-04-13T04:19:24.724000Z bid=np.float64(0.83207) ask=np.float64(0.83203) |
| `AUDUSD` | 2007-04-13 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T06:00Z: 3 crossed quote(s) (bid > ask); first at index 55 ts=2007-04-13T06:13:05.224000Z bid=np.float64(0.83187) ask=np.float64(0.83183) |
| `AUDUSD` | 2007-04-13 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T08:00Z: 4 crossed quote(s) (bid > ask); first at index 14 ts=2007-04-13T08:04:08.889000Z bid=np.float64(0.83287) ask=np.float64(0.83283) |
| `AUDUSD` | 2007-04-13 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T10:00Z: 6 crossed quote(s) (bid > ask); first at index 65 ts=2007-04-13T10:05:07.888000Z bid=np.float64(0.83307) ask=np.float64(0.83303) |
| `AUDUSD` | 2007-04-13 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T11:00Z: 2 crossed quote(s) (bid > ask); first at index 213 ts=2007-04-13T11:23:13.546000Z bid=np.float64(0.83147) ask=np.float64(0.83143) |
| `AUDUSD` | 2007-04-13 | 12:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T12:00Z: 1 crossed quote(s) (bid > ask); first at index 84 ts=2007-04-13T12:44:29.460000Z bid=np.float64(0.83277) ask=np.float64(0.83273) |
| `AUDUSD` | 2007-04-13 | 13:00Z | CROSSED_QUOTE | AUDUSD 2007-04-13T13:00Z: 1 crossed quote(s) (bid > ask); first at index 113 ts=2007-04-13T13:53:03.610000Z bid=np.float64(0.83327) ask=np.float64(0.83323) |
| `AUDUSD` | 2007-04-16 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T02:00Z: 2 crossed quote(s) (bid > ask); first at index 176 ts=2007-04-16T02:35:52.018000Z bid=np.float64(0.83197) ask=np.float64(0.83193) |
| `AUDUSD` | 2007-04-16 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T03:00Z: 11 crossed quote(s) (bid > ask); first at index 11 ts=2007-04-16T03:01:40.285000Z bid=np.float64(0.83277) ask=np.float64(0.83273) |
| `AUDUSD` | 2007-04-16 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T04:00Z: 1 crossed quote(s) (bid > ask); first at index 97 ts=2007-04-16T04:28:17.996000Z bid=np.float64(0.83357) ask=np.float64(0.83263) |
| `AUDUSD` | 2007-04-16 | 05:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T05:00Z: 1 crossed quote(s) (bid > ask); first at index 129 ts=2007-04-16T05:46:07.628000Z bid=np.float64(0.83347) ask=np.float64(0.83263) |
| `AUDUSD` | 2007-04-16 | 07:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T07:00Z: 2 crossed quote(s) (bid > ask); first at index 4 ts=2007-04-16T07:03:36.680000Z bid=np.float64(0.83367) ask=np.float64(0.83363) |
| `AUDUSD` | 2007-04-16 | 08:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T08:00Z: 2 crossed quote(s) (bid > ask); first at index 69 ts=2007-04-16T08:28:30.384000Z bid=np.float64(0.83357) ask=np.float64(0.83263) |
| `AUDUSD` | 2007-04-16 | 10:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T10:00Z: 5 crossed quote(s) (bid > ask); first at index 23 ts=2007-04-16T10:05:18.551000Z bid=np.float64(0.83397) ask=np.float64(0.83393) |
| `AUDUSD` | 2007-04-16 | 11:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T11:00Z: 5 crossed quote(s) (bid > ask); first at index 102 ts=2007-04-16T11:39:53.040000Z bid=np.float64(0.83277) ask=np.float64(0.83233) |
| `AUDUSD` | 2007-04-16 | 17:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T17:00Z: 1 crossed quote(s) (bid > ask); first at index 69 ts=2007-04-16T17:43:37.942000Z bid=np.float64(0.83207) ask=np.float64(0.83203) |
| `AUDUSD` | 2007-04-16 | 21:00Z | CROSSED_QUOTE | AUDUSD 2007-04-16T21:00Z: 1 crossed quote(s) (bid > ask); first at index 103 ts=2007-04-16T21:57:38.733000Z bid=np.float64(0.83217) ask=np.float64(0.83213) |
| `AUDUSD` | 2007-04-17 | 01:00Z | CROSSED_QUOTE | AUDUSD 2007-04-17T01:00Z: 3 crossed quote(s) (bid > ask); first at index 101 ts=2007-04-17T01:59:43.768000Z bid=np.float64(0.83247) ask=np.float64(0.83233) |
| `AUDUSD` | 2007-04-17 | 02:00Z | CROSSED_QUOTE | AUDUSD 2007-04-17T02:00Z: 1 crossed quote(s) (bid > ask); first at index 84 ts=2007-04-17T02:22:39.246000Z bid=np.float64(0.83287) ask=np.float64(0.83283) |
| `AUDUSD` | 2007-04-17 | 03:00Z | CROSSED_QUOTE | AUDUSD 2007-04-17T03:00Z: 6 crossed quote(s) (bid > ask); first at index 31 ts=2007-04-17T03:04:39.892000Z bid=np.float64(0.83217) ask=np.float64(0.83213) |
| `AUDUSD` | 2007-04-17 | 04:00Z | CROSSED_QUOTE | AUDUSD 2007-04-17T04:00Z: 4 crossed quote(s) (bid > ask); first at index 92 ts=2007-04-17T04:33:03.779000Z bid=np.float64(0.83257) ask=np.float64(0.83253) |
| `AUDUSD` | 2007-04-17 | 06:00Z | CROSSED_QUOTE | AUDUSD 2007-04-17T06:00Z: 2 crossed quote(s) (bid > ask); first at index 67 ts=2007-04-17T06:27:27.865000Z bid=np.float64(0.83297) ask=np.float64(0.83293) |

…and 12,895 more. The result document lists 2,000 of 13,015; the counts everywhere else in this report are complete.

### What the pull recorded, and what the sweep recovered

The count above is the **end** state, and on its own it flatters the run. During the pull itself **13,126** hour(s) across 135 pair-month(s) were recorded as gaps. The card's closing sweep re-asked every one of them, and **111** came back.

That is the difference between a gap meaning *absent history* and a gap meaning *a feed in a bad mood on the Tuesday it was asked*. 13,015 hour(s) survived, and they are not the first kind either: **13,014** of them carry a validation reason rather than a fetch failure. The feed served those hours; this pipeline refused them. They are neither absent history nor a feed in a bad mood, but data that arrived and did not pass — which is why the reason token, not the count, is the part worth reading. The remaining 1 could not be had at all.

Re-asking every gap is what makes the distinction available at all: a transient refusal clears on the second ask and a deterministic one does not. A run that reported only its final gap count would have hidden which kind it had.

Surviving gaps by reason:

| reason | hours |
| --- | --- |
| `CROSSED_QUOTE` | 12,998 |
| `CLOSED_MARKET_TICK` | 16 |
| `FETCH_ERROR` | 1 |

## Validation anomalies

### Hard rejections

A hard validation failure rejects the hour, which is recorded as a gap carrying its reason token. These are the Phase 1 tokens, unchanged.

| reason | hours |
| --- | --- |
| `CLOSED_MARKET_TICK` | 16 |
| `CROSSED_QUOTE` | 12,998 |

### Warnings

A warning records something worth knowing that is not a reason to reject data. `EMPTY_TRADING_HOUR` — the feed serving nothing during an hour the derived week calls open — is the holiday-calendar input of pre-reg #5, and turning those into a calendar is T3's card, not this one's. They are counted here and interpreted nowhere.

| reason | hours |
| --- | --- |
| `EMPTY_TRADING_HOUR` | 1,589 |
| `FETCH_ERROR` | 1 |
| `SPREAD_OUTLIER` | 717 |
| `TICK_COUNT_OUTLIER` | 76 |

#### Where the spread flags fall

`SPREAD_OUTLIER` fired on 717 hour(s). They do not concentrate: the busiest hour, 21:00Z, takes only 22%. A flag that scatters this evenly is more likely to be describing the ceiling than the market, and is worth revisiting before any card leans on the spread series.

| hour (UTC) | hours flagged | share |
| --- | --- | --- |
| 21:00Z | 160 | 22.3% |
| 22:00Z | 160 | 22.3% |
| 00:00Z | 64 | 8.9% |
| 23:00Z | 59 | 8.2% |
| 01:00Z | 51 | 7.1% |
| 20:00Z | 26 | 3.6% |

By year, which is the regime question T2b inherits — its card notes 2005 spreads ran 1.5-3.6x wider than the modern era these ceilings were tuned on:

| year | hours flagged |
| --- | --- |
| 2007 | 9 |
| 2008 | 418 |
| 2009 | 156 |
| 2010 | 31 |
| 2011 | 38 |
| 2012 | 21 |
| 2013 | 31 |
| 2014 | 13 |

### The derived week boundary, checked against the feed

The shut hour either side of every week boundary was fetched rather than assumed, at about 1.7% more requests than skipping them. What came back:

| pair | shut hours fetched | shut but carried ticks | open but served empty |
| --- | --- | --- | --- |
| `AUDJPY` | 1,042 | 0 | 79 |
| `AUDUSD` | 1,042 | 0 | 972 |
| `EURCHF` | 1,042 | 0 | 76 |
| `EURGBP` | 1,042 | 0 | 82 |
| `EURJPY` | 1,042 | 0 | 419 |
| `EURUSD` | 1,042 | 0 | 78 |
| `GBPJPY` | 1,042 | 0 | 81 |
| `GBPUSD` | 1,042 | 0 | 82 |
| `NZDUSD` | 1,042 | 0 | 113 |
| `USDCAD` | 1,042 | 0 | 84 |
| `USDCHF` | 1,042 | 0 | 77 |
| `USDJPY` | 1,042 | 0 | 417 |

The derivation and the feed agree: no hour the derived week called shut came back carrying ticks.

Across the universe, 2,560 hour(s) the derived week calls open were served empty. Those are the `EMPTY_TRADING_HOUR` warnings above, and most of them are holidays.

## Throughput, and what it cost

Recorded because T2b ingests the same feed for the years before this range and should budget from a measurement rather than from optimism.

| measure | value |
| --- | --- |
| sessions that finished | 2 |
| pair-months completed | 1,440 |
| requests issued | 704,768 |
| throttled responses | 18,805 (2.67%) |
| wall clock across sessions | 204.5 h |
| of which parked waiting out the feed | 43.0 h (21.03%) |
| sustained rate | 0.96 requests/s |
| time inside the ingest pipeline | 141.8 h |
| time building bars | 22.2 h |

### Concurrency calibration

The rule was fixed before the run: start at level 2 — T1's proven-safe setting — and after an unbroken clean hour probe the next level, to the card's ceiling of 4. A level is judged against the measured throttle rate of the level below it, and two consecutive ten-minute windows above 1.5× that rate (or two percentage points above it) back the level off and block it for six hours.

A level here is both a connection count and a paced rate: level *n* means *n* connections and a gap of `0.8/n` seconds. Raising the connection count alone changes nothing measurable — a fetch costs about a second, so the worker count is what binds — and probing a concurrency that cannot offer more load would not be a probe.

| level | pair-months | requests | throttles | throttle rate | requests/s | ingest time |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | 534 | 224,125 | 6,647 | 2.97% | 1.06 | 58.5 h |
| 3 | 477 | 253,536 | 6,435 | 2.54% | 1.48 | 47.7 h |
| 4 | 429 | 227,107 | 5,723 | 2.52% | 1.77 | 35.6 h |

Transitions, with the measurement that caused each:

| session | at | to level | why |
| --- | --- | --- | --- |
| 1 | 0 min | 2 | start; T1's proven-safe level; baselines carried forward {2: 0.0061, 3: 0.017, 4: 0.019} |
| 1 | 7527 min | 3 | clean for 3600s at level 2 (throttle rate 9.107%) |
| 1 | 7587 min | 4 | clean for 3600s at level 3 (throttle rate 6.339%) |
| 1 | 7689 min | 3 | level 4 blocked: 7.885% throttled against a tolerance of 7.853% for 2 consecutive windows |
| 1 | 8061 min | 4 | clean for 3600s at level 3 (throttle rate 1.034%) |
| 1 | 8172 min | 3 | level 4 blocked: 7.491% throttled against a tolerance of 5.483% for 2 consecutive windows |
| 1 | 8536 min | 4 | clean for 3600s at level 3 (throttle rate 1.113%) |
| 1 | 8596 min | 3 | level 4 blocked: 5.000% throttled against a tolerance of 3.014% for 2 consecutive windows |
| 1 | 8958 min | 4 | clean for 3600s at level 3 (throttle rate 2.440%) |
| 1 | 9022 min | 3 | level 4 blocked: 9.350% throttled against a tolerance of 5.761% for 2 consecutive windows |
| 1 | 9384 min | 4 | clean for 3600s at level 3 (throttle rate 1.894%) |
| 1 | 9902 min | 3 | level 4 blocked: 5.370% throttled against a tolerance of 4.462% for 2 consecutive windows |
| 1 | 10267 min | 4 | clean for 3600s at level 3 (throttle rate 5.371%) |
| 1 | 10415 min | 3 | level 4 blocked: 7.477% throttled against a tolerance of 5.608% for 2 consecutive windows |
| 1 | 10782 min | 4 | clean for 3600s at level 3 (throttle rate 6.429%) |
| 1 | 11515 min | 3 | level 4 blocked: 14.179% throttled against a tolerance of 6.789% for 2 consecutive windows |
| 1 | 11580 min | 2 | level 3 blocked: 16.978% throttled against a tolerance of 9.473% for 2 consecutive windows |
| 1 | 11953 min | 3 | clean for 3600s at level 2 (throttle rate 1.779%) |
| 1 | 11999 min | 2 | level 3 blocked: 12.034% throttled against a tolerance of 5.286% for 2 consecutive windows |
| 1 | 12369 min | 3 | clean for 3600s at level 2 (throttle rate 1.337%) |
| 1 | 12476 min | 2 | level 3 blocked: 6.239% throttled against a tolerance of 3.472% for 2 consecutive windows |
| 1 | 12868 min | 3 | clean for 3600s at level 2 (throttle rate 1.456%) |
| 1 | 12934 min | 4 | clean for 3600s at level 3 (throttle rate 2.366%) |
| 1 | 13112 min | 3 | level 4 blocked: 4.658% throttled against a tolerance of 3.857% for 2 consecutive windows |
| 1 | 13243 min | 2 | level 3 blocked: 8.961% throttled against a tolerance of 4.231% for 2 consecutive windows |
| 1 | 13607 min | 3 | clean for 3600s at level 2 (throttle rate 1.026%) |
| 1 | 13669 min | 4 | clean for 3600s at level 3 (throttle rate 2.407%) |
| 1 | 13984 min | 3 | level 4 blocked: 7.500% throttled against a tolerance of 4.288% for 2 consecutive windows |
| 1 | 14103 min | 2 | level 3 blocked: 4.786% throttled against a tolerance of 3.497% for 2 consecutive windows |
| 1 | 14495 min | 3 | clean for 3600s at level 2 (throttle rate 1.556%) |
| 1 | 14558 min | 4 | clean for 3600s at level 3 (throttle rate 1.429%) |
| 1 | 14678 min | 3 | level 4 blocked: 8.915% throttled against a tolerance of 3.551% for 2 consecutive windows |
| 1 | 14757 min | 2 | level 3 blocked: 10.190% throttled against a tolerance of 4.238% for 2 consecutive windows |
| 1 | 15125 min | 3 | clean for 3600s at level 2 (throttle rate 0.783%) |
| 1 | 15204 min | 4 | clean for 3600s at level 3 (throttle rate 1.696%) |
| 1 | 15274 min | 3 | level 4 blocked: 7.857% throttled against a tolerance of 4.056% for 2 consecutive windows |
| 1 | 15301 min | 2 | level 3 blocked: 9.286% throttled against a tolerance of 3.181% for 2 consecutive windows |
| 1 | 15687 min | 2 | level 3 blocked: 12.500% throttled against a tolerance of 10.000% for 2 consecutive windows |
| 1 | 16049 min | 3 | clean for 3600s at level 2 (throttle rate 7.407%) |
| 1 | 16111 min | 4 | clean for 3600s at level 3 (throttle rate 1.895%) |
| 1 | 16200 min | 3 | level 4 blocked: 17.857% throttled against a tolerance of 4.289% for 2 consecutive windows |
| 1 | 16432 min | 2 | level 3 blocked: 7.664% throttled against a tolerance of 6.914% for 2 consecutive windows |
| 1 | 16473 min | 2 | level 3 blocked: 11.589% throttled against a tolerance of 10.000% for 2 consecutive windows |
| 1 | 16843 min | 3 | clean for 3600s at level 2 (throttle rate 2.095%) |
| 1 | 16907 min | 4 | clean for 3600s at level 3 (throttle rate 4.664%) |
| 1 | 17089 min | 3 | level 4 blocked: 6.266% throttled against a tolerance of 5.275% for 2 consecutive windows |
| 1 | 17159 min | 2 | level 3 blocked: 9.286% throttled against a tolerance of 5.499% for 2 consecutive windows |
| 1 | 17531 min | 3 | clean for 3600s at level 2 (throttle rate 0.982%) |
| 1 | 17619 min | 4 | clean for 3600s at level 3 (throttle rate 1.220%) |
| 1 | 17654 min | 3 | level 4 blocked: 3.936% throttled against a tolerance of 3.508% for 2 consecutive windows |
| 1 | 17688 min | 2 | level 3 blocked: 7.513% throttled against a tolerance of 3.707% for 2 consecutive windows |
| 1 | 18058 min | 3 | clean for 3600s at level 2 (throttle rate 1.028%) |
| 1 | 18133 min | 4 | clean for 3600s at level 3 (throttle rate 1.866%) |
| 1 | 18277 min | 3 | level 4 blocked: 4.766% throttled against a tolerance of 3.799% for 2 consecutive windows |
| 1 | 18496 min | 2 | level 3 blocked: 6.518% throttled against a tolerance of 5.000% for 2 consecutive windows |
| 1 | 18859 min | 3 | clean for 3600s at level 2 (throttle rate 2.315%) |
| 1 | 18920 min | 4 | clean for 3600s at level 3 (throttle rate 1.566%) |
| 1 | 19012 min | 3 | level 4 blocked: 9.821% throttled against a tolerance of 4.023% for 2 consecutive windows |
| 1 | 19061 min | 2 | level 3 blocked: 7.589% throttled against a tolerance of 5.462% for 2 consecutive windows |
| 2 | 0 min | 2 | start; T1's proven-safe level; baselines carried forward {2: 0.0582, 3: 0.0199, 4: 0.0241} |
| 2 | 19366 min | 2 | level 3 blocked: level 2 ran 216.667% throttled against a tolerance of 10.000% for 2 consecutive windows |
| 2 | 19387 min | 2 | level 3 blocked: level 2 ran 172.727% throttled against a tolerance of 10.000% for 2 consecutive windows |
| 2 | 19718 min | 2 | level 3 blocked: level 2 ran 111.111% throttled against a tolerance of 10.000% for 2 consecutive windows |

### Sessions

| # | status | pair-months | hours ok | wall | requests/s | outages ridden out | parked |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | ok | 1,440 | 735,435 | 198.0 h | 1.07 | 30 | 42.4 h |
| 2 | ok | 135 | 56,827 | 6.5 h | 0.56 | 0 | 0.6 h |

A session that was interrupted leaves a ledger start record and no end record, which is what the ledger is for. Only sessions that finished and reported their own counters appear here.

## Storage footprint

| pair | tick files | day partitions | on disk | ticks | bytes/tick |
| --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 62,513 | 3,129 | 2.39 GiB | 210,098,578 | 12.2 |
| `AUDUSD` | 48,623 | 3,024 | 1.61 GiB | 117,396,382 | 14.7 |
| `EURCHF` | 62,516 | 3,129 | 1.77 GiB | 155,353,423 | 12.2 |
| `EURGBP` | 62,510 | 3,129 | 1.90 GiB | 169,556,250 | 12.0 |
| `EURJPY` | 62,173 | 3,120 | 2.68 GiB | 233,564,035 | 12.3 |
| `EURUSD` | 62,514 | 3,129 | 2.38 GiB | 179,770,072 | 14.2 |
| `GBPJPY` | 62,511 | 3,129 | 2.61 GiB | 229,008,354 | 12.2 |
| `GBPUSD` | 62,510 | 3,129 | 2.17 GiB | 183,785,649 | 12.7 |
| `NZDUSD` | 62,479 | 3,128 | 1.41 GiB | 115,865,319 | 13.1 |
| `USDCAD` | 62,508 | 3,129 | 1.46 GiB | 119,755,096 | 13.1 |
| `USDCHF` | 62,515 | 3,129 | 1.97 GiB | 168,403,733 | 12.6 |
| `USDJPY` | 62,173 | 3,120 | 1.96 GiB | 166,637,569 | 12.6 |

Total tick store: **24.31 GiB** across 735,545 files — one Parquet per ingested hour, so an hour can be re-ingested without rewriting a day and a partial day is still readable.

## Bar tables

Bars are built incrementally (SPEC2 prerequisite P0-B, landed for this card). Only the days whose stored ticks changed since the last build are resampled, and the coarser timeframes are rolled up from the 1m bars rather than re-read from ticks — which is exact, because every timeframe in the research set tiles UTC days and the bins nest.

Rows per pair and timeframe:

| pair | `1min` | `5min` | `30min` | `1h` | `4h` | `1D` |
| --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 7,542,135 | 1,509,919 | 251,709 | 125,863 | 32,527 | 6,305 |
| `AUDUSD` | 6,648,806 | 1,341,466 | 223,842 | 111,974 | 30,341 | 6,201 |
| `EURCHF` | 7,517,235 | 1,509,341 | 251,699 | 125,862 | 32,528 | 6,306 |
| `EURGBP` | 7,534,805 | 1,509,815 | 251,706 | 125,860 | 32,527 | 6,306 |
| `EURJPY` | 7,511,606 | 1,504,878 | 250,985 | 125,525 | 32,457 | 6,298 |
| `EURUSD` | 7,534,190 | 1,510,036 | 251,717 | 125,868 | 32,529 | 6,307 |
| `GBPJPY` | 7,539,956 | 1,509,465 | 251,702 | 125,861 | 32,526 | 6,305 |
| `GBPUSD` | 7,538,071 | 1,509,911 | 251,704 | 125,859 | 32,526 | 6,305 |
| `NZDUSD` | 7,522,487 | 1,509,190 | 251,627 | 125,824 | 32,518 | 6,302 |
| `USDCAD` | 7,530,253 | 1,509,733 | 251,697 | 125,858 | 32,526 | 6,306 |
| `USDCHF` | 7,514,354 | 1,509,542 | 251,696 | 125,861 | 32,527 | 6,306 |
| `USDJPY` | 7,498,446 | 1,505,357 | 250,995 | 125,525 | 32,457 | 6,298 |

Build cost, one build per pair-month:

| timeframe | builds | days folded in | rows spliced | total time | per build |
| --- | --- | --- | --- | --- | --- |
| `1min` | 1,395 | 34,098 | 40,805,222 | 63079 s | 45218 ms |
| `5min` | 1,395 | 34,098 | 8,169,857 | 12978 s | 9304 ms |
| `30min` | 1,395 | 34,098 | 1,361,941 | 2288 s | 1640 ms |
| `1h` | 1,395 | 34,098 | 681,057 | 1169 s | 838 ms |
| `4h` | 1,395 | 34,098 | 176,026 | 350 s | 251 ms |
| `1D` | 1,395 | 34,098 | 34,098 | 104 s | 75 ms |

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

* The least complete pair is `AUDUSD` at 79.24% of the open hours the derived week contains. T1 found no missing region in this range and predicted near-complete coverage; that prediction is what this column tests.
* **No duplicate ticks at all.** De-duplication is on the whole record, so two ticks sharing a millisecond but differing in price or volume are both kept; the feed served none that were identical.
* 2,560 hour(s) the derived week calls open were served empty. Those are candidate holidays and are pre-reg #5's raw material; T3 turns them into a calendar, and until it does an empty open hour stays a warning rather than a `closed`.
* The tick store averages 12.7 bytes per stored tick after Snappy. That is the number T2b should size the years before this range with.
* **Nearly every gap is one pair, and it is a data fault rather than a fetch failure.** Of 13,015 surviving gaps, **12,998 are `CROSSED_QUOTE` in AUDUSD** — bid above ask by one to three units of the fifth decimal, sporadic within the hour, and Phase 1 rejects the whole hour on a single crossed tick. They fall in two bounded episodes: **2007-04 → 2008-09** (18 months, 6,228 hours) and **2009-04 → 2010-10** (19 months, 6,768 hours), with six clean months between them and clean AUDUSD either side. Both begin in April and both run 18–19 months; 2006 offered the one test a third episode would have confirmed, and produced none, so it stays a two-sample coincidence. Outside AUDUSD the entire card holds two crossed-quote hours.
* **The decoder was ruled out before the feed was blamed.** A bid/ask swap on the `>IIIff` record is the obvious suspect and would have been our fault, not Dukascopy's. It is not that: rejected hours carry a median of 7 crossed ticks (min 1, max 92) out of hours holding thousands, where a swap would cross every tick in every hour; T2a pulled AUDUSD across 2015-2025 through the identical decoder with zero crossed quotes; and every other pair in the same months is clean.
* **Hour-level rejection is expensive against tick-level corruption — a proposal for a checkpoint, not a change made here.** A handful of bad quotes discards an hour of good ones, which cost AUDUSD two thirds of 2010 and half of 2009. Dropping the offending ticks and counting them, exactly as duplicates are already handled, would have preserved those hours. Changing that is a validation-rule change and this card's non-goals forbid it; the point is recorded so a card empowered to weigh it can.
* **The week-boundary audit fired for the first time, and only here.** 16 hours were rejected `CLOSED_MARKET_TICK`: `EURJPY` and `USDJPY` only, 21:00Z only, Sunday only — one isolated pair on 2011-03-06 and a run from 2012-01-01 to 2012-02-26. T2a saw none in 763,752 hours. The derivation was checked, not assumed: `is_market_open` is correctly False at 21:00Z and True at 22:00Z on those dates under EST, so the boundary is not drifting; the feed published JPY ticks an hour before the week opened. Note that **every occurrence sits in a New York standard-time window** — under EDT the week opens at 21:00Z and such a tick is legal — so the anomaly is only *detectable* in northern winter, and its absence from summer says nothing about the feed.
* **The spread-ceiling worry did not materialise, and the reason matters.** The card expected wide early-era spreads to cluster warn-level flags in 2005-2006, and forbade widening any ceiling unattended. Zero flags fired in either year. By year: 2008: 468, 2009: 164, 2011: 40, 2013: 33, 2010: 32, 2012: 21, 2014: 17, 2007: 9, 2006: 0, 2005: 0 — a crisis spike with a tail, not an era effect. But *the ceiling did not fire* and *the spreads were not wide* are different claims and only the first is evidenced: p99.9 over an hour holding a thousand ticks is a weaker instrument than p99.9 over one holding six thousand, and 2005 averages 976 ticks/hour. T5's regime question inherits the second claim, unanswered.
* **Early-era throughput, for anyone sizing the next pull.** 1,440 pair-months took 198 hours of wall clock, 42 of them parked, against T2a's 1,464 in roughly 166. Early hours are far thinner — 976 ticks/hour in 2005 and 1,070 in 2006 against 6,200 in 2022 — so this era is request-bound rather than bytes-bound, and 24.31 GiB of store came from nearly as many hours as T2a's 37.65 GiB. Thirty outages were ridden out, none reaching the three-hour budget.
* **A caveat on the calibration transitions above.** Until 2026-09-02 a back-off taken at the concurrency floor recorded the throttle rate against the level it blocked rather than the level it was measured at. The behaviour was always correct — hold at the floor, block the level above — but transitions written before that fix may attribute a floor measurement to the level above it. Later entries carry both levels explicitly.

## Provenance

* Config: `experiments/T2a-ingestion/config.toml` (sha256 `5be163909b01cbcd`)
* Manifests: `data/research/manifests/pair=<PAIR>/<YYYY-MM>/manifest.json` — one shard per pair-month, one entry per requested hour
* Progress records: `experiments/T2a-ingestion/chunks.jsonl` and `sessions.jsonl`
* Result: `experiments/T2a-ingestion/result.json`, hash `725d008ef1d698d3083f40f628d942849ac96fae63d3018d64e28f016e860fdc`
* Loader mode `scoring`, scored `False`, re-run class `full`. The loader served 72 bar file(s) across 12 pair(s) and 6307 date(s); sealed dates served: none.
* Research gate: exit 0 (full, 2026-09-05)

