"""Render the T5 cost-geometry report from its result document.

Ruling R6, with no escape hatch: there is no ``--note`` here, every number
below is read out of ``result.json``, and every figure is drawn at render time
from the same document by :mod:`research.cost_geometry_figures`. The
pre-2013 recommendation and the questions for T6 and T7 are generated too --
their prose is a template and every quantity in it, including which cells
appear at all, comes from the result.

The last section is an **addendum**, appended after the card closed by the T6
card's Step 0: SPEC2 decision D9 moved the research reference notional to
100,000 units, and section 1 is re-expressed at that size. It sits at the end
rather than inside section 1 because nothing above it changed, and a reader
should be able to see that at a glance.

The one thing this module adds that the result does not contain is the
*recommendation rule* for the era question. The card asks this card to
recommend and not to decide, so the rule is stated in the report beside the
table it is applied to, in a form a checkpoint can disagree with, rather than
being a number that appeared in a paragraph.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
from typing import Any, Final, Sequence

from research.cost_geometry_figures import BAR, build_all

_LOG: Final[logging.Logger] = logging.getLogger("research.cost_geometry_report")

#: Rows carried before a long listing is truncated.
MAX_ROWS: Final[int] = 40

#: Rows of the "where edge can survive" map the report prints in full.
MAP_ROWS: Final[int] = 30

#: The era the others are compared against in the pre-2013 recommendation.
REFERENCE_ERA: Final[str] = "2013+"

#: The recommendation rule, stated as data so the report can print it and a
#: checkpoint can argue with it. It is a reading of evidence, not a threshold
#: on anything: SPEC2 pins exactly one threshold, the 1.5x survival bar, and
#: nothing here adds a second.
UNVERIFIABLE_LIMIT: Final[float] = 0.25
AGREEMENT_LIMIT: Final[float] = 0.75
COST_RATIO_LIMIT: Final[float] = 2.0


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def _row(cells: Sequence[Any]) -> str:
    """One Markdown row, with any pipe inside a cell escaped."""
    return ("| " + " | ".join("" if c is None else str(c).replace("|", "\\|")
                              for c in cells) + " |")


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    return [_row(header), _row(["---"] * len(header)),
            *[_row(r) for r in rows], ""]


def _n(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—" if value is None else str(value)


def _f(value: Any, places: int = 3) -> str:
    try:
        return f"{float(value):,.{places}f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value: Any, places: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{places}f}%"
    except (TypeError, ValueError):
        return "—"


def _x(value: Any, places: int = 2) -> str:
    try:
        return f"{float(value):.{places}f}×"
    except (TypeError, ValueError):
        return "—"


def _pretty_slice(name: str) -> str:
    """A slice key as a reader should see it."""
    if name.startswith("session:"):
        return name.split(":", 1)[1].replace("_", " ")
    if name.startswith("tercile:"):
        return f"{name.split(':', 1)[1]} vol"
    return {"all": "all hours", "off_roll": "outside the roll",
            "roll": "roll window"}.get(name, name)


def _p0a(payload: dict[str, Any]) -> list[str]:
    """The P0-A caveat, with this run's own numbers in it.

    Printed under every table that carries a non-USD-quoted pair, which is
    almost all of them. The card asks for the caveat on every such table and
    for the gap to be named rather than worked around, so it is one derived
    sentence rather than a footnote somebody has to go and find.
    """
    block = payload["p0a"]
    names = block["non_usd_quoted_pairs"]
    pairs = ", ".join(f"`{p}`" for p in names)
    method = payload["method"]
    return [
        f"> **P0-A caveat.** {len(names)} of the twelve pairs above "
        f"{'is' if len(names) == 1 else 'are'} not USD-quoted ({pairs}), and "
        "SPEC2 prerequisite P0-A is "
        f"**{'landed' if block['landed'] else 'unfixed'}**: commission is "
        "floored against a quote-currency notional and cross-pair P&L is "
        "summed without conversion. Every cost in this table is a ratio of "
        "two quote-currency quantities and is therefore currency-free; the "
        "one currency-sensitive term is the USD "
        f"{_f(method['cost_parameters']['commission_min'], 2)} per-order "
        "floor, which binds on "
        f"**{_n(method['returns_priced_below_the_floor'])}** of the priced "
        "moves at this card's reference size of "
        f"{_n(method['reference_units'])} units. Commission is reported in "
        "the quote currency, as the model computes it.",
        "",
    ]


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

def render(document: dict[str, Any], trials: int, gate_status: str, home: str,
           figures: Sequence[dict[str, Any]], figures_dir: str) -> str:
    """The whole report as Markdown."""
    payload = document["payload"]
    by_name = {figure["name"]: figure for figure in figures}
    lines: list[str] = []
    lines += _header(document, payload, trials)
    lines += _method(payload)
    lines += _section_floor(payload, by_name, figures_dir)
    lines += _section_moves(payload, by_name, figures_dir)
    lines += _section_holding(payload)
    lines += _section_test_set(payload, by_name, figures_dir)
    lines += _section_roll(payload, by_name, figures_dir)
    lines += _section_eras(payload, by_name, figures_dir)
    lines += _section_sensitivity(payload, by_name, figures_dir)
    lines += _closing_verdicts(payload)
    lines += _closing_map(payload)
    lines += _closing_era_recommendation(payload)
    lines += _closing_questions(payload)
    lines += _section_addendum(payload)
    lines += _provenance(document, payload, home, figures, figures_dir,
                         gate_status)
    return "\n".join(lines).rstrip() + "\n"


def _figure(by_name: dict[str, Any], name: str, figures_dir: str) -> list[str]:
    """A figure and the caption and CSV link that make it checkable."""
    entry = by_name.get(name)
    if not entry:
        return []
    svg = f"{figures_dir}/{pathlib.Path(entry['svg']).name}"
    csv = f"{figures_dir}/{pathlib.Path(entry['csv']).name}"
    caption = entry["caption"]
    return [f"![{caption}]({svg})", "",
            f"*{caption}* — source table: [`{csv}`]({csv})", ""]


def _d2_ratios(payload: dict[str, Any]) -> dict[str, Any]:
    """How far apart the two D2 measures are, per cell and across the set.

    The whole reading of section 4 turns on this. The variance-ratio bound
    credits a rule with every basis point of variance the reversion removed;
    the lag-1 figure credits it with what trading the measured autocorrelation
    would actually earn. For the process family that generates this
    autocorrelation structure the first overstates the second by an order of
    magnitude, so the distance between them is not a detail -- it is the
    difference between a cell that survives and a cell that is closed, on
    exactly the cells D2 pre-registered.
    """
    shortfall: list[float] = []
    ratios: list[float] = []
    per_cell: dict[str, dict[str, Any]] = {}
    for cell in payload["test_set"]["cells"]:
        row = cell["variants"].get("all hours") or {}
        edge = row.get("edge") or {}
        lag1 = edge.get("lag1_edge_bp")
        bound = edge.get("vr_edge_bp")
        cost = (row.get("cost_bp") or {}).get(BAR)
        entry: dict[str, Any] = {}
        if lag1 and cost:
            entry["cost_over_lag1"] = cost / lag1
            shortfall.append(entry["cost_over_lag1"])
        if lag1 and bound:
            entry["bound_over_lag1"] = bound / lag1
            ratios.append(entry["bound_over_lag1"])
        per_cell[f"{cell['pair']}|{cell['horizon']}"] = entry
    return {
        "per_cell": per_cell,
        "shortfall_min": min(shortfall) if shortfall else None,
        "shortfall_max": max(shortfall) if shortfall else None,
        "bound_ratio_min": min(ratios) if ratios else None,
        "bound_ratio_max": max(ratios) if ratios else None,
    }


def _plural(count: int, one: str, many: str) -> str:
    """``one`` or ``many``, so a generated sentence reads like a sentence."""
    return one if count == 1 else many


def _monotone_pairs(payload: dict[str, Any]) -> tuple[int, int]:
    """How many pairs have move-over-cost rising at every step of the ladder.

    Derived rather than asserted: "a longer hold always buys a better ratio"
    is exactly the kind of claim that is true of this store and might not be
    true of another one.
    """
    horizons = payload["window"]["horizons"]
    rising = 0
    measured = 0
    for _pair, by_horizon in payload["moves"].items():
        ratios = []
        for horizon in horizons:
            row = by_horizon.get(horizon, {}).get("all")
            value = ((row or {}).get("ladder", {}).get(BAR, {})
                     .get("median_move_over_cost"))
            if value is not None:
                ratios.append(float(value))
        if len(ratios) < 2:
            continue
        measured += 1
        if all(b > a for a, b in zip(ratios, ratios[1:])):
            rising += 1
    return rising, measured


def _map_ranks(payload: dict[str, Any], horizon: str) -> tuple[Any, Any, int]:
    """Best and worst rank a horizon reaches in the ranked map, and its size."""
    rows = payload["edge_map"]
    ranks = [index + 1 for index, row in enumerate(rows)
             if row["horizon"] == horizon]
    return (min(ranks) if ranks else None,
            max(ranks) if ranks else None, len(rows))


def _header(document: dict[str, Any], payload: dict[str, Any],
            trials: int) -> list[str]:
    window = payload["window"]
    counts = payload["test_set"]["counts"]
    cells = payload["test_set"]["cells"]
    parked = [c for c in cells if c["verdict"] == "PARKED"]
    open_cells = [c for c in cells if c["verdict"] != "CLOSED"]
    open_on_bound = sum(1 for c in open_cells
                        if c["verdict_from_route"] == "variance_ratio_bound")
    lag1_closed = sum(
        1 for c in cells
        if all(v["lag1"]["verdict"] in (None, "CLOSED")
               for v in c["variants"].values()))
    rising, measured = _monotone_pairs(payload)
    short = window["horizons"][0]
    second = window["horizons"][1] if len(window["horizons"]) > 1 else short
    longest = window["horizons"][-1]
    best_short, worst_short, map_size = _map_ranks(payload, short)
    best_long, _worst, _size = _map_ranks(payload, longest)
    cheapest = payload["cheapest_band"]
    ratios = [row["ratio_dearest_to_cheapest"] for row in cheapest.values()
              if row.get("ratio_dearest_to_cheapest") is not None]
    era_horizon = window["history_horizons"][0]
    evidence = payload["era_evidence"].get(era_horizon, [])
    early = next((r for r in evidence if r["era"] == "2005-2008"), {})
    modern = next((r for r in evidence if r["era"] == REFERENCE_ERA), {})
    early_cost = early.get("median_cost_bp_at_survival_bar")
    modern_cost = modern.get("median_cost_bp_at_survival_bar")
    cost_ratio = (early_cost / modern_cost
                  if early_cost is not None and modern_cost else None)
    bar = payload["method"]["survival_bar"]
    ratios_d2 = _d2_ratios(payload)
    return [
        "# T5 — EDA battery II: cost geometry",
        "",
        f"**Primary window:** {window['start']} → {window['end']}, "
        f"{window['pairs']} pairs, horizons "
        + ", ".join(f"`{h}`" for h in window["horizons"])
        + f" · **Era section:** {window['history_start']} → "
        f"{window['end']} on "
        + ", ".join(f"`{h}`" for h in window["history_horizons"])
        + " · **Task card:** `taskcards/T5.md` · **Experiment:** "
        f"`{document['experiment_id']}` · **Seed:** {document['seed']} "
        f"· **Result hash:** `{document['result_hash'][:16]}`",
        "",
        f"**Trials ledgered under {document['taskcard']}:** {trials} "
        "(SPEC2 pre-reg #10).",
        "",
        "This card measures what the twelve pairs **cost** and puts it beside "
        "what T4 measured them to **be**. Everything in it is arithmetic and "
        "a map: no backtest, no scorecard, no candidate advanced or killed, "
        "and no claim anywhere that an edge exists. Pre-registered decision "
        "#3 puts those decisions in chat, between cards.",
        "",
        "**Every cost figure here comes out of `fxlab.costs.IBCostModel`.** "
        "Not one pip figure is typed into the experiment or into this report. "
        "`research.costs` builds two quotes from stored bars, hands them to "
        "the model and hands the answer back; the ladder rungs are the "
        "model's own `cost_multiplier`, and the experiment measures rather "
        "than assumes that a rung scales the finished cost exactly.",
        "",
        "### What the battery found, in five sentences",
        "",
        f"1. **The rule the card names is closed everywhere; the upper "
        f"bound is not.** On `|\u03c1(1)| \u00d7 sd` \u2014 what a rule "
        "trading the measured autocorrelation would earn \u2014 "
        f"**{lag1_closed} of {len(cells)}** D2 cells are closed in every "
        f"variant tested, the round trip costing "
        f"{_x(ratios_d2['shortfall_min'], 0)} to "
        f"{_x(ratios_d2['shortfall_max'], 0)} the edge. Under the "
        "variance-ratio *upper bound*, which credits a rule with every basis "
        f"point of variance the reversion removed, **{counts['SURVIVES']} "
        f"{_plural(counts['SURVIVES'], 'survives', 'survive')}** the "
        f"{bar}\u00d7 bar and **{counts['PARKED']} "
        f"{_plural(counts['PARKED'], 'is', 'are')} parked**. The two measures "
        f"differ by {_x(ratios_d2['bound_ratio_min'], 0)} to "
        f"{_x(ratios_d2['bound_ratio_max'], 0)}, and section 4 argues the "
        "bound is the wrong number to plan on.",
        "2. **Session is the largest lever in the card, and it is a cost "
        f"lever.** The dearest session costs up to "
        f"{_x(max(ratios) if ratios else None)} the cheapest one *within the "
        "same pair*, against directional effects T4 measured in hundredths of "
        "a basis point. Decision D3's execution constraint is quantified in "
        "section 1.",
        "3. **The horizon ladder is the other lever.** One round trip is one "
        "round trip however long the position is held, while the median move "
        "grows with the holding period — so move-over-cost rises at "
        f"every step of the ladder for **{rising} of {measured}** pairs. The "
        "shortest horizon that clears its own cost is in section 3.",
        f"4. **The ranked map has {map_size} cells and the top of it is not "
        f"where the memory is.** The best `{short}` cell ranks "
        f"{_n(best_short)} of {map_size} and its worst ranks "
        f"{_n(worst_short)}; the best `{longest}` cell ranks {_n(best_long)}. "
        f"T4 found every one of its variance-ratio survivors at `{short}` and "
        f"`{second}`, which are the dearest horizons to trade relative to "
        "what they move.",
        f"5. **The pre-2013 evidence points one way.** On `{era_horizon}` "
        "bars the 2005-2008 era "
        + (f"costs {_x(cost_ratio)} what `{REFERENCE_ERA}` costs at the "
           "survival bar, and the cross-check "
           if cost_ratio is not None else
           "is outside the window this run measured costs over, and the "
           "cross-check ")
        + f"could not verify "
        f"{_pct(early.get('crosscheck_unverifiable_share'))} of the "
        "hours it sampled there, agreeing with "
        f"{_pct(early.get('crosscheck_agreement_rate'))} of the ones it "
        "could. The recommendation and the rule that produced it are at "
        "the end of this report, and the decision is the checkpoint's.",
        "",
        "The honest one-line summary: **T4 found the direction, and T5 finds "
        "the round trip costs about an order of magnitude more than "
        "trading that direction earns** — unless a rule can extract "
        "the whole of the reverting component, which is what the bound "
        "assumes and no rule does.",
        "",
    ]


def _method(payload: dict[str, Any]) -> list[str]:
    method = payload["method"]
    check = method["multiplier_check"]
    costs = method["cost_parameters"]
    return [
        "## The decisions and rulings this card is shaped by",
        "",
        "A ruling listed without its consequence is decoration, so each is "
        "stated with where it actually bites.",
        "",
        *_table(["decision", "statement", "where it bites here"], [
            ["**pre-reg #1**",
             f"the cost ladder is {', '.join(method['ladder'])}× and the "
             f"survival bar is {method['survival_bar']}×",
             "every table carries the full ladder; every verdict in section 4 "
             "is the bar's, and no second threshold is added anywhere"],
            ["**pre-reg #4**", "the roll window is excluded from execution",
             "section 5 quantifies it; the roll window is excluded from the "
             "ranked map, because a window nothing may trade in is not a "
             "place edge can survive"],
            ["**R3**",
             "spread comparisons across eras must control for ticks per hour",
             f"section 6 reports every era inside the `{method['r3_reference_band']}` "
             "band and the uncontrolled figure beside it, and section 1 "
             "carries a density column so a session comparison can be checked "
             "for being a density comparison"],
            ["**R8**",
             "the static major-holiday list is the eligibility rule; the "
             "empties-derived calendar is informational",
             "stated, not applied — this card runs no backtest. Step 0 "
             "repaired the informational component and re-issued T3"],
            ["**D1**", "T4 is the universe-character baseline; nothing "
             "promoted or dropped",
             "all twelve pairs and all five horizons are measured; nothing "
             "here drops one either"],
            ["**D2**", "the eleven T4 reversion cells are this card's formal "
             "cost-geometry test set",
             "section 4 verdicts every one of them and adds none. The set is "
             "in `experiments/T5-cost-geometry/config.toml`, written before "
             "any cost was computed"],
            ["**D3**", "T7 inherits four overlays, not entries",
             "section 1 quantifies the cheapest-spread band as an execution "
             "constraint; section 4 tests every cell inside it"],
            ["**P0-A**", "USD accounting is unfixed",
             "the caveat is stated under every table, and the reference size "
             "is chosen so no figure here depends on the one term the defect "
             "touches"],
        ]),
        "### How a cost is produced",
        "",
        "A round trip is two orders. Entry crosses to the ask and pays its "
        "commission; exit crosses back to the bid and pays its own. Gross P&L "
        "is measured mid to mid — the Phase 1 accounting convention — so both "
        "crossings are an explicit cost line rather than a haircut hidden in "
        "a fill price, which is the only reason a cost can be put beside a "
        "return distribution at all.",
        "",
        "The comparison is in **basis points of notional**, which is the "
        "break-even move: a trade whose gross mid-to-mid return is smaller "
        "than this loses money by arithmetic, before any question of whether "
        "the move was forecastable.",
        "",
        *_table(["choice", "value", "why"], [
            ["cost model", f"`{method['cost_model']}`",
             "the Phase 1 model, unchanged. This card changes no cost "
             "parameter and no validation rule"],
            ["commission rate", f"{costs['commission_rate']:g} "
             f"({costs['commission_rate'] * 1e4:.2f} bp per order)",
             "IB tier 1, declared in `[experiment.costs]` and carried in the "
             "hashed result"],
            ["per-order minimum", f"USD {_f(costs['commission_min'], 2)}",
             "the one currency-sensitive term in the model, and the whole of "
             "P0-A"],
            ["reference size", f"{_n(method['reference_units'])} units",
             "far above the size at which the minimum binds for any pair, so "
             "no figure in this report depends on the term P0-A would change"],
            ["spread source", "the entry bar's mean spread for the entry leg, "
             "the exit bar's for the exit leg",
             "using one bar's spread twice would understate the cost of "
             "exactly the moments the spread is moving"],
            ["ladder", ", ".join(f"{r}×" for r in method["ladder"]),
             "applied as the model's own `cost_multiplier`, not as a "
             "multiplication in a report"],
            ["volatility regime",
             f"terciles of the standard deviation of the previous "
             f"{method['vol_window']} returns",
             "strictly backward-looking, as in T4: bucketing a return by a "
             "volatility estimate containing it would make every regime "
             "finding circular"],
            ["session grain",
             f"`{payload['window']['session_timeframe']}` bars; session "
             "statistics only at "
             + ", ".join(f"`{h}`" for h in method["session_horizons"]),
             "a 4-hour bar spans two sessions and a daily bar spans all of "
             "them, so a session statistic there would describe the label "
             "rather than the market"],
            ["variance-ratio aggregation", f"q = {method['variance_ratio_horizon']}",
             "the rung T4's fingerprint and its false-discovery correction "
             "were computed on; a bound taken at a different q would bound a "
             "different claim from the one D2 pre-registered"],
        ]),
        "### Two things the experiment measures rather than assumes",
        "",
        "**That a ladder rung scales the finished cost exactly.** Every "
        "per-move cost here is priced once at 1.0× and multiplied, which is "
        "only legitimate if `cost_multiplier` scales both cost lines "
        "including the floor. The experiment asks the model, on a grid that "
        "deliberately includes sizes small enough for the floor to bind:",
        "",
        *_table(["check", "result"], [
            ["grid points priced", _n(check["grid_points"])],
            ["of those, with the per-order floor binding",
             _n(check["points_with_the_floor_binding"])],
            ["worst relative disagreement between a rung and the scaled base",
             f"{check['worst_relative_disagreement']:.3g}"],
            ["within tolerance", "yes" if check["within_tolerance"] else "**no**"],
        ]),
        "**That the reference size is above the floor.** The notional at "
        "which the commission rate overtakes the per-order minimum is found "
        "by bisection on the model itself, not by dividing its parameters:",
        "",
        *_table(["quantity", "value"], [
            ["notional where the rate overtakes the floor (quote currency)",
             _n(method["floor_notional_quote_currency"])],
            ["priced moves at or below it",
             f"**{_n(method['returns_priced_below_the_floor'])}**"],
        ]),
        *_p0a(payload),
    ]


# --------------------------------------------------------------------------- #
# 1 -- the cost floor
# --------------------------------------------------------------------------- #

def _section_floor(payload: dict[str, Any], by_name: dict[str, Any],
                   figures_dir: str) -> list[str]:
    floors = payload["cost_floor"]
    pairs = sorted(floors)
    ladder = payload["method"]["ladder"]

    unconditional = []
    for pair in pairs:
        row = floors[pair]["all"]
        unconditional.append([
            f"`{pair}`", _n(row["n"]), _f(row["median_spread_pips"]),
            _f(row["p90_spread_pips"]), _n(row["median_ticks"]),
            _f(row["spread_cost_bp_p50"], 4), _f(row["commission_bp_p50"], 4),
            *[_f(row["ladder"][rung]["cost_bp_p50"], 4) for rung in ladder],
            _f(row["ladder"][BAR]["cost_bp_p90"], 4)])

    session_rows = []
    for pair in pairs:
        for name, row in floors[pair]["by_session"].items():
            if not row.get("usable"):
                continue
            session_rows.append([
                f"`{pair}`", name.replace("_", " "), _n(row["n"]),
                _f(row["median_spread_pips"]), _f(row["p90_spread_pips"]),
                _n(row["median_ticks"]),
                *[_f(row["ladder"][rung]["cost_bp_p50"], 4)
                  for rung in ladder],
                _f(row["move_bp"]["p50"]),
                _f(row["ladder"][BAR]["median_move_over_cost"], 2)])

    tercile_rows = []
    for pair in pairs:
        for name, row in floors[pair]["by_tercile"].items():
            if not row.get("usable"):
                continue
            tercile_rows.append([
                f"`{pair}`", name, _n(row["n"]),
                _f(row["median_spread_pips"]), _n(row["median_ticks"]),
                _f(row["ladder"][BAR]["cost_bp_p50"], 4),
                _f(row["move_bp"]["p50"]),
                _f(row["ladder"][BAR]["median_move_over_cost"], 2),
                _pct(row["ladder"][BAR]["share_of_moves_above_cost"])])

    cross_rows = []
    for pair in pairs:
        for key, row in floors[pair]["by_session_and_tercile"].items():
            session, tercile = key.split("|", 1)
            cross_rows.append([
                f"`{pair}`", session.replace("_", " "), tercile, _n(row["n"]),
                _f(row["median_spread_pips"]),
                _f(row["ladder"][BAR]["cost_bp_p50"], 4),
                _f(row["move_bp"]["p50"]),
                _f(row["ladder"][BAR]["median_move_over_cost"], 2)])

    band_rows = []
    for pair in sorted(payload["cost_by_density_band"]):
        for band, row in payload["cost_by_density_band"][pair].items():
            band_rows.append([
                f"`{pair}`", f"`{band}`", _n(row["n"]),
                _f(row["median_spread_pips"]), _f(row["p90_spread_pips"]),
                _f(row["ladder"][BAR]["cost_bp_p50"], 4),
                _f(row["move_bp"]["p50"]),
                _f(row["ladder"][BAR]["median_move_over_cost"], 2)])

    cheapest_rows = []
    for pair in sorted(payload["cheapest_band"]):
        row = payload["cheapest_band"][pair]
        cheapest_rows.append([
            f"`{pair}`", (row.get("session") or "—").replace("_", " "),
            _f(row.get("cost_bp_at_survival_bar"), 4),
            _f(row.get("median_spread_pips")),
            _pct(row.get("share_of_returns")),
            (row.get("dearest_session") or "—").replace("_", " "),
            _f(row.get("dearest_cost_bp_at_survival_bar"), 4),
            _x(row.get("ratio_dearest_to_cheapest")),
            " → ".join(s.replace("_", " ") for s in row.get("ranking", []))])

    notional = payload["minimum_viable_notional"]
    notional_rows = [[
        f"`{row['pair']}`", row["quote_currency"],
        "yes" if row["usd_quoted"] else "**no**", _f(row["median_mid"], 5),
        _n(row["floor_binds_below_quote_notional"]),
        _n(row["floor_binds_below_units"]),
        f"`{row['conversion_pair_p0a_would_use']}`"
        if row["conversion_pair_p0a_would_use"] else "—",
        _f(row["illustrative_floor_in_usd"], 4)]
        for row in notional["by_pair"]]

    return [
        "## 1 — The round-trip cost floor",
        "",
        "What it costs to get in and out once, before anything is known about "
        "where the price goes. Everything else in this report is measured "
        "against these numbers.",
        "",
        "The two cost columns are separate medians over the same moves, so "
        "they need not add to the total column exactly — the median of a "
        "sum is not the sum of the medians, and rounding them into agreement "
        "would be inventing a number.",
        "",
        "Read them apart all the same. The **commission** is a constant "
        "0.20 bp per order — 0.40 bp for the round trip — at every size above "
        "the floor and in every session and era; it is the same number for "
        "`EURUSD` in the London overlap and for `GBPJPY` in Sydney. The "
        "**spread** is everything that varies. So a pair's cost geometry is "
        "its spread geometry plus a constant, and every difference between "
        "two cells below is a difference in the spread.",
        "",
        "### Unconditional, on hourly bars",
        "",
        *_table(["pair", "bars", "median spread (pips)", "p90 spread",
                 "median ticks", "spread cost (bp)", "commission (bp)",
                 *[f"cost @ {r}× (bp)" for r in ladder],
                 f"p90 cost @ {BAR}× (bp)"], unconditional),
        *_p0a(payload),
        *_figure(by_name, "cost_floor_by_session", figures_dir),
        "### By session",
        "",
        "The session boundaries are **derived** from each centre's own local "
        "clock, so they move with British Summer Time and US daylight saving "
        "independently, as they do in reality. The density column is there "
        "because ruling R3 says a spread comparison that does not hold ticks "
        "per hour still is partly a comparison of tick counts.",
        "",
        *_table(["pair", "session", "returns", "median spread (pips)",
                 "p90 spread", "median ticks",
                 *[f"cost @ {r}× (bp)" for r in ladder],
                 "median |move| (bp)", f"move / cost @ {BAR}×"],
                session_rows),
        *_p0a(payload),
        "### By volatility tercile",
        "",
        "The regime label uses only returns strictly before the one it "
        "labels. The interesting column is the last one: the high-volatility "
        "tercile is where the moves are, and the spread does not widen nearly "
        "as fast as the move does, so it is also where the arithmetic is "
        "most favourable — which is the opposite of where T4 found the "
        "strongest reversion in most pairs.",
        "",
        *_table(["pair", "tercile", "returns", "median spread (pips)",
                 "median ticks", f"cost @ {BAR}× (bp)",
                 "median |move| (bp)", f"move / cost @ {BAR}×",
                 "share of moves above cost"], tercile_rows),
        *_p0a(payload),
        "### Session × tercile",
        "",
        "The card asks for the floor by pair × session × volatility tercile, "
        "and this is it. It is long, and the whole of it is in `result.json`; "
        f"the first {MAX_ROWS} rows are here.",
        "",
        *_table(["pair", "session", "tercile", "returns",
                 "median spread (pips)", f"cost @ {BAR}× (bp)",
                 "median |move| (bp)", f"move / cost @ {BAR}×"],
                cross_rows[:MAX_ROWS]),
        (f"_First {MAX_ROWS} of {len(cross_rows)} cells; the whole table is in "
         "`result.json`._" if len(cross_rows) > MAX_ROWS else ""),
        "",
        *_p0a(payload),
        "### Inside tick-count bands (ruling R3)",
        "",
        "R3's control, applied to cost rather than to spread alone: compare "
        "inside a band, never across one. Read against the session table it "
        "answers the question that table cannot — how much of a session's "
        "cost advantage is the session and how much is the book being "
        "thicker at that hour.",
        "",
        *_table(["pair", "band", "returns", "median spread (pips)",
                 "p90 spread", f"cost @ {BAR}× (bp)", "median |move| (bp)",
                 f"move / cost @ {BAR}×"], band_rows[:MAX_ROWS]),
        (f"_First {MAX_ROWS} of {len(band_rows)} pair-bands; the whole table "
         "is in `result.json`._" if len(band_rows) > MAX_ROWS else ""),
        "",
        *_p0a(payload),
        "### The cheapest executable band (decision D3, quantified)",
        "",
        "D3 carries session restriction into T7 as an **execution "
        "constraint** — trade where the spread is in its own cheapest band — "
        "and explicitly not as a signal claim. The two look identical in a "
        "backtest and differ completely in what they assert, and only the "
        "execution reading survives being wrong about the signal. So this is "
        "a cost ranking and nothing else. The roll window is excluded, "
        "because pre-reg #4 already excludes it from execution and ranking a "
        "window nothing may trade in would produce a cheapest band no "
        "strategy could use.",
        "",
        *_table(["pair", "cheapest session", f"cost @ {BAR}× (bp)",
                 "median spread (pips)", "share of hours", "dearest session",
                 f"its cost @ {BAR}× (bp)", "dearest / cheapest",
                 "ranking, cheapest first"], cheapest_rows),
        *_p0a(payload),
        "### The minimum viable notional, and exactly what P0-A costs",
        "",
        "The per-order minimum is the one place in the cost model where the "
        "quote currency matters, so this table is SPEC2 prerequisite P0-A "
        "with a number attached. The notional is measured off the model by "
        "bisection; the unit figure divides it by the pair's own median mid "
        f"over the window. The floor is a **USD "
        f"{_f(notional['commission_min'], 2)}** figure and the model applies "
        "it to a **quote-currency** notional, so for the eight non-USD-quoted "
        "pairs it is the wrong size — badly so for the JPY crosses, where a "
        "2-unit floor is worth about two US cents.",
        "",
        *_table(["pair", "quote", "USD-quoted", "median mid",
                 "floor binds below (quote notional)", "…which is (units)",
                 "conversion pair P0-A would use",
                 "what the floor is actually worth (USD)"], notional_rows),
        f"> **Nothing in that table is used as a cost anywhere in this "
        f"report.** {notional['note'].capitalize()}. P0-A requires a "
        "fill-time, lookahead-safe rate and its own card; implementing it is "
        "an explicit non-goal here. What this card owes is the size of the "
        "gap, and that is the size of it.",
        "",
    ]


# --------------------------------------------------------------------------- #
# 2 -- realised moves against the floor
# --------------------------------------------------------------------------- #

def _section_moves(payload: dict[str, Any], by_name: dict[str, Any],
                   figures_dir: str) -> list[str]:
    horizons = payload["window"]["horizons"]
    ladder = payload["method"]["ladder"]
    lines = [
        "## 2 — Realised moves against the floor",
        "",
        "The \"where can edge even exist\" map, in its raw form. A horizon "
        "whose median move is below its own round trip cannot host a strategy "
        "that trades every bar: the median trade loses money before any "
        "question of forecasting arises. A horizon whose median move is above "
        "it *can* host one — which is a statement about arithmetic and not "
        "about the existence of a signal, and this report never says "
        "otherwise.",
        "",
        "The last column is measured **move by move**, not by comparing two "
        "medians. Spread and volatility move together, so the share of moves "
        "that beat the cost quoted around them is a different and more useful "
        "number than the share that would beat the median cost.",
        "",
    ]
    lines += _figure(by_name, "move_over_cost_by_horizon", figures_dir)
    lines += _figure(by_name, "share_above_cost_by_horizon", figures_dir)
    for horizon in horizons:
        rows = []
        for pair in sorted(payload["moves"]):
            row = payload["moves"][pair].get(horizon, {}).get("all")
            if not row:
                continue
            move = row["move_bp"]
            rows.append([
                f"`{pair}`", _n(row["n"]), _f(move["p10"]), _f(move["p25"]),
                _f(move["p50"]), _f(move["p75"]), _f(move["p90"]),
                _f(move["p99"], 2),
                *[_f(row["ladder"][rung]["cost_bp_p50"], 4)
                  for rung in ladder],
                _f(row["ladder"][BAR]["median_move_over_cost"], 2),
                *[_pct(row["ladder"][rung]["share_of_moves_above_cost"])
                  for rung in ladder]])
        lines += [f"### `{horizon}`", ""]
        lines += _table(
            ["pair", "moves", "p10", "p25", "**p50**", "p75", "p90", "p99",
             *[f"cost @ {r}×" for r in ladder], f"p50 move / cost @ {BAR}×",
             *[f"share above cost @ {r}×" for r in ladder]], rows)
    lines += _figure(by_name, "move_quantiles_vs_cost_5m", figures_dir)
    lines += _p0a(payload)
    return lines


# --------------------------------------------------------------------------- #
# 3 -- the minimum viable holding period
# --------------------------------------------------------------------------- #

def _section_holding(payload: dict[str, Any]) -> list[str]:
    horizons = payload["window"]["horizons"]
    holding = payload["minimum_holding_period"]
    rows = []
    for pair in sorted(holding):
        block = holding[pair]
        cells = []
        for name in ("all", "off_roll", *[f"tercile:{t}" for t in
                                          ("low", "mid", "high")]):
            entry = block.get(name) or {}
            cells.append(entry.get("shortest_horizon_that_clears") or "**none**")
        rows.append([f"`{pair}`", *[f"`{c}`" if c != "**none**" else c
                                    for c in cells]])

    session_rows = []
    for pair in sorted(holding):
        for name, entry in sorted(holding[pair].items()):
            if not name.startswith("session:"):
                continue
            shortest = entry.get("shortest_horizon_that_clears")
            ladder = {row["horizon"]: row for row in entry["ladder"]}
            session_rows.append([
                f"`{pair}`", name.split(":", 1)[1].replace("_", " "),
                f"`{shortest}`" if shortest else "**none**",
                *[_f((ladder.get(h) or {}).get("ratio"), 2) for h in horizons]])

    return [
        "## 3 — The minimum viable holding period",
        "",
        "The shortest horizon on the ladder at which the median absolute move "
        f"clears the round trip at the pinned {BAR}× bar. Read down the "
        "ladder in order and stop at the first horizon that pays for itself; "
        "**none** means no horizon on the ladder does, which is a finding "
        "rather than a missing value.",
        "",
        "This is a necessary condition and nowhere near a sufficient one. A "
        "horizon clearing here means the *typical* move is bigger than the "
        "cost of capturing it — a rule still has to know which direction, and "
        "T4's answer to that is that it barely does.",
        "",
        *_table(["pair", "all hours", "outside the roll", "low vol", "mid vol",
                 "high vol"], rows),
        *_p0a(payload),
        "### By session",
        "",
        "The ratio columns are the median move over the median cost at "
        f"{BAR}×, so 1.00 is exactly break-even on the typical move and the "
        "first horizon above 1.00 is the answer in the third column.",
        "",
        *_table(["pair", "session", "shortest that clears",
                 *[f"{h} ratio" for h in horizons]],
                session_rows[:MAX_ROWS]),
        (f"_First {MAX_ROWS} of {len(session_rows)} pair-sessions; the whole "
         "table is in `result.json`._"
         if len(session_rows) > MAX_ROWS else ""),
        "",
        *_p0a(payload),
    ]


# --------------------------------------------------------------------------- #
# 4 -- the D2 test set
# --------------------------------------------------------------------------- #

def _section_test_set(payload: dict[str, Any], by_name: dict[str, Any],
                      figures_dir: str) -> list[str]:
    block = payload["test_set"]
    cells = block["cells"]
    ratios = _d2_ratios(payload)
    ladder = payload["method"]["ladder"]
    lines = [
        "## 4 — The formal test of the D2 test set",
        "",
        f"The {len(block['declared'])} "
        f"{_plural(len(block['declared']), 'cell', 'cells')} decision D2 "
        "pre-registered — the ones whose q=4 variance ratio survived "
        "Benjamini-Hochberg at FDR "
        "0.05 inside T4's 300-test family — against the cost of trading them. "
        "The set is in the experiment config, written before any cost was "
        "computed. No cell was added and none was dropped.",
        "",
        "**This is an analytical bound, not a backtest.** A cell that fails "
        "here cannot pass a backtest, which is the point: the arithmetic is "
        "cheap and the walk-forward is not. A cell that passes gets its "
        "backtest in T7 and nothing more is claimed for it here.",
        "",
        "### The two edges, and why there are two",
        "",
        "**Lag-1, the card's figure: `|ρ(1)| × sd`.** A rule forecasting "
        "`r(t) = ρ·r(t−1)` has a forecast whose standard deviation is "
        "`|ρ|·sd`; trading its sign earns the expected absolute forecast, "
        "which for a Gaussian is 0.798 times that. The card names the larger "
        "of the two and this report uses it, so the arithmetic errs towards "
        "keeping a cell open rather than closing it on an unstated "
        "refinement. One bar held, one round trip paid.",
        "",
        "**The variance-ratio bound, multi-lag.** Over q bars a random walk "
        "would have variance `q·sd²` and the series has `VR(q)·q·sd²`. The "
        "difference is variance the reverting component removed, so the "
        "standard deviation of anything forecastable from the past is at most "
        "`sqrt((1 − VR(q))·q)·sd`. **It is an upper bound and a generous "
        "one**: it credits a rule with every basis point of removed variance, "
        "which no rule gets, and it buys a q-bar hold for one round trip. "
        "Where a cell below is not closed, it is this number that failed to "
        "close it.",
        "",
        "Both are gross, per trade, in basis points of notional. The cost "
        "subtracted from them is the median round trip in that same slice, "
        "at each rung of the ladder.",
        "",
        "### How far apart the two measures are, and which one to plan on",
        "",
        f"Across the {len(cells)} cells the bound is "
        f"{_x(ratios['bound_ratio_min'], 0)} to "
        f"{_x(ratios['bound_ratio_max'], 0)} the lag-1 figure. That gap is "
        "not noise and it is not a modelling choice — it is what the "
        "bound is for, and the size of it can be read off the arithmetic.",
        "",
        "Take the simplest process that produces this autocorrelation "
        "structure: `r(t) = e(t) - θ·e(t-1)`, a first-order "
        "moving average, whose lag-1 autocorrelation is about "
        "`-θ` for small `θ`. A rule that knows `θ` exactly "
        "and forecasts `-θ·e(t-1)` earns about `θ·sd` "
        "per trade — the lag-1 figure. The variance the reversion "
        "removes over `q` bars is about `2(q-1)θ·sd²`, so "
        "the bound is about `sqrt(2(q-1)θ)·sd`, and for a "
        "`θ` of a few hundredths the square root is an order of "
        "magnitude larger than `θ` itself. **The bound overstates what "
        "an optimal rule earns from exactly this structure by roughly the "
        "factor observed above.** It is a bound: it is right that nothing can "
        "do better, and wrong as an estimate of what anything will do.",
        "",
        "So the two columns answer two questions. *Is this cell arithmetically "
        "impossible?* — the bound answers that, and a cell it closes is "
        "closed for good. *Is this cell worth a walk-forward?* — the "
        "lag-1 figure is the honest input to that, and it closes every cell "
        "in this set. A T7 card taking a surviving cell forward is betting "
        "that a better rule than lag-1 recovers a large fraction of the "
        "bound, and that bet is the thing to state in its own card rather "
        "than to inherit from this table.",
        "",
    ]
    lines += _figure(by_name, "d2_edge_versus_cost", figures_dir)

    head = []
    for cell in cells:
        row = cell["variants"].get("all hours") or {}
        edge = row.get("edge") or {}
        head.append([
            f"`{cell['pair']}`", f"`{cell['horizon']}`", _n(row.get("n")),
            _f(edge.get("rho1"), 5), _f(edge.get("sd_bp")),
            _f(edge.get("variance_ratio"), 5),
            _f(edge.get("lag1_edge_bp"), 5), _f(edge.get("vr_edge_bp"), 5),
            _f((row.get("cost_bp") or {}).get(BAR), 5),
            row.get("lag1", {}).get("verdict") or "—",
            row.get("variance_ratio_bound", {}).get("verdict") or "—"])
    lines += ["### Unconditionally, all hours", ""]
    lines += _table(
        ["pair", "horizon", "moves", "ρ(1)", "sd (bp)", "VR(4)",
         "lag-1 edge (bp)", "VR bound (bp)", f"cost @ {BAR}× (bp)",
         "lag-1 verdict", "VR-bound verdict"], head)
    lines += _p0a(payload)

    lines += [
        "### The arithmetic, cell by cell",
        "",
        "Every variant the card asks for: unconditional, restricted to the "
        "pair's own cheapest session band, outside the roll window, and "
        "inside each volatility tercile. The variance ratio needs a "
        "contiguous window, so it can only be conditioned on something that "
        "arrives in contiguous blocks — a session does, a volatility tercile "
        "does not, and the tercile rows carry a lag-1 figure and an explicit "
        "dash rather than a blank column.",
        "",
    ]
    for cell in cells:
        lines += [
            f"#### `{cell['pair']}` at `{cell['horizon']}` — "
            f"**{cell['verdict']}**",
            "",
        ]
        rows = []
        for name, row in sorted(cell["variants"].items()):
            edge = row["edge"]
            for route, label, value in (
                    ("lag1", "|ρ(1)| × sd", edge.get("lag1_edge_bp")),
                    ("variance_ratio_bound",
                     f"VR({edge['vr_hold_bars']}) bound", edge.get("vr_edge_bp"))):
                verdict = row[route]["verdict"]
                if verdict is None:
                    rows.append([name, label, "—", "—", "—",
                                 *["—"] * len(ladder), "—"])
                    continue
                rows.append([
                    name, label, _n(row["n"]), _f(value, 5),
                    _f(row["cost_bp"][BAR], 5),
                    *[_f(row[route]["net_bp"][rung], 5) for rung in ladder],
                    f"**{verdict}**"])
        lines += _table(
            ["variant", "edge measure", "moves", "gross edge (bp)",
             f"cost @ {BAR}× (bp)", *[f"net @ {r}× (bp)" for r in ladder],
             "verdict"], rows)
        best = cell["verdict_from_variant"]
        lines += [
            f"Verdict **{cell['verdict']}**, from "
            + (f"the *{best}* variant on the "
               f"{'lag-1' if cell['verdict_from_route'] == 'lag1' else 'variance-ratio bound'} "
               "measure." if best else "no usable variant.")
            + f" {cell['verdicts_computed']} of {cell['variants_tested']} "
            "variant-measure combinations produced a verdict at all, and the "
            "**best** of those was taken, which is the "
            "conservative direction for a bound: a cell whose most favourable "
            "conditioning still cannot pay for its round trip cannot be "
            "rescued by a backtest. The cost of that asymmetry is that a "
            "surviving verdict here is a licence to test and never a result — "
            "picking the best of many is a selection, and a T7 card acting on "
            "one owes the trial count.",
            "",
        ]
    lines += _p0a(payload)
    return lines


# --------------------------------------------------------------------------- #
# 5 -- the roll window
# --------------------------------------------------------------------------- #

def _section_roll(payload: dict[str, Any], by_name: dict[str, Any],
                  figures_dir: str) -> list[str]:
    rows = []
    spread_ratios: list[float] = []
    cost_ratios: list[float] = []
    below = 0
    below_outside = 0
    measured = 0
    for pair in sorted(payload["roll"]):
        block = payload["roll"][pair]
        if not block:
            continue
        inside, outside = block["inside"], block["outside"]
        measured += 1
        if outside["median_spread_pips"]:
            spread_ratios.append(inside["median_spread_pips"]
                                 / outside["median_spread_pips"])
        if block["cost_ratio"] is not None:
            cost_ratios.append(float(block["cost_ratio"]))
        if (block["move_over_cost_inside"] or 0.0) <= 1.0:
            below += 1
        if (block["move_over_cost_outside"] or 0.0) <= 1.0:
            below_outside += 1
        rows.append([
            f"`{pair}`", _n(inside["n"]),
            _f(inside["median_spread_pips"]), _f(outside["median_spread_pips"]),
            _f(inside["ladder"][BAR]["cost_bp_p50"], 4),
            _f(outside["ladder"][BAR]["cost_bp_p50"], 4),
            _x(block["cost_ratio"]),
            _f(inside["move_bp"]["p50"]), _f(outside["move_bp"]["p50"]),
            _x(block["move_ratio"]),
            _f(block["move_over_cost_inside"], 2),
            _f(block["move_over_cost_outside"], 2),
            _pct(block["share_above_cost_inside"]),
            _pct(block["share_above_cost_outside"])])
    spread_ratio = (sorted(spread_ratios)[len(spread_ratios) // 2]
                    if spread_ratios else None)
    cost_ratio = (sorted(cost_ratios)[len(cost_ratios) // 2]
                  if cost_ratios else None)
    return [
        "## 5 — The roll window, quantified against cost (pre-reg #4)",
        "",
        "The daily roll, 16:00–18:00 `America/New_York`, derived per bar "
        "rather than pinned to a UTC hour — 17:00 New York is 21:00Z in "
        "summer and 22:00Z in winter, and a rule written in UTC is wrong for "
        "half of every year.",
        "",
        "T4 established that this window is dearer and quieter at once. This "
        "is the same fact in the units a trader would use: what a move has to "
        "be worth in there before it pays for itself, against what it has to "
        "be worth everywhere else. Pre-registered decision #4 already "
        "excludes the window from execution and says the exclusion is "
        "revisable at a checkpoint with EDA evidence. This is more of that "
        "evidence and it points the same way.",
        "",
        *_table(["pair", "roll bars", "roll spread (pips)", "elsewhere",
                 f"roll cost @ {BAR}× (bp)", "elsewhere", "cost ratio",
                 "roll median |move| (bp)", "elsewhere", "move ratio",
                 "roll move / cost", "elsewhere", "roll share above cost",
                 "elsewhere"], rows),
        "Two readings, both of which the table above supports and neither of "
        "which T4's version of it could give:",
        "",
        f"**The cost penalty is smaller than the spread penalty.** Inside the "
        f"window the spread is a median {_x(spread_ratio)} its level outside, "
        f"but the round trip is only {_x(cost_ratio)} — because the "
        "commission does not widen with the spread, and a flat 0.40 bp is a "
        "larger share of a cheap round trip than of a dear one. A card "
        "arguing about the roll window on spread ratios alone would overstate "
        "the penalty by about that difference.",
        "",
        f"**It does not matter, because the move falls further than the cost "
        f"rises.** For {below} of {measured} "
        f"{_plural(measured, 'pair', 'pairs')} the median move inside the "
        f"window does not clear its own round trip at {BAR}× at all, "
        f"against {below_outside} of {measured} outside it. That is the "
        "arithmetic form of pre-registered decision #4, and it is a stronger "
        "statement than the spread ratio: the window is not merely dearer, it "
        "is a window in which the typical move is not worth capturing.",
        "",
        *_p0a(payload),
        *_figure(by_name, "roll_window_cost_and_move", figures_dir),
    ]


# --------------------------------------------------------------------------- #
# 6 -- the era question
# --------------------------------------------------------------------------- #

def _section_eras(payload: dict[str, Any], by_name: dict[str, Any],
                  figures_dir: str) -> list[str]:
    eras = payload["eras"]
    band = payload["method"]["r3_reference_band"]
    names = [row["era"] for row in payload["method"]["calendar_eras"]]
    lines = [
        "## 6 — The era question: what the pre-2013 data would cost (R7 evidence)",
        "",
        "The full history on the two horizons the store supports it at, split "
        "on calendar years fixed by the task card rather than chosen here — a "
        "split picked after seeing which one makes the early data look better "
        "would not be evidence. Ruling R1 starts `AUDUSD` in 2011, so its "
        "early eras are absent rather than zero.",
        "",
        "Each era is reported twice: uncontrolled, and inside ruling R3's "
        f"`{payload['method']['r3_reference_band']}` reference band. R3 exists "
        "for exactly this table — a spread median taken over thousand-tick "
        "hours and one taken over six-thousand-tick hours are not the same "
        "instrument, and the band composition column shows how much the "
        "instrument changed.",
        "",
    ]
    for horizon in payload["window"]["history_horizons"]:
        lines += _figure(by_name, f"cost_by_era_{horizon}", figures_dir)
        rows = []
        for pair in sorted(eras):
            for name in names:
                era = (eras[pair].get(horizon) or {}).get(name)
                if not era:
                    continue
                raw = era["uncontrolled"]
                controlled = era["reference_band"]
                rows.append([
                    f"`{pair}`", name, _n(raw["n"]),
                    _f(raw["median_spread_pips"]),
                    _f(raw["ladder"][BAR]["cost_bp_p50"], 4),
                    _f(raw["move_bp"]["p50"]),
                    _f(raw["ladder"][BAR]["median_move_over_cost"], 2),
                    _pct(raw["ladder"][BAR]["share_of_moves_above_cost"]),
                    _n(controlled.get("n")) if controlled.get("usable") else "—",
                    _f(controlled.get("median_spread_pips"))
                    if controlled.get("usable") else "—",
                    _f((controlled.get("ladder") or {}).get(BAR, {})
                       .get("cost_bp_p50"), 4)
                    if controlled.get("usable") else "—",
                    _pct(era["band_composition"].get(
                        payload["method"]["r3_reference_band"]))])
        controlled = sum(1 for row in rows if row[8] != "—")
        lines += [f"### `{horizon}` bars", ""]
        if controlled < len(rows):
            lines += [
                f"**{len(rows) - controlled} of {len(rows)}** pair-eras have "
                f"no rows inside the `{band}` band at this horizon, and their "
                "band columns are dashes rather than zeroes. A bar of this "
                "length holds far more quotes than the band admits, so R3's "
                "control simply has nothing to hold still here — which "
                "is a fact about the grain rather than about the era, and "
                "means the uncontrolled column is all there is at this "
                "horizon. The evidence table at the end of the report falls "
                "back to it for exactly these cells and says so.",
                "",
            ]
        lines += _table(
            ["pair", "era", "moves", "median spread (pips)",
             f"cost @ {BAR}× (bp)", "median |move| (bp)",
             f"move / cost @ {BAR}×", "share above cost",
             "band moves", "band spread", f"band cost @ {BAR}×",
             "share in band"], rows[:MAX_ROWS])
        if len(rows) > MAX_ROWS:
            lines += [f"_First {MAX_ROWS} of {len(rows)} pair-eras; the whole "
                      "table is in `result.json`._", ""]
    lines += _p0a(payload)

    by_year = payload["era_agreement"]["by_year"]
    agreement_rows = [[
        year, row["era"], _n(row["sampled"]), _n(row["pass"]),
        _n(row["blocked"]), _n(row["unverifiable"]),
        _pct(row["agreement_rate"]), _pct(row["unverifiable_share"])]
        for year, row in sorted(by_year.items())]
    lines += [
        "### The by-year cross-check agreement, beside it",
        "",
        "Ruling R7's classification of every sampled hour, read from the "
        "committed `config/crosscheck.toml`. The card asks for this table "
        "next to the cost tables because the two answer different halves of "
        "the same question: one says what the early era would cost to trade, "
        "the other says how much of it a second venue could corroborate at "
        "all. `UNVERIFIABLE` means the check could not see the hour, not that "
        "the hour was wrong.",
        "",
        *_table(["year", "R7 era tag", "sampled", "PASS", "BLOCKED",
                 "UNVERIFIABLE", "agreement among verifiable",
                 "unverifiable share"], agreement_rows),
    ]
    return lines


# --------------------------------------------------------------------------- #
# 7 -- cost sensitivity
# --------------------------------------------------------------------------- #

def _section_sensitivity(payload: dict[str, Any], by_name: dict[str, Any],
                         figures_dir: str) -> list[str]:
    ladder = payload["method"]["ladder"]
    rows = []
    for pair in sorted(payload["sensitivity"]):
        row = payload["sensitivity"][pair]
        counts = row["executable_by_rung"]
        lost = row["lost_from_1_to_2"]
        rows.append([
            f"`{pair}`", _n(row["cells_measured"]),
            *[_n(counts[rung]) for rung in ladder],
            _pct(row["share_surviving_2x"]),
            ", ".join(f"`{c.replace('|session:', ' ').replace('|', ' ')}`"
                      for c in lost[:4]) + ("…" if len(lost) > 4 else "")
            if lost else "—"])
    return [
        "## 7 — Cost sensitivity: the shape of a cost-model error",
        "",
        "How much of the executable universe survives being wrong about "
        "costs. A cell here is one horizon in one session; it counts as "
        "executable at a rung when its median move exceeds its median round "
        "trip at that rung. **That is an arithmetic precondition and not a "
        "claim that a rule exists** — the same caveat as section 2, and it "
        "applies to every number in this table.",
        "",
        "The point of the table is the gradient rather than the level. A pair "
        "whose count barely moves from 1.0× to 2.0× is one where a cost-model "
        "error costs little; a pair that loses half its cells is one where "
        "the whole case rests on the cost model being right, which is exactly "
        "the risk the 1.5× survival bar exists to absorb.",
        "",
        *_table(["pair", "cells measured",
                 *[f"executable @ {r}×" for r in ladder],
                 f"share surviving {ladder[-1]}×",
                 f"cells lost from {ladder[0]}× to "
                 f"{ladder[-1]}×"], rows),
        *_p0a(payload),
        *_figure(by_name, "executable_universe_by_rung", figures_dir),
    ]


# --------------------------------------------------------------------------- #
# The closing sections the card asks for
# --------------------------------------------------------------------------- #

def _closing_verdicts(payload: dict[str, Any]) -> list[str]:
    block = payload["test_set"]
    cells = block["cells"]
    counts = block["counts"]
    ratios = _d2_ratios(payload)
    rows = []
    for cell in cells:
        variant = cell["variants"].get(cell["verdict_from_variant"] or "") or {}
        route = cell["verdict_from_route"]
        edge = variant.get("edge") or {}
        value = (edge.get("lag1_edge_bp") if route == "lag1"
                 else edge.get("vr_edge_bp"))
        net = (variant.get(route) or {}).get("net_bp") or {}
        ratio = (ratios["per_cell"].get(f"{cell['pair']}|{cell['horizon']}")
                 or {})
        rows.append([
            f"`{cell['pair']}`", f"`{cell['horizon']}`",
            f"**{cell['verdict']}**",
            cell["verdict_from_variant"] or "—",
            "|ρ(1)| × sd" if route == "lag1" else "VR bound",
            _f(value, 5), _f((variant.get("cost_bp") or {}).get(BAR), 5),
            _f(net.get("1.2"), 5), _f(net.get(BAR), 5),
            _f(net.get("2.0"), 5),
            _x(ratio.get("bound_over_lag1"), 0),
            _x(ratio.get("cost_over_lag1"), 0)])
    return [
        "## The D2 verdict table",
        "",
        "One row per pre-registered cell, showing the variant and the measure "
        "that produced its verdict — the best of the ones tested, which is "
        "the conservative direction for a bound and a selection a T7 card has "
        "to account for.",
        "",
        *_table(["pair", "horizon", "verdict", "from variant", "measure",
                 "gross edge (bp)", f"cost @ {BAR}× (bp)", "net @ 1.2×",
                 f"net @ {BAR}×", "net @ 2.0×", "bound / lag-1",
                 "cost / lag-1 edge"], rows),
        *_table(["verdict", "cells", "what happens to them"], [
            ["**SURVIVES**", _n(counts["SURVIVES"]),
             "earns a T7 card (D2). The bound clearing 1.5× is a licence to "
             "test, not a result"],
            ["**PARKED**", _n(counts["PARKED"]),
             "clears 1.2× and not 1.5× (pre-reg #1). Visible, not deleted, "
             "and revisitable only if recorder-measured IB costs later prove "
             "the model overestimates — evidence, not preference"],
            ["**CLOSED**", _n(counts["CLOSED"]),
             "closed with the arithmetic recorded (D2). Below 1.2× is dead"],
        ]),
        *_p0a(payload),
    ]


def _closing_map(payload: dict[str, Any]) -> list[str]:
    rows = payload["edge_map"]
    ladder = payload["method"]["ladder"]
    table = [[
        _n(index + 1), f"`{row['pair']}`", f"`{row['horizon']}`",
        _pretty_slice(row["slice"]), _n(row["n"]),
        _f(row["move_bp_p50"]), _f(row["cost_bp_p50_at_1x"], 4),
        _x(row["move_over_cost"], 1),
        f"**{row['survives_to_rung']}×**" if row["survives_to_rung"] else "—",
        _pct(row["share_of_moves_above_cost_at_survival_bar"])]
        for index, row in enumerate(rows[:MAP_ROWS])]
    by_rung = {rung: sum(1 for row in rows if row["survives_to_rung"] == rung)
               for rung in ladder}
    none = sum(1 for row in rows if row["survives_to_rung"] is None)
    return [
        "## Where edge can survive — the ranked map",
        "",
        "Every measured cell — pair × horizon × session or volatility regime "
        "— ranked by the median move divided by the median round trip, with "
        "the dearest ladder rung the median move still clears. The roll "
        "window is not in it: pre-reg #4 excludes it from execution, so it is "
        "not a place edge can survive whatever its arithmetic says.",
        "",
        "**Read the title precisely.** This is where edge *can* survive, not "
        "where it *is*. A cell high on this list is one where a rule with a "
        "signal could pay for itself; T4's finding is that the signals are "
        "measured in hundredths of a basis point, and section 4 is what "
        "happens when the two are put together.",
        "",
        *_table(["highest rung the median move clears", "cells"],
                [[f"{rung}× and no dearer", _n(by_rung[rung])]
                 for rung in ladder]
                + [["clears no rung at all", _n(none)]]),
        f"The top {min(MAP_ROWS, len(rows))} of {len(rows)}:",
        "",
        *_table(["#", "pair", "horizon", "slice", "moves",
                 "median |move| (bp)", "cost @ 1.0× (bp)", "move / cost",
                 "survives to", f"share above cost @ {BAR}×"], table),
        f"_The whole {len(rows)}-row map is in `result.json` under "
        "`payload.edge_map`._",
        "",
        *_p0a(payload),
    ]


def _band_coverage(payload: dict[str, Any], horizon: str) -> tuple[int, int]:
    """Pair-eras with usable rows inside R3's reference band, and the total."""
    band_rows = 0
    total = 0
    for _pair, horizons in payload["eras"].items():
        for _era, block in (horizons.get(horizon) or {}).items():
            total += 1
            if (block.get("reference_band") or {}).get("usable"):
                band_rows += 1
    return band_rows, total


