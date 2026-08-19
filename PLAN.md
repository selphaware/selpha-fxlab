# selpha-fxlab — Master Plan

> Living document. Source of truth for the overall roadmap, settled decisions,
> and current status. Phase-level detail lives in per-phase BOOTSTRAP.md /
> spec.md / HANDOFF.md files in the repo (github.com/selphaware/selpha-fxlab) —
> this file is the map, not the territory.

## Goal

Find the best possible FX trading algorithm across a multi-pair universe, and
run it live. Cross-pair structure (cointegration, currency strength, lead-lag,
relative value) is a first-class research question — the system analyses many
pairs simultaneously, not one at a time.

## Settled architecture

| Concern | Decision | Rationale (short) |
|---|---|---|
| Research data (primary) | Dukascopy public tick feed — free, no account, bi5 hourly files decoded in pure Python | Deep bid/ask tick history ~2003+; spread/session analysis needs ticks |
| Research data (cross-check) | OANDA v20 fxpractice candles, `OANDA_API_TOKEN` env var (practice token ONLY — live token banned from repo) | FX has no consolidated tape; second source validates the first (proved: 0.000-pip median mid agreement over live week) |
| Execution venue | Interactive Brokers (IDEALPRO) via IB Gateway + IBC + `ib_async`, paper first | Near-interbank spreads + commission beats retail spread-only pricing |
| Cost model | Explicit, parametric, IB-calibrated (raw spread + 0.20bp commission, $2 min/order, cost_multiplier stress knob). Never inherited from a data feed | Mids are venue-independent; costs are venue-specific; costs decide viability (live week: costs = 25% of gross at 1h trade rate) |
| Cost-model validation | Own tick recorder captures live IB bid/ask from day one of connectivity | Accumulating venue-true spread distributions shrinks proxy risk |
| Repo structure | ONE repo (`selpha-fxlab`), one package (`fxlab`), three sequential phases | Phases share schema, cost model, backtester; split repos = drift |
| Build methodology | ailoop engineering (selphaware/ailoop-engineering): per-phase BOOTSTRAP.md builds a gate-first harness, agent loops unattended against a binary offline gate | Proven in Phase 1: gate caught real bug classes; loop ran unattended to green |
| Language/env | Python 3.12, venv at `E:\CODE\selpha-fxlab\env_fxlab` (inside repo, git-ignored, agent-write-denied), typed/PEP8/pytest | — |
| Git | Agent commits+pushes autonomously via SSH after each gate-green milestone; user reviews between sessions | Checkpoints tied to verified states |

## The three phases

**Phase 1 — Plumbing (COMPLETE, 2026-08-19).** Gate exit 0, 202 unit tests.
Delivered: `fxlab.ingestion` (Dukascopy bi5 + OANDA client + validation +
Parquet + bars), `fxlab.costs` (CostModel protocol, IBCostModel,
RecordedSpreadCostModel shape), `fxlab.backtest` (event-ordered multi-pair,
mid-to-mid gross with explicit spread line, no-lookahead by construction),
`fxlab.recorder` (Feed protocol, FakeFeed, IBFeed stub), typed TOML config.
Live proof: one full EURUSD FX week — 205,088 ticks, 120 open hours, 0 gaps
after resume, OANDA mid agreement median 0.000 pips, derived DST week boundary
matched feed within seconds, offline re-ingest of archived payloads reproduced
the manifest identically. HANDOFF.md in repo: 10 known limitations, 7 open
questions for Phase 2.

**Phase 2 — Research (NEXT).** EDA as hypothesis generation across the
universe; universe ranking; strategy development incl. cross-pair;
walk-forward/purged validation; portfolio-level evaluation. Checkpointed
autonomy — bounded loop tasks between human review gates; ailoop template
adapted for ML/analytics (binary gates verify runs; human judges findings).
Bootstrap 2 to be drafted against HANDOFF.md. Multiple-testing honesty is a
core requirement.

**Phase 3 — Execution engineering (after a strategy survives Phase 2).**
Event-driven live engine, Gateway+IBC automation, hard risk controls,
paper-trading validation, monitoring. Specced against the surviving strategy.

