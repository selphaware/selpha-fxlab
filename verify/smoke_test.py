"""The gate for selpha-fxlab Phase 1.

Usage::

    python verify/smoke_test.py fxlab        # judge the deliverable
    python verify/smoke_test.py --selftest   # prove the gate discriminates

Exit codes
----------
``0``
    PASS. Everything below held.
``1``
    DELIVERABLE FAILURE. The code under test is wrong. The message says how.
``2``
    HARNESS ERROR. The judge itself is broken or incomplete (missing fixtures,
    missing pytest/pandas/pyarrow). *Not* a deliverable problem -- do not go
    changing ``fxlab/`` in response to one of these.
``3``
    ENVIRONMENT ERROR. The machine is not in a state where anything can be
    judged: wrong interpreter, or a mapped drive the interpreter depends on has
    gone away. Also not a deliverable problem.

The three-way split matters on this machine specifically: the pinned interpreter
lives in a venv whose ``home`` is ``a:\\envs\\py312``, a *network*-mapped drive.
If that mapping is absent the interpreter cannot start, and a two-way pass/fail
gate would report that as "your decoder is broken" and burn build iterations
chasing a bug that does not exist.

What this gate checks (and which feared failure mode each one closes)
--------------------------------------------------------------------
1. Ingest runs on the frozen bi5 fixtures, fully offline.
2. Exact tick counts per hour vs ``expected.json``          -> silent data loss
3. Exact first/last tick timestamps and prices              -> timestamp corruption
4. Every tick inside its hour; UTC; sorted; no duplicates   -> timestamp corruption
5. ``ask >= bid > 0``; no closed-market rows; pinned schema -> impossible quotes
6. Poisoned fixtures are REJECTED, by the right named reason-> impossible quotes
7. Backtest known-answer P&L, both sizings                  -> lookahead / mid-fill
8. Cost total > 0, reconciles trade-by-trade, $2 min binds  -> zeroed cost model
9. No network egress escaped during any of the above
10. pytest
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Final

# --------------------------------------------------------------------------- #
# Layout and exit codes
# --------------------------------------------------------------------------- #

GATE_DIR: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
ROOT: Final[pathlib.Path] = GATE_DIR.parent
FIX: Final[pathlib.Path] = GATE_DIR / "fixtures"
RAW: Final[pathlib.Path] = FIX / "raw"
POISON: Final[pathlib.Path] = FIX / "poison"
BT_FIX: Final[pathlib.Path] = FIX / "backtest"
ARTIFACTS: Final[pathlib.Path] = GATE_DIR / "artifacts"
RUNNER: Final[pathlib.Path] = GATE_DIR / "_run_guarded.py"
REFIMPL: Final[pathlib.Path] = GATE_DIR / "reference" / "refimpl"

EXIT_PASS: Final[int] = 0
EXIT_DELIVERABLE: Final[int] = 1
EXIT_HARNESS: Final[int] = 2
EXIT_ENV: Final[int] = 3

#: Money/price comparison tolerance. The fixture's discrimination margins are
#: >= $0.50, so this is three orders of magnitude tighter than it needs to be.
TOL: Final[float] = 1e-6

#: The Arrow schema the tick Parquet must match exactly. Pinned rather than
#: inferred: pandas 3 changed the default string dtype, and a gate that accepts
#: "whatever pandas produced" cannot notice dtype drift.
TICK_SCHEMA: Final[dict[str, str]] = {
    "pair": "large_string",
    "ts": "timestamp[us, tz=UTC]",
    "bid": "double",
    "ask": "double",
    "bid_volume": "double",
    "ask_volume": "double",
    "source": "large_string",
}


class DeliverableFailure(Exception):
    """The code under test is wrong."""


class HarnessError(Exception):
    """The gate itself cannot do its job."""


class EnvError(Exception):
    """The machine is not in a judgable state."""


# --------------------------------------------------------------------------- #
# Subprocess plumbing
# --------------------------------------------------------------------------- #

def clean_env() -> dict[str, str]:
    """Return an environment with ambient Python path settings removed.

    ``PYTHONPATH`` is set to ``i:\\xstar`` on this machine, which shadows even
    the stdlib ``test`` package. ``-E`` already makes the interpreter ignore it;
    scrubbing it too means the subprocess report cannot claim otherwise.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def run_module(module: str, deliverable_parent: pathlib.Path, args: list[str],
               tag: str, allow_network: bool = False,
               timeout: int = 180) -> tuple[int, str, str, dict[str, Any]]:
    """Run ``module`` as ``__main__`` under the network guard.

    Returns:
        ``(exit_code, stdout, stderr, report)`` where ``report`` is the JSON the
        guarded runner wrote (including any network attempts it blocked).
    """
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report_path = ARTIFACTS / f"{tag}.runner.json"
    cmd = [
        sys.executable, "-E", "-s", str(RUNNER),
        "--deliverable-parent", str(deliverable_parent),
        "--module", module,
        "--report", str(report_path),
    ]
    if allow_network:
        cmd.append("--allow-network")
    cmd += ["--", *args]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=clean_env(), cwd=str(deliverable_parent),
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DeliverableFailure(
            f"{module} did not finish within {timeout}s -- the gate must stay fast; "
            "suspect an unbounded retry loop or a live network call"
        ) from None

    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf8"))
        except json.JSONDecodeError:
            report = {}

    (ARTIFACTS / f"{tag}.stdout.txt").write_text(proc.stdout, encoding="utf8")
    (ARTIFACTS / f"{tag}.stderr.txt").write_text(proc.stderr, encoding="utf8")
    return proc.returncode, proc.stdout, proc.stderr, report


