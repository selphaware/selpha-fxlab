"""Reference experiment: mean spread by session, on the quarantined live week.

This exists to exercise the Phase 2 protocol end to end -- task card, ledger,
result, report, research gate, reproducibility re-run -- on data that is not
research data. The Phase 1 live week is 2026 data and therefore inside the
seal; pre-reg #2 allows it for pipeline and mechanical checks and forbids it
for strategy scoring or selection, so this experiment runs in ``mechanical``
mode and can never emit a scorecard.

Nothing here is a research finding, and the numbers it produces must not be
cited as one. It answers "does the protocol work", not "how wide is the
market".

Determinism note: every reported statistic is rounded to six decimal places.
Summing 200,000 floats is order-dependent in the last bits, and a result hash
that changes on the last bit of a mean would fail the reproducibility check for
a reason that has nothing to do with honesty. Six decimals on a pip figure is
far finer than anything the data supports.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import numpy as np

from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import SESSIONS, session_labels
from research.loader import ResearchLoader

_LOG: Final[logging.Logger] = logging.getLogger("research.spread_session")

#: Percentiles reported per session, matching the Phase 1 coverage report so
#: the two can be compared by eye.
PERCENTILES: Final[tuple[float, ...]] = (50.0, 75.0, 90.0, 99.0)

#: Decimal places every reported statistic is rounded to. See the module note.
ROUNDING: Final[int] = 6


def _stats(spreads: Any) -> dict[str, Any]:
    """Summarise one spread series in pips, rounded for hash stability."""
    if spreads.size == 0:
        return {"ticks": 0, "mean": None, "percentiles": {}}
    percentiles = np.percentile(spreads, PERCENTILES)
    return {
        "ticks": int(spreads.size),
        "mean": round(float(spreads.mean()), ROUNDING),
        "percentiles": {f"p{p:g}": round(float(v), ROUNDING)
                        for p, v in zip(PERCENTILES, percentiles)},
    }


def run(params: dict[str, Any], seed: int, loader: ResearchLoader,
        costs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute mean and percentile spread by session for one pair.

    Args:
        params: ``pair`` (required) and optional ``dates`` to restrict to.
        seed: Recorded and returned. This experiment is deterministic and does
            not draw random numbers; the seed is still carried so that the
            result document says what it was run with, and so an unseeded
            variant is impossible to write by omission.
        loader: The loader built from the experiment config's declared mode.

    Returns:
        A JSON-serialisable payload: per-session tick counts, mean spread and
        percentiles in pips, plus the overall figures.
    """
    pair = str(params["pair"])
    dates = params.get("dates")
    ticks = loader.load_ticks(pair, list(dates) if dates else None)

    spec = pair_spec(pair)
    pip = spec.pip_size
    spreads = ((ticks["ask"].to_numpy(dtype="float64")
                - ticks["bid"].to_numpy(dtype="float64")) / pip)
    labels = np.asarray(session_labels(ticks["ts"]))

    by_session: dict[str, Any] = {}
    for session in SESSIONS:
        by_session[session] = _stats(spreads[labels == session])

    payload = {
        "pair": pair,
        "seed": int(seed),
        "pip_size": pip,
        "mode": loader.mode,
        "scorable": loader.access.scorable,
        "overall": _stats(spreads),
        "by_session": by_session,
        "note": ("Mechanical check on quarantined live-week data. Not a "
                 "research finding and not usable for strategy selection."),
    }
    _LOG.info("%s: %d ticks, mean spread %.3f pips across %d sessions",
              pair, payload["overall"]["ticks"],
              payload["overall"]["mean"] or 0.0, len(SESSIONS))
    return payload
