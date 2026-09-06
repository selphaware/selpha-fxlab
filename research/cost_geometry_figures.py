"""Every figure in the T5 report, drawn from the result document.

Ruling R6 in its strongest form, the same as T4: nothing here reads data, opens
a bar table, or calls a cost model. Each function takes the payload the
experiment already hashed and turns part of it into a picture, and writes the
numbers it drew from next to the picture as a CSV -- which is what the card
means by "figures under ``reports/T5/`` beside their CSVs". A figure whose
numbers cannot be checked is decoration.

The plotting is :mod:`research.svgplot`, so the output is deterministic: two
renders of an unchanged result produce identical bytes and ``git diff`` on a
figure shows the data that moved rather than a changed timestamp.
"""

from __future__ import annotations

import math
import pathlib
from typing import Any, Sequence

from research.svgplot import PALETTE, Figure, data_range, write

#: The rung every "at the survival bar" figure is drawn at (pre-reg #1).
BAR = "1.5"


def _colour(pairs: Sequence[str], pair: str) -> str:
    """A stable colour for a pair across every figure."""
    return PALETTE[list(pairs).index(pair) % len(PALETTE)]


def _categorical(labels: Sequence[str]) -> list[tuple[float, str]]:
    """Tick positions for a categorical x-axis."""
    return [(float(i), str(label)) for i, label in enumerate(labels)]


def _log10(value: Any) -> float:
    """log10 of a positive number, or NaN so the plotter skips the point."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return math.log10(number) if number > 0.0 else float("nan")


def build_all(payload: dict[str, Any],
              out_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Draw every figure and return a manifest the report links to."""
    pairs = sorted(payload["moves"])
    horizons = list(payload["window"]["horizons"])
    manifest: list[dict[str, Any]] = []

    def record(entry: dict[str, str], name: str, caption: str) -> None:
        manifest.append({"name": name, "caption": caption, **entry})

    record(_move_over_cost(payload, pairs, horizons, out_dir),
           "move_over_cost_by_horizon",
           "The median absolute move divided by the median round-trip cost, "
           f"at the {BAR}x rung, on a log10 axis. The dashed line is parity: "
           "below it the median move does not pay for the trade that captured "
           "it, and no signal can change that. The CSV carries the "
           "untransformed ratio.")
    record(_share_above_cost(payload, pairs, horizons, out_dir),
           "share_above_cost_by_horizon",
           "The share of individual moves larger than the round trip quoted "
           f"around them, at the {BAR}x rung. Measured move by move rather "
           "than by comparing two medians, because spread and volatility move "
           "together.")
    record(_cost_by_session(payload, pairs, out_dir),
           "cost_floor_by_session",
           f"The round-trip cost floor by session at the {BAR}x rung, on "
           "hourly bars, in basis points of notional. This is decision D3's "
           "execution constraint as a picture: the cheapest band per pair is "
           "the low point of its line.")
    record(_move_quantiles(payload, pairs, out_dir),
           "move_quantiles_vs_cost_5m",
           "Absolute-move quantiles at the 5-minute horizon against that "
           f"pair's own round-trip cost at {BAR}x, both in basis points. "
           "Where the cost line sits inside the quantile fan is where a "
           "5-minute rule has to find its edge.")
    record(_d2_edge(payload, out_dir),
           "d2_edge_versus_cost",
           f"The {len(payload['test_set']['cells'])} pre-registered D2 cells: "
           "the lag-1 implied edge, the "
           "variance-ratio upper bound, and the round trip they have to pay, "
           "on a log10 axis in basis points. The gap between the two edge "
           "series is the difference between what a rule could earn and what "
           "an oracle could.")
    record(_ladder_shrinkage(payload, pairs, out_dir),
           "executable_universe_by_rung",
           "How many horizon-by-session cells still have a median move larger "
           "than their median round trip, as the cost ladder is climbed. This "
           "is the shape of what a cost-model error would cost -- and "
           "'executable' here means only the arithmetic works, never that a "
           "signal exists.")
    record(_roll(payload, pairs, out_dir),
           "roll_window_cost_and_move",
           "Inside the derived 16:00-18:00 New York roll window against "
           "outside it: the cost ratio and the move ratio, on hourly bars. "
           "Pre-registered decision #4 already excludes the window; a bar "
           "above 1 on cost and below 1 on move is why.")
    for horizon in payload["window"]["history_horizons"]:
        record(_eras(payload, pairs, horizon, out_dir), f"cost_by_era_{horizon}",
               f"The round-trip cost floor at {BAR}x by calendar era on the "
               f"{horizon} horizon, over the full history. Ruling R1 starts "
               "AUDUSD in 2011, so its early eras are absent rather than "
               "zero.")
    return manifest