def assert_offline(tag: str, report: dict[str, Any], stderr: str) -> None:
    """Fail if the guarded run tried to reach the network."""
    attempts = report.get("network_attempts") or []
    if attempts:
        targets = ", ".join(str(a.get("target")) for a in attempts)
        raise DeliverableFailure(
            f"{tag}: fixture mode attempted network access to {targets} -- "
            "the gate must be fully offline; guard the HTTP layer in fixture mode"
        )
    if "NetworkAccessBlocked" in stderr:
        raise DeliverableFailure(
            f"{tag}: fixture mode attempted network access (blocked by the gate's "
            "socket guard); see verify/artifacts/ for the traceback"
        )


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #

def preflight(deliverable: pathlib.Path) -> None:
    """Establish that the gate and the machine can do their jobs at all."""
    if sys.version_info[:2] != (3, 12):
        raise EnvError(
            f"interpreter is Python {sys.version.split()[0]}, expected 3.12.x -- "
            f"run the gate with E:\\CODE\\selpha-fxlab\\env_fxlab\\Scripts\\python.exe"
        )

    base = pathlib.Path(sys.base_prefix)
    if not base.exists():
        raise EnvError(
            f"the interpreter's base prefix {base} is unreachable -- this venv's "
            "stdlib lives on a mapped network drive; restore the mapping and retry. "
            "This is NOT a problem with fxlab/."
        )

    missing = []
    for mod in ("pandas", "pyarrow", "pytest"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise HarnessError(
            f"gate dependencies missing from {sys.executable}: {', '.join(missing)}. "
            "Install them into that interpreter; do not create a new venv."
        )

    if not RUNNER.exists():
        raise HarnessError(f"missing gate component {RUNNER}")
    for path, what in ((FIX / "expected.json", "frozen tick ground truth"),
                       (BT_FIX / "bars_EURUSD_1h.parquet", "backtest bars fixture"),
                       (BT_FIX / "expected_backtest.json", "backtest known answers")):
        if not path.exists():
            raise HarnessError(
                f"missing {what}: {path} -- rebuild with verify/tools/. "
                "This is NOT a deliverable problem."
            )

    if not deliverable.exists():
        raise DeliverableFailure(
            f"deliverable package not found at {deliverable} -- expected an "
            f"importable package directory named '{deliverable.name}'"
        )
    if not (deliverable / "__init__.py").exists():
        raise DeliverableFailure(
            f"{deliverable} exists but has no __init__.py; it is not an importable package"
        )


# --------------------------------------------------------------------------- #
# Config generation
# --------------------------------------------------------------------------- #

def write_ingest_config(path: pathlib.Path, raw_dir: pathlib.Path,
                        out_dir: pathlib.Path, hours: list[dict[str, Any]]) -> None:
    """Write the TOML config the ingest entrypoint consumes."""
    lines = ["[ingest]", 'mode = "fixture"',
             f'raw_dir = "{raw_dir.as_posix()}"',
             f'out_dir = "{out_dir.as_posix()}"', ""]
    for h in hours:
        lines += ["[[ingest.hours]]",
                  f'pair = "{h["pair"]}"',
                  f'date = "{h["date"]}"',
                  f'hour = {h["hour"]}', ""]
    path.write_text("\n".join(lines), encoding="utf8")


def write_backtest_config(path: pathlib.Path, bars: pathlib.Path,
                          out_path: pathlib.Path, units: int,
                          fast: int, slow: int, expected: dict[str, Any]) -> None:
    """Write the TOML config the backtest entrypoint consumes."""
    path.write_text(
        "\n".join([
            "[backtest]",
            f'bars_path = "{bars.as_posix()}"',
            'pair = "EURUSD"',
            f"units = {units}",
            f"fast = {fast}",
            f"slow = {slow}",
            f'out_path = "{out_path.as_posix()}"',
            "",
            "[backtest.costs]",
            f"commission_rate = {expected['commission_rate']!r}",
            f"commission_min = {expected['commission_min']!r}",
            "cost_multiplier = 1.0",
            "",
        ]), encoding="utf8")


# --------------------------------------------------------------------------- #
# Check 1-5: ingest the clean fixtures and judge the Parquet
# --------------------------------------------------------------------------- #

def check_ingest(deliverable: pathlib.Path, expected: dict[str, Any],
                 workdir: pathlib.Path) -> None:
    """Run ingest on the frozen fixtures and judge every tick it wrote."""
    import pandas as pd
    import pyarrow.parquet as pq

    hours = [h for h in expected["hours"]]
    out_dir = workdir / "data"
    cfg = workdir / "ingest.toml"
    write_ingest_config(cfg, RAW, out_dir, hours)

    code, _out, err, report = run_module(
        "fxlab.ingest", deliverable.parent, ["--config", str(cfg)], tag="ingest")
    assert_offline("ingest", report, err)

    if code != 0:
        raise DeliverableFailure(
            f"`python -m fxlab.ingest --config {cfg.name}` exited {code} on the CLEAN "
            f"fixtures (it must succeed). stderr tail:\n{_tail(err)}"
        )

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise DeliverableFailure(
            f"ingest succeeded but wrote no manifest at {manifest_path}; the contract "
            "requires a manifest recording coverage, gaps and duplicate counts"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    by_key = {(m["pair"], m["date"], m["hour"]): m
              for m in manifest.get("hours", [])}

    total_expected = 0
    for h in hours:
        key = (h["pair"], h["date"], h["hour"])
        label = f"{h['pair']} {h['date']}T{h['hour']:02d}:00Z"
        if key not in by_key:
            raise DeliverableFailure(
                f"{label}: absent from the manifest entirely -- every requested hour "
                "must appear, including empty ones (recorded as a gap, never skipped)"
            )
        got = by_key[key]
        if h["status"] == "empty":
            if got.get("status") not in {"empty", "closed"}:
                raise DeliverableFailure(
                    f"{label} is a Saturday hour that Dukascopy serves as an empty "
                    f"body; manifest status is {got.get('status')!r}, expected 'empty'"
                )
            continue
        total_expected += h["tick_count"]
        if got.get("written_ticks") != h["tick_count"]:
            raise DeliverableFailure(
                f"{label} fixture hour: expected {h['tick_count']:,} ticks, "
                f"manifest reports {got.get('written_ticks')!r} written -- "
                "the decoder is dropping or inventing ticks"
            )

    # ---- judge the Parquet itself, independently of the manifest ----
    for h in hours:
        if h["status"] == "empty":
            continue
        label = f"{h['pair']} {h['date']}T{h['hour']:02d}:00Z"
        part = out_dir / "ticks" / f"pair={h['pair']}" / f"date={h['date']}"
        if not part.exists():
            raise DeliverableFailure(
                f"{label}: no Parquet partition at {part} -- documented layout is "
                "data/ticks/pair=<PAIR>/date=<YYYY-MM-DD>/"
            )

    for pair in sorted({h["pair"] for h in hours if h["status"] == "ok"}):
        for date_s in sorted({h["date"] for h in hours
                              if h["pair"] == pair and h["status"] == "ok"}):
            part = out_dir / "ticks" / f"pair={pair}" / f"date={date_s}"
            files = sorted(part.glob("*.parquet"))
            if not files:
                raise DeliverableFailure(f"{pair} {date_s}: partition {part} has no .parquet file")

            table = pq.read_table(part)
            _check_schema(table, f"{pair} {date_s}")
            df = table.to_pandas().sort_values("ts", kind="stable").reset_index(drop=True)

            day_hours = [h for h in hours
                         if h["pair"] == pair and h["date"] == date_s and h["status"] == "ok"]
            want = sum(h["tick_count"] for h in day_hours)
            if len(df) != want:
                raise DeliverableFailure(
                    f"{pair} {date_s}: Parquet holds {len(df):,} ticks, expected "
                    f"{want:,} (sum of frozen per-hour counts "
                    f"{[h['tick_count'] for h in day_hours]}) -- decoder is losing ticks"
                )

            _check_invariants(df, pair, date_s)

            for h in day_hours:
                _check_boundary_ticks(df, h)

    print(f"    ingest: {total_expected:,} ticks across "
          f"{len([h for h in hours if h['status'] == 'ok'])} hours, all exact")


def _check_schema(table: Any, label: str) -> None:
    """Assert the Parquet schema matches the pinned Arrow types exactly."""
    got = {f.name: str(f.type) for f in table.schema}
    for name, want in TICK_SCHEMA.items():
        if name not in got:
            raise DeliverableFailure(
                f"{label}: tick schema is missing column {name!r}; "
                f"got {sorted(got)}, contract requires {sorted(TICK_SCHEMA)}"
            )
        if got[name] != want:
            raise DeliverableFailure(
                f"{label}: column {name!r} has Arrow type {got[name]!r}, contract "
                f"pins {want!r} -- dtype drift (pandas 3 defaults changed; pin them)"
            )
    extra = sorted(set(got) - set(TICK_SCHEMA))
    if extra:
        raise DeliverableFailure(
            f"{label}: unexpected extra column(s) {extra} in the tick schema; "
            f"the documented layout is exactly {sorted(TICK_SCHEMA)}"
        )


def _check_invariants(df: Any, pair: str, date_s: str) -> None:
    """Assert the quote/timestamp invariants that must hold everywhere."""
    import pandas as pd

    label = f"{pair} {date_s}"

    if str(df["ts"].dt.tz) not in {"UTC", "utc"}:
        raise DeliverableFailure(
            f"{label}: ts column tz is {df['ts'].dt.tz!r}, must be UTC "
            "(naive or local-time timestamps silently shift every session boundary)"
        )

    bad = df[df["ask"] < df["bid"]]
    if len(bad):
        r = bad.iloc[0]
        raise DeliverableFailure(
            f"{label}: {len(bad):,} crossed quote(s) reached the output, first at "
            f"{r['ts']} bid={r['bid']} ask={r['ask']} -- ask >= bid must hold"
        )

    nonpos = df[(df["bid"] <= 0) | (df["ask"] <= 0)]
    if len(nonpos):
        r = nonpos.iloc[0]
        raise DeliverableFailure(
            f"{label}: {len(nonpos):,} non-positive price(s) reached the output, "
            f"first at {r['ts']} bid={r['bid']} ask={r['ask']}"
        )

    diffs = df["ts"].diff().dropna()
    if len(diffs) and (diffs < pd.Timedelta(0)).any():
        n = int((diffs < pd.Timedelta(0)).sum())
        raise DeliverableFailure(
            f"{label}: timestamps are not non-decreasing ({n:,} backward step(s)) -- "
            "ticks must be ordered per pair"
        )

    dupes = df.duplicated(subset=["ts", "bid", "ask", "bid_volume", "ask_volume"])
    if dupes.any():
        raise DeliverableFailure(
            f"{label}: {int(dupes.sum()):,} exact duplicate tick row(s) survived into "
            "the output; duplicates must be dropped and counted in the manifest"
        )

    weekday = dt.date.fromisoformat(date_s).weekday()
    if weekday == 5:
        raise DeliverableFailure(
            f"{label}: {len(df):,} rows written for a Saturday; closed-market ticks "
            "must never reach the store"
        )


def _offset_hint(df: Any, h: dict[str, Any]) -> str:
    """Measure how far the decoded ticks actually moved, and name the shift.

    A time shift leaves prices and volumes untouched, so the frozen first tick
    can be located by its *values* and the constant offset read off directly.
    That turns "the counts are wrong" into "everything is exactly one hour late",
    which is the difference between one build iteration and several.
    """
    import pandas as pd

    exp = h["first_tick"]
    # Tolerance, not equality: the frozen prices are rounded to 10dp while the
    # deliverable's are raw int*scale, so they can differ by an ULP. An exact
    # match here would silently find nothing and drop the diagnosis.
    match = df[((df["bid"] - exp["bid"]).abs() < 1e-9)
               & ((df["ask"] - exp["ask"]).abs() < 1e-9)
               & ((df["bid_volume"] - exp["bid_volume"]).abs() < 1e-6)
               & ((df["ask_volume"] - exp["ask_volume"]).abs() < 1e-6)]
    if match.empty:
        return ""
    delta = (match.iloc[0]["ts"] - pd.Timestamp(exp["ts"])).total_seconds()
    if delta == 0:
        return ""
    named = ""
    if abs(abs(delta) - 3600) < 1e-6:
        named = (" That is exactly one hour: the bi5 hour offset is wrong, or a "
                 "local/DST conversion is being applied where UTC is required.")
    return (f" The frozen first tick of this hour (bid={exp['bid']}, ask={exp['ask']}) "
            f"was found at {match.iloc[0]['ts'].isoformat()} instead of {exp['ts']}, "
            f"a constant shift of {delta:+.3f}s.{named}")


def _check_boundary_ticks(df: Any, h: dict[str, Any]) -> None:
    """Assert the exact first/last tick of one frozen hour.

    This is the check a one-hour DST/offset bug cannot survive: the values were
    captured from the real bytes at freeze time.
    """
    import pandas as pd

    label = f"{h['pair']} {h['date']}T{h['hour']:02d}:00Z"
    start = pd.Timestamp(f"{h['date']}T{h['hour']:02d}:00:00", tz="UTC")
    end = start + pd.Timedelta(hours=1)
    win = df[(df["ts"] >= start) & (df["ts"] < end)]

    if len(win) != h["tick_count"]:
        raise DeliverableFailure(
            f"{label}: {len(win):,} ticks fall inside the hour "
            f"[{start.isoformat()}, {end.isoformat()}), expected {h['tick_count']:,}. "
            "A tick count that is right in total but wrong per hour means the hour "
            "offset is being applied incorrectly (classic +/-1h or DST bug)."
            + _offset_hint(df, h)
        )

    for which, exp in (("first", h["first_tick"]), ("last", h["last_tick"])):
        row = win.iloc[0] if which == "first" else win.iloc[-1]
        want_ts = pd.Timestamp(exp["ts"])
        if row["ts"] != want_ts:
            delta = (row["ts"] - want_ts).total_seconds()
            hint = ""
            if abs(abs(delta) - 3600) < 1e-6:
                hint = (" -- that is exactly one hour: the bi5 hour offset or a "
                        "local/DST conversion is wrong")
            raise DeliverableFailure(
                f"{label}: {which} tick timestamp is {row['ts'].isoformat()}, "
                f"expected {want_ts.isoformat()} (off by {delta:+.3f}s){hint}"
            )
        for field in ("bid", "ask", "bid_volume", "ask_volume"):
            got, want = float(row[field]), float(exp[field])
            if abs(got - want) > 1e-9:
                raise DeliverableFailure(
                    f"{label}: {which} tick {field} is {got!r}, expected {want!r} -- "
                    "wrong price scaling or wrong field order in the bi5 record "
                    "(layout is >IIIff with ASK before BID)"
                )


# --------------------------------------------------------------------------- #
# Check 6: the validator must reject poison
# --------------------------------------------------------------------------- #

def check_poison(deliverable: pathlib.Path, expected: dict[str, Any],
                 workdir: pathlib.Path) -> None:
    """Prove the validator rejects deliberately corrupted input, by named reason.

    The four poison runs are independent, so they are launched concurrently.
    Each subprocess pays ~3s importing pandas/pyarrow from the network-mapped
    stdlib on this machine; serialising them alone would push the gate past its
    30s budget without testing anything extra.
    """
    jobs: dict[str, tuple[pathlib.Path, Any]] = {}
    for name, spec in sorted(expected["poison"].items()):
        run_dir = workdir / f"poison_{name}"
        raw_dir = run_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        # Present the poison blob under the filename the config will ask for.
        target = raw_dir / f"{spec['pair']}_{spec['date']}_{spec['hour']:02d}h.bi5"
        shutil.copyfile(POISON / spec["file"], target)

        cfg = run_dir / "ingest.toml"
        write_ingest_config(cfg, raw_dir, run_dir / "data",
                            [{"pair": spec["pair"], "date": spec["date"],
                              "hour": spec["hour"]}])
        jobs[name] = (run_dir, cfg)

    with futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        launched = {
            name: pool.submit(run_module, "fxlab.ingest", deliverable.parent,
                              ["--config", str(cfg)], f"poison_{name}")
            for name, (_run_dir, cfg) in jobs.items()
        }
        results = {name: fut.result() for name, fut in launched.items()}

    for name, spec in sorted(expected["poison"].items()):
        run_dir, _cfg = jobs[name]
        code, _out, err, report = results[name]
        assert_offline(f"poison/{name}", report, err)

        if spec["expect_exit_nonzero"]:
            if code == 0:
                raise DeliverableFailure(
                    f"poison fixture '{name}' ({spec['note']}) was ACCEPTED: ingest "
                    f"exited 0. The validator must reject it with reason "
                    f"{spec['expect_reason']}."
                )
            reason = spec["expect_reason"]
            manifest_text = ""
            mf = run_dir / "data" / "manifest.json"
            if mf.exists():
                manifest_text = mf.read_text(encoding="utf8")
            if reason not in err and reason not in _out and reason not in manifest_text:
                raise DeliverableFailure(
                    f"poison fixture '{name}' was rejected (exit {code}) but never named "
                    f"the reason {reason!r} on stderr/stdout or in the manifest. "
                    f"A validator that fails for an unnamed reason cannot be trusted to "
                    f"have failed for the RIGHT reason. stderr tail:\n{_tail(err)}"
                )
        else:
            if code != 0:
                raise DeliverableFailure(
                    f"poison fixture '{name}' ({spec['note']}) must be ACCEPTED with "
                    f"duplicates dropped and counted, but ingest exited {code}. "
                    f"stderr tail:\n{_tail(err)}"
                )
            mf = run_dir / "data" / "manifest.json"
            if not mf.exists():
                raise DeliverableFailure(
                    f"poison fixture '{name}': ingest succeeded but wrote no manifest")
            manifest = json.loads(mf.read_text(encoding="utf8"))
            entry = next((m for m in manifest.get("hours", [])
                          if m["pair"] == spec["pair"] and m["date"] == spec["date"]
                          and m["hour"] == spec["hour"]), None)
            if entry is None:
                raise DeliverableFailure(
                    f"poison fixture '{name}': hour missing from the manifest")
            want_dupes = spec["expect_duplicates_dropped"]
            if entry.get("duplicates_dropped") != want_dupes:
                raise DeliverableFailure(
                    f"poison fixture '{name}': manifest reports duplicates_dropped="
                    f"{entry.get('duplicates_dropped')!r}, expected {want_dupes}. "
                    "Duplicates must be counted and reported, never silently swallowed."
                )
            want_written = spec["expect_written_ticks"]
            if entry.get("written_ticks") != want_written:
                raise DeliverableFailure(
                    f"poison fixture '{name}': manifest reports written_ticks="
                    f"{entry.get('written_ticks')!r}, expected {want_written}"
                )
    print(f"    poison: {len(expected['poison'])} corrupted fixtures handled correctly")


# --------------------------------------------------------------------------- #
# Check 7-8: the backtest known answers
# --------------------------------------------------------------------------- #

def check_backtest(deliverable: pathlib.Path, expected_bt: dict[str, Any],
                   workdir: pathlib.Path) -> None:
    """Run the reference strategy and assert the hand-computed P&L and costs.

    Both sizings are launched concurrently for the same reason as the poison
    runs: the work is independent and dominated by interpreter startup.
    """
    jobs: dict[str, pathlib.Path] = {}
    for scenario, spec in sorted(expected_bt["scenarios"].items()):
        run_dir = workdir / f"backtest_{scenario}"
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "results.json"
        cfg = run_dir / "backtest.toml"
        write_backtest_config(cfg, BT_FIX / "bars_EURUSD_1h.parquet", out_path,
                              spec["units"], expected_bt["fast"], expected_bt["slow"],
                              expected_bt)
        jobs[scenario] = out_path

    with futures.ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        launched = {
            scenario: pool.submit(
                run_module, "fxlab.backtest", deliverable.parent,
                ["--config", str(workdir / f"backtest_{scenario}" / "backtest.toml")],
                f"backtest_{scenario}")
            for scenario in jobs
        }
        results = {s: f.result() for s, f in launched.items()}

    for scenario, spec in sorted(expected_bt["scenarios"].items()):
        out_path = jobs[scenario]
        code, _out, err, report = results[scenario]
        assert_offline(f"backtest/{scenario}", report, err)

        if code != 0:
            raise DeliverableFailure(
                f"backtest scenario '{scenario}' exited {code}. stderr tail:\n{_tail(err)}")
        if not out_path.exists():
            raise DeliverableFailure(
                f"backtest scenario '{scenario}' wrote no results at {out_path}")

        res = json.loads(out_path.read_text(encoding="utf8"))
        want = spec["correct"]
        summary = res.get("summary")
        if not isinstance(summary, dict):
            raise DeliverableFailure(
                f"backtest '{scenario}': results file has no 'summary' object")
        trades = res.get("trades")
        if not isinstance(trades, list):
            raise DeliverableFailure(
                f"backtest '{scenario}': results file has no 'trades' list")

        if len(trades) != want["trade_count"]:
            raise DeliverableFailure(
                f"backtest '{scenario}': {len(trades)} trade(s), expected "
                f"{want['trade_count']} -- the reference strategy makes exactly one "
                "round trip on this fixture"
            )

        # --- net P&L: the single number that separates all four failure modes ---
        got_net = _num(summary, "net_pnl", scenario)
        if abs(got_net - want["net_pnl"]) > TOL:
            hint = _diagnose_pnl(got_net, spec)
            raise DeliverableFailure(
                f"backtest '{scenario}' ({spec['units']:,} units): net_pnl is "
                f"{got_net:.6f}, expected {want['net_pnl']:.6f}{hint}"
            )

        for field in ("gross_pnl", "spread_cost", "commission", "total_costs"):
            got = _num(summary, field, scenario)
            if abs(got - want[field]) > TOL:
                raise DeliverableFailure(
                    f"backtest '{scenario}': {field} is {got:.6f}, expected "
                    f"{want[field]:.6f}"
                )

        # --- failure mode 5: costs must be real and must reconcile ---
        total_costs = _num(summary, "total_costs", scenario)
        if total_costs <= 0:
            raise DeliverableFailure(
                f"backtest '{scenario}': total_costs is {total_costs} -- the cost model "
                "returned nothing. Commission and spread must both be charged."
            )
        gross = _num(summary, "gross_pnl", scenario)
        if abs((gross - total_costs) - got_net) > 1e-9:
            raise DeliverableFailure(
                f"backtest '{scenario}': gross_pnl - total_costs != net_pnl "
                f"({gross:.6f} - {total_costs:.6f} = {gross - total_costs:.6f}, but "
                f"net_pnl = {got_net:.6f}). Costs must reconcile exactly."
            )

        recomputed = 0.0
        for i, tr in enumerate(trades):
            for field in ("spread_cost", "commission"):
                if field not in tr:
                    raise DeliverableFailure(
                        f"backtest '{scenario}': trade {i} has no {field!r}; costs must "
                        "be attributable trade by trade, not only in the summary"
                    )
            recomputed += float(tr["spread_cost"]) + float(tr["commission"])
        if abs(recomputed - total_costs) > TOL:
            raise DeliverableFailure(
                f"backtest '{scenario}': summary total_costs is {total_costs:.6f} but "
                f"the trade list sums to {recomputed:.6f} -- the summary does not "
                "reconcile with the trades it claims to summarise"
            )

        # --- the $2.00 minimum must bind exactly where the fixture says it does ---
        want_legs = spec["commission_legs"]
        got_comm = float(trades[0]["commission"])
        if abs(got_comm - sum(want_legs)) > TOL:
            raise DeliverableFailure(
                f"backtest '{scenario}': trade commission is {got_comm:.6f}, expected "
                f"{sum(want_legs):.6f} (legs {want_legs}). "
                + ("This sizing must hit the $2.00-per-order MINIMUM on both legs."
                   if all(spec["commission_at_minimum"])
                   else "This sizing must be priced by the 0.20bp RATE, not the minimum.")
            )

        # --- equity curve must agree with the trade list ---
        equity = res.get("equity")
        if not isinstance(equity, list) or len(equity) < 2:
            raise DeliverableFailure(
                f"backtest '{scenario}': results need an 'equity' curve with at least "
                "two points"
            )
        moved = float(equity[-1]["equity"]) - float(equity[0]["equity"])
        if abs(moved - got_net) > TOL:
            raise DeliverableFailure(
                f"backtest '{scenario}': equity curve moves {moved:.6f} end-to-end but "
                f"net_pnl is {got_net:.6f} -- the curve and the trades disagree"
            )

        print(f"    backtest[{scenario:5s}]: net={got_net:>12.2f}  costs={total_costs:>9.2f}  "
              f"reconciled")


def _num(d: dict[str, Any], key: str, scenario: str) -> float:
    """Fetch a numeric summary field or fail with a specific message."""
    if key not in d:
        raise DeliverableFailure(
            f"backtest '{scenario}': summary is missing required field {key!r}; "
            f"got {sorted(d)}"
        )
    try:
        return float(d[key])
    except (TypeError, ValueError):
        raise DeliverableFailure(
            f"backtest '{scenario}': summary field {key!r} is {d[key]!r}, not a number"
        ) from None


def _diagnose_pnl(got: float, spec: dict[str, Any]) -> str:
    """Name the likely execution bug when net P&L misses the known answer."""
    for label, key, why in (
        ("LOOKAHEAD", "counterfactual_lookahead_net_pnl",
         "orders are being filled at the price of the bar that generated the signal; "
         "a signal on bar t may not fill before bar t+1"),
        ("MID-FILL", "counterfactual_mid_fill_net_pnl",
         "orders are being filled at the mid instead of crossing the spread "
         "(buy at ask, sell at bid)"),
        ("ZERO-COST", "counterfactual_zero_cost_net_pnl",
         "the cost model is contributing nothing; commission and spread are not "
         "being charged"),
        ("ZERO-COST (commission)", "counterfactual_zero_commission_net_pnl",
         "the spread is being crossed correctly but the commission component of "
         "the cost model returns 0.0 -- check the config keys reach IBCostModel"),
        ("ZERO-COST (spread)", "counterfactual_zero_spread_net_pnl",
         "commission is charged but the spread is not; fills are not crossing "
         "the quoted spread"),
    ):
        if abs(got - spec[key]) <= TOL:
            return f" -- this is exactly the {label} answer: {why}"
    return ""


# --------------------------------------------------------------------------- #
# Check 10: pytest
# --------------------------------------------------------------------------- #

def check_pytest(deliverable: pathlib.Path) -> None:
    """Run the deliverable's own unit tests as the final step."""
    tests = deliverable.parent / "tests"
    if not tests.exists():
        raise DeliverableFailure(
            f"no tests/ directory next to {deliverable.name}; the contract requires "
            "pytest unit tests under tests/"
        )
    proc = subprocess.run(
        [sys.executable, "-E", "-s", "-m", "pytest", str(tests), "-q",
         "-p", "no:cacheprovider"],
        capture_output=True, text=True, env=clean_env(),
        cwd=str(deliverable.parent), timeout=300)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "pytest.txt").write_text(proc.stdout + proc.stderr, encoding="utf8")
    if proc.returncode == 5:
        raise DeliverableFailure(
            "pytest collected 0 tests; the contract requires real unit tests under tests/")
    if proc.returncode != 0:
        raise DeliverableFailure(
            f"pytest failed (exit {proc.returncode}):\n{_tail(proc.stdout + proc.stderr, 40)}")
    last = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    print(f"    pytest: {last[-1] if last else 'passed'}")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def _tail(text: str, n: int = 20) -> str:
    """Return the last ``n`` non-empty lines, indented for readability."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join("      " + ln for ln in lines[-n:]) or "      (no output)"


def run_gate(deliverable: pathlib.Path, workdir: pathlib.Path) -> None:
    """Run every check in order. Raises on the first failure."""
    expected = json.loads((FIX / "expected.json").read_text(encoding="utf8"))
    expected_bt = json.loads((BT_FIX / "expected_backtest.json").read_text(encoding="utf8"))

    print("  [1/4] ingest + Parquet judgement")
    check_ingest(deliverable, expected, workdir)
    print("  [2/4] validator rejects poison")
    check_poison(deliverable, expected, workdir)
    print("  [3/4] backtest known answers")
    check_backtest(deliverable, expected_bt, workdir)
    print("  [4/4] pytest")
    check_pytest(deliverable)


def main(argv: list[str] | None = None) -> int:
    """Entry point. See module docstring for exit-code meanings."""
    ap = argparse.ArgumentParser(description="selpha-fxlab Phase 1 gate")
    ap.add_argument("deliverable", nargs="?", default="fxlab",
                    help="path to the deliverable package (default: fxlab)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the gate discriminates good from broken")
    ap.add_argument("--keep-workdir", action="store_true")
    ns = ap.parse_args(argv)

    if ns.selftest:
        from _selftest import run_selftest  # noqa: PLC0415 - selftest-only import
        return run_selftest()

    deliverable = pathlib.Path(ns.deliverable)
    if not deliverable.is_absolute():
        deliverable = (ROOT / ns.deliverable).resolve()

    print(f"GATE  deliverable : {deliverable}")
    print(f"      interpreter : {sys.executable}")
    print(f"      fixtures    : {FIX}")

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="fxlab_gate_"))
    try:
        preflight(deliverable)
        run_gate(deliverable, workdir)
    except DeliverableFailure as exc:
        print(f"\nGATE FAIL (deliverable)\n  {exc}", file=sys.stderr)
        return EXIT_DELIVERABLE
    except HarnessError as exc:
        print(f"\nHARNESS ERROR -- this is not a deliverable problem\n  {exc}",
              file=sys.stderr)
        return EXIT_HARNESS
    except EnvError as exc:
        print(f"\nENVIRONMENT ERROR -- this is not a deliverable problem\n  {exc}",
              file=sys.stderr)
        return EXIT_ENV
    finally:
        if ns.keep_workdir:
            print(f"      workdir kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    print("\nGATE PASS")
    return EXIT_PASS


if __name__ == "__main__":
    sys.path.insert(0, str(GATE_DIR))
    sys.exit(main())