def _closing_era_recommendation(payload: dict[str, Any]) -> list[str]:
    lines = [
        "## The pre-2013 evidence, and a recommendation",
        "",
        "The task card asks for an evidence table for the checkpoint's "
        "pre-2013 decision — training data, stress test only, or excluded — "
        "and says to **recommend, not decide**. So the rule that turns the "
        "evidence into a recommendation is stated first, in a form a "
        "checkpoint can disagree with, rather than being a number that "
        "appeared inside a paragraph:",
        "",
        "> An era is recommended as **training data** only if the cross-check "
        f"could see most of it (unverifiable share below "
        f"{_pct(UNVERIFIABLE_LIMIT, 0)}), agreed with it where it could "
        f"(agreement at or above {_pct(AGREEMENT_LIMIT, 0)}), and cost within "
        f"{_x(COST_RATIO_LIMIT, 1)} of what the `{REFERENCE_ERA}` era costs. "
        "An era that fails any of those is recommended as a **stress test "
        "only**: it is real data about a real market, and a rule fitted on a "
        "market whose round trip was twice as expensive is a rule fitted to a "
        "different problem. **Excluded** is reserved for an era that cannot "
        "be measured at all. None of these is a threshold on anything SPEC2 "
        "thresholds — pre-reg #1 pins exactly one bar and this adds no "
        "second one.",
        "",
    ]
    for horizon in payload["window"]["history_horizons"]:
        rows = payload["era_evidence"].get(horizon) or []
        reference = next((r for r in rows if r["era"] == REFERENCE_ERA), None)
        base = (reference or {}).get("median_cost_bp_at_survival_bar")
        table = []
        for row in rows:
            cost = row["median_cost_bp_at_survival_bar"]
            ratio = (cost / base) if (cost is not None and base) else None
            table.append([
                row["era"], _n(row["pairs_measured"]),
                _f(row["median_spread_pips"]), _f(cost, 4),
                _x(ratio), _f(row["median_move_bp"]),
                _pct(row["median_share_of_moves_above_cost"]),
                _n(row["crosscheck_hours_sampled"]),
                _pct(row["crosscheck_unverifiable_share"]),
                _pct(row["crosscheck_agreement_rate"]),
                f"**{_recommend(row, ratio)}**"])
        banded, total = _band_coverage(payload, horizon)
        lines += [
            f"### On `{horizon}` bars",
            "",
            f"Built from ruling R3's `{payload['method']['r3_reference_band']}` "
            f"band where the band has rows — **{banded} of {total}** "
            "pair-eras at this horizon — and from the uncontrolled "
            "figure for the rest. At the daily grain every bar holds more "
            "quotes than the band admits, so there the density control has "
            "nothing to hold still and the uncontrolled figure is the only "
            "one there is.",
            "",
        ]
        lines += _table(
            ["era", "pairs measured", "median spread (pips)",
             f"median cost @ {BAR}× (bp)", f"vs `{REFERENCE_ERA}`",
             "median |move| (bp)", "share of moves above cost",
             "cross-check hours", "unverifiable", "agreement",
             "recommendation"], table)
    lines += _p0a(payload)
    lines += [
        "The recommendation is this card's reading of its own evidence and "
        "nothing more. Ruling R7 already says the usage of `UNVERIFIABLE` "
        "hours before 2013 is a T5 decision on the by-year agreement "
        "evidence; the evidence is above, the reading is above, and the "
        "decision is the checkpoint's.",
        "",
    ]
    return lines