def _move_over_cost(payload: dict[str, Any], pairs: Sequence[str],
                    horizons: Sequence[str],
                    out_dir: pathlib.Path) -> dict[str, str]:
    """Median move over median cost, by horizon, one line per pair."""
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        points: list[tuple[float, float]] = []
        for index, horizon in enumerate(horizons):
            row = (payload["moves"].get(pair, {}).get(horizon, {})
                   .get("all"))
            ratio = (row or {}).get("ladder", {}).get(BAR, {}).get(
                "median_move_over_cost")
            table.append([pair, horizon, ratio])
            if ratio is not None:
                points.append((float(index), _log10(ratio)))
                values.append(_log10(ratio))
        series[pair] = points
    figure = Figure(
        f"Median move over median round-trip cost, at {BAR}x costs",
        x_label="horizon", y_label="log10 (median |move| / median cost)",
        x_range=(0.0, float(len(horizons) - 1)),
        y_range=data_range(values + [0.0]),
        x_ticks=_categorical(horizons))
    figure.reference(0.0, "parity")
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "move_over_cost_by_horizon.svg", table,
                 ["pair", "horizon", "median_move_over_cost"])


def _share_above_cost(payload: dict[str, Any], pairs: Sequence[str],
                      horizons: Sequence[str],
                      out_dir: pathlib.Path) -> dict[str, str]:
    """Share of moves exceeding their own round trip, by horizon."""
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        points: list[tuple[float, float]] = []
        for index, horizon in enumerate(horizons):
            row = payload["moves"].get(pair, {}).get(horizon, {}).get("all")
            share = (row or {}).get("ladder", {}).get(BAR, {}).get(
                "share_of_moves_above_cost")
            table.append([pair, horizon, share])
            if share is not None:
                points.append((float(index), float(share)))
                values.append(float(share))
        series[pair] = points
    figure = Figure(
        f"Share of moves larger than their own round trip, at {BAR}x costs",
        x_label="horizon", y_label="share of moves",
        x_range=(0.0, float(len(horizons) - 1)),
        y_range=data_range(values), x_ticks=_categorical(horizons))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "share_above_cost_by_horizon.svg", table,
                 ["pair", "horizon", "share_of_moves_above_cost"])


def _cost_by_session(payload: dict[str, Any], pairs: Sequence[str],
                     out_dir: pathlib.Path) -> dict[str, str]:
    """The cost floor by session, one line per pair."""
    sessions: list[str] = []
    for pair in pairs:
        for name in (payload["cost_floor"].get(pair, {}).get("by_session")
                     or {}):
            if name not in sessions:
                sessions.append(name)
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        by_session = payload["cost_floor"].get(pair, {}).get("by_session", {})
        points: list[tuple[float, float]] = []
        for index, name in enumerate(sessions):
            row = by_session.get(name) or {}
            cost = row.get("ladder", {}).get(BAR, {}).get("cost_bp_p50")
            table.append([pair, name, cost, row.get("median_spread_pips"),
                          row.get("median_ticks")])
            if cost is not None:
                points.append((float(index), float(cost)))
                values.append(float(cost))
        series[pair] = points
    figure = Figure(
        f"Round-trip cost floor by session, at {BAR}x costs",
        x_label="session", y_label="cost (basis points of notional)",
        x_range=(0.0, float(max(len(sessions) - 1, 1))),
        y_range=data_range(values), x_ticks=_categorical(sessions))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "cost_floor_by_session.svg", table,
                 ["pair", "session", "cost_bp_p50", "median_spread_pips",
                  "median_ticks"])


