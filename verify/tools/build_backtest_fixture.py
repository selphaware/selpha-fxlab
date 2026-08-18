"""Harness-time builder for the known-answer backtest fixture.

This script is part of the HARNESS, not the deliverable. It runs offline and is
re-runnable; it writes ``verify/fixtures/backtest/``.

Design intent
-------------
The bars below are hand-constructed so that three different execution policies
produce three *materially different* P&L numbers:

* **correct**   -- a signal computed on bar ``t`` is acted on at bar ``t+1``'s
  OPEN, buying at the ask and selling at the bid.
* **lookahead** -- the order is filled using bar ``t``'s own close (the bar that
  generated the signal). This is the classic backtest lie.
* **mid-fill**  -- correct timing, but filled at the mid instead of crossing the
  spread.

Two deliberate price gaps (bar 5 open and bar 9 open) separate "next bar open"
from "signal bar close", which is what makes lookahead visible at all. A
constant 2.0 pip spread makes mid-fill visible.

Cost/P&L accounting convention pinned by this fixture
-----------------------------------------------------
``gross_pnl`` is measured **mid-to-mid**, so that the spread paid is an explicit
cost line rather than being silently buried in the fill price::

    gross_pnl   = (mid_exit - mid_entry) * units * direction
    spread_cost = (ask_entry - mid_entry) * units + (mid_exit - bid_exit) * units
    commission  = sum over both legs of max(rate * notional, minimum)
    total_costs = spread_cost + commission
    net_pnl     = gross_pnl - total_costs

The gate asserts this identity exactly; a backtester that buries the spread in
the fill price and reports ``gross == net`` fails it.

The script asserts its own discrimination margins before writing, so a future
edit that accidentally collapses two policies onto the same answer fails loudly
here rather than silently weakening the gate.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Final

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# --------------------------------------------------------------------------- #
# Fixture parameters
# --------------------------------------------------------------------------- #

PAIR: Final[str] = "EURUSD"
BAR_SECONDS: Final[int] = 3600
HALF_SPREAD: Final[float] = 0.0001  # -> constant 2.0 pip spread
START: Final[dt.datetime] = dt.datetime(2026, 7, 14, 0, 0, tzinfo=dt.timezone.utc)

#: Mid CLOSE of each bar. Shaped to drive one clean SMA(2)/SMA(4) round trip.
MID_CLOSE: Final[list[float]] = [
    1.1000, 1.1000, 1.1000, 1.1000, 1.1100,
    1.1200, 1.1300, 1.1200, 1.1000, 1.0900,
]

#: Gap applied to a bar's mid OPEN relative to the previous bar's mid CLOSE.
#: Non-zero only on the two execution bars -- that separation is the whole point.
OPEN_GAP: Final[dict[int, float]] = {5: +0.0020, 9: -0.0020}

FAST: Final[int] = 2
SLOW: Final[int] = 4

#: Two sizings: the large one is priced by the commission RATE, the small one is
#: priced by the $2.00-per-order MINIMUM. The gate runs both.
#:
#: 1,000,000 units puts the rate-based commission (~$22/leg) an order of
#: magnitude clear of the minimum; at 100,000 units it would land at ~$2.22,
#: only 10% above the floor, which is too thin a margin to prove the rate branch
#: is really being taken.
SIZINGS: Final[dict[str, int]] = {"large": 1_000_000, "small": 5_000}

COMMISSION_RATE: Final[float] = 0.20 / 10_000.0  # 0.20 basis points of notional
COMMISSION_MIN: Final[float] = 2.00              # USD per order

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "verify" / "fixtures" / "backtest"


# --------------------------------------------------------------------------- #
# Bar construction
# --------------------------------------------------------------------------- #

def build_bars() -> pd.DataFrame:
    """Construct the synthetic 1h bar series.

    Returns:
        A frame with one row per bar, timestamped at the bar's OPEN time and
        covering ``[open, open + 1h)``.
    """
    mid_open: list[float] = []
    for i, _ in enumerate(MID_CLOSE):
        if i == 0:
            mid_open.append(MID_CLOSE[0])
        else:
            mid_open.append(MID_CLOSE[i - 1] + OPEN_GAP.get(i, 0.0))

    rows = []
    for i, (mo, mc) in enumerate(zip(mid_open, MID_CLOSE)):
        mh, ml = max(mo, mc), min(mo, mc)
        rows.append(
            {
                "pair": PAIR,
                "ts": START + dt.timedelta(seconds=BAR_SECONDS * i),
                "bid_open": round(mo - HALF_SPREAD, 5),
                "bid_high": round(mh - HALF_SPREAD, 5),
                "bid_low": round(ml - HALF_SPREAD, 5),
                "bid_close": round(mc - HALF_SPREAD, 5),
                "ask_open": round(mo + HALF_SPREAD, 5),
                "ask_high": round(mh + HALF_SPREAD, 5),
                "ask_low": round(ml + HALF_SPREAD, 5),
                "ask_close": round(mc + HALF_SPREAD, 5),
                "mid_open": round(mo, 5),
                "mid_high": round(mh, 5),
                "mid_low": round(ml, 5),
                "mid_close": round(mc, 5),
                "tick_count": 100 + i,
                "spread_mean": round(2 * HALF_SPREAD, 5),
                "spread_max": round(2 * HALF_SPREAD, 5),
            }
        )
    return pd.DataFrame(rows)


def sma(values: list[float], window: int, end: int) -> float | None:
    """Simple moving average of ``values`` over ``window`` bars ending at ``end``.

    Returns:
        The mean, or ``None`` when there is not yet enough history.
    """
    if end + 1 < window:
        return None
    return sum(values[end + 1 - window: end + 1]) / window


def signals(bars: pd.DataFrame) -> list[int]:
    """Target position (1 = long, 0 = flat) implied by each bar's CLOSE.

    The signal on bar ``t`` may not be acted on before bar ``t+1``.
    """
    closes = bars["mid_close"].tolist()
    out: list[int] = []
    for t in range(len(closes)):
        f, s = sma(closes, FAST, t), sma(closes, SLOW, t)
        out.append(1 if (f is not None and s is not None and f > s) else 0)
    return out


# --------------------------------------------------------------------------- #
# Known-answer computation (independent of any fxlab code)
# --------------------------------------------------------------------------- #

def commission(units: int, price: float) -> float:
    """IB-style commission for one order: 0.20 bp of notional, min $2.00."""
    return max(COMMISSION_RATE * units * price, COMMISSION_MIN)


def simulate(bars: pd.DataFrame, sig: list[int], units: int, policy: str) -> dict:
    """Compute the P&L a given execution ``policy`` produces.

    Args:
        bars: The bar frame.
        sig: Target position implied by each bar's close.
        units: Position size in base-currency units.
        policy: One of ``correct``, ``lookahead``, ``mid_fill``.

    Returns:
        A dict with the trade list and the reconciled P&L breakdown.
    """
    trades: list[dict] = []
    pos = 0
    entry: dict | None = None

    for t in range(len(bars) - 1):
        target = sig[t]
        if target == pos:
            continue
        # Where the fill happens.
        if policy == "lookahead":
            row, when = bars.iloc[t], bars.iloc[t]["ts"]      # same bar: the lie
        else:
            row, when = bars.iloc[t + 1], bars.iloc[t + 1]["ts"]

        if policy == "mid_fill":
            buy_px = sell_px = float(row["mid_open"])
        elif policy == "lookahead":
            buy_px, sell_px = float(row["ask_close"]), float(row["bid_close"])
        else:
            buy_px, sell_px = float(row["ask_open"]), float(row["bid_open"])

        mid_px = float(row["mid_close"] if policy == "lookahead" else row["mid_open"])

        if target == 1:
            entry = {"ts": when, "fill": buy_px, "mid": mid_px}
            pos = 1
        else:
            assert entry is not None
            trades.append(
                {
                    "entry_ts": entry["ts"].isoformat(),
                    "exit_ts": when.isoformat(),
                    "entry_fill": entry["fill"],
                    "exit_fill": sell_px,
                    "entry_mid": entry["mid"],
                    "exit_mid": mid_px,
                    "units": units,
                }
            )
            pos = 0
            entry = None

    gross = spread_cost = comm = 0.0
    for tr in trades:
        gross += (tr["exit_mid"] - tr["entry_mid"]) * tr["units"]
        spread_cost += ((tr["entry_fill"] - tr["entry_mid"]) * tr["units"]
                        + (tr["exit_mid"] - tr["exit_fill"]) * tr["units"])
        c_in = commission(tr["units"], tr["entry_fill"])
        c_out = commission(tr["units"], tr["exit_fill"])
        tr["commission"] = round(c_in + c_out, 10)
        tr["spread_cost"] = round(
            (tr["entry_fill"] - tr["entry_mid"]) * tr["units"]
            + (tr["exit_mid"] - tr["exit_fill"]) * tr["units"], 10)
        comm += c_in + c_out

    total_costs = spread_cost + comm
    return {
        "policy": policy,
        "units": units,
        "trades": trades,
        "trade_count": len(trades),
        "gross_pnl": round(gross, 10),
        "spread_cost": round(spread_cost, 10),
        "commission": round(comm, 10),
        "total_costs": round(total_costs, 10),
        "net_pnl": round(gross - total_costs, 10),
    }


def main() -> None:
    """Build the fixture, assert it discriminates, and write it out."""
    bars = build_bars()
    sig = signals(bars)

    print(f"{'bar':>3} {'ts':25s} {'mid_open':>9} {'mid_close':>9} "
          f"{'sma2':>8} {'sma4':>8} {'sig':>3}")
    closes = bars["mid_close"].tolist()
    for t in range(len(bars)):
        f, s = sma(closes, FAST, t), sma(closes, SLOW, t)
        print(f"{t:>3} {bars.iloc[t]['ts'].isoformat():25s} "
              f"{bars.iloc[t]['mid_open']:>9.5f} {bars.iloc[t]['mid_close']:>9.5f} "
              f"{(f if f is not None else float('nan')):>8.5f} "
              f"{(s if s is not None else float('nan')):>8.5f} {sig[t]:>3}")

    expected: dict = {"pair": PAIR, "bar_seconds": BAR_SECONDS,
                      "fast": FAST, "slow": SLOW,
                      "commission_rate": COMMISSION_RATE,
                      "commission_min": COMMISSION_MIN,
                      "scenarios": {}}

    for name, units in SIZINGS.items():
        res = {p: simulate(bars, sig, units, p)
               for p in ("correct", "lookahead", "mid_fill")}
        zero_cost_net = res["correct"]["gross_pnl"]  # what a nulled cost model reports

        print(f"\n--- sizing '{name}' ({units:,} units) ---")
        for p, r in res.items():
            print(f"  {p:10s} trades={r['trade_count']} gross={r['gross_pnl']:>10.2f} "
                  f"spread={r['spread_cost']:>7.2f} comm={r['commission']:>7.2f} "
                  f"net={r['net_pnl']:>10.2f}")
        print(f"  {'zero_cost':10s} (broken) net would be {zero_cost_net:>10.2f}")

        # --- discrimination assertions: the gate is worthless without these ---
        c, la, mf = res["correct"], res["lookahead"], res["mid_fill"]
        assert c["trade_count"] == 1, f"expected exactly 1 round trip, got {c['trade_count']}"
        assert abs(c["net_pnl"] - la["net_pnl"]) > 1.0, "lookahead is indistinguishable"
        assert abs(c["net_pnl"] - mf["net_pnl"]) > 0.5, "mid-fill is indistinguishable"
        assert abs(c["net_pnl"] - zero_cost_net) > 0.5, "zero-cost is indistinguishable"
        assert c["total_costs"] > 0, "correct policy must have positive costs"
        assert abs((c["gross_pnl"] - c["total_costs"]) - c["net_pnl"]) < 1e-9, \
            "cost reconciliation identity does not hold"

        # Which commission branch binds?
        legs = [commission(units, c["trades"][0]["entry_fill"]),
                commission(units, c["trades"][0]["exit_fill"])]
        at_min = [abs(x - COMMISSION_MIN) < 1e-12 for x in legs]
        print(f"  commission legs={[round(x, 5) for x in legs]} at_minimum={at_min}")

        expected["scenarios"][name] = {
            "units": units,
            "correct": c,
            "counterfactual_lookahead_net_pnl": la["net_pnl"],
            "counterfactual_mid_fill_net_pnl": mf["net_pnl"],
            "counterfactual_zero_cost_net_pnl": zero_cost_net,
            # A nulled cost model usually nulls ONE component, not both: a config
            # mismatch that zeroes the commission still leaves the spread being
            # crossed. Both partial cases get their own known answer so the gate
            # can name which half of the cost model went missing.
            "counterfactual_zero_commission_net_pnl": round(
                c["gross_pnl"] - c["spread_cost"], 10),
            "counterfactual_zero_spread_net_pnl": round(
                c["gross_pnl"] - c["commission"], 10),
            "commission_legs": [round(x, 10) for x in legs],
            "commission_at_minimum": at_min,
        }

    # The small sizing must pin the $2.00 minimum on BOTH legs; the large one must
    # not touch it. If that ever stops being true the fixture has lost its teeth.
    assert all(expected["scenarios"]["small"]["commission_at_minimum"]), \
        "'small' sizing no longer exercises the $2.00 commission minimum"
    assert not any(expected["scenarios"]["large"]["commission_at_minimum"]), \
        "'large' sizing unexpectedly hit the $2.00 commission minimum"

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    table = pa.Table.from_pandas(bars, preserve_index=False)
    pq.write_table(table, OUT_DIR / "bars_EURUSD_1h.parquet", compression="snappy")
    bars.to_csv(OUT_DIR / "bars_EURUSD_1h.csv", index=False)

    with open(OUT_DIR / "expected_backtest.json", "w", encoding="utf8") as fh:
        json.dump(expected, fh, indent=2, sort_keys=True)

    print(f"\nwrote {OUT_DIR / 'bars_EURUSD_1h.parquet'}")
    print(f"wrote {OUT_DIR / 'bars_EURUSD_1h.csv'}")
    print(f"wrote {OUT_DIR / 'expected_backtest.json'}")
    print("\narrow schema of the bars fixture:")
    print(table.schema)


if __name__ == "__main__":
    main()