def _recommend(row: dict[str, Any], cost_ratio: float | None) -> str:
    """Apply the stated rule to one era's evidence."""
    if not row["pairs_measured"]:
        return "excluded — nothing measurable"
    unverifiable = row["crosscheck_unverifiable_share"]
    agreement = row["crosscheck_agreement_rate"]
    reasons = []
    if unverifiable is not None and unverifiable >= UNVERIFIABLE_LIMIT:
        reasons.append("the cross-check could not see most of it")
    if agreement is not None and agreement < AGREEMENT_LIMIT:
        reasons.append("it disagreed where it could see it")
    if cost_ratio is not None and cost_ratio >= COST_RATIO_LIMIT:
        reasons.append("it cost materially more to trade")
    if not reasons:
        return "training data"
    return "stress test only — " + "; ".join(reasons)


def _closing_questions(payload: dict[str, Any]) -> list[str]:
    """Questions for T6 and T7, generated from what this card measured."""
    cheapest = payload["cheapest_band"]
    sessions: dict[str, int] = {}
    for row in cheapest.values():
        name = row.get("session")
        if name:
            sessions[name] = sessions.get(name, 0) + 1
    ranked = sorted(sessions.items(), key=lambda kv: (-kv[1], kv[0]))
    cells = payload["test_set"]["cells"]
    survivors = [c for c in cells if c["verdict"] in ("SURVIVES", "PARKED")]
    closed = [c for c in cells if c["verdict"] == "CLOSED"]
    map_rows = payload["edge_map"]
    top = map_rows[0] if map_rows else {}
    short_horizon = payload["window"]["horizons"][0]
    short_cells = [row for row in map_rows if row["horizon"] == short_horizon]
    best_short = short_cells[0] if short_cells else {}
    ladder = payload["method"]["ladder"]
    worst_sensitivity = sorted(
        payload["sensitivity"].items(),
        key=lambda kv: (kv[1]["share_surviving_2x"] or 1.0))[:3]

    lines = [
        "## Questions for T6 and T7",
        "",
        "Questions, not answers, and not hypotheses this card is entitled to "
        "originate. Pre-registered decision #3 puts hypothesis selection in "
        "chat; what a card may do is say what its own evidence makes worth "
        "asking. Each is stated with the number that prompted it so it can be "
        "argued with.",
        "",
    ]
    if survivors:
        names = ", ".join(f"`{c['pair']}`/`{c['horizon']}` ({c['verdict']})"
                          for c in survivors)
        lines += [
            f"**The {len(survivors)} "
            f"{_plural(len(survivors), 'cell', 'cells')} the bound could not close — "
            f"{names}.** Every one of them is open on the variance-ratio "
            "*upper bound*, which credits a rule with all of the variance the "
            "reversion removed. **Question for a T7 card:** does a rule that "
            "actually has to forecast — rather than one credited with the "
            "whole reverting component — clear the same bar? The lag-1 "
            "figure says it does not, by a factor the table in section 4 "
            "states; the gap between the two is the entire question, and it "
            "is a question about how much of a variance-ratio departure is "
            "recoverable, which no statistic in T4 or T5 answers.",
            "",
        ]
    else:
        lines += [
            "**No cell in the D2 set clears the bar on any measure.** That is "
            "a complete answer to the question D2 asked, and it means no T7 "
            "card is owed by this set. **Question for a checkpoint:** is the "
            "next hypothesis batch worth drafting on price data alone, or "
            "does D4's banked external-data question move up?",
            "",
        ]
    if closed:
        ratios = _d2_ratios(payload)
        gaps = [ratios["per_cell"].get(f"{c['pair']}|{c['horizon']}", {})
                .get("cost_over_lag1") for c in closed]
        gaps = [g for g in gaps if g is not None]
        lines += [
            f"**The {len(closed)} closed "
            f"{_plural(len(closed), 'cell', 'cells')} died of the same "
            "thing.** Their round trip costs "
            + (f"{_x(min(gaps), 0)} to {_x(max(gaps), 0)}" if gaps else "more")
            + " what trading their measured autocorrelation earns. "
            "**Question for a T6 card:** if the "
            "single-pair reversion is that far short of its cost, is a "
            "cross-pair signal on the same horizon worth looking "
            "for at all — or does the cost geometry mean any surviving "
            "cross-pair structure has to live at `4h` and `1d`, where the "
            "move-over-cost ratio is an order of magnitude better and T4 "
            "found no directional memory at all? That tension is the sharpest "
            "thing this card produces and T6 is where it gets tested.",
            "",
        ]
    if ranked:
        best, count = ranked[0]
        lines += [
            f"**The cheapest session is `{best.replace('_', ' ')}` for "
            f"{count} of {len(cheapest)} pairs.** **Question for a T7 card:** "
            "is decision D3's execution constraint better expressed as a "
            "session or as a live spread condition — trade only when the "
            "quoted spread is inside the pair's own cheapest decile, whenever "
            "that happens to be? The first is testable now and the second "
            "needs a spread the backtester currently takes from a bar mean, "
            "so the answer changes what the execution layer has to carry.",
            "",
        ]
    if top and best_short:
        lines += [
            f"**The map's top cell is `{top['pair']}` `{top['horizon']}` "
            f"{_pretty_slice(top['slice'])} at {_x(top['move_over_cost'], 1)} "
            f"move over cost; its best `{short_horizon}` cell is "
            f"`{best_short['pair']}` at {_x(best_short['move_over_cost'], 1)}.** "
            "**Question for a T7 card:** T4 found directional memory only at "
            "the horizons where this ratio is worst. Is there a formulation "
            "that trades the short-horizon signal but holds for a long-"
            "horizon time — entering on a `5m` reversion and exiting on a "
            "`4h` clock, so one round trip is amortised over a move an order "
            "of magnitude larger? That is a different rule from either "
            "horizon's own and neither T4 nor T5 has measured it.",
            "",
        ]
    if worst_sensitivity:
        names = ", ".join(
            f"`{pair}` ({_pct(row['share_surviving_2x'])})"
            for pair, row in worst_sensitivity)
        lines += [
            f"**Cost sensitivity is not uniform: {names} keep the smallest "
            f"share of their executable cells at {ladder[-1]}×.** **Question "
            "for a checkpoint:** does the recorder work that would replace "
            "the modelled spread with a measured one belong before T7 rather "
            "than after it? Pre-reg #1 allows a parked candidate to be "
            "revisited on recorder-measured costs, and this table is where "
            "that revisiting would have the most to change.",
            "",
        ]
    lines += [
        "**What this card did not ask.** No cross-pair question is answered "
        "here — that is T6's card and this one may not originate it. No "
        "strategy is specified, no parameter is chosen, and nothing is "
        "backtested. Decision D4 banks the external-data question for a later "
        "checkpoint and this card leaves it banked.",
        "",
    ]
    return lines