def _move_quantiles(payload: dict[str, Any], pairs: Sequence[str],
                    out_dir: pathlib.Path) -> dict[str, str]:
    """The 5-minute move distribution against the 5-minute cost floor."""
    quantiles = ("p10", "p25", "p50", "p75", "p90")
    table: list[list[Any]] = []
    values: list[float] = []
    fan: dict[str, list[tuple[float, float]]] = {}
    costs: list[tuple[float, float]] = []
    for index, pair in enumerate(pairs):
        row = payload["moves"].get(pair, {}).get("5m", {}).get("all") or {}
        move = row.get("move_bp") or {}
        cost = row.get("ladder", {}).get(BAR, {}).get("cost_bp_p50")
        table.append([pair, *(move.get(name) for name in quantiles), cost])
        for name in quantiles:
            value = move.get(name)
            if value is not None:
                fan.setdefault(name, []).append((float(index),
                                                 _log10(value)))
                values.append(_log10(value))
        if cost is not None:
            costs.append((float(index), _log10(cost)))
            values.append(_log10(cost))
    figure = Figure(
        f"5-minute move quantiles against the round trip, at {BAR}x costs",
        x_label="pair", y_label="log10 basis points",
        x_range=(0.0, float(max(len(pairs) - 1, 1))),
        y_range=data_range(values), x_ticks=_categorical(pairs))
    for position, name in enumerate(quantiles):
        figure.line(fan.get(name, []), PALETTE[position % len(PALETTE)],
                    f"|move| {name}")
    figure.line(costs, "#c0603a", f"round trip at {BAR}x", dashed=True)
    return write(figure, out_dir / "move_quantiles_vs_cost_5m.svg", table,
                 ["pair", *quantiles, "cost_bp_p50"])


def _d2_edge(payload: dict[str, Any],
             out_dir: pathlib.Path) -> dict[str, str]:
    """The eleven D2 cells: two implied edges against one round trip."""
    cells = payload["test_set"]["cells"]
    labels = [f"{c['pair']} {c['horizon']}" for c in cells]
    table: list[list[Any]] = []
    values: list[float] = []
    lag1: list[tuple[float, float]] = []
    bound: list[tuple[float, float]] = []
    cost: list[tuple[float, float]] = []
    for index, cell in enumerate(cells):
        row = cell["variants"].get("all hours") or {}
        edge = row.get("edge") or {}
        a = edge.get("lag1_edge_bp")
        b = edge.get("vr_edge_bp")
        c = (row.get("cost_bp") or {}).get(BAR)
        table.append([cell["pair"], cell["horizon"], a, b, c, cell["verdict"],
                      cell["verdict_from_variant"], cell["verdict_from_route"]])
        for value, target in ((a, lag1), (b, bound), (c, cost)):
            if value is not None:
                target.append((float(index), _log10(value)))
                values.append(_log10(value))
    figure = Figure(
        "The D2 test set: implied edge against the round trip",
        x_label="cell", y_label="log10 basis points",
        x_range=(0.0, float(max(len(labels) - 1, 1))),
        y_range=data_range(values), x_ticks=_categorical(labels))
    figure.dots(lag1, PALETTE[0], "|rho(1)| x sd")
    figure.dots(bound, PALETTE[2], "variance-ratio bound")
    figure.dots(cost, "#c0603a", f"round trip at {BAR}x")
    return write(figure, out_dir / "d2_edge_versus_cost.svg", table,
                 ["pair", "horizon", "lag1_edge_bp", "vr_edge_bp",
                  "cost_bp_p50", "verdict", "verdict_from_variant",
                  "verdict_from_route"])


