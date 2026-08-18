"""Prove the gate discriminates good code from broken code.

A gate you have not watched fail is not a gate. This module builds a known-good
reference implementation and one deliberately broken variant per feared failure
mode, runs the real ``smoke_test.py`` against each as a subprocess, and asserts:

* the good build **passes** (exit 0);
* every broken build **fails as a deliverable failure** (exit 1) -- not as a
  crash, not as a harness error;
* the failure message **names the right reason**, because a gate that fails for
  the wrong reason sends the build agent chasing the wrong bug.

It also checks the two classifications that must never be blamed on the code
(harness error, environment error) and that the verdict does not depend on the
working directory.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Final

GATE_DIR: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
ROOT: Final[pathlib.Path] = GATE_DIR.parent
SMOKE: Final[pathlib.Path] = GATE_DIR / "smoke_test.py"

EXIT_PASS, EXIT_DELIVERABLE, EXIT_HARNESS, EXIT_ENV = 0, 1, 2, 3

#: variant -> substrings the gate's message must contain (all of them).
EXPECTED_REASONS: Final[dict[str, tuple[str, ...]]] = {
    "timestamp_shift": ("exactly one hour", "constant shift"),
    "drop_ticks": ("dropping or inventing ticks",),
    "crossed_quotes": ("CROSSED_QUOTE",),
    "weekend_rows": ("CLOSED_MARKET_TICK",),
    "lookahead": ("LOOKAHEAD",),
    "mid_fill": ("MID-FILL",),
    "zero_cost": ("ZERO-COST",),
    "network_dukascopy": ("network access", "datafeed.dukascopy.com"),
    "network_oanda": ("network access", "api-fxpractice.oanda.com"),
}


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def _run_gate(deliverable: pathlib.Path, cwd: pathlib.Path,
              smoke: pathlib.Path = SMOKE) -> tuple[int, str, str]:
    """Run the gate as a subprocess.

    Returns:
        ``(exit_code, verdict_message, full_output)``. The verdict message is
        just the text the gate printed after its ``GATE FAIL`` / ``ERROR``
        banner. Reason matching happens against *that* and nothing else --
        matching against the full output would let the variant's own directory
        name (``.../lookahead/fxlab``) satisfy a check for the word "LOOKAHEAD",
        which is a false pass that hides a gate that never diagnosed anything.
    """
    proc = subprocess.run(
        [sys.executable, "-E", "-s", str(smoke), str(deliverable)],
        capture_output=True, text=True, env=_clean_env(), cwd=str(cwd), timeout=900)
    full = proc.stdout + proc.stderr
    verdict = ""
    for banner in ("GATE FAIL (deliverable)", "HARNESS ERROR", "ENVIRONMENT ERROR"):
        idx = proc.stderr.find(banner)
        if idx != -1:
            verdict = proc.stderr[idx:]
            break
    return proc.returncode, verdict, full


def run_selftest() -> int:
    """Run the whole discrimination suite. Returns a process exit code."""
    sys.path.insert(0, str(GATE_DIR / "reference"))
    from mutate import MUTATIONS, materialise  # noqa: PLC0415

    failures: list[str] = []
    tmp_root = pathlib.Path(tempfile.mkdtemp(prefix="fxlab_selftest_"))
    print(f"SELFTEST  workspace: {tmp_root}\n")

    try:
        # ---------- 1. the known-good reference must PASS ----------
        good_dir = tmp_root / "good"
        good_dir.mkdir()
        good_pkg = materialise(good_dir)
        code, _verdict, out = _run_gate(good_pkg, good_dir)
        if code == EXIT_PASS:
            print("  [ OK ] good reference          -> PASS (exit 0)")
        else:
            failures.append(
                f"the known-GOOD reference did not pass: exit {code}\n{_indent(out)}")
            print(f"  [FAIL] good reference          -> exit {code} (expected 0)")

        # ---------- 2. every broken variant must FAIL, for the right reason ----------
        for variant in sorted(MUTATIONS):
            vdir = tmp_root / variant
            vdir.mkdir()
            pkg = materialise(vdir, variant)
            code, verdict, out = _run_gate(pkg, vdir)

            if code != EXIT_DELIVERABLE:
                kind = {EXIT_PASS: "PASSED (gate is blind to this bug)",
                        EXIT_HARNESS: "reported HARNESS ERROR",
                        EXIT_ENV: "reported ENVIRONMENT ERROR"}.get(code, f"exit {code}")
                failures.append(f"variant {variant!r} {kind}; expected exit 1\n{_indent(out)}")
                print(f"  [FAIL] {variant:22s} -> {kind}")
                continue

            wanted = EXPECTED_REASONS[variant]
            # Case-sensitive, and against the verdict message only.
            missing = [w for w in wanted if w not in verdict]
            if missing:
                failures.append(
                    f"variant {variant!r} failed correctly (exit 1) but the message did "
                    f"not mention {missing}; a vague message costs an iteration\n{_indent(out)}")
                print(f"  [FAIL] {variant:22s} -> failed, but message missing {missing}")
            else:
                print(f"  [ OK ] {variant:22s} -> FAIL (exit 1), named {list(wanted)}")

        # ---------- 3. harness error must not be blamed on the code ----------
        broken_harness = tmp_root / "harness_missing_fixture"
        shutil.copytree(GATE_DIR, broken_harness / "verify",
                        ignore=shutil.ignore_patterns("__pycache__", "artifacts"))
        (broken_harness / "verify" / "fixtures" / "expected.json").unlink()
        code, _verdict, out = _run_gate(good_pkg, good_dir,
                                        smoke=broken_harness / "verify" / "smoke_test.py")
        if code == EXIT_HARNESS and "HARNESS ERROR" in out:
            print("  [ OK ] missing fixture          -> HARNESS ERROR (exit 2)")
        else:
            failures.append(
                f"a missing fixture produced exit {code} instead of a HARNESS ERROR "
                f"(exit 2); the build agent would be told its code is broken\n{_indent(out)}")
            print(f"  [FAIL] missing fixture          -> exit {code} (expected 2)")

        # ---------- 4. environment error must not be blamed on the code ----------
        alt = _find_non_312_interpreter()
        if alt is None:
            print("  [SKIP] wrong interpreter        -> no non-3.12 interpreter available")
        else:
            proc = subprocess.run([str(alt), "-E", "-s", str(SMOKE), str(good_pkg)],
                                  capture_output=True, text=True, env=_clean_env(),
                                  cwd=str(good_dir), timeout=300)
            out = proc.stdout + proc.stderr
            if proc.returncode == EXIT_ENV and "ENVIRONMENT ERROR" in out:
                print(f"  [ OK ] wrong interpreter        -> ENVIRONMENT ERROR (exit 3)")
            else:
                failures.append(
                    f"running the gate on {alt} gave exit {proc.returncode}, expected an "
                    f"ENVIRONMENT ERROR (exit 3)\n{_indent(out)}")
                print(f"  [FAIL] wrong interpreter        -> exit {proc.returncode} (expected 3)")

        # ---------- 5. the verdict must not depend on the working directory ----------
        other_cwd = tmp_root / "elsewhere"
        other_cwd.mkdir()
        code_tmp, _v1, _o1 = _run_gate(good_pkg, other_cwd)
        code_proj, _v2, _o2 = _run_gate(good_pkg, ROOT)
        if code_tmp == code_proj == EXIT_PASS:
            print("  [ OK ] cwd independence         -> same verdict from temp dir and project dir")
        else:
            failures.append(
                f"the gate disagreed with itself depending on cwd: temp dir -> {code_tmp}, "
                f"project dir -> {code_proj}")
            print(f"  [FAIL] cwd independence         -> temp={code_tmp} project={code_proj}")

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print()
    if failures:
        print(f"SELFTEST FAIL -- {len(failures)} problem(s):\n", file=sys.stderr)
        for f in failures:
            print(f"  * {f}\n", file=sys.stderr)
        return 1
    print("SELFTEST PASS -- the gate discriminates every feared failure mode "
          "and classifies harness/environment problems separately")
    return 0


def _indent(text: str, n: int = 30) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join("        " + ln for ln in lines[-n:])


def _find_non_312_interpreter() -> pathlib.Path | None:
    """Locate any Python that is not 3.12, to exercise the environment-error path."""
    candidates = [
        pathlib.Path(r"C:\Users\uahma\anaconda3\python.exe"),
        pathlib.Path(r"C:\msys64\ucrt64\bin\python.exe"),
    ]
    for c in candidates:
        if not c.exists():
            continue
        try:
            out = subprocess.run([str(c), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True, timeout=60)
        except OSError:
            continue
        if out.returncode == 0 and out.stdout.strip() != "3.12":
            return c
    return None
