"""Build known-good and known-broken variants of the reference implementation.

HARNESS MACHINERY. Used only by ``smoke_test.py --selftest``.

Each variant is produced by rewriting a single anchored line in
``refimpl/_core.py``, so a broken variant is genuinely different *source*, not a
runtime flag the gate might accidentally be aware of.

Every mutation asserts that its anchor matched exactly once. A mutation that
silently failed to apply would produce a "broken" variant that is actually
correct, the gate would pass it, and the self-test would report a discrimination
failure that does not exist -- or worse, would quietly stop testing that failure
mode. Loud is the only acceptable behaviour here.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Callable, Final

HERE: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
REFIMPL: Final[pathlib.Path] = HERE / "refimpl"
REFTESTS: Final[pathlib.Path] = HERE / "reftests"


def _sub(src: str, old: str, new: str, what: str) -> str:
    """Replace the whole line ``old`` with ``new`` exactly once, or raise.

    Anchors must match a COMPLETE line. A substring match is not good enough:
    an anchor written with too little leading whitespace will happily match the
    tail of a more deeply indented line, and the replacement then lands at the
    wrong indentation. That produced a variant that failed with a SyntaxError
    instead of the bug it was supposed to model -- the gate still went red, so
    nothing looked wrong, while the failure mode had quietly stopped being
    tested at all.
    """
    n = src.count(old)
    if n != 1:
        raise RuntimeError(
            f"mutation {what!r}: anchor matched {n} time(s), expected exactly 1. "
            f"The reference implementation changed shape; fix mutate.py rather than "
            f"letting the self-test silently stop covering this failure mode.\n"
            f"anchor: {old!r}"
        )
    if old not in src.splitlines():
        raise RuntimeError(
            f"mutation {what!r}: anchor is not a complete line -- it matches only "
            f"part of one, so the replacement would land at the wrong indentation. "
            f"Include the full leading whitespace.\nanchor: {old!r}"
        )
    return src.replace(old, new)


# --------------------------------------------------------------------------- #
# The mutations, one per feared failure mode
# --------------------------------------------------------------------------- #

def _m_timestamp_shift(src: str) -> str:
    """Failure mode 1: every tick lands one hour late (the DST/offset classic)."""
    return _sub(
        src,
        "        ts = hour_start + dt.timedelta(milliseconds=ms)  # MUTATION-ANCHOR: timestamp",
        "        ts = hour_start + dt.timedelta(hours=1) + dt.timedelta(milliseconds=ms)",
        "timestamp_shift")


def _m_drop_ticks(src: str) -> str:
    """Failure mode 2: quietly lose 5% of ticks, report success anyway."""
    return _sub(
        src,
        "            rows, dropped = dedupe(rows)",
        "            rows, dropped = dedupe(rows)\n"
        "            rows = [r for i, r in enumerate(rows) if i % 20]  # lose 5%",
        "drop_ticks")


def _m_crossed_quotes(src: str) -> str:
    """Failure mode 3: stop checking that ask >= bid."""
    return _sub(
        src,
        "        if ask < bid:  # MUTATION-ANCHOR: crossed",
        "        if False:  # crossed-quote check disabled",
        "crossed_quotes")


def _m_weekend_rows(src: str) -> str:
    """Failure mode 3 (variant): stop rejecting closed-market ticks."""
    return _sub(
        src,
        "    if hour_start.weekday() == 5 and rows:  # MUTATION-ANCHOR: closed-market",
        "    if False:  # closed-market check disabled",
        "weekend_rows")


def _m_lookahead(src: str) -> str:
    """Failure mode 4: fill on the bar that generated the signal, at its close.

    Both halves matter. Moving the fill to bar ``t`` but still reading the
    ``*_open`` prices would be a *different* wrong answer than the one the
    fixture computed as the lookahead counterfactual, and the gate would report
    an unexplained number instead of naming the bug.
    """
    src = _sub(
        src,
        '            fill_bar = bars.iloc[t + 1]  # MUTATION-ANCHOR: fill-bar',
        '            fill_bar = bars.iloc[t]  # lookahead: fills on the signal bar',
        "lookahead/bar")
    src = _sub(
        src,
        '            buy_px = float(fill_bar["ask_open"])   # MUTATION-ANCHOR: buy-price',
        '            buy_px = float(fill_bar["ask_close"])',
        "lookahead/buy")
    src = _sub(
        src,
        '            sell_px = float(fill_bar["bid_open"])  # MUTATION-ANCHOR: sell-price',
        '            sell_px = float(fill_bar["bid_close"])',
        "lookahead/sell")
    return _sub(
        src,
        '            mid_px = float(fill_bar["mid_open"])',
        '            mid_px = float(fill_bar["mid_close"])',
        "lookahead/mid")


def _m_mid_fill(src: str) -> str:
    """Failure mode 4 (variant): correct timing, but never cross the spread."""
    src = _sub(
        src,
        '            buy_px = float(fill_bar["ask_open"])   # MUTATION-ANCHOR: buy-price',
        '            buy_px = float(fill_bar["mid_open"])',
        "mid_fill/buy")
    return _sub(
        src,
        '            sell_px = float(fill_bar["bid_open"])  # MUTATION-ANCHOR: sell-price',
        '            sell_px = float(fill_bar["mid_open"])',
        "mid_fill/sell")


def _m_zero_cost(src: str) -> str:
    """Failure mode 5: the cost model returns nothing on a config mismatch."""
    return _sub(
        src,
        "    return max(rate * units * price, minimum) * multiplier  # MUTATION-ANCHOR: commission",
        "    return 0.0  # cost model silently nulled",
        "zero_cost")


def _network_call(host: str) -> Callable[[str], str]:
    """Build a mutation that makes fixture-mode ingest phone home to ``host``.

    The gate promises fixture mode is provably offline. That promise is only
    worth something if we have watched the guard actually fire, against the two
    hosts this project really talks to.
    """
    def _apply(src: str) -> str:
        return _sub(
            src,
            "    manifest: dict[str, Any] = {\"hours\": [], \"validation\": {\"ok\": True, \"errors\": []}}",
            "    import urllib.request as _u\n"
            f"    _u.urlopen('https://{host}/', timeout=5)  # illegal egress in fixture mode\n"
            "    manifest: dict[str, Any] = {\"hours\": [], \"validation\": {\"ok\": True, \"errors\": []}}",
            f"network_call/{host}")
    return _apply


#: variant name -> (mutation, the reason token the gate must name when it fails)
MUTATIONS: Final[dict[str, tuple[Callable[[str], str], str]]] = {
    "timestamp_shift": (_m_timestamp_shift, "timestamp"),
    "drop_ticks": (_m_drop_ticks, "ticks"),
    "crossed_quotes": (_m_crossed_quotes, "CROSSED_QUOTE"),
    "weekend_rows": (_m_weekend_rows, "CLOSED_MARKET_TICK"),
    "lookahead": (_m_lookahead, "LOOKAHEAD"),
    "mid_fill": (_m_mid_fill, "MID-FILL"),
    "zero_cost": (_m_zero_cost, "ZERO-COST"),
    "network_dukascopy": (_network_call("datafeed.dukascopy.com"), "network access"),
    "network_oanda": (_network_call("api-fxpractice.oanda.com"), "network access"),
}


def materialise(dest_parent: pathlib.Path, variant: str | None = None) -> pathlib.Path:
    """Write a copy of the reference implementation as an importable ``fxlab``.

    Args:
        dest_parent: Directory that will contain ``fxlab/`` and ``tests/``.
        variant: ``None`` for the known-good build, otherwise a key of
            :data:`MUTATIONS`.

    Returns:
        The path of the created ``fxlab`` package directory.
    """
    pkg = dest_parent / "fxlab"
    if pkg.exists():
        shutil.rmtree(pkg)
    shutil.copytree(REFIMPL, pkg, ignore=shutil.ignore_patterns("__pycache__"))

    tests_dst = dest_parent / "tests"
    if tests_dst.exists():
        shutil.rmtree(tests_dst)
    shutil.copytree(REFTESTS, tests_dst, ignore=shutil.ignore_patterns("__pycache__"))

    if variant is not None:
        if variant not in MUTATIONS:
            raise KeyError(f"unknown variant {variant!r}; have {sorted(MUTATIONS)}")
        core = pkg / "_core.py"
        mutated = MUTATIONS[variant][0](core.read_text(encoding="utf8"))
        # A variant that does not even parse would still turn the gate red, so
        # the self-test would look healthy while actually testing nothing. Fail
        # here, loudly, where the cause is obvious.
        try:
            compile(mutated, str(core), "exec")
        except SyntaxError as exc:
            raise RuntimeError(
                f"mutation {variant!r} produced source that does not compile "
                f"({exc}). The variant must model a BUG, not a syntax error, or "
                f"the self-test silently stops covering this failure mode."
            ) from exc
        core.write_text(mutated, encoding="utf8")
    return pkg
