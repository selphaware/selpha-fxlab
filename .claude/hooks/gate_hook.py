"""PostToolUse hook: run the gate after every Write/Edit to the deliverable.

Standard library only, by design -- the hook must keep working even when the
deliverable's dependencies are mid-install or broken.

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

#: Only these trees are worth re-judging. Editing docs or configs elsewhere
#: should not cost a 20-second gate run.
WATCHED = ("fxlab", "tests", "config")


def _edited_path(payload: dict) -> str | None:
    """Extract the edited file path from a PostToolUse payload."""
    tool_input = payload.get("tool_input") or {}
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_watched(path_str: str) -> bool:
    """True if the edited path lies inside a tree the gate judges."""
    try:
        rel = pathlib.Path(path_str).resolve().relative_to(PROJECT)
    except (ValueError, OSError):
        return False
    return rel.parts and rel.parts[0] in WATCHED


def main() -> int:
    """Run the gate if the edit touched watched code, and translate the verdict."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed payload is not the deliverable's fault

    if payload.get("tool_name") not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return 0

    path_str = _edited_path(payload)
    if path_str is None or not _is_watched(path_str):
        return 0

    if not pathlib.Path(PYTHON).exists():
        print(f"HARNESS ERROR -- pinned interpreter missing: {PYTHON}\n"
              "This is not a deliverable problem; the gate did not run.",
              file=sys.stderr)
        return 0
    if not GATE.exists():
        print(f"HARNESS ERROR -- gate missing: {GATE}\n"
              "This is not a deliverable problem; the gate did not run.",
              file=sys.stderr)
        return 0

    try:
        proc = subprocess.run(
            [PYTHON, "-E", "-s", str(GATE), DELIVERABLE],
            capture_output=True, text=True, cwd=str(PROJECT), timeout=600)
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
        print("\nGate failed. Fix fxlab/ and try again. Do not edit verify/.",
              file=sys.stderr)
        return 2
    if proc.returncode == 2:
        print(f"HARNESS ERROR -- the judge could not run. NOT a deliverable problem; "
              f"do not change fxlab/ in response.\n{output}", file=sys.stderr)
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