## Non-negotiable research principles (all phases)

No lookahead/leakage; realistic venue-true costs everywhere, same cost model
across all candidates; proper time-series validation (walk-forward/purged,
never random K-fold); multiple-testing honesty (track what was tried; prefer
parsimony); sceptical review of stability across regimes.

## Phase 1 findings that shape Phase 2

- **Session structure is real and large**: EURUSD spread p50 0.3 pip in all
  liquid sessions, but roll/reopen hours run 1.7× median / 8–9× p90 on 4% of
  volume; the one cross-check disagreement (-1.45 pips) was the roll hour.
  Roll-hour treatment (exclude / model / refuse-to-trade) must be decided
  before any strategy is scored.
- **Cost floor measured**: ~0.6–0.8 pips round trip (spread + commission) on
  EURUSD in liquid hours. Reference 1h MA-cross paid 25% of gross in costs.
  Horizon-vs-cost ranking is a first-class EDA deliverable.
- **JPY commission floor is overstated** (quote-currency notional bug,
  HANDOFF known issue 1) — must be fixed before any JPY-quoted pair (5 of 12
  in universe) is ranked or believed.
- **Throttling is real**: ≤2 concurrent connections sustainable; 116 retries
  across 145 requests on a one-week pull. Multi-year 12-pair ingestion needs
  patience budgeting; resume path proven.
- **Coverage before ~2015 unmeasured** — cheap first Phase 2 loop task; bounds
  every backtest.

## Phase 2 pre-decisions (to settle before Bootstrap 2 is drafted)

1. Cost stress ladder, pre-registered (proposal: must survive 1.5×; report 2×).
2. Holdout policy: how much recent history is sealed until final evaluation.
3. Checkpoint cadence: what the loop may do unattended vs what waits for
   human review (EDA battery → review → hypothesis batch → review →
   walk-forward survivors).
4. Roll-hour / session regime treatment (see findings).
5. Holiday calendar vs EMPTY_TRADING_HOUR warnings over multi-year ranges.
6. Research bar timeframe(s) and the horizon ladder to be ranked.
7. OANDA cross-check threshold calibration (1.0 pip flagged only the roll).

## Current status (as of 2026-08-19)

- Phase 1 complete and pushed to main; working tree clean; HANDOFF.md
  reviewed and accepted in chat.
- Harness lessons banked: Claude Code hooks run via bash — hook commands must
  use forward-slash paths; project trust is keyed to path casing (always
  launch from `E:\CODE\selpha-fxlab`); per-edit gate ~25s, timeout 700s.
- OANDA: practice token in env, working (cross-check ran green). Old live
  token: revoke (hygiene, still pending).
- IB: application submitted (UK entity, £10 funded), awaiting approval.
  Post-approval sequence: create paper account (Client Portal → Settings →
  Paper Trading) → activate IB Key 2FA → check Trading Permissions grant
  IDEALPRO cash FX vs CFDs (HANDOFF open question 7 — invalidates cost basis
  if CFDs) → top up balance before market-data subscription (tick recorder
  needs it; ~USD 500 min equity historically).
- Next concrete steps: settle Phase 2 pre-decisions in chat → draft
  BOOTSTRAP2.md + spec (ML/analytics-adapted gates) → Phase 2 session
  run book (model/effort per session, checkpointed).

## Working conventions with Claude (chat)

- Claude provides each Claude Code prompt as an exact block: model + effort +
  prompt text. Phase 1 used opus/high (harness+tests) and opus/xhigh (build
  loop); ultracode deliberately not used (the harness is the orchestration).
  Fable available on Max plan as escalation for conceptually-stuck moments.
- Direct, sceptical research partner; flags data-mining smell; correctness →
  leakage → risk → performance → style.
- Between-session git review is the user's; agents own git during loops.
- PLAN.md refreshed at each phase boundary.

## Open items

1. IB approval → paper account → IB Key → permissions check (IDEALPRO?) →
   (later) market data subscription.
2. Revoke the old live OANDA token.
3. Settle Phase 2 pre-decisions → draft Bootstrap 2.
4. Phase 2 → surviving strategy → Bootstrap 3.