# BOOTSTRAP2 — generate the Phase 2 research harness

You are going to build the **Phase 2 harness**: the scaffolding that lets an
agent run bounded research tasks autonomously while making dishonest or broken
research mechanically impossible. You are NOT doing research yet. You are
building the thing that will judge the research.

Phase 1 is complete and its harness is frozen. Read `HANDOFF.md`, `CLAUDE.md`,
`SPEC.md` and `PLAN.md` before anything else. The Phase 1 gate
(`verify\smoke_test.py fxlab`) must still exit 0 at every point in this phase —
it is now a regression gate.

---

## What Phase 2 is

Research: EDA across a 12-pair universe, universe ranking, strategy candidates
(single-pair and cross-pair), walk-forward validation, portfolio evaluation.
The full task list, pre-registered decisions and scorecard formats live in
`spec2.md` — that file is the law of this phase; this file builds its enforcer.

**The critical difference from Phase 1:** a binary gate cannot judge whether a
finding is *true* — only whether it was produced *honestly*. So Phase 2 runs
checkpointed: the loop executes bounded task cards unattended; every decision
(which hypotheses advance, which pairs are tradeable, what a result means)
returns to the user between tasks. The gate's job is to make the outputs of
each task trustworthy enough to decide on.

## What "broken but passes" looks like — the failure modes this harness exists to catch

1. **Leakage**: a walk-forward that lets any information from a test window
   reach a decision made inside it — future bars, full-sample normalisation,
   labels computed across the boundary, unpurged overlap between train and
   test. Results look great and mean nothing.
2. **Holdout breach**: research code reading data after the sealed cutoff.
   Enforced by ABSENCE — sealed-period data is never downloaded in Phase 2
   (see spec2). The gate must still prove the loader and ingest configs refuse
   the sealed range, so it cannot be fetched "by accident".
3. **Non-reproducible results**: a reported number that a re-run does not
   reproduce exactly (unseeded randomness, order-dependent pandas ops, wall-
   clock dependence). If it can't be reproduced, it doesn't exist.
4. **Unledgered experiments**: results reported without a record of what else
   was tried. Multiple-testing honesty dies silently this way. Every
   experiment run must write a ledger entry BEFORE results exist; a result
   file without a matching ledger entry is a gate failure.
5. **Cost-model drift or bypass**: any scored result produced with costs
   zeroed, defaulted differently between candidates, or with the known
   JPY-notional commission bug (HANDOFF known issue 1) unfixed. Same cost
   model, same parameters, every candidate — asserted, not assumed.
6. **Silent scope creep in the loop**: an unattended task that drifts from its
   task card into choosing hypotheses, touching pairs it wasn't given, or
   "trying a few more variants". The ledger diff against the task card catches
   this at review; the gate asserts the ledger is complete.

## Environment

Everything established in Phase 1 stands: interpreter
`E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe` (3.12.13), `-E -s` always,
forward-slash paths in any hook command, launch dir `E:\CODE\selpha-fxlab`
exact casing. New runtime packages you may install (justify in CLAUDE.md):
`statsmodels`, `scipy`, `scikit-learn`, `matplotlib`, `duckdb`. Nothing that
phones home. No notebooks in the loop — scripts and reports only, so
everything is diffable and reproducible.

---

## Phase 0 — Verify the ground. Do not skip.

1. Run the Phase 1 gate. It must exit 0 before you build anything. If not,
   STOP and report — Phase 1 regressed, nothing else matters.
2. Run `python -m fxlab.report` on the live-week data to confirm the data path
   still works end to end.
3. Read `spec2.md` §Pre-registered decisions. Echo back the holdout cutoff
   date, the survival multiplier, and the research window as you understand
   them. If anything in spec2.md is ambiguous or contradicts HANDOFF.md, STOP
   and say so before building — the pre-registrations cannot be "interpreted"
   later.
4. Report findings and WAIT for go-ahead.

## Phase 1 (of this bootstrap) — Design the research gate FIRST

