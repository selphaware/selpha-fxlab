"""PostToolUse hook: run the right gate after every Write/Edit to a judged tree.

PROPOSED REPLACEMENT for .claude/hooks/gate_hook.py. The agent cannot install
it: `Edit(./.claude/**)` is denied, which is the rule working as intended. Copy
it into place yourself:

    cp verify2/proposed/gate_hook.py .claude/hooks/gate_hook.py

Then restart the session -- Claude Code reads hooks and settings at startup, so
a live-fire test needs a fresh session. The hook can be tested without one by
piping a payload into it directly; see verify2/proposed/README.md.

Standard library only, by design -- the hook must keep working even when the
deliverable's dependencies are mid-install or broken.

Two judged surfaces, two gates
------------------------------

======================================  ==================================
edited path                             gate run
======================================  ==================================
``fxlab/``, ``tests/``, ``config/``     Phase 1: ``verify/smoke_test.py``
``research/``, ``tests2/``,             Phase 2 fast subset:
``experiments/``, ``taskcards/``        ``verify2/research_gate.py --fast``
anything else                           nothing
======================================  ==================================

The Phase 2 fast subset is the Phase 1 gate, the leakage known answers, the
seal and ledger integrity -- everything cheap, and everything a research edit
can break silently. Reproducibility runs at task end against the finished
experiment directory, because it re-executes the experiment.

Contract with Claude Code
-------------------------
* exit ``0``  -- nothing to say; the tool call stands.
* exit ``2``  -- blocking failure; whatever is on stderr is fed back to the agent.

Mapping from gate exit codes:

=========================  ==============  ====================================
gate                       hook            why
=========================  ==============  ====================================
0 pass                     0               nothing to report
1 deliverable failure      2               real feedback, block and explain
2 harness error            0 (+ warning)   the judge is broken, not the code
3 environment error        0 (+ warning)   the machine is broken, not the code
=========================  ==============  ====================================

Harness and environment problems deliberately do **not** block. Telling a build
agent "your code is wrong" when the actual problem is a missing fixture or an
unmounted network drive is how a loop burns a dozen iterations on a phantom bug.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

#: Pinned absolutely. Bare ``python`` on this machine resolves to the Microsoft
#: Store alias stub, which cannot execute.
PYTHON = r"E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe"
PROJECT = pathlib.Path(r"E:\CODE\selpha-fxlab")

GATE = PROJECT / "verify" / "smoke_test.py"
DELIVERABLE = "fxlab"

RESEARCH_GATE = PROJECT / "verify2" / "research_gate.py"

#: Phase 1's judged trees. Editing docs or configs elsewhere should not cost a
#: 20-second gate run.
WATCHED = ("fxlab", "tests", "config")

#: Phase 2's judged trees. ``reports/`` is deliberately absent: a report is
#: prose written after the gate has already judged the experiment behind it.
WATCHED_RESEARCH = ("research", "tests2", "experiments", "taskcards")


def _edited_path(payload: dict) -> str | None:
    """Extract the edited file path from a PostToolUse payload."""
    tool_input = payload.get("tool_input") or {}
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _top_level(path_str: str) -> str | None:
    """The first path component inside the project, or None if outside it."""
    try:
        rel = pathlib.Path(path_str).resolve().relative_to(PROJECT)
    except (ValueError, OSError):
        return None
    return rel.parts[0] if rel.parts else None


def _command_for(top: str | None) -> tuple[list[str], pathlib.Path, str] | None:
    """The gate command for an edited tree, or None if nothing should run."""
    if top in WATCHED:
        return ([PYTHON, "-E", "-s", str(GATE), DELIVERABLE], GATE,
                "Fix fxlab/ and try again. Do not edit verify/.")
    if top in WATCHED_RESEARCH:
        return ([PYTHON, "-E", "-s", str(RESEARCH_GATE), "--fast"],
                RESEARCH_GATE,
                "Fix research/ and try again. Do not edit verify2/ or verify/. "
                "Full reproducibility runs at task end: "
                "verify2/research_gate.py <experiment_dir>.")
    return None


def main() -> int:
    """Run the gate the edit calls for, and translate the verdict."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed payload is not the deliverable's fault

    if payload.get("tool_name") not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return 0

    path_str = _edited_path(payload)
    if path_str is None:
        return 0
    chosen = _command_for(_top_level(path_str))
    if chosen is None:
        return 0
    command, gate_path, advice = chosen

    if not pathlib.Path(PYTHON).exists():
        print(f"HARNESS ERROR -- pinned interpreter missing: {PYTHON}\n"
              "This is not a deliverable problem; the gate did not run.",
              file=sys.stderr)
        return 0
    if not gate_path.exists():
        print(f"HARNESS ERROR -- gate missing: {gate_path}\n"
              "This is not a deliverable problem; the gate did not run.",
              file=sys.stderr)
        return 0

    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              cwd=str(PROJECT), timeout=600)
    except subprocess.TimeoutExpired:
        print("HARNESS ERROR -- the gate exceeded 600s and was killed.\n"
              "This is not a deliverable problem; investigate the harness.",
              file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"HARNESS ERROR -- could not launch the gate: {exc}\n"
              "This is not a deliverable problem.", file=sys.stderr)
        return 0

    output = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode == 0:
        return 0
    if proc.returncode == 1:
        print(output, file=sys.stderr)
        print(f"\nGate failed. {advice}", file=sys.stderr)
        return 2
    if proc.returncode == 2:
        print(f"HARNESS ERROR -- the judge could not run. NOT a deliverable problem; "
              f"do not change the deliverable in response.\n{output}", file=sys.stderr)
        return 0
    if proc.returncode == 3:
        print(f"ENVIRONMENT ERROR -- the machine is not in a judgable state "
              f"(wrong interpreter, or a mapped drive is gone). NOT a deliverable "
              f"problem.\n{output}", file=sys.stderr)
        return 0

    print(f"HARNESS ERROR -- gate returned unexpected exit {proc.returncode}.\n{output}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