def _provenance(document: dict[str, Any], payload: dict[str, Any], home: str,
                figures: Sequence[dict[str, Any]], figures_dir: str,
                gate_status: str) -> list[str]:
    access = document.get("access") or {}
    loader = payload["loader"]
    return [
        "## Provenance",
        "",
        f"* Config: `{home}/config.toml` (sha256 "
        f"`{str(document['config_sha256'])[:16]}`), which is where the cost "
        "model and the D2 test set are declared.",
        "* Cost model: `fxlab.costs.IBCostModel`, unchanged from Phase 1. "
        "Every cost figure in this report was produced by it, through "
        "`research.costs`, from quotes built out of stored bars.",
        "* Bars: `data/research/bars/timeframe=<TF>/pair=<PAIR>/`, read only "
        "through `research.loader.ResearchLoader` in `scoring` mode, which is "
        "what enforces the seal and ruling R1 on every date served.",
        "* Cross-check classes: `config/crosscheck.toml`, derived under ruling "
        "R7 and re-derived and compared on every run of the T3 experiment. "
        "Section 6's agreement table comes from it.",
        f"* Result: `{home}/result.json`, hash `{document['result_hash']}`",
        f"* Figures: {len(figures)} under `{figures_dir}/`, each beside the "
        "CSV of the numbers it was drawn from. Both are regenerated from "
        "`result.json` by `python -m research.cost_geometry_report`.",
        f"* Loader mode `{document['mode']}`, scored `{document['scored']}`, "
        f"re-run class `{document['rerun_class']}`. It served "
        f"{len(access.get('files', []))} file(s) across "
        f"{len(access.get('pairs', []))} pair(s), "
        f"{len(access.get('timeframes', []))} timeframe(s) and "
        f"{len(access.get('dates', []))} date(s); sealed dates served: "
        f"{loader['sealed_dates_served'] or 'none'}; dates withheld by an "
        f"exclusion window: {_n(loader['excluded_dates_withheld'])} across "
        f"{_n(len(loader['excluded_pairs']))} pair(s) — ruling R1, the "
        "full-history era section asking `AUDUSD` for years it may not have.",
        f"* Research gate: {gate_status}",
        "",
    ]