New directory `verify2/` (the Phase 1 `verify/` stays frozen and deny-edited).
Standard layout: gate at `verify2/research_gate.py`, `--selftest` flag,
artifacts to `verify2/artifacts/`.

The gate takes a completed experiment directory as its argument and judges:

1. **Regression**: Phase 1 gate exits 0. (Run it; do not reimplement it.)
2. **Seal**: every ingest/experiment config in the run names no date ≥ the
   sealed cutoff; the research data loader refuses a canary request for a
   sealed date with a named reason `HOLDOUT_SEALED`; no Parquet exists on disk
   for any sealed date.
3. **Reproducibility**: re-execute the experiment from its recorded config +
   seed; assert the result hash matches the reported one exactly.
4. **Leakage self-checks**: the walk-forward engine passes its constructed
   known-answer fixtures — a synthetic series where a leaky implementation
   (test-window peeking, unpurged overlap, full-sample normalisation) produces
   a detectably different, hand-computed result. Same known-answer discipline
   as Phase 1's backtest gate, applied to the validator itself.
5. **Ledger integrity**: every result file in the experiment dir has a ledger
   entry (config hash, code commit, seed, start/end time) written before the
   result timestamp; ledger entry count ≥ result count; the ledger's declared
   task-card ID matches the experiment's.
6. **Cost honesty**: scored results carry the full cost ladder
   (1.0/1.2/1.5/2.0×) from the same `IBCostModel` parameters; the JPY
   commission known-answer test passes (fix is a spec2 task — until it lands,
   the gate must FAIL any experiment that scores a JPY-quoted pair).

Then **prove the gate**: `--selftest` with broken variants — one per failure
mode above (a leaky walk-forward, a sealed-date read, an unseeded experiment,
a result with no ledger entry, a zero-cost scorecard, a JPY score without the
fix) — each failing with the right named reason, plus a good reference
experiment passing. A gate you have not watched fail is not a gate.

## Phase 2 (of this bootstrap) — The rest of the harness

* **Extend `CLAUDE.md`** (do not rewrite Phase 1 content): Phase 2 section
  with the research gate command, the task-card protocol below, the seal rule,
  the ledger rule, and the autonomy clause for research loops.
* **Task-card protocol**: each bounded loop task is defined by a
  `taskcards/T<N>.md` written by the user (with chat-Claude), stating scope,
  allowed pairs/dates, deliverable report path, and explicit non-goals. The
  agent may not act outside the card. Cards are committed before the loop
  starts; the ledger references the card.
* **Hook**: extend the existing PostToolUse hook so edits under `research/`
  run a fast subset (seal + ledger + Phase 1 gate) rather than full
  reproducibility (which runs at task end). Forward-slash paths. Keep the
  Phase 1 behaviour for `fxlab/` edits unchanged.
* **settings.json**: add `Edit(./research/**)`, `Edit(./experiments/**)`,
  `Edit(./reports/**)`, `Edit(./taskcards/**)` to allow; add
  `Edit(./verify2/**)` to deny. `verify/` stays denied.
* **Directory skeleton**: `research/` (analysis code, part of the judged
  surface), `experiments/` (ledger + per-experiment configs/results),
  `reports/` (human-readable task deliverables), `data/` layout extended per
  spec2.
* Update `spec2.md` ⚠ facts with anything Phase 0 established.

## Phase 3 (of this bootstrap) — Prove it

1. `--selftest` green across all named failure modes.
2. Hook proof by direct invocation for a `research/` edit and an `fxlab/`
   edit (live-fire needs a session restart — say so).
3. A trivial end-to-end reference experiment (e.g. "mean spread by session on
   the live week") runs through the full protocol: task card → ledger →
   result → report → research gate exit 0 → reproducibility re-run.
4. Report: gate commands, what changed in spec2.md, anything that contradicted
   this brief. Then STOP. Research begins in fresh sessions per task card.

## Rules

Verify, don't assert. Flag contradictions rather than working around them.
The pre-registered decisions in spec2.md are not yours to reinterpret — if a
task seems to require breaking one, stop and say so. Prefer fewer moving
parts. Never weaken the Phase 1 gate.