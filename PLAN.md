# selpha-fxlab — Master Plan

> Living document. Source of truth for the overall roadmap, settled decisions,
> and current status. Phase-level detail lives in per-phase BOOTSTRAP.md /
> spec.md files in the repo (github.com/selphaware/selpha-fxlab) — this file
> is the map, not the territory.

## Goal

Find the best possible FX trading algorithm across a multi-pair universe, and
run it live. Cross-pair structure (cointegration, currency strength, lead-lag,
relative value) is a first-class research question — the system analyses many
pairs simultaneously, not one at a time.

## Settled architecture

| Concern | Decision | Rationale (short) |
|---|---|---|
| Research data (primary) | Dukascopy public tick feed — free, no account, bi5 hourly files decoded in pure Python | Deep bid/ask tick history ~2003+, spread/session analysis needs ticks |
| Research data (cross-check) | OANDA v20 fxpractice candles, `OANDA_API_TOKEN` env var (practice token ONLY — live token explicitly banned from this repo) | FX has no consolidated tape; second source validates the first |
| Execution venue | Interactive Brokers (IDEALPRO) via IB Gateway + IBC + `ib_async`, paper first | Near-interbank spreads + commission beats retail spread-only pricing |
| Cost model | Explicit, parametric, IB-calibrated (raw spread + 0.20bp commission, $2 min/order, cost_multiplier stress knob). Never inherited from a data feed | Mids are venue-independent; costs are venue-specific; strategy viability is decided by costs |
| Cost-model validation | Own tick recorder captures live IB bid/ask from day one of connectivity | Accumulating venue-true spread distributions shrinks the proxy risk over time |
| Repo structure | ONE repo (`selpha-fxlab`), one package (`fxlab`), three sequential phases | Phases share schema, cost model, backtester; split repos = drift |
| Build methodology | ailoop engineering (selphaware/ailoop-engineering): per-phase BOOTSTRAP.md builds a gate-first harness, agent loops unattended against a binary offline gate | Autonomous but verifiable; the gate is the judge |
| Language/env | Python 3.12, venv at `E:\CODE\selpha-fxlab\env_fxlab` (inside repo, git-ignored, agent-write-denied), typed/PEP8/pytest | — |
| Git | Agent commits+pushes autonomously via SSH (ssh-agent holds ed25519 key), after each gate-green milestone | Checkpoints tied to verified states |

## The three phases

**Phase 1 — Plumbing (ACTIVE).** Ingestion (Dukascopy ticks + OANDA candles →
validate → normalize → Parquet + manifest), IB cost model, event-ordered
backtester skeleton (multi-pair, no-lookahead by construction), tick recorder
against a Feed interface (FakeFeed for tests; live IB validated post-approval).
Universe: 12 pairs (EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD EURGBP
EURJPY GBPJPY EURCHF AUDJPY), ticks 2015→present, extendable to ~2005.
Deliberately EXCLUDES: strategies, EDA beyond validation stats, ML, IB
execution. Ends with HANDOFF.md (coverage, gap stats, spread distributions,
known issues) → feeds Bootstrap 2.

**Phase 2 — Research (after Phase 1).** EDA as hypothesis generation across
the universe; universe ranking (liquidity/spread/vol regimes, autocorrelation,
trend vs mean-reversion, correlation/cointegration structure, session
effects, stability); strategy development incl. cross-pair (stat-arb,
currency-strength, lead-lag); walk-forward/purged validation; portfolio-level
evaluation. NOT fire-and-forget: checkpointed autonomy — bounded loop tasks
(EDA battery, specified walk-forward) between human review gates. Bootstrap 2
written only after HANDOFF.md exists; ailoop template needs adaptation for
ML/analytics work (binary gates verify runs, humans judge findings — user has
flagged this explicitly). Multiple-testing honesty is a core requirement.

**Phase 3 — Execution engineering (after a strategy survives Phase 2).**
Event-driven live engine, Gateway+IBC automation (incl. 2FA/restart
handling), hard risk controls (position limits, max daily loss, kill
switch), paper-trading validation, monitoring. Specced against the actual
surviving strategy's requirements — deliberately not designed earlier.
Bootstrap 3 written then.

## Non-negotiable research principles (apply to all phases)

No lookahead/leakage (enforced by gate in Phase 1, by review in Phase 2);
realistic venue-true costs everywhere, same cost model across all candidates;
proper time-series validation (walk-forward/purged, never random K-fold);
multiple-testing honesty (track what was tried; prefer parsimony); sceptical
review of stability across regimes before building on any property.

## Current status (as of 2026-08-18)

- Repo live with BOOTSTRAP.md + spec.md (Phase 1). Harness build loop RUNNING
  in Claude Code (Opus, high effort): Phase 0 approved; Dukascopy reachable
  post-VPN-fix; month-zero-indexing empirically confirmed (URL month 06 =
  July, corroborated by server Last-Modified); fixture freeze + gate build in
  progress. Next: Phase 3 final report → live-fire hook test (session
  restart) → Phase 1 build loop kickoff.
- OANDA: practice account + fxpractice token set. (A live token was briefly
  in the env var — replaced; recommend revoking the live token.)
- IB: individual margin application submitted (UK entity, £10 funded),
  awaiting approval. Paper account creatable only post-approval (Client
  Portal → Settings → Paper Trading). IB Key 2FA to be activated. Later:
  check Trading Permissions grant IDEALPRO cash FX vs CFDs (UK retail
  question) — cost model assumes IDEALPRO; top up balance before market-data
  subscription (needed for tick recorder, ~USD 500 min equity historically).
- Environment facts discovered: venv stdlib base on network-mapped A: drive
  (harness classifies missing-mapping as environment error, not code
  failure); global PYTHONPATH=i:\xstar was poisoning sys.path — user
  deleting it; pandas 3.x (str dtype default) — Parquet schema pinned to
  exact Arrow types; gate runs python -E with scrubbed env, fully offline,
  network guard self-tested.

## Working conventions with Claude (chat)

- Claude provides each Claude Code prompt as an exact block: model + effort +
  prompt text. Session 1 = harness (opus/high). Session 2 = hook live-fire.
  Session 3 = Phase 1 build loop.
- Direct, sceptical research partner; flags data-mining smell; correctness →
  leakage → risk → performance → style.
- Between-session git review is the user's; agents own git during loops.

## Open items

1. IB approval → paper account → IB Key → (later) market data subscription.
2. Revoke the old live OANDA token (hygiene).
3. Phase 1 loop completion → review HANDOFF.md here → draft Bootstrap 2
   (with ML/analytics gate adaptations).
4. Phase 2 → surviving strategy → Bootstrap 3.
