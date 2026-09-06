"""Every figure in the T6 report, drawn from the result document.

Ruling R6 in its strongest form, as in T4 and T5: nothing here reads data,
opens a bar table, or calls a cost model. Each function takes the payload the
experiment already hashed and turns part of it into a picture, and writes the
numbers it drew from next to the picture as a CSV. A figure whose numbers
cannot be checked is decoration.

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


def _colour(names: Sequence[str], name: str) -> str:
    """A stable colour for a series across every figure."""
    return PALETTE[list(names).index(name) % len(PALETTE)]


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
    horizons = list(payload["window"]["horizons"])
    pairs = list(payload["window"]["pairs"])
    manifest: list[dict[str, Any]] = []

    def record(entry: dict[str, str], name: str, caption: str) -> None:
        manifest.append({"name": name, "caption": caption, **entry})

    record(_mean_correlation(payload, pairs, horizons, out_dir),
           "mean_correlation_by_pair",
           "Each pair's mean absolute correlation with the other eleven, by "
           "horizon. A pair high on this axis is a pair a portfolio gets "
           "little new information from.")
    record(_rolling_bets(payload, horizons, out_dir),
           "effective_bets_rolling",
           "The effective number of independent bets on rolling two-year "
           "windows, both measures, at the hourly horizon. The dashed line is "
           "the structural ceiling: twelve pairs across eight currencies span "
           "at most seven directions however they are correlated.")
    record(_spectrum(payload, horizons, out_dir),
           "eigen_spectrum",
           "Share of universe variance explained by each principal component, "
           "by horizon. The tail is the arithmetic: five of the twelve pairs "
           "are exact functions of the other seven.")
    record(_regime_bets(payload, horizons, out_dir),
           "effective_bets_by_regime",
           "Effective bets by volatility regime, by horizon. If correlations "
           "went to one in a crisis the high-volatility bar would be the "
           "shortest of the three.")
    record(_identity_cost(payload, out_dir),
           "identity_spread_versus_cost",
           "The universe's triangular identities: the standard deviation of "
           "the arbitrage spread against the round trip of all three legs, at "
           "the 1.5x rung and 100,000 units, on a log10 axis in basis points. "
           "The dashed line is parity -- above it a one-sigma reversion pays "
           "for the trade that captured it.")
    record(_leadlag_cost(payload, horizons, out_dir),
           "leadlag_edge_versus_cost",
           "Every lead-lag cell the result carries: the implied edge against "
           "the lagging pair's own round trip at the 1.5x rung and 100,000 "
           "units, on a log10 axis. The dashed line is parity.")
    record(_shock_sensitivity(payload, horizons, out_dir),
           "leadlag_shock_sensitivity",
           "The same cells with and without the two declared shock days of "
           "January 2015. A point on the dashed line is unaffected by the SNB "
           "de-peg; a point far below it was mostly that afternoon.")
    record(_factor_memory(payload, horizons, out_dir),
           "currency_factor_variance_ratio",
           "The q=4 variance ratio of each currency-strength factor against "
           "the pairs', by horizon. One is a random walk; below one is mean "
           "reversion.")
    record(_null_check(payload, out_dir),
           "simulated_null_against_published",
           "The simulated Engle-Granger null against MacKinnon's published "
           "asymptotic critical values, at both scan widths. The simulation "
           "is what every p-value in the cointegration scan is read off.")
    return manifest


# --------------------------------------------------------------------------- #
# Correlation and geometry
# --------------------------------------------------------------------------- #

def _mean_correlation(payload: dict[str, Any], pairs: Sequence[str],
                      horizons: Sequence[str],
                      out_dir: pathlib.Path) -> dict[str, str]:
    rows: list[list[Any]] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for horizon in horizons:
        block = payload["correlation"].get(horizon)
        if not block:
            continue
        names = block["pairs"]
        matrix = block["matrix"]
        points: list[tuple[float, float]] = []
        for index, pair in enumerate(pairs):
            if pair not in names:
                continue
            position = names.index(pair)
            values = [abs(float(matrix[position][other]))
                      for other in range(len(names)) if other != position]
            mean = sum(values) / len(values) if values else float("nan")
            points.append((float(index), mean))
            rows.append([pair, horizon, round(mean, 6)])
        series[horizon] = points
    figure = Figure(
        "Mean absolute correlation with the rest of the universe",
        x_label="pair", y_label="mean |rho|",
        x_range=(0.0, max(len(pairs) - 1, 1)),
        y_range=data_range(y for points in series.values() for _x, y in points),
        x_ticks=_categorical(pairs))
    for horizon, points in series.items():
        figure.line(points, _colour(horizons, horizon), horizon)
        figure.dots(points, _colour(horizons, horizon))
    return write(figure, out_dir / "mean_correlation_by_pair.svg", rows,
                 ["pair", "horizon", "mean_abs_rho"])


def _rolling_bets(payload: dict[str, Any], horizons: Sequence[str],
                  out_dir: pathlib.Path) -> dict[str, str]:
    horizon = horizons[0] if horizons else ""
    windows = payload["rolling_geometry"].get(horizon) or []
    rows = [[w["from"], w["to"], w["n"], w["mean_abs_rho"],
             w["participation_ratio"], w["entropy_bets"]] for w in windows]
    labels = [w["from"][:7] for w in windows]
    ceiling = float(payload["portfolio"]["structural_ceiling"])
    values = [w["participation_ratio"] for w in windows] + \
             [w["entropy_bets"] for w in windows] + [ceiling]
    figure = Figure(
        f"Effective independent bets, rolling two years ({horizon})",
        x_label="window start", y_label="effective bets",
        x_range=(0.0, max(len(windows) - 1, 1)),
        y_range=data_range(values),
        x_ticks=_categorical(labels))
    figure.line([(float(i), float(w["participation_ratio"]))
                 for i, w in enumerate(windows)], PALETTE[0],
                "participation ratio")
    figure.line([(float(i), float(w["entropy_bets"]))
                 for i, w in enumerate(windows)], PALETTE[1], "entropy bets")
    figure.reference(ceiling, "structural ceiling")
    return write(figure, out_dir / "effective_bets_rolling.svg", rows,
                 ["from", "to", "n", "mean_abs_rho", "participation_ratio",
                  "entropy_bets"])


def _spectrum(payload: dict[str, Any], horizons: Sequence[str],
              out_dir: pathlib.Path) -> dict[str, str]:
    rows: list[list[Any]] = []
    figure = Figure(
        "Variance explained by each principal component",
        x_label="component", y_label="share of universe variance",
        x_range=(0.0, 11.0), y_range=(0.0, 0.5),
        x_ticks=_categorical([str(i + 1) for i in range(12)]))
    for horizon in horizons:
        block = payload["correlation"].get(horizon)
        if not block:
            continue
        shares = block["geometry"]["variance_explained"]
        points = [(float(i), float(v)) for i, v in enumerate(shares)
                  if v is not None]
        figure.line(points, _colour(horizons, horizon), horizon)
        figure.dots(points, _colour(horizons, horizon))
        for i, value in enumerate(shares):
            rows.append([horizon, i + 1, value])
    return write(figure, out_dir / "eigen_spectrum.svg", rows,
                 ["horizon", "component", "share_of_variance"])


def _regime_bets(payload: dict[str, Any], horizons: Sequence[str],
                 out_dir: pathlib.Path) -> dict[str, str]:
    regimes = ("low", "mid", "high")
    rows: list[list[Any]] = []
    values: list[float] = []
    figure_rows: list[tuple[float, float, float, str, str]] = []
    for h_index, horizon in enumerate(horizons):
        block = next((row for row in payload["portfolio"]["rows"]
                      if row["horizon"] == horizon), None)
        if not block:
            continue
        for r_index, regime in enumerate(regimes):
            entry = block["by_regime"].get(regime)
            if not entry:
                continue
            value = float(entry["participation_ratio"])
            left = h_index * (len(regimes) + 1) + r_index
            figure_rows.append((left, left + 0.85, value, regime, horizon))
            values.append(value)
            rows.append([horizon, regime, entry["mean_abs_rho"],
                         entry["participation_ratio"],
                         entry["components_for_90pct"]])
    labels = []
    for horizon in horizons:
        labels.extend([f"{horizon} {regime}" for regime in regimes])
        labels.append("")
    figure = Figure(
        "Effective independent bets by volatility regime",
        x_label="horizon and regime", y_label="participation ratio",
        x_range=(0.0, max(len(labels) - 1, 1)),
        y_range=(0.0, max(values, default=1.0) * 1.15),
        x_ticks=_categorical(labels))
    for left, right, value, regime, _horizon in figure_rows:
        figure.bar(left, right, value, _colour(regimes, regime), regime)
    return write(figure, out_dir / "effective_bets_by_regime.svg", rows,
                 ["horizon", "regime", "mean_abs_rho", "participation_ratio",
                  "components_for_90pct"])


# --------------------------------------------------------------------------- #
# Cost geometry
# --------------------------------------------------------------------------- #

def _identity_cost(payload: dict[str, Any],
                   out_dir: pathlib.Path) -> dict[str, str]:
    entries = [row for row in payload["cointegration"]["ranked"]
               if row["identity"]]
    entries.sort(key=lambda r: -(float(
        (r["cost"] or {}).get("amplitude_over_cost", {}).get(BAR) or 0.0)))
    labels = [f"{'+'.join(r['members'])} {r['horizon']}" for r in entries]
    rows: list[list[Any]] = []
    spread: list[tuple[float, float]] = []
    cost: list[tuple[float, float]] = []
    for index, row in enumerate(entries):
        window = row["windows"].get("confirmation") or row["discovery"]
        sd = window.get("residual_sd_bp")
        total = (row["cost"] or {}).get("cost_bp", {}).get(BAR)
        spread.append((float(index), _log10(sd)))
        cost.append((float(index), _log10(total)))
        rows.append(["+".join(row["members"]), row["horizon"], sd, total,
                     (row["cost"] or {}).get("amplitude_over_cost", {}).get(BAR),
                     (row["cost"] or {}).get("break_even_entry_sd")])
    figure = Figure(
        "Triangular identities: spread amplitude against three legs' cost",
        x_label="identity and horizon", y_label="log10 basis points",
        x_range=(0.0, max(len(entries) - 1, 1)),
        y_range=data_range([y for _x, y in spread] + [y for _x, y in cost]),
        x_ticks=_categorical(labels))
    figure.line(spread, PALETTE[0], "spread sd (bp)")
    figure.dots(spread, PALETTE[0])
    figure.line(cost, PALETTE[1], f"round trip @ {BAR}x (bp)")
    figure.dots(cost, PALETTE[1])
    return write(figure, out_dir / "identity_spread_versus_cost.svg", rows,
                 ["relationship", "horizon", "residual_sd_bp",
                  f"cost_bp_at_{BAR}x", "amplitude_over_cost",
                  "break_even_entry_sd"])


def _leadlag_cost(payload: dict[str, Any], horizons: Sequence[str],
                  out_dir: pathlib.Path) -> dict[str, str]:
    rows: list[list[Any]] = []
    points: dict[str, list[tuple[float, float]]] = {}
    for horizon in horizons:
        block = payload["leadlag_headline"].get(horizon) or {}
        cells: list[tuple[float, float]] = []
        for row in block.get("rows", []):
            edge = _log10(row.get("edge_bp"))
            cost = _log10(row.get("cost_bp_at_survival_bar"))
            if math.isnan(edge) or math.isnan(cost):
                continue
            cells.append((cost, edge))
            rows.append([horizon, row["lead"], row["lagging"], row["lag"],
                         row["rho"], row["edge_bp"],
                         row["cost_bp_at_survival_bar"],
                         row["pays_at_survival_bar"], row["qualifies"]])
        points[horizon] = cells
    every = [value for cells in points.values() for pair in cells
             for value in pair]
    figure = Figure(
        f"Lead-lag implied edge against the lagging pair's round trip @ {BAR}x",
        x_label="log10 round-trip cost (bp)", y_label="log10 implied edge (bp)",
        x_range=data_range(every), y_range=data_range(every))
    for horizon, cells in points.items():
        figure.dots(cells, _colour(horizons, horizon), horizon)
    low, high = data_range(every)
    figure.line([(low, low), (high, high)], "#8a8f96", "parity", dashed=True)
    return write(figure, out_dir / "leadlag_edge_versus_cost.svg", rows,
                 ["horizon", "lead", "lagging", "lag", "rho", "edge_bp",
                  f"cost_bp_at_{BAR}x", "pays", "qualifies"])


def _shock_sensitivity(payload: dict[str, Any], horizons: Sequence[str],
                       out_dir: pathlib.Path) -> dict[str, str]:
    rows: list[list[Any]] = []
    points: dict[str, list[tuple[float, float]]] = {}
    for horizon in horizons:
        block = payload["leadlag_headline"].get(horizon) or {}
        cells: list[tuple[float, float]] = []
        for row in block.get("rows", []):
            with_shock = row.get("rho")
            without = row.get("rho_without_shock")
            if with_shock is None or without is None:
                continue
            cells.append((abs(float(with_shock)), abs(float(without))))
            rows.append([horizon, row["lead"], row["lagging"], row["lag"],
                         with_shock, without, row["shock_share_of_rho"],
                         row["survives_correction"],
                         row["survives_without_shock"]])
        points[horizon] = cells
    every = [value for cells in points.values() for pair in cells
             for value in pair]
    figure = Figure(
        "Lead-lag correlation with and without the January 2015 shock days",
        x_label="|rho| with the shock days",
        y_label="|rho| without them",
        x_range=data_range(every), y_range=data_range(every))
    for horizon, cells in points.items():
        figure.dots(cells, _colour(horizons, horizon), horizon)
    low, high = data_range(every)
    figure.line([(low, low), (high, high)], "#8a8f96", "unaffected",
                dashed=True)
    return write(figure, out_dir / "leadlag_shock_sensitivity.svg", rows,
                 ["horizon", "lead", "lagging", "lag", "rho",
                  "rho_without_shock", "share_of_rho_from_the_shock",
                  "survives", "survives_without_shock"])


# --------------------------------------------------------------------------- #
# Memory and the null
# --------------------------------------------------------------------------- #

def _factor_memory(payload: dict[str, Any], horizons: Sequence[str],
                   out_dir: pathlib.Path) -> dict[str, str]:
    rows: list[list[Any]] = []
    currencies: list[str] = []
    for horizon in horizons:
        block = payload["currency"].get(horizon)
        if block:
            currencies = [f["currency"] for f in block["factors"]]
            break
    figure = Figure(
        "Currency-factor variance ratio at q=4, by horizon",
        x_label="currency factor", y_label="VR(4)",
        x_range=(0.0, max(len(currencies) - 1, 1)),
        y_range=(0.80, 1.10),
        x_ticks=_categorical(currencies))
    for horizon in horizons:
        block = payload["currency"].get(horizon)
        if not block:
            continue
        points = []
        for index, factor in enumerate(block["factors"]):
            value = factor.get("vr_headline")
            if value is None:
                continue
            points.append((float(index), float(value)))
            rows.append([horizon, factor["currency"], value,
                         factor.get("vr_q_value"),
                         factor.get("vr_survives_correction"),
                         factor.get("pairs_it_appears_in")])
        figure.line(points, _colour(horizons, horizon), horizon)
        figure.dots(points, _colour(horizons, horizon))
    figure.reference(1.0, "random walk")
    return write(figure, out_dir / "currency_factor_variance_ratio.svg", rows,
                 ["horizon", "currency", "variance_ratio_q4", "q_value",
                  "survives_correction", "pairs_it_appears_in"])


def _null_check(payload: dict[str, Any],
                out_dir: pathlib.Path) -> dict[str, str]:
    null = payload["method"]["null"]
    published = null["engle_granger_published"]
    simulated = null["quantiles"]["engle_granger"]
    levels = ("1%", "5%", "10%")
    labels: list[str] = []
    rows: list[list[Any]] = []
    sim_points: list[tuple[float, float]] = []
    pub_points: list[tuple[float, float]] = []
    index = 0
    for width in sorted(simulated):
        for level in levels:
            value = simulated[width].get(level)
            reference = published.get(width, {}).get(level)
            labels.append(f"n={width} {level}")
            if value is not None:
                sim_points.append((float(index), float(value)))
            if reference is not None:
                pub_points.append((float(index), float(reference)))
            rows.append([width, level, value, reference,
                         (round(float(value) - float(reference), 4)
                          if value is not None and reference is not None
                          else None)])
            index += 1
    figure = Figure(
        "The simulated Engle-Granger null against MacKinnon's published values",
        x_label="scan width and level", y_label="critical tau",
        x_range=(0.0, max(index - 1, 1)),
        y_range=data_range([y for _x, y in sim_points]
                           + [y for _x, y in pub_points]),
        x_ticks=_categorical(labels))
    figure.dots(sim_points, PALETTE[0], "simulated", radius=3.4)
    figure.dots(pub_points, PALETTE[1], "published", radius=3.4)
    return write(figure, out_dir / "simulated_null_against_published.svg",
                 rows, ["variables", "level", "simulated", "published",
                        "difference"])