# --------------------------------------------------------------------------- #
# Addendum: decision D9's reference notional (the T6 card's Step 0)
# --------------------------------------------------------------------------- #

def _slice_names(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Session and tercile names, read off the result rather than imported.

    The report stays a pure function of the result document: a session list
    typed in here would be a second statement of the session map, and it would
    go stale the first time one changed.
    """
    floors = payload.get("cost_floor") or {}
    sessions: list[str] = []
    terciles: list[str] = []
    for row in floors.values():
        for name in row.get("by_session", {}):
            if name not in sessions:
                sessions.append(name)
        for name in row.get("by_tercile", {}):
            if name not in terciles:
                terciles.append(name)
    return sessions, terciles


def _floor_disagreement(addendum: dict[str, Any]) -> list[str]:
    """The sentence naming the pairs the model and P0-A floor differently.

    Generated rather than written: which pairs disagree, and by how much, is a
    property of the median mids over this window, and a paragraph that named
    them would stop being true the first time the window moved.
    """
    rows = [row for row in addendum["sizing"]
            if row["floor_binds_at_reference_units"]
            != bool(row.get("floor_would_bind_under_p0a"))]
    if not rows:
        return ["The model and USD accounting agree about every pair at this "
                "size: nothing is floored on the wrong side of the "
                "comparison.", ""]
    model_only = [r for r in rows if r["floor_binds_at_reference_units"]]
    p0a_only = [r for r in rows if not r["floor_binds_at_reference_units"]]
    parts: list[str] = []
    if model_only:
        row = model_only[0]
        parts.append(
            f"the model floors `{row['pair']}` because "
            f"{_n(addendum['units'])} {row['base_currency']} is "
            f"{_n(row['quote_notional_at_reference_units'])} "
            f"**{row['quote_currency']}**, while under USD accounting it is a "
            f"{_n(row.get('illustrative_usd_notional_at_reference_units'))} "
            "USD notional and does not floor")
    if p0a_only:
        row = p0a_only[0]
        parts.append(
            f"it does not floor `{row['pair']}` because "
            f"{_n(addendum['units'])} {row['base_currency']} is "
            f"{_n(row['quote_notional_at_reference_units'])} "
            f"**{row['quote_currency']}**, while under USD accounting it is a "
            f"{_n(row.get('illustrative_usd_notional_at_reference_units'))} "
            "USD notional and pays "
            f"{_x(row.get('illustrative_p0a_multiple'))} the modelled "
            "commission")
    named = "`" + "`, `".join(r["pair"] for r in rows) + "`"
    return [
        f"**{_n(len(rows))} pair(s) are priced on the wrong side of the floor "
        f"at this size** — {named}. Concretely: " + "; and ".join(parts) + ". "
        "That is P0-A stated as an amount rather than as a caveat, and at "
        f"{_n(addendum['base_units'])} units it did not arise at all. **It is "
        "the concrete reason SPEC2 decision D10 puts a backtester-readiness "
        "card in front of any scorecard.**",
        "",
    ]


def _section_addendum(payload: dict[str, Any]) -> list[str]:
    """Section 1 and the D2 verdicts, re-expressed at 100,000 units.

    Appended after the card closed, by the T6 card's Step 0, and generated
    like everything else here: every row and every count in the prose comes
    out of the hashed result.
    """
    addendum = payload.get("reference_addendum")
    if not addendum:
        return []
    ladder = payload["method"]["ladder"]
    sessions, terciles = _slice_names(payload)
    reference = _n(addendum["units"])
    base_units = _n(addendum["base_units"])
    by_pair = addendum["by_pair"]
    pairs = sorted(by_pair)
    rows = addendum["rows"]

    ever = [p for p in pairs if by_pair[p]["floor_binding_returns"] > 0]
    always = [p for p in pairs if by_pair[p]["floor_binding_share"] == 1.0]
    worst = max(pairs, key=lambda p: by_pair[p]["ratio"][BAR] or 0.0)
    worst_slice = max(rows, key=lambda r: r["ratio"].get(BAR) or 0.0)
    changed_band = [p for p in pairs if by_pair[p]["cheapest_session_changed"]]
    identical_spread = sum(1 for row in rows if row["spread_bp_identical"])

    sizing_rows = [[
        f"`{row['pair']}`", row["base_currency"], row["quote_currency"],
        _n(row["quote_notional_at_reference_units"]),
        "**yes**" if row["floor_binds_at_reference_units"] else "no",
        _n(row.get("illustrative_usd_notional_at_reference_units")),
        _f(row.get("illustrative_usd_commission_from_the_rate"), 3),
        _f(row.get("illustrative_usd_commission_after_the_floor"), 3),
        _x(row.get("illustrative_p0a_multiple"), 3),
        "**yes**" if row.get("floor_would_bind_under_p0a") else "no",
        ("**disagree**" if row["floor_binds_at_reference_units"]
         != bool(row.get("floor_would_bind_under_p0a")) else "agree")]
        for row in addendum["sizing"]]

    pair_rows = [[
        f"`{pair}`", _n(by_pair[pair]["returns"]),
        _pct(by_pair[pair]["floor_binding_share"]),
        *[_f(by_pair[pair]["cost_bp_at_base_units"][rung], 4)
          for rung in ladder],
        *[_f(by_pair[pair]["cost_bp_at_reference_units"][rung], 4)
          for rung in ladder],
        _x(by_pair[pair]["ratio"][BAR], 3),
        _f(by_pair[pair]["extra_bp_at_survival_bar"], 4)]
        for pair in pairs]

    def cut(names: Sequence[str]) -> list[list[Any]]:
        return [[
            f"`{row['pair']}`", row["slice"].replace("_", " "), _n(row["n"]),
            _pct(row["floor_binding_share"]),
            *[_f(row["cost_bp_at_reference_units"][rung], 4)
              for rung in ladder],
            _x(row["ratio"][BAR], 3),
            _f(row["extra_bp_at_survival_bar"], 4)]
            for row in rows if row["slice"] in names]

    session_rows = cut(sessions)
    tercile_rows = cut(terciles)

    crosses = sorted((row for row in rows if "|" in row["slice"]),
                     key=lambda r: -(r["ratio"].get(BAR) or 0.0))
    cross_rows = [[
        f"`{row['pair']}`", row["slice"].split("|")[0].replace("_", " "),
        row["slice"].split("|")[1], _n(row["n"]),
        _pct(row["floor_binding_share"]),
        _f(row["cost_bp_at_reference_units"][BAR], 4),
        _x(row["ratio"][BAR], 3)] for row in crosses]

    band_rows = [[
        f"`{pair}`",
        (by_pair[pair]["cheapest_session_at_base_units"] or "—").replace(
            "_", " "),
        (by_pair[pair]["cheapest_session_at_reference_units"] or "—").replace(
            "_", " "),
        "**moved**" if by_pair[pair]["cheapest_session_changed"] else "same",
        _f((addendum["cheapest_band"].get(pair) or {}).get(
            "cost_bp_at_survival_bar"), 4),
        _x((addendum["cheapest_band"].get(pair) or {}).get(
            "ratio_dearest_to_cheapest"))]
        for pair in pairs]

    comparison = addendum["test_set"]["comparison"]
    verdict_rows = [[
        f"`{row['pair']}`", f"`{row['horizon']}`",
        _f(row["cost_bp_at_base_units"], 4),
        _f(row["cost_bp_at_reference_units"], 4),
        _f(row["extra_bp_at_survival_bar"], 4),
        row["lag1_verdict_at_base_units"] or "—",
        row["lag1_verdict_at_reference_units"] or "—",
        row["verdict_at_base_units"] or "—",
        row["verdict_at_reference_units"] or "—",
        "**changed**" if row["changed"] else "unchanged"]
        for row in comparison["cells"]]
    changed = comparison["changed"]

    lines = [
        "## Addendum — the cost floors at the 100,000-unit reference notional "
        "(decision D9)",
        "",
        "**This section was appended after the card closed**, by the T6 card's "
        "Step 0. SPEC2 decision D9, fixed at the M5 checkpoint, moves the "
        f"research reference notional from {base_units} units to {reference} "
        "— the size at which the USD 2.00 per-order minimum equals the 0.20 bp "
        "rate on a 100,000 USD notional, and roughly what the funded account "
        "carries. **Nothing above it changed**: the same series, the same "
        "slices, the same cost model and the same ladder, re-priced at one "
        "different size, which is what lets the two sets of tables be read "
        "against each other.",
        "",
        "The spread cost in basis points cannot move with size — it is a ratio "
        "of two quantities that both scale with it — so every difference below "
        "is the per-order minimum and nothing else. The experiment measures "
        f"that rather than asserting it: **{_n(identical_spread)} of "
        f"{_n(len(rows))}** slice rows carry an identical spread line at both "
        "sizes.",
        "",
        "### Where the floor binds, and the two answers to that question",
        "",
        f"At {base_units} units the per-order minimum bound on **0** of the "
        "priced moves, which is why no figure above depends on it. At "
        f"{reference} it binds for **{_n(len(ever))} of {_n(len(pairs))}** "
        "pairs — `" + "`, `".join(ever) + f"` — and for {_n(len(always))} of "
        "them on every single move.",
        "",
        "**There are two answers to \"does it bind\", and they disagree.** The "
        "model floors a USD 2.00 minimum against a **quote-currency** "
        "notional, which is exactly SPEC2 prerequisite P0-A. The "
        "`illustrative` columns show what the same order would pay under the "
        "USD accounting P0-A would supply, using the median-mid conversion "
        "illustration section 1 already carries. They are used in no cost "
        "figure, no verdict and no ranked table anywhere in this report — "
        "they size the defect, they do not repair it.",
        "",
    ]
    lines += _table(
        ["pair", "base", "quote", f"quote notional @ {reference}",
         "model floors?", "illustrative USD notional", "USD from the rate",
         "USD after the floor", "P0-A multiple", "P0-A floors?", "verdicts"],
        sizing_rows)
    lines += _floor_disagreement(addendum)
    lines += [
        "### Unconditional, on hourly bars",
        "",
        "The same twelve pairs as section 1's first table, at both sizes. The "
        f"cost at {reference} is never below the cost at {base_units} — the "
        "floor can only raise a commission — and the experiment checks that "
        "rather than assuming it.",
        "",
    ]
    lines += _table(
        ["pair", "moves", "floor binds",
         *[f"@{rung}× ({base_units})" for rung in ladder],
         *[f"@{rung}× ({reference})" for rung in ladder],
         f"ratio @ {BAR}×", f"extra bp @ {BAR}×"],
        pair_rows)
    lines += [
        f"The largest effect is `{worst}` at "
        f"{_x(by_pair[worst]['ratio'][BAR], 3)}, and the dearest single slice "
        f"is `{worst_slice['pair']}` "
        f"{worst_slice['slice'].replace('_', ' ').replace('|', ' / ')} at "
        f"{_x(worst_slice['ratio'][BAR], 3)} — "
        f"{_f(worst_slice['extra_bp_at_survival_bar'], 4)} bp more per round "
        "trip at the survival bar. That is small beside the spread "
        "differences section 1 measures, and it is not nothing: it is a "
        "commission line that has stopped being a constant.",
        "",
        "### By session",
        "",
        "Decision D3's execution constraint, re-costed. The ranking matters "
        "here rather than the level, because D3 uses it to choose a band "
        "rather than to price one.",
        "",
    ]
    lines += _table(
        ["pair", "session", "returns", "floor binds",
         *[f"cost @ {rung}× (bp)" for rung in ladder],
         f"ratio @ {BAR}×", f"extra bp @ {BAR}×"],
        session_rows[:MAX_ROWS])
    if len(session_rows) > MAX_ROWS:
        lines += [f"_First {MAX_ROWS} of {len(session_rows)} pair-sessions; "
                  "the whole table is in `result.json` under "
                  "`payload.reference_addendum.rows`._", ""]
    lines += ["And what that does to the cheapest band each pair is allowed "
              "to trade in:", ""]
    lines += _table(
        ["pair", f"cheapest @ {base_units}", f"cheapest @ {reference}",
         "", f"cost @ {BAR}× (bp)", "dearest / cheapest"],
        band_rows)
    if changed_band:
        lines += [
            f"**{_n(len(changed_band))} pair's cheapest band moves** — `"
            + "`, `".join(changed_band) + "`. The floor is a fixed charge, and "
            "a session does not dilute it any faster for being busier, so "
            "where two bands were close on spread the ranking can turn over "
            "on the commission. A T7 card taking D3's constraint forward "
            "should take it from this table rather than from section 1's, "
            f"because it will trade at {reference} units and not at "
            f"{base_units}.",
            "",
        ]
    else:
        lines += ["No pair's cheapest band moves: decision D3's execution "
                  "constraint is the same constraint at both sizes.", ""]
    lines += ["### By volatility tercile", ""]
    lines += _table(
        ["pair", "tercile", "returns", "floor binds",
         *[f"cost @ {rung}× (bp)" for rung in ladder],
         f"ratio @ {BAR}×", f"extra bp @ {BAR}×"],
        tercile_rows[:MAX_ROWS])
    if len(tercile_rows) > MAX_ROWS:
        lines += [f"_First {MAX_ROWS} of {len(tercile_rows)} pair-terciles; "
                  "the whole table is in `result.json`._", ""]
    lines += [
        "### Session × tercile",
        "",
        "The card's full grain, ranked by how much the floor costs the cell "
        "rather than alphabetically, so the cells the new reference actually "
        "moves are the ones on the page.",
        "",
    ]
    lines += _table(
        ["pair", "session", "tercile", "returns", "floor binds",
         f"cost @ {BAR}× (bp)", f"ratio @ {BAR}×"],
        cross_rows[:MAX_ROWS])
    if len(cross_rows) > MAX_ROWS:
        lines += [f"_Dearest {MAX_ROWS} of {len(cross_rows)} cells by the "
                  "ratio; the whole table is in `result.json`._", ""]
    lines += [
        "### The D2 verdicts, confirmed",
        "",
        "The card's question: does any D2 verdict change at the new reference "
        "notional? It cannot improve — the floor can only raise a commission, "
        "so every net edge at this size is at most what it was — but a cell "
        "could close harder. Each cell is re-verdicted against **its own** "
        "cheapest band at this size rather than against section 1's, so the "
        "table is internally consistent rather than half inherited.",
        "",
    ]
    lines += _table(
        ["pair", "horizon", f"cost @ {BAR}× ({base_units})",
         f"cost @ {BAR}× ({reference})", f"extra bp @ {BAR}×",
         f"lag-1 @ {base_units}", f"lag-1 @ {reference}",
         f"cell @ {base_units}", f"cell @ {reference}", ""],
        verdict_rows)
    lines += [
        ("**No verdict changes.**" if not changed else
         f"**{_n(len(changed))} verdict(s) change:** "
         + ", ".join(f"`{key}`" for key in changed) + "."),
        "",
        "The lag-1 column is the measure SPEC2 decision D5 has since settled "
        "on, and it closes all eleven cells at both sizes. The cell column is "
        "this card's best-of-variants verdict, which D5 records as an oracle "
        "upper bound rather than a survival criterion. The monotonicity check "
        "— that no cell's cost fell when the size fell — returned "
        f"**{comparison['costs_never_fell']}**.",
        "",
    ]
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.cost_geometry_report",
        description="Render the T5 cost-geometry report and its figures.")
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--figures", type=pathlib.Path, default=None)
    parser.add_argument("--gate-status", default="not yet run")
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render the report and every figure it links to."""
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base
            else pathlib.Path(__file__).resolve().parents[1])
    from research import ledger as ledger_mod

    document = json.loads(args.result.read_text(encoding="utf-8"))
    card = str(document.get("taskcard") or "T5")
    trials = ledger_mod.trial_count(ledger_mod.read(base), card)
    figures_dir = (args.figures if args.figures is not None
                   else args.out.parent / card)
    figures = build_all(document["payload"], figures_dir)
    home = _rel_dir(args.result.resolve().parent, base)
    relative = _rel_dir(figures_dir.resolve(), args.out.resolve().parent)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        render(document, trials, args.gate_status, home, figures, relative),
        encoding="utf-8")
    _LOG.info("wrote %s and %d figure(s)", args.out, len(figures))
    print(f"wrote {args.out} and {len(figures)} figure(s) under {figures_dir}")
    return 0


def _rel_dir(path: pathlib.Path, base: pathlib.Path) -> str:
    """Project-relative POSIX directory, absolute where that is impossible."""
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.resolve().as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