def _ladder_shrinkage(payload: dict[str, Any], pairs: Sequence[str],
                      out_dir: pathlib.Path) -> dict[str, str]:
    """How the executable universe shrinks from 1.0x to 2.0x."""
    rungs = list(payload["method"]["ladder"])
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        row = payload["sensitivity"].get(pair) or {}
        counts = row.get("executable_by_rung") or {}
        points: list[tuple[float, float]] = []
        for index, rung in enumerate(rungs):
            count = counts.get(rung)
            table.append([pair, rung, count, row.get("cells_measured")])
            if count is not None:
                points.append((float(index), float(count)))
                values.append(float(count))
        series[pair] = points
    figure = Figure(
        "Horizon-by-session cells whose median move clears their median cost",
        x_label="cost multiplier", y_label="cells",
        x_range=(0.0, float(max(len(rungs) - 1, 1))),
        y_range=data_range(values), x_ticks=_categorical(rungs))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "executable_universe_by_rung.svg", table,
                 ["pair", "rung", "executable_cells", "cells_measured"])


def _roll(payload: dict[str, Any], pairs: Sequence[str],
          out_dir: pathlib.Path) -> dict[str, str]:
    """Cost and move inside the roll window, relative to outside it."""
    table: list[list[Any]] = []
    values: list[float] = [1.0]
    cost: list[tuple[float, float]] = []
    move: list[tuple[float, float]] = []
    for index, pair in enumerate(pairs):
        row = payload["roll"].get(pair) or {}
        c = row.get("cost_ratio")
        m = row.get("move_ratio")
        table.append([pair, c, m, row.get("move_over_cost_inside"),
                      row.get("move_over_cost_outside"),
                      row.get("share_above_cost_inside"),
                      row.get("share_above_cost_outside")])
        if c is not None:
            cost.append((float(index), float(c)))
            values.append(float(c))
        if m is not None:
            move.append((float(index), float(m)))
            values.append(float(m))
    figure = Figure(
        "The roll window against the rest of the day (hourly bars)",
        x_label="pair", y_label="ratio, inside / outside",
        x_range=(0.0, float(max(len(pairs) - 1, 1))),
        y_range=data_range(values), x_ticks=_categorical(pairs))
    figure.reference(1.0, "no difference")
    figure.line(cost, "#c0603a", "round-trip cost")
    figure.line(move, PALETTE[2], "median |move|")
    return write(figure, out_dir / "roll_window_cost_and_move.svg", table,
                 ["pair", "cost_ratio", "move_ratio", "move_over_cost_inside",
                  "move_over_cost_outside", "share_above_cost_inside",
                  "share_above_cost_outside"])


def _eras(payload: dict[str, Any], pairs: Sequence[str], horizon: str,
          out_dir: pathlib.Path) -> dict[str, str]:
    """The cost floor by calendar era, one line per pair."""
    names = [row["era"] for row in payload["method"]["calendar_eras"]]
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        by_era = (payload["eras"].get(pair) or {}).get(horizon) or {}
        points: list[tuple[float, float]] = []
        for index, name in enumerate(names):
            era = by_era.get(name) or {}
            row = era.get("reference_band") or {}
            if not row.get("usable"):
                row = era.get("uncontrolled") or {}
            cost = (row.get("ladder") or {}).get(BAR, {}).get("cost_bp_p50")
            table.append([pair, name, cost, row.get("median_spread_pips"),
                          row.get("median_ticks"), row.get("n")])
            if cost is not None:
                points.append((float(index), float(cost)))
                values.append(float(cost))
        series[pair] = points
    figure = Figure(
        f"Round-trip cost floor by era at {BAR}x costs, {horizon} bars",
        x_label="era", y_label="cost (basis points of notional)",
        x_range=(0.0, float(max(len(names) - 1, 1))),
        y_range=data_range(values), x_ticks=_categorical(names))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / f"cost_by_era_{horizon}.svg", table,
                 ["pair", "era", "cost_bp_p50", "median_spread_pips",
                  "median_ticks", "returns"])
