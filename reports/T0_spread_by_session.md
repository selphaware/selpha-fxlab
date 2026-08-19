# T0 — Protocol shakedown: mean spread by session, live week

**Task card:** `taskcards/T0.md` · **Experiment:** `experiments/T0-spread-by-session/`
**Result hash:** `dd82fe67ec67ab2b8ed000b1450d8e6b205a81077a71c8d16081e34b21a742e5`
**Trial count for T0:** 1 (from `experiments/ledger.jsonl`)
**Mode:** `mechanical` — quarantined live-week data, **not scorable**

> These numbers are a pipeline check, not a research finding. The data is the
> Phase 1 live week, 2026-08-09 → 2026-08-14, which is inside the holdout seal.
> Pre-registered decision #2 permits it for mechanical checks and forbids it
> for strategy scoring or selection, and the runner enforces that: this
> experiment cannot emit a scorecard. Nothing here may be cited as evidence
> about where edge lives.

## What ran

```
python -m research.run     --config experiments/T0-spread-by-session/config.toml
python verify2/research_gate.py experiments/T0-spread-by-session
```

205,088 EURUSD ticks across six dates, read through `research.loader` in
mechanical mode against the `data/live_week` allowlist. Spreads in pips, using
the Phase 1 `pair_spec` pip size; every statistic rounded to six decimals so the
result hash does not depend on the last bit of a 200,000-term sum.

## Result

| session | ticks | mean | p50 | p75 | p90 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| all | 205,088 | 0.327 | 0.30 | 0.40 | 0.50 | 1.30 |
| london | 46,643 | 0.276 | 0.30 | 0.40 | 0.40 | 0.50 |
| london_ny_overlap | 60,627 | 0.279 | 0.30 | 0.40 | 0.40 | 0.60 |
| new_york | 46,265 | 0.286 | 0.30 | 0.40 | 0.40 | 0.80 |
| tokyo | 44,052 | 0.331 | 0.30 | 0.40 | 0.50 | 0.60 |
| sydney | 7,501 | 1.255 | 0.50 | 1.40 | 3.80 | 6.40 |

## Review question 1 — does this reproduce the Phase 1 report?

Yes, exactly. Every tick count and every percentile matches
`data/live_week/coverage_report.json` as summarised in HANDOFF §3, session by
session: 46,643 London ticks at p50 0.30 / p99 0.50; 7,501 Sydney ticks at p50
0.50 / p90 3.80 / p99 6.40; 205,088 overall at p50 0.30 / p99 1.30. Two
independent code paths — `fxlab.report` reading the store directly and
`research.spread_session` reading it through the research loader — agree on
every figure, which is the only evidence available that the loader does not
quietly reshape what it serves.

The mean is new (Phase 1 reported percentiles only) and is where the tail
shows: Sydney's mean is **4.5× the London mean** while its median is 1.7×.

## Review question 2 — does the protocol need changing before T1?

Nothing blocking. Three observations, for the checkpoint rather than for
action in-loop:

1. **The config named no dates and that was the right default.** The loader
   read every date present under the root and recorded all six in the result's
   scope. For a scoring experiment the same default means the seal is enforced
   partition by partition rather than by trusting the config, which is the
   stronger arrangement.
2. **`code_dirty: true` in the ledger entry** because the harness was still
   being built when T0 ran. Real task cards should run from a committed tree so
   the commit hash means something; the field records it rather than blocking
   it, which is the intended behaviour but is worth knowing.
3. **Six decimals of rounding is doing real work.** Without it the mean of
   205,088 floats is order-dependent in the last bits and the reproducibility
   check would fail for reasons that have nothing to do with honesty. Any T1+
   entry point that reports an aggregate needs the same discipline.

## What this does not say

Nothing about any pair other than EURUSD, nothing about any horizon, nothing
about cost geometry, and nothing about whether the roll hour should be traded.
That is T4 and T5, on research-window data that has not been ingested yet.
