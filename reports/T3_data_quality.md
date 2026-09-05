# T3 — Data quality, holiday calendar and cross-venue check

**Window:** 2005-01-03 → 2025-02-28, 12 pairs · **Task card:** `taskcards/T3.md` · **Experiment:** `T3-quality` · **Seed:** 20260905 · **Result hash:** `b33e6949a9ed844a`

**Trials ledgered under T3:** 2 (SPEC2 pre-reg #10).

This card asks four questions about the store the two ingestion cards built, and answers them from four different directions: does the bookkeeping agree with the files, is every stored hour still valid, which quiet days were holidays, and does a second venue quote the same market. It produces no strategy content and is not scorable.

**Every number in this report is derived at render time** from the result document, which is itself derived from the manifests, the store and two checkpointed passes. That is ruling R6, and it is not a style preference: the M2 audit found three figures in the previous reports that had been correct when typed and had since stopped being true. There is no authored-prose escape hatch in this renderer.

## The rulings in force

R1-R6 were fixed at the M2 checkpoint before any T3 result existed and are recorded in `SPEC2.md`. They are restated here because a report that a ruling shapes should say which ruling shaped it.

| ruling | statement | enforced by |
| --- | --- | --- |
| **R1** | AUDUSD before 2011-01-01 is excluded from research; the crossed-quote rule is unchanged | `research.exclusions via research.loader.check_date` |
| **R2** | the JPY Sunday pre-open hours stay rejected and gain the sub-label PRE_OPEN_FEED_DATA | `research.ingest_summary._sublabels` |
| **R3** | spread-regime comparisons across eras must control for ticks per hour; never a raw SPREAD_OUTLIER count | `report framing; a T5 requirement` |
| **R4** | tick counts are not a volume or activity proxy until a T4 card has characterised the density series | `report framing` |
| **R5** | the holiday calendar derives from manifest status == empty, never from the EMPTY_TRADING_HOUR warning list | `research.calendar_build.scan` |
| **R6** | no hand-written numbers in reports; every figure is derived at render time | `research.ingest_report.check_note` |

Three of those constrain how a report may *speak* and have nothing to exercise. Two are code, and code that is never exercised is code nobody knows is still wired up — so both refusals were run while this result was produced:

| refusal | reason token | refused? |
| --- | --- | --- |
| the holdout seal, asked for 2025-03-01 | `HOLDOUT_SEALED` | yes |
| ruling R1's exclusion window, asked for an excluded pair-date | `PAIR_EXCLUDED_WINDOW` | yes |

## Step 0 — Report reconciliation

The audit that opened this card compared three descriptions of the same store and found the ingestion reports wrong in six places. Each was fixed at its source rather than in the prose that carried it:

1. **The report generator hardcoded one card's name.** T2b's report was titled, linked and provenanced as T2a's while printing T2b's numbers. The card, the trial count and every path now come from the result document, so a report cannot be rendered under a card its experiment did not declare.
2. **Bar rows were counted store-wide.** A bar table is one file per pair spanning every card's window, so both ingestion reports claimed the same total for different decades. The count is now bounded by the experiment's window, like the storage walk already was.
3. **Three of the sharpest claims were authored prose.** The AUDUSD gap attribution, the episode boundaries and the by-year spread counts were typed into bullets, correct when written and stale afterwards. All three are derived and tabulated now, and `--note` refuses any note carrying a count — ruling R6, enforced rather than intended.
4. **A manifest shard kept validation flags twice and the copies disagreed.** Root-caused, and settled in `SPEC2.md` §The canonical manifest reading: the hour records plus the derived coverage block are canonical, the session warning log answers exactly one question, and reports state flags on stored data apart from flags observed on hours that were then discarded. Annotating the shards themselves would be a manifest-format change, so it is proposed rather than made.
5. **The throughput table mixed two sources silently.** Requests came from the chunk log and wall clock from the session log, and the two disagree in both directions between the cards. Each rate now stays inside one source and the table says which.
6. **The reconciliation itself is now a standing check** rather than something somebody once did by hand — which is the rest of this section.

It re-runs *inside the experiment*, against the fixed reports, on every gate run.

The three sources are gathered by different means on purpose. The manifest walk reads every shard; the file listing is a directory scan that never consults a manifest; the ingestion results are the documents the reports actually print from. Asking the manifest where its files are and then asking the manifest whether they are there would prove nothing.

| measure | value |
| --- | --- |
| manifest shards read | 2,904 |
| pair-years compared | 252 |
| hours recorded `ok` | 1,495,740 |
| tick Parquet files on disk | 1,495,740 |
| files a manifest claims that are absent | **0** |
| files on disk no manifest claims | **0** |
| ticks recorded | 5,347,764,214 |
| duplicate ticks dropped | 0 |
| **pair-years where anything disagrees** | **0** |

An `ok` hour and a file are the same object counted twice. A manifest-only file is a record of data that is not there; a disk-only file is data no record accounts for, which is the more dangerous of the two because the tick reader globs a day directory rather than consulting the manifest, so it would read as settled data.

### Against the ingestion results

What each ingestion report prints, against a fresh walk of the manifests it printed from:

| experiment | window | result hash | hours `ok` | verdict |
| --- | --- | --- | --- | --- |
| `T2a-ingestion` | 2015-01-01 → 2025-02-28 | `8a1f1ad775dcb7f3` | 760,195 | agrees |
| `T2b-backfill` | 2005-01-03 → 2014-12-31 | `80d631d1b7ab79a9` | 735,545 | agrees |

### By year

| year | ok | empty | closed | gap | files on disk | files − ok |
| --- | --- | --- | --- | --- | --- | --- |
| 2005 | 74,832 | 24 | 29,688 | 0 | 74,832 | 0 |
| 2006 | 74,904 | 0 | 30,216 | 0 | 74,904 | 0 |
| 2007 | 71,642 | 463 | 29,952 | 3,063 | 71,642 | 0 |
| 2008 | 71,913 | 377 | 29,952 | 3,166 | 71,913 | 0 |
| 2009 | 71,850 | 578 | 29,952 | 2,740 | 71,850 | 0 |
| 2010 | 71,014 | 100 | 29,976 | 4,030 | 71,014 | 0 |
| 2011 | 74,873 | 7 | 30,238 | 2 | 74,873 | 0 |
| 2012 | 75,076 | 116 | 30,202 | 14 | 75,076 | 0 |
| 2013 | 74,723 | 445 | 29,952 | 0 | 74,723 | 0 |
| 2014 | 74,718 | 450 | 29,952 | 0 | 74,718 | 0 |
| 2015 | 74,712 | 456 | 29,952 | 0 | 74,712 | 0 |
| 2016 | 74,870 | 274 | 30,264 | 0 | 74,870 | 0 |
| 2017 | 74,698 | 206 | 30,216 | 0 | 74,698 | 0 |
| 2018 | 74,713 | 455 | 29,952 | 0 | 74,713 | 0 |
| 2019 | 74,680 | 488 | 29,952 | 0 | 74,680 | 0 |
| 2020 | 74,998 | 458 | 29,952 | 0 | 74,998 | 0 |
| 2021 | 74,880 | 264 | 29,976 | 0 | 74,880 | 0 |
| 2022 | 74,877 | 3 | 30,240 | 0 | 74,877 | 0 |
| 2023 | 74,673 | 231 | 30,216 | 0 | 74,673 | 0 |
| 2024 | 74,998 | 458 | 29,952 | 0 | 74,998 | 0 |
| 2025 | 12,096 | 264 | 4,632 | 0 | 12,096 | 0 |

The last column is the reconciliation in one number per year: it is zero when every stored hour has exactly one file and every file has exactly one stored hour.

## Step 1 — Full-store validation

Every stored hour re-opened offline and checked against the rules that stored it, and against its own manifest entry. This is not the same check the ingestion ran, and the difference is the point: between the two sit a Parquet writer, a resumable driver that rewrites shards, a host power loss mid-chunk, and a store two cards filled into the same tree. Each of those is a way for the manifest and the files to drift apart without either being wrong on its own.

Per hour: the pinned Arrow schema column by column with no extras; the row count against `written_ticks`; `ask >= bid` and both strictly positive; timestamps non-decreasing, UTC, and inside the hour the file is named for; and the hour open under the derived FX week. Because every tick is proven inside its own hour, a Saturday tick cannot hide in a Friday file, so that last check covers the whole `CLOSED_MARKET_TICK` rule.

| measure | value |
| --- | --- |
| pair-months validated | 2,904 |
| hours validated | 1,495,740 |
| ticks validated | 5,347,764,214 |
| **failures** | **0** |

| pair | hours | ticks | failures |
| --- | --- | --- | --- |
| `AUDJPY` | 125,863 | 531,302,031 | 0 |
| `AUDUSD` | 111,974 | 326,011,181 | 0 |
| `EURCHF` | 125,862 | 355,767,522 | 0 |
| `EURGBP` | 125,860 | 414,960,812 | 0 |
| `EURJPY` | 125,525 | 687,287,246 | 0 |
| `EURUSD` | 125,868 | 464,846,065 | 0 |
| `GBPJPY` | 125,861 | 633,151,170 | 0 |
| `GBPUSD` | 125,859 | 477,092,950 | 0 |
| `NZDUSD` | 125,824 | 292,334,743 | 0 |
| `USDCAD` | 125,858 | 359,140,520 | 0 |
| `USDCHF` | 125,861 | 348,794,795 | 0 |
| `USDJPY` | 125,525 | 457,075,179 | 0 |

**No stored hour disagrees with its manifest entry, in any respect, anywhere in the store.** That is the result the card expected and it is worth stating plainly rather than burying: the bookkeeping and the data are the same thing described twice.

### Bars against stored hours

The `1h` table for every pair, compared as **sets** of hour timestamps against the hours the manifests record as stored. A bar no stored hour backs is a bar built from data that is no longer there; a stored hour with no bar is an hour no strategy will ever see. Counts alone would miss both if they happened to cancel.

| pair | bars | stored hours | bars with no hour | hours with no bar | on the hour | strictly increasing |
| --- | --- | --- | --- | --- | --- | --- |
| `EURUSD` | 125,868 | 125,868 | 0 | 0 | yes | yes |
| `GBPUSD` | 125,859 | 125,859 | 0 | 0 | yes | yes |
| `USDJPY` | 125,525 | 125,525 | 0 | 0 | yes | yes |
| `USDCHF` | 125,861 | 125,861 | 0 | 0 | yes | yes |
| `AUDUSD` | 88,285 | 88,285 | 0 | 0 | yes | yes |
| `USDCAD` | 125,858 | 125,858 | 0 | 0 | yes | yes |
| `NZDUSD` | 125,824 | 125,824 | 0 | 0 | yes | yes |
| `EURGBP` | 125,860 | 125,860 | 0 | 0 | yes | yes |
| `EURJPY` | 125,525 | 125,525 | 0 | 0 | yes | yes |
| `GBPJPY` | 125,861 | 125,861 | 0 | 0 | yes | yes |
| `EURCHF` | 125,862 | 125,862 | 0 | 0 | yes | yes |
| `AUDJPY` | 125,863 | 125,863 | 0 | 0 | yes | yes |

Pairs where all of that agrees: **12**; pairs where anything does not: **0**. Bar timestamps are bar **open** times in UTC, which is what "on the hour" checks.

## Step 2 — The holiday calendar

Pre-registered decision #5, under ruling R5: the input is the manifest hour **status**, never the `EMPTY_TRADING_HOUR` warning list. The audit measured that list short of the statuses across the store, for a structural reason, so a calendar built from it would have been a calendar with holidays missing and nothing downstream would ever have noticed.

The derivation is one idea. The derived FX week already says which hours should have traded. Where the feed answered *nothing* during one, either the market was shut or the data is missing — and those look identical in one pair and completely different across twelve. So a date where every pair research may read went quiet for at least 6 open hours is a **full** holiday; one where at least 3 did but not all is a **partial** holiday; anything less is **unexplained** and is a data fact rather than a market closure.

"Every pair research may read" is doing real work: ruling R1 excludes AUDUSD before 2011, so the unanimity test is over eleven pairs there. Testing twelve would make every pre-2011 holiday fail because the twelfth pair is not there to agree.

| classification | dates |
| --- | --- |
| full market holidays | **19** |
| partial holidays | 3 |
| unexplained empty dates | 312 |

### The finding that matters more than the calendar

A calendar derived from emptiness can only contain the holidays the feed left empty — and **the feed did not always leave them empty.** Read down this table, which takes the static major-holiday list and asks what the feed actually did on each date:

| year | full | partial | unexplained | traded through | fell on a closed week |
| --- | --- | --- | --- | --- | --- |
| 2005 | 0 | 0 | 0 | 6 | 0 |
| 2006 | 0 | 0 | 0 | 7 | 0 |
| 2007 | 0 | 0 | 3 | 4 | 0 |
| 2008 | 0 | 0 | 4 | 3 | 0 |
| 2009 | 0 | 0 | 3 | 2 | 2 |
| 2010 | 0 | 0 | 2 | 4 | 1 |
| 2011 | 0 | 0 | 0 | 6 | 1 |
| 2012 | 0 | 1 | 0 | 6 | 0 |
| 2013 | 2 | 0 | 0 | 5 | 0 |
| 2014 | 2 | 0 | 0 | 5 | 0 |
| 2015 | 2 | 0 | 0 | 3 | 2 |
| 2016 | 1 | 0 | 0 | 6 | 0 |
| 2017 | 1 | 0 | 1 | 5 | 0 |
| 2018 | 2 | 0 | 0 | 5 | 0 |
| 2019 | 2 | 0 | 0 | 5 | 0 |
| 2020 | 2 | 0 | 0 | 3 | 2 |
| 2021 | 1 | 0 | 0 | 5 | 1 |
| 2022 | 0 | 0 | 1 | 5 | 1 |
| 2023 | 1 | 0 | 1 | 5 | 0 |
| 2024 | 2 | 0 | 0 | 5 | 0 |
| 2025 | 1 | 0 | 0 | 0 | 0 |

Through the early years the feed quoted straight across days the whole market was shut. There is no emptiness there to derive a holiday from, so those dates are absent from the calendar and their bars contain prices nobody traded at. **This calendar is dense in the later years and near-empty in the early ones, and that is a fact about the feed rather than about the market.** Any card that treats an early-era holiday bar as tradeable is reading a quote that had no market behind it; `config/calendar.toml` carries the same warning at the top of the file.

### Derived against the static list

Two independent statements: one is what the feed did, the other is what a calendar says. Neither is the authority.

| comparison | dates |
| --- | --- |
| derived full holidays | 19 |
| static major holidays in the window | 140 |
| both agree | **19** |
| derived, not on the static list | 0 |
| static, and the feed traded through it | 110 |
| static, and the derived week was already shut | 10 |
| static, and only some pairs stopped | 1 |

The two rows that a naive set difference would have merged are kept apart deliberately. "The feed traded through it" says the market was open on a bank holiday, which is a fact about FX. "The derived week was already shut" says the holiday fell on a weekend and nobody was ever asked, which is a fact about the calendar. Reported as one number they would cancel into nonsense.

Every derived full holiday appears on the static list: there is no date where the whole market stopped and no major-holiday list explains it.

### Empty hours the calendar does not explain

The card is explicit that these are data facts for T4 and not holidays, and keeping them out of the calendar is the point: a date where two pairs went quiet and ten did not is evidence about the feed, and filing it as a market closure would launder that evidence into a fact about the market.

| measure | value |
| --- | --- |
| dates | 312 |
| empty hours on them | 963 |
| dates where no pair reached the depth threshold | 298 |

By year:

| year | dates |
| --- | --- |
| 2005 | 1 |
| 2007 | 86 |
| 2008 | 76 |
| 2009 | 72 |
| 2010 | 39 |
| 2011 | 5 |
| 2012 | 12 |
| 2013 | 1 |
| 2014 | 1 |
| 2015 | 1 |
| 2016 | 1 |
| 2017 | 3 |
| 2018 | 1 |
| 2019 | 3 |
| 2020 | 1 |
| 2022 | 3 |
| 2023 | 4 |
| 2024 | 2 |

The deepest of them:

| date | pairs empty | empty hours |
| --- | --- | --- |
| 2007-12-25 | 2 | 48 |
| 2009-06-15 | 2 | 48 |
| 2009-06-16 | 2 | 48 |
| 2009-06-17 | 2 | 48 |
| 2009-06-18 | 2 | 48 |
| 2008-01-01 | 2 | 44 |
| 2008-12-25 | 2 | 44 |
| 2009-01-01 | 2 | 44 |
| 2009-12-25 | 2 | 44 |
| 2010-01-01 | 2 | 44 |
| 2019-05-26 | 12 | 33 |
| 2009-06-19 | 2 | 28 |
| 2015-12-31 | 12 | 24 |
| 2017-12-31 | 12 | 24 |
| 2020-12-31 | 12 | 24 |
| 2023-12-31 | 12 | 24 |
| 2024-12-31 | 12 | 24 |
| 2018-12-31 | 12 | 23 |
| 2005-01-18 | 11 | 22 |
| 2019-12-31 | 12 | 22 |

### The committed calendar

`config/calendar.toml` is tracked and versioned, which means anybody can open and edit it — so it is re-derived on every run of this experiment and compared against what is on disk. A holiday quietly added by hand fails the comparison instead of propagating into every card that trusts the calendar.

| check | result |
| --- | --- |
| the file exists | yes |
| its rules match the ones used here | yes |
| its full holidays match the re-derivation | yes |
| its partial holidays match the re-derivation | yes |
| full holidays recorded | 19 |
| partial holidays recorded | 3 |

After this card, `EMPTY_TRADING_HOUR` on a calendar date is `closed` rather than a warning — pre-reg #5's closing clause, now that there is a calendar to test a date against.

## Step 3 — Cross-check against OANDA

Pre-registered decision #7. FX has no consolidated tape, so there is no authority to check Dukascopy against — only a second venue, whose disagreement with the first is evidence about **both**. That asymmetry decides what this step may conclude: a difference beyond threshold does not mean Dukascopy is wrong, it means the stored data cannot be relied on until somebody looks.

Compared: the mid of the first and last stored tick in an hour against the open and close of OANDA's H1 candle for the same hour. Both boundaries, and the worse of the two decides — thresholding one of them would have been a choice about which half of the hour to check, and the table below shows they behave the same way anyway. Read from the ticks rather than from the bar tables, so the comparison tests the stored data itself rather than a resampling of it.

| parameter | value |
| --- | --- |
| threshold (pre-reg #7, pinned) | 1.0 pip |
| dates sampled per pair per year | 12 |
| hours sampled per date | 4 (01:00Z, 09:00Z, 15:00Z, 21:00Z) |
| roll window exempt (pre-reg #4, derived per date) | 16:00–18:00 `America/New_York` |
| pair-dates fetched | 2,952 |
| **hours compared** | **11,790** |
| of which inside the roll window, exempt | 2,941 |
| sampled hours the store or the venue lacked | 18 |
| **hours beyond threshold outside the roll window** | **1,327** |
| **verdict (pre-reg #7)** | **BLOCKED** |

The sample deliberately includes an hour inside the roll window. An exemption that is never exercised is an exemption nobody has tested, and the roll is the one hour where two venues have most reason to disagree — so those hours are compared and reported, and excluded from the threshold and from the statistics below.

### OANDA's own history, per pair

Asked rather than assumed. "Both feeds agree" means much less for a pair whose second feed starts late, so the window each comparison actually had is measured:

| pair | earliest H1 candle | available |
| --- | --- | --- |
| `AUDJPY` | 2004-05-31 | yes |
| `AUDUSD` | 2003-01-01 | yes |
| `EURCHF` | 2003-01-01 | yes |
| `EURGBP` | 2003-01-01 | yes |
| `EURJPY` | 2003-01-01 | yes |
| `EURUSD` | 2003-01-01 | yes |
| `GBPJPY` | 2003-01-02 | yes |
| `GBPUSD` | 2003-01-01 | yes |
| `NZDUSD` | 2003-01-01 | yes |
| `USDCAD` | 2003-01-01 | yes |
| `USDCHF` | 2003-01-01 | yes |
| `USDJPY` | 2003-01-01 | yes |

### The difference distribution

Absolute difference in pips, worst of the hour's open and close, roll-window hours excluded:

| pair | hours compared | roll-exempt | mean | median | p95 | max | beyond threshold |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 1,008 | 252 | 0.771 | 0.250 | 3.850 | 26.200 | 127 |
| `AUDUSD` | 720 | 180 | 0.299 | 0.250 | 0.600 | 3.650 | 2 |
| `EURCHF` | 1,007 | 251 | 0.671 | 0.300 | 2.900 | 7.000 | 131 |
| `EURGBP` | 1,005 | 249 | 0.462 | 0.250 | 1.700 | 5.850 | 78 |
| `EURJPY` | 1,005 | 251 | 0.676 | 0.350 | 2.350 | 9.600 | 122 |
| `EURUSD` | 1,004 | 251 | 0.654 | 0.300 | 2.500 | 6.800 | 152 |
| `GBPJPY` | 1,007 | 251 | 0.970 | 0.400 | 4.100 | 18.350 | 163 |
| `GBPUSD` | 1,008 | 252 | 0.656 | 0.350 | 2.700 | 7.200 | 118 |
| `NZDUSD` | 1,008 | 252 | 0.703 | 0.300 | 3.300 | 8.800 | 115 |
| `USDCAD` | 1,004 | 250 | 0.672 | 0.350 | 2.900 | 7.900 | 114 |
| `USDCHF` | 1,008 | 252 | 0.630 | 0.350 | 2.200 | 11.150 | 118 |
| `USDJPY` | 1,006 | 250 | 0.463 | 0.250 | 1.900 | 4.500 | 87 |

The widest single disagreement anywhere in the sample is 26.200 pip on `AUDJPY`, against a 1.0 pip threshold.

### The result depends on how many ticks the hour holds

This is the cut that explains the rest of the step, and it is not a property of either feed's accuracy.

Two independent quote streams are being compared by their last print before the same instant. What separates them is the product of two things: how far apart in time the two prints are, and how fast price is moving. Split the same sample by tick count and both show up:

| ticks in the hour | hours | median abs Δ (pips) | p95 | max | beyond threshold | share |
| --- | --- | --- | --- | --- | --- | --- |
| `1k-3k` | 2,391 | 0.300 | 1.500 | 7.100 | 216 | 9.0% |
| `3k-10k` | 4,794 | 0.250 | 1.100 | 9.950 | 273 | 5.7% |
| `500-1k` | 424 | 1.900 | 5.500 | 26.200 | 260 | 61.3% |
| `<500` | 624 | 2.000 | 4.700 | 11.000 | 505 | 80.9% |
| `>=10k` | 616 | 0.300 | 1.500 | 18.350 | 73 | 11.8% |

The relationship is **not** monotonic, and the shape is the interesting part. The thinnest hours are much the worst: their two prints can be minutes apart, so the comparison measures how far the market moved in between rather than whether the venues agree about price. But the densest hours are worse than the merely busy ones too — for the opposite reason, since an hour holding that many quotes is usually one where price is moving fast enough that even a sub-second gap between prints is worth a pip. Agreement is best in the middle, where prints are close together and price is not sprinting.

So a fixed pip threshold has different resolving power in different eras, because the eras have different quote densities. That is the same instrument problem ruling **R3** states about spread percentiles, arriving here in a different statistic — and it means the count of hours beyond threshold is **not** comparable across years without this column beside it.

Nothing above softens pre-reg #7. The threshold is pinned, it was applied as pinned, and the hours beyond it are blocked. What the table changes is the *diagnosis* a reviewer should reach for: a thin hour that disagrees is evidence about quote density, and a dense hour that disagrees is evidence about the data.

The hour's two boundaries were compared separately, and they behave the same way — so neither the open nor the close is the noisy one, and the difference is a property of the hour rather than of which edge of it was sampled:

| boundary | hours | median abs Δ (pips) | p95 | max |
| --- | --- | --- | --- | --- |
| first tick vs candle open | 8,849 | 0.250 | 2.100 | 26.200 |
| last tick vs candle close | 8,849 | 0.150 | 2.050 | 20.000 |

### By year

Read against the density table above, not on its own:

| year | hours compared | median abs Δ (pips) | p95 | max |
| --- | --- | --- | --- | --- |
| 2005 | 396 | 2.700 | 5.400 | 12.200 |
| 2006 | 396 | 2.100 | 4.700 | 26.200 |
| 2007 | 396 | 1.000 | 3.400 | 11.000 |
| 2008 | 396 | 0.950 | 3.300 | 13.500 |
| 2009 | 394 | 0.700 | 2.800 | 7.350 |
| 2010 | 396 | 0.450 | 1.900 | 5.200 |
| 2011 | 432 | 0.300 | 0.800 | 2.000 |
| 2012 | 432 | 0.300 | 0.650 | 4.550 |
| 2013 | 432 | 0.200 | 0.600 | 1.450 |
| 2014 | 432 | 0.250 | 0.700 | 2.550 |
| 2015 | 432 | 0.350 | 0.900 | 2.050 |
| 2016 | 429 | 0.300 | 0.750 | 2.350 |
| 2017 | 432 | 0.300 | 0.750 | 1.500 |
| 2018 | 432 | 0.300 | 0.750 | 1.150 |
| 2019 | 432 | 0.250 | 0.500 | 1.500 |
| 2020 | 432 | 0.200 | 0.550 | 0.850 |
| 2021 | 432 | 0.200 | 0.500 | 0.800 |
| 2022 | 430 | 0.200 | 0.500 | 3.700 |
| 2023 | 432 | 0.200 | 0.550 | 7.600 |
| 2024 | 432 | 0.150 | 0.450 | 11.150 |
| 2025 | 432 | 0.150 | 0.700 | 18.350 |

### Hours beyond threshold, and what is blocked

Pre-reg #7: any hour outside the roll window beyond threshold **blocks the affected data from research use** pending review. "That data" is the hour — the blocked set is per hour, not per pair and not per year, because widening it to the pair-year would block decades over a handful of thin hours and nobody registered that. The set is enumerated in the result document; this is its shape.

| measure | value |
| --- | --- |
| **hours blocked** | **1,327** |
| of the hours compared outside the roll window | 8,849 (15.0%) |
| pair-years they fall in | 122 |

Where they fall, by pair and year — read against the density table above, which is what explains the shape:

| pair | year | hours blocked |
| --- | --- | --- |
| `AUDJPY` | 2005 | 36 |
| `AUDJPY` | 2006 | 36 |
| `GBPJPY` | 2005 | 36 |
| `USDCAD` | 2005 | 35 |
| `USDCHF` | 2005 | 35 |
| `EURCHF` | 2005 | 34 |
| `EURUSD` | 2005 | 34 |
| `GBPJPY` | 2006 | 34 |
| `GBPUSD` | 2005 | 33 |
| `USDCHF` | 2006 | 33 |
| `EURCHF` | 2006 | 32 |
| `EURJPY` | 2005 | 32 |
| `NZDUSD` | 2006 | 32 |
| `GBPUSD` | 2006 | 30 |
| `NZDUSD` | 2005 | 30 |
| `USDJPY` | 2006 | 30 |
| `EURJPY` | 2006 | 29 |
| `EURUSD` | 2006 | 29 |
| `GBPJPY` | 2007 | 28 |
| `GBPJPY` | 2008 | 28 |
| `EURGBP` | 2005 | 27 |
| `EURUSD` | 2007 | 26 |
| `USDJPY` | 2005 | 26 |
| `USDCAD` | 2006 | 25 |
| `EURUSD` | 2008 | 24 |
| `EURUSD` | 2010 | 22 |
| `USDCAD` | 2007 | 21 |
| `EURCHF` | 2009 | 20 |
| `NZDUSD` | 2007 | 20 |
| `GBPUSD` | 2008 | 19 |
| `AUDJPY` | 2008 | 18 |
| `EURGBP` | 2006 | 18 |
| `GBPUSD` | 2007 | 18 |
| `AUDJPY` | 2007 | 17 |
| `EURCHF` | 2008 | 17 |
| `EURJPY` | 2007 | 17 |
| `NZDUSD` | 2008 | 17 |
| `USDCAD` | 2008 | 17 |
| `EURJPY` | 2008 | 15 |
| `GBPJPY` | 2009 | 15 |

The widest disagreements, worst first:

| pair | date | hour | open Δ pips | close Δ pips | worst abs Δ (pips) | roll exempt |
| --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 2006-08-17 | 09:00Z | 26.200 | 20.000 | 26.200 | **no** |
| `GBPJPY` | 2025-01-07 | 15:00Z | -18.350 | -0.850 | 18.350 | **no** |
| `GBPJPY` | 2008-03-04 | 09:00Z | -0.600 | 13.500 | 13.500 | **no** |
| `GBPJPY` | 2005-06-07 | 15:00Z | -12.200 | -6.100 | 12.200 | **no** |
| `USDCHF` | 2024-01-05 | 15:00Z | 11.150 | 0.150 | 11.150 | **no** |
| `GBPJPY` | 2007-01-04 | 15:00Z | -11.000 | -3.300 | 11.000 | **no** |
| `GBPJPY` | 2005-12-02 | 15:00Z | 1.300 | -10.700 | 10.700 | **no** |
| `GBPJPY` | 2008-10-21 | 09:00Z | -0.100 | 9.950 | 9.950 | **no** |
| `EURJPY` | 2025-02-03 | 15:00Z | 9.600 | -0.050 | 9.600 | **no** |
| `GBPJPY` | 2006-12-12 | 15:00Z | 9.400 | -1.600 | 9.400 | **no** |
| `EURJPY` | 2025-01-10 | 15:00Z | -9.150 | 0.100 | 9.150 | **no** |
| `NZDUSD` | 2005-08-29 | 09:00Z | -8.800 | -6.100 | 8.800 | **no** |
| `NZDUSD` | 2005-08-29 | 15:00Z | -2.400 | -8.600 | 8.600 | **no** |
| `EURJPY` | 2005-01-12 | 09:00Z | -1.300 | -8.400 | 8.400 | **no** |
| `AUDJPY` | 2006-08-17 | 15:00Z | -0.600 | -8.200 | 8.200 | **no** |
| `USDCHF` | 2008-12-31 | 01:00Z | -8.150 | -0.450 | 8.150 | **no** |
| `EURJPY` | 2025-01-07 | 15:00Z | 8.100 | -0.550 | 8.100 | **no** |
| `USDCAD` | 2008-12-04 | 01:00Z | -7.900 | -3.800 | 7.900 | **no** |
| `EURJPY` | 2008-10-27 | 09:00Z | -7.650 | -2.550 | 7.650 | **no** |
| `EURJPY` | 2023-12-05 | 15:00Z | -7.600 | 0.000 | 7.600 | **no** |
| `GBPJPY` | 2005-06-07 | 09:00Z | -7.500 | -3.500 | 7.500 | **no** |
| `EURJPY` | 2009-02-25 | 15:00Z | -7.350 | 0.600 | 7.350 | **no** |
| `GBPUSD` | 2008-12-01 | 15:00Z | 7.200 | -0.850 | 7.200 | **no** |
| `AUDJPY` | 2006-10-24 | 09:00Z | -7.100 | -2.400 | 7.100 | **no** |
| `GBPJPY` | 2007-02-16 | 15:00Z | -7.100 | -0.900 | 7.100 | **no** |
| `NZDUSD` | 2005-10-13 | 09:00Z | -7.100 | -3.300 | 7.100 | **no** |
| `EURCHF` | 2007-04-24 | 01:00Z | -7.000 | 0.000 | 7.000 | **no** |
| `AUDJPY` | 2005-04-18 | 01:00Z | -6.950 | -3.000 | 6.950 | **no** |
| `EURCHF` | 2009-05-07 | 01:00Z | 0.950 | -6.900 | 6.900 | **no** |
| `GBPUSD` | 2007-02-16 | 15:00Z | -6.900 | -0.600 | 6.900 | **no** |
| `EURUSD` | 2009-06-17 | 15:00Z | -3.400 | -6.800 | 6.800 | **no** |
| `AUDJPY` | 2005-09-14 | 01:00Z | -2.550 | -6.500 | 6.500 | **no** |
| `AUDJPY` | 2005-09-14 | 09:00Z | -5.700 | -6.500 | 6.500 | **no** |
| `USDCAD` | 2009-01-02 | 01:00Z | -6.500 | 4.250 | 6.500 | **no** |
| `AUDJPY` | 2005-07-05 | 09:00Z | -6.400 | -4.700 | 6.400 | **no** |
| `AUDJPY` | 2005-07-05 | 15:00Z | -5.100 | -6.300 | 6.300 | **no** |
| `AUDJPY` | 2006-12-06 | 09:00Z | 0.200 | -6.200 | 6.200 | **no** |
| `NZDUSD` | 2006-10-23 | 15:00Z | -4.300 | -6.200 | 6.200 | **no** |
| `USDCAD` | 2008-11-25 | 15:00Z | 6.200 | -0.750 | 6.200 | **no** |
| `EURCHF` | 2005-01-20 | 09:00Z | -6.100 | -3.800 | 6.100 | **no** |

**What this does and does not mean.** It does not mean the stored data is wrong: there is no consolidated tape to be wrong against, and the density table shows the disagreement tracking quote sparsity rather than anything about either feed's accuracy. It does mean these specific hours are not corroborated by a second venue, and pre-reg #7 says an uncorroborated hour is out of research use until a checkpoint says otherwise. Both halves of that are the pre-registration working as intended, and neither is this card's to reinterpret.

## The AUDUSD exclusion (ruling R1)

Stated here because every report and scorecard that touches the pair must state it, and this one touches it in all four steps.

| pair | ruling | window | why |
| --- | --- | --- | --- |
| `AUDUSD` | R1 | before 2011-01-01 | crossed-quote corruption in two bounded episodes, 2007-04 to 2008-09 and 2009-04 to 2010-10, rejected most of four years and left what survived a biased sample of the window rather than merely a thin one |

The loader refused it while this result was produced (`PAIR_EXCLUDED_WINDOW`: yes), and withheld 1,772 date(s) across 1 pair(s) from the reads this experiment made. The hours are on disk and validated — Step 1 checked them like any other — and research may not read them. A cross-pair analysis spanning the excluded window runs on eleven pairs and has to say so.

## Provenance

* Config: `experiments/T3-quality/config.toml` (sha256 `a778f17dc18e0452`)
* Manifests: `data/research/manifests/pair=<PAIR>/<YYYY-MM>/manifest.json`, read the canonical way (SPEC2 §The canonical manifest reading) — hour records and the derived coverage block, never the session warning log.
* Validation pass: `experiments/T3-quality/validation.jsonl`, one record per pair-month, written by `python -m research.validate_store`.
* Cross-check: `experiments/T3-quality/oanda.jsonl` and `experiments/T3-quality/oanda_availability.jsonl`, written by `python -m research.crosscheck_oanda` against the OANDA practice host. The token comes from `OANDA_API_TOKEN` and is never logged.
* Calendar: `config/calendar.toml`, written by `python -m research.calendar_build` and re-derived and compared on every run of this experiment.
* Result: `experiments/T3-quality/result.json`, hash `b33e6949a9ed844a1c10a331de841e23551ae87564909d68963d6007dde22449`
* Loader mode `scoring`, scored `False`, re-run class `full`. It served 12 file(s) across 12 pair(s) and 6307 date(s); sealed dates served: none; dates withheld by an exclusion window: 1,772.
* Research gate: exit 0 (full, 2026-09-06)

