"""A tiny, seeded experiment the research gate's selftest runs and breaks.

It reads no data, so the selftest does not depend on any dataset being present,
and it is deterministic given its seed, so the reproducibility check has
something honest to verify. Every way of breaking it is a switch in ``params``,
because the selftest must be able to construct a *broken* experiment without
editing the judged surface -- an agent that could break research code to make
the gate fail could equally break it to make the gate pass.

Switches, all judge-side and all deliberately dishonest:

``ignore_seed``
    Draw from fresh entropy instead of the declared seed. The result hash then
    differs between runs, which is what ``NOT_REPRODUCIBLE`` exists to catch.

``zero_costs``
    Report a scorecard whose cost lines are all zero.

``drop_rung``
    Omit one rung of the cost ladder.

``drift``
    Score the 2.0x rung on different cost-model parameters from the rest.

``wrong_verdict``
    State a survival verdict the ladder does not support.
"""

from __future__ import annotations

import os
import random
import struct
from typing import Any, Final

from research.experiment import LADDER, verdict_for

#: Default cost model for the synthetic scorecard: IB tier 1, as Phase 1.
DEFAULT_COSTS: Final[dict[str, float]] = {
    "commission_rate": 2e-05, "commission_min": 2.0}


def _scorecard(params: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Build a synthetic cost ladder from the parameters given.

    The honest form scales both cost lines linearly with the multiplier, which
    is what ``IBCostModel.cost_multiplier`` does, and derives the verdict from
    the ladder rather than asserting one.
    """
    pairs = [str(p) for p in params.get("pairs", ["EURUSD"])]
    gross = float(params.get("gross_pnl", 1000.0))
    spread = float(params.get("spread_cost", 200.0))
    commission = float(params.get("commission", 120.0))
    trades = int(params.get("trade_count", 12))
    if params.get("zero_costs"):
        spread = commission = 0.0

    costs = dict(DEFAULT_COSTS)
    ladder: dict[str, Any] = {}
    for rung in LADDER:
        multiplier = float(rung)
        rung_spread = round(spread * multiplier, 6)
        rung_commission = round(commission * multiplier, 6)
        total = round(rung_spread + rung_commission, 6)
        entry: dict[str, Any] = {
            "gross_pnl": round(gross, 6),
            "spread_cost": rung_spread,
            "commission": rung_commission,
            "total_costs": total,
            "net_pnl": round(gross - total, 6),
            "trade_count": trades,
        }
        if params.get("drift") and rung == "2.0":
            entry["cost_model"] = {"commission_rate": 4e-05,
                                   "commission_min": 5.0}
        ladder[rung] = entry

    if params.get("drop_rung"):
        ladder.pop(str(params["drop_rung"]), None)

    verdict = (str(params["wrong_verdict"]) if params.get("wrong_verdict")
               else verdict_for(ladder))
    return {
        "level": str(params.get("level", "pair")),
        "pairs": pairs,
        "accounting_currency": str(params.get("accounting_currency", "USD")),
        "cost_model": costs,
        "ladder": ladder,
        "survival": {"bar": 1.5,
                     "net_pnl": (ladder.get("1.5") or {}).get("net_pnl"),
                     "verdict": verdict},
        "trial_note": f"synthetic, seed jitter {rng.random():.6f}",
    }


def run(params: dict[str, Any], seed: int, loader: Any) -> dict[str, Any]:
    """Draw a few seeded numbers and, optionally, build a scorecard.

    Args:
        params: Switches described in the module docstring.
        seed: The declared seed. Honoured unless ``ignore_seed`` is set.
        loader: Unused; present because every entry point takes the same
            arguments and the runner records what the loader served.

    Returns:
        A payload dict; deterministic unless deliberately broken.
    """
    effective = seed
    if params.get("ignore_seed"):
        effective = struct.unpack("<I", os.urandom(4))[0]
    rng = random.Random(effective)

    draws = [round(rng.random(), 9) for _ in range(int(params.get("n", 5)))]
    payload: dict[str, Any] = {
        "draws": draws,
        "mean": round(sum(draws) / len(draws), 9),
        "loader_mode": getattr(loader, "mode", None),
    }
    if params.get("scorecard"):
        payload["scorecard"] = _scorecard(params, rng)
    return payload
