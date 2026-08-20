# T1 — Dukascopy coverage survey

**Task card:** `taskcards/T1.md` · **Experiment:** `T1-coverage` · **Seed:** 20260819 · **Result hash:** `e78986e4293ef95a`

**Window probed:** 2005-01-03 → 2025-02-28, 5,260 trading days per pair at 13:00 UTC. **Trials ledgered under T1:** 7 (SPEC2 pre-reg #10; the count includes the probe harvest sessions, which are data collection rather than analysis).

This is a **sampling probe survey, not an ingestion**. Every number below comes from what the Dukascopy datafeed answered to a single hourly request; nothing was written to `data/`, no bars were built, and no strategy content appears anywhere in this report. The experiment is not scorable and carries no scorecard.

## Method, and the rules applied

* **First pass.** One probe per pair per trading day at 13:00 UTC across the whole window — 63,120 probes. Each is classified `data` (HTTP 200, decodes, at least one tick), `empty` (HTTP 200, zero bytes — the feed's way of saying the market was closed), `missing` (HTTP 404) or `error` (every attempt failed).
* **Recommended start.** The earliest probed trading day that itself returned data, and from which the data fraction is at least 95% over **both** the next 120 trading days and the whole remainder of the window. The near window rejects an island of early coverage; the far window rejects a start just before a long hole.
* **Material hole.** A maximal run of at least 5 consecutive trading days from the recommended start onward that did not return data. Each is reported with its composition, because a run of `empty` is a closed market and a run of `missing` is absent history.
* **Refinement.** The first pass already dates every boundary and hole to the day, which is finer than the week the card asks for. Refinement therefore spends its probes on the other axis: alternate hours between 08:00 and 16:00 UTC around each boundary and inside each hole, which settles whether the hole is a property of the day or only of the survey hour.
* **Quality.** Three probes per pair, spread across its history (earliest, midpoint, latest), decoded in full and put through the Phase 1 validator. Presence is not usability.

## Survey completeness

The survey is **100.00% complete**: 63,120 of 63,120 planned first-pass probes were answered. Everything below is conditional on that.

| classification | probes | share of planned |
| --- | --- | --- |
| data | 62,849 | 99.6% |
| empty | 271 | 0.4% |
| missing | 0 | 0.0% |
| error | 0 | 0.0% |
| unprobed | 0 | 0.0% |

## Per-pair verdict

| pair | first data | recommended start | years | data % from start | material holes | quality checks ok |
| --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 2005-01-03 | **2005-01-03** | 20.1 | 99.64% | 0 | 3/3 |
| `AUDUSD` | 2005-01-03 | **2005-01-03** | 20.1 | 99.37% | 0 | 3/3 |
| `EURCHF` | 2005-01-03 | **2005-01-03** | 20.1 | 99.64% | 0 | 3/3 |
| `EURGBP` | 2005-01-03 | **2005-01-03** | 20.1 | 99.62% | 0 | 3/3 |
| `EURJPY` | 2005-01-03 | **2005-01-03** | 20.1 | 99.41% | 1 | 3/3 |
| `EURUSD` | 2005-01-03 | **2005-01-03** | 20.1 | 99.64% | 0 | 3/3 |
| `GBPJPY` | 2005-01-03 | **2005-01-03** | 20.1 | 99.62% | 0 | 3/3 |
| `GBPUSD` | 2005-01-03 | **2005-01-03** | 20.1 | 99.62% | 0 | 3/3 |
| `NZDUSD` | 2005-01-03 | **2005-01-03** | 20.1 | 99.62% | 0 | 3/3 |
| `USDCAD` | 2005-01-03 | **2005-01-03** | 20.1 | 99.62% | 0 | 3/3 |
| `USDCHF` | 2005-01-03 | **2005-01-03** | 20.1 | 99.64% | 0 | 3/3 |
| `USDJPY` | 2005-01-03 | **2005-01-03** | 20.1 | 99.41% | 1 | 3/3 |

Read the recommended start as *the earliest date research may begin*, not as a claim that everything after it is flawless — the holes column is where that claim is qualified.

## Per pair, in full

### AUDJPY

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.64% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5241 | 19 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 984 | 0 | 0 | 0 | 3.00 | 9.00 | 40.0 | none | ok |
| 2015-01-13T13Z | 6,239 | 0 | 0 | 0 | 1.20 | 1.70 | 40.0 | none | ok |
| 2025-02-28T13Z | 8,841 | 0 | 0 | 0 | 0.90 | 4.52 | 40.0 | none | ok |

### AUDUSD

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.37% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5227 | 33 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2007 | 252 | 9 | 0 | 0 | 0 |
| 2008 | 260 | 2 | 0 | 0 | 0 |
| 2009 | 259 | 2 | 0 | 0 | 0 |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 507 | 0 | 0 | 0 | 2.00 | 4.70 | 20.0 | none | ok |
| 2015-01-22T13Z | 6,637 | 0 | 0 | 0 | 1.40 | 7.10 | 20.0 | none | ok |
| 2025-02-28T13Z | 3,969 | 0 | 0 | 0 | 1.00 | 2.00 | 20.0 | none | ok |

### EURCHF

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.64% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5241 | 19 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 908 | 0 | 0 | 0 | 2.00 | 5.00 | 40.0 | none | ok |
| 2015-01-13T13Z | 543 | 0 | 0 | 0 | 0.70 | 1.50 | 40.0 | none | ok |
| 2025-02-28T13Z | 6,773 | 0 | 0 | 0 | 1.10 | 3.42 | 40.0 | none | ok |

### EURGBP

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.62% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5240 | 20 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 454 | 0 | 0 | 0 | 1.40 | 2.60 | 40.0 | none | ok |
| 2015-01-13T13Z | 4,344 | 0 | 0 | 0 | 0.80 | 1.20 | 40.0 | none | ok |
| 2025-02-28T13Z | 4,730 | 0 | 0 | 0 | 0.80 | 3.53 | 40.0 | none | ok |

### EURJPY

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.41% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

| from | to | trading days | composition | refined days | days with data at another hour | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2009-06-15 | 2009-06-19 | 5 | empty 5 | 3 | 1 | partial |

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5229 | 31 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2007 | 260 | 1 | 0 | 0 | 0 |
| 2008 | 260 | 2 | 0 | 0 | 0 |
| 2009 | 254 | 7 | 0 | 0 | 0 |
| 2010 | 260 | 1 | 0 | 0 | 0 |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 478 | 0 | 0 | 0 | 2.00 | 4.00 | 40.0 | none | ok |
| 2015-01-15T13Z | 14,976 | 0 | 0 | 0 | 1.00 | 10.91 | 40.0 | none | ok |
| 2025-02-28T13Z | 17,454 | 0 | 0 | 0 | 1.20 | 6.21 | 40.0 | none | ok |

### EURUSD

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.64% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5241 | 19 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 923 | 0 | 0 | 0 | 1.00 | 2.63 | 20.0 | none | ok |
| 2015-01-13T13Z | 5,338 | 0 | 0 | 0 | 0.20 | 0.57 | 20.0 | none | ok |
| 2025-02-28T13Z | 6,980 | 0 | 0 | 0 | 0.50 | 1.60 | 20.0 | none | ok |

### GBPJPY

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.62% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5240 | 20 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 493 | 0 | 0 | 0 | 4.00 | 7.45 | 40.0 | none | ok |
| 2015-01-13T13Z | 8,176 | 0 | 0 | 0 | 1.70 | 2.50 | 40.0 | none | ok |
| 2025-02-28T13Z | 13,619 | 0 | 0 | 0 | 2.10 | 9.24 | 40.0 | none | ok |

### GBPUSD

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.62% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5240 | 20 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 499 | 0 | 0 | 0 | 2.00 | 4.00 | 20.0 | none | ok |
| 2015-01-13T13Z | 5,552 | 0 | 0 | 0 | 0.70 | 1.10 | 20.0 | none | ok |
| 2025-02-28T13Z | 5,225 | 0 | 0 | 0 | 0.80 | 2.70 | 20.0 | none | ok |

### NZDUSD

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.62% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5240 | 20 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 466 | 0 | 0 | 0 | 4.00 | 8.54 | 20.0 | none | ok |
| 2015-01-13T13Z | 3,123 | 0 | 0 | 0 | 1.10 | 1.60 | 20.0 | none | ok |
| 2025-02-28T13Z | 3,092 | 0 | 0 | 0 | 1.10 | 2.49 | 20.0 | none | ok |

### USDCAD

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.62% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5240 | 20 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 447 | 0 | 0 | 0 | 2.00 | 4.93 | 20.0 | none | ok |
| 2015-01-13T13Z | 3,566 | 0 | 0 | 0 | 1.00 | 1.70 | 20.0 | none | ok |
| 2025-02-28T13Z | 7,295 | 0 | 0 | 0 | 1.30 | 5.10 | 20.0 | none | ok |

### USDCHF

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.64% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

_none_

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5241 | 19 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 476 | 0 | 0 | 0 | 2.00 | 4.00 | 20.0 | none | ok |
| 2015-01-13T13Z | 4,422 | 0 | 0 | 0 | 0.80 | 1.30 | 20.0 | none | ok |
| 2025-02-28T13Z | 5,157 | 0 | 0 | 0 | 0.90 | 2.78 | 20.0 | none | ok |

### USDJPY

**Recommended start: 2005-01-03.** The feed's first answer carrying data for this pair is that same day, so coverage begins at or before the edge of the probe window. Measured there, the data fraction is 100.00% over the near window and 99.41% over the remainder of the window. Last day returning data: 2025-02-28.

Probe density around the boundary, 20 trading days each side:

| side | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| before | 0 | 0 | 0 | 0 | 0 |
| from start | 20 | 0 | 0 | 0 | 0 |

Material holes (runs of at least 5 consecutive non-data trading days at or after the recommended start):

| from | to | trading days | composition | refined days | days with data at another hour | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2009-06-15 | 2009-06-19 | 5 | empty 5 | 3 | 1 | partial |

Probe classifications across the whole window:

| data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- |
| 5229 | 31 | 0 | 0 | 0 |

Years containing anything other than `data`:

| year | data | empty | missing | error | unprobed |
| --- | --- | --- | --- | --- | --- |
| 2007 | 260 | 1 | 0 | 0 | 0 |
| 2008 | 260 | 2 | 0 | 0 | 0 |
| 2009 | 254 | 7 | 0 | 0 | 0 |
| 2010 | 260 | 1 | 0 | 0 | 0 |
| 2012 | 260 | 1 | 0 | 0 | 0 |
| 2013 | 259 | 2 | 0 | 0 | 0 |
| 2014 | 259 | 2 | 0 | 0 | 0 |
| 2015 | 259 | 2 | 0 | 0 | 0 |
| 2016 | 260 | 1 | 0 | 0 | 0 |
| 2017 | 259 | 1 | 0 | 0 | 0 |
| 2018 | 259 | 2 | 0 | 0 | 0 |
| 2019 | 259 | 2 | 0 | 0 | 0 |
| 2020 | 260 | 2 | 0 | 0 | 0 |
| 2021 | 260 | 1 | 0 | 0 | 0 |
| 2023 | 259 | 1 | 0 | 0 | 0 |
| 2024 | 260 | 2 | 0 | 0 | 0 |
| 2025 | 42 | 1 | 0 | 0 | 0 |

Data-quality spot checks (full decode, Phase 1 validator):

| hour | ticks | dupes | crossed | non-positive | median spread (pips) | p99.9 | ceiling | issues |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2005-01-03T09Z | 462 | 0 | 0 | 0 | 1.50 | 3.77 | 20.0 | none | ok |
| 2015-01-15T13Z | 9,946 | 0 | 0 | 0 | 0.40 | 3.60 | 20.0 | none | ok |
| 2025-02-28T13Z | 13,256 | 0 | 0 | 0 | 0.60 | 3.40 | 20.0 | none | ok |

## What this bounds

Bar counts below are ceilings, not forecasts: they are the trading days that returned data multiplied by the bars a full session yields at each timeframe of SPEC2 pre-reg #6 (5m=288, 30m=48, 1h=24, 4h=6, 1d=1). Holidays, half days and intraday gaps will take real counts below these; nothing will take them above.

| pair | research start | years | trading days with data | ≤ 5m bars | ≤ 30m bars | ≤ 1h bars | ≤ 4h bars | ≤ 1d bars |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 2005-01-03 | 20.1 | 5,241 | 1,509,408 | 251,568 | 125,784 | 31,446 | 5,241 |
| `AUDUSD` | 2005-01-03 | 20.1 | 5,227 | 1,505,376 | 250,896 | 125,448 | 31,362 | 5,227 |
| `EURCHF` | 2005-01-03 | 20.1 | 5,241 | 1,509,408 | 251,568 | 125,784 | 31,446 | 5,241 |
| `EURGBP` | 2005-01-03 | 20.1 | 5,240 | 1,509,120 | 251,520 | 125,760 | 31,440 | 5,240 |
| `EURJPY` | 2005-01-03 | 20.1 | 5,229 | 1,505,952 | 250,992 | 125,496 | 31,374 | 5,229 |
| `EURUSD` | 2005-01-03 | 20.1 | 5,241 | 1,509,408 | 251,568 | 125,784 | 31,446 | 5,241 |
| `GBPJPY` | 2005-01-03 | 20.1 | 5,240 | 1,509,120 | 251,520 | 125,760 | 31,440 | 5,240 |
| `GBPUSD` | 2005-01-03 | 20.1 | 5,240 | 1,509,120 | 251,520 | 125,760 | 31,440 | 5,240 |
| `NZDUSD` | 2005-01-03 | 20.1 | 5,240 | 1,509,120 | 251,520 | 125,760 | 31,440 | 5,240 |
| `USDCAD` | 2005-01-03 | 20.1 | 5,240 | 1,509,120 | 251,520 | 125,760 | 31,440 | 5,240 |
| `USDCHF` | 2005-01-03 | 20.1 | 5,241 | 1,509,408 | 251,568 | 125,784 | 31,446 | 5,241 |
| `USDJPY` | 2005-01-03 | 20.1 | 5,229 | 1,505,952 | 250,992 | 125,496 | 31,374 | 5,229 |

### Flags for the checkpoint

Flagged mechanically, by the rules stated here, and **not decided**: universe membership is a checkpoint decision (SPEC2 pre-reg #3), and this card's non-goals put it out of scope. A pair is flagged when it has no start date at all, when its usable history is under 90% of the longest pair's, when more than 2% of its trading days from its start did not return data, when it carries a hole of 20 trading days or more, or when a quality spot check failed.

**No pair met any flag condition.**

## What the survey cost

Recorded because T2 ingests this same feed in bulk and should budget from a measurement rather than from optimism. These are the harvest sessions' own counters, summed from their ledger end records.

| measure | value |
| --- | --- |
| harvest sessions that finished | 4 |
| probes completed in them | 58,386 |
| wall clock | 10.0 h |
| sustained rate | 1.63 probes/s |
| seconds parked waiting out the feed | 5,046 (14% of wall clock, summed across both workers) |
| throttled responses | 782 |
| outages ridden out | 0 |

A session that is interrupted leaves a start record and no end record, which is exactly what the ledger is for. 4,980 probes on disk were collected by such a session and are counted in the survey above but not in this table, so the rate here is measured over the sessions that finished and reported their own counters.

One probe is one hourly file. A full ingestion asks for every hour of every day rather than one hour per trading day, so at this rate the arithmetic for T2 follows directly from the hour count it plans to fetch — and the parked share above is the part that no amount of client tuning removes, because it is the feed being unavailable.

## Observations

Recorded for the checkpoint review. Per the card, an observation worth chasing becomes a next card only after a checkpoint; nothing here proposes work.

* Every pair's recommended start is the same day, 2005-01-03, and that day is the first the probe window contains. The common window and the per-pair windows are therefore identical, and the binding constraint on how far back research can go is the card's range rather than anything the feed is short of.
* A material hole is *hour-specific* when every day in it has data at another liquid hour, *partial* when only some do, and *whole-day* when none do. Of the ones refinement reached: 0 hour-specific, 2 partial, 0 whole-day. Only whole-day holes are gaps in the feed's history at every hour; an hour-specific one will not survive a full ingestion that asks for all twenty-four.
* Every hole's composition is reported. A run of `empty` is the feed reporting a closed market and is a candidate input to the holiday calendar of pre-reg #5, which is T3's work, not this card's.
* Early history is present and clean, and it is **not the same market**. The earliest and latest spot checks are the same hour of the same pair twenty years apart:

| pair | earliest | ticks | median spread (pips) | latest | ticks | median spread (pips) | spread ratio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `AUDJPY` | 2005-01-03 | 984 | 3.00 | 2025-02-28 | 8,841 | 0.90 | 3.3× |
| `AUDUSD` | 2005-01-03 | 507 | 2.00 | 2025-02-28 | 3,969 | 1.00 | 2.0× |
| `EURCHF` | 2005-01-03 | 908 | 2.00 | 2025-02-28 | 6,773 | 1.10 | 1.8× |
| `EURGBP` | 2005-01-03 | 454 | 1.40 | 2025-02-28 | 4,730 | 0.80 | 1.7× |
| `EURJPY` | 2005-01-03 | 478 | 2.00 | 2025-02-28 | 17,454 | 1.20 | 1.7× |
| `EURUSD` | 2005-01-03 | 923 | 1.00 | 2025-02-28 | 6,980 | 0.50 | 2.0× |
| `GBPJPY` | 2005-01-03 | 493 | 4.00 | 2025-02-28 | 13,619 | 2.10 | 1.9× |
| `GBPUSD` | 2005-01-03 | 499 | 2.00 | 2025-02-28 | 5,225 | 0.80 | 2.5× |
| `NZDUSD` | 2005-01-03 | 466 | 4.00 | 2025-02-28 | 3,092 | 1.10 | 3.6× |
| `USDCAD` | 2005-01-03 | 447 | 2.00 | 2025-02-28 | 7,295 | 1.30 | 1.5× |
| `USDCHF` | 2005-01-03 | 476 | 2.00 | 2025-02-28 | 5,157 | 0.90 | 2.2× |
| `USDJPY` | 2005-01-03 | 462 | 1.50 | 2025-02-28 | 13,256 | 0.60 | 2.5× |

  Both columns passed every validation rule, so this is a change in the market rather than a defect in the data. What it means for a strategy — which horizons can clear a spread that wide, and whether early history should be weighted differently or excluded — is the cost-geometry question of T5, and is not answered here.

## Provenance

* Config: `experiments/T1-coverage/config.toml` (sha256 `6f64a8423bcb05fc`)
* Probe records: `experiments/T1-coverage/probes.jsonl` and `probes.parquet`; quality spot checks: `quality.jsonl`
* Result: `experiments/T1-coverage/result.json`, hash `e78986e4293ef95a2261190bd54f6f37c1fa6da26468ede689fd4346f10a028d`
* Loader mode `scoring`, scored `False`, re-run class `full`. The loader served 0 files: a coverage survey reads the feed, not the data store.
* Research gate: exit 0 on experiments/T1-coverage, 2026-08-20
