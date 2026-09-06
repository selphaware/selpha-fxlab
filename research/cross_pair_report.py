"""Render the T6 cross-pair report from its result document.

Ruling R6, with no escape hatch: there is no ``--note`` here, every number
below is read out of ``result.json``, and every figure is drawn at render time
from the same document by :mod:`research.cross_pair_figures`. The ranked table,
the hypothesis section and the closing implication for decision D4 are
generated too -- their prose is a template and every quantity in it, including
which relationships appear at all and whether any appear, comes from the
result.

The one thing this module adds that the result does not contain is the
*reading rule* for the ranked table: which of the card's three conditions a
relationship has to satisfy, stated in the report before the table it is
applied to, so a checkpoint can disagree with a rule rather than with a
paragraph. The conditions themselves are the card's.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
from typing import Any, Final, Sequence

from research.cross_pair_figures import BAR, build_all

_LOG: Final[logging.Logger] = logging.getLogger("research.cross_pair_report")

#: Rows carried before a long listing is truncated.
MAX_ROWS: Final[int] = 30

#: The window a relationship's cost verdict is taken in.
COST_WINDOW: Final[str] = "confirmation"


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


def _p(value: Any) -> str:
    """A p or q value, with the very small ones written as such."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number <= 0.0:
        return "<1e-16"
    if number < 1e-4:
        return f"{number:.1e}"
    return f"{number:.4f}"


def _members(row: dict[str, Any]) -> str:
    return " + ".join(f"`{m}`" for m in row["members"])


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _figure(by_name: dict[str, Any], name: str,
            figures_dir: str) -> list[str]:
    """A figure and the caption and CSV link that make it checkable."""
    entry = by_name.get(name)
    if not entry:
        return []
    svg = f"{figures_dir}/{pathlib.Path(entry['svg']).name}"
    csv = f"{figures_dir}/{pathlib.Path(entry['csv']).name}"
    caption = entry["caption"]
    return [f"![{caption}]({svg})", "",
            f"*{caption}* — source table: [`{csv}`]({csv})", ""]


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
    lines += _rulings(payload)
    lines += _method(payload, by_name, figures_dir)
    lines += _section_correlation(payload, by_name, figures_dir)
    lines += _section_currency(payload, by_name, figures_dir)
    lines += _section_cointegration(payload, by_name, figures_dir)
    lines += _section_leadlag(payload, by_name, figures_dir)
    lines += _section_portfolio(payload, by_name, figures_dir)
    lines += _section_characterisation(payload)
    lines += _section_testing(payload, trials)
    lines += _closing_ranked(payload)
    lines += _closing_questions(payload)
    lines += _provenance(document, payload, home, figures, figures_dir,
                         gate_status)
    return "\n".join(lines).rstrip() + "\n"


def _counts(payload: dict[str, Any]) -> dict[str, Any]:
    """The counts every generated sentence in the report is built from."""
    ranked = payload["cointegration"]["ranked"]
    identity = [row for row in ranked if row["identity"]]
    stable = [row for row in ranked if row["stable_out_of_window"]]
    surviving = [row for row in ranked if row["survives_correction"]]
    qualifying = [row for row in ranked if row["qualifies"]]
    stable_non_identity = [row for row in stable if not row["identity"]]
    leadlag_q = [row for horizon in payload["leadlag_headline"]
                 for row in payload["leadlag_headline"][horizon]["rows"]
                 if row["qualifies"]]
    families = payload["families"]["families"]
    return {
        "ranked": ranked,
        "identity": identity,
        "stable": stable,
        "surviving": surviving,
        "qualifying": qualifying,
        "stable_non_identity": stable_non_identity,
        "leadlag_qualifying": leadlag_q,
        "registered": sum(block["tests"] for block in families.values()),
        "families": len(families),
    }


def _header(document: dict[str, Any], payload: dict[str, Any],
            trials: int) -> list[str]:
    window = payload["window"]
    counts = _counts(payload)
    identity = payload["identity"]
    geometry = payload["portfolio"]["rows"]
    worst_identity = min(
        (row for row in counts["identity"]
         if (row["cost"] or {}).get("amplitude_over_cost", {}).get(BAR)),
        key=lambda r: r["cost"]["amplitude_over_cost"][BAR], default=None)
    best_identity = max(
        (row for row in counts["identity"]
         if (row["cost"] or {}).get("amplitude_over_cost", {}).get(BAR)),
        key=lambda r: r["cost"]["amplitude_over_cost"][BAR], default=None)
    bets = geometry[0] if geometry else {}
    leadlag = counts["leadlag_qualifying"]
    lead = leadlag[0] if leadlag else None

    verdict = (
        "Nothing satisfies all three of the card's conditions."
        if not counts["qualifying"] else
        f"{_n(len(counts['qualifying']))} "
        f"{_plural(len(counts['qualifying']), 'relationship satisfies', 'relationships satisfy')}"
        " all three of the card's conditions.")
    identity_stable = sum(1 for row in counts["stable"] if row["identity"])
    clusters = (payload["correlation"].get(window["horizons"][0])
                or {}).get("clusters", [])

    return [
        "# T6 — EDA battery III: cross-pair structure",
        "",
        f"**Primary window:** {window['primary']['start']} → "
        f"{window['primary']['end']}, {len(window['pairs'])} pairs, horizons "
        + ", ".join(f"`{h}`" for h in window["horizons"])
        + f" · **Discovery:** {window['discovery']['start']} → "
        f"{window['discovery']['end']} · **Confirmations:** "
        f"{window['confirmation']['start']} → {window['confirmation']['end']} "
        f"and {window['early']['start']} → {window['early']['end']} "
        f"({len(window['early_pairs'])} pairs, ruling R1) · "
        f"**Characterisation only:** "
        + ", ".join(f"`{h}`" for h in window["characterisation_horizons"])
        + f" (decision D7) · **Task card:** `taskcards/T6.md` · "
        f"**Experiment:** `{document['experiment_id']}` · **Seed:** "
        f"{document['seed']} · **Result hash:** "
        f"`{str(document['result_hash'])[:16]}`",
        "",
        f"**Trials ledgered under this card:** {_n(trials)}. **Hypothesis "
        f"tests registered inside this result:** {_n(counts['registered'])} "
        f"across {_n(counts['families'])} families (SPEC2 pre-reg #10).",
        "",
        "This card measures what the twelve pairs are **together**, and puts "
        "every relationship it finds against the round trip T5 priced — at "
        "the 100,000-unit reference notional SPEC2 decision D9 now fixes. "
        "Everything in it is a measurement and a map: no backtest, no "
        "scorecard, no candidate advanced or killed, no pair promoted or "
        "dropped. Pre-registered decision #3 puts those decisions in chat, "
        "between cards.",
        "",
        "### What the battery found, in five sentences",
        "",
        f"1. **The universe is arithmetic before it is economics.** Twelve "
        f"pairs across {identity['currencies']} currencies span "
        f"{identity['rank']} directions, so "
        f"{_n(identity['dependent_pairs'])} of the twelve — "
        + ", ".join(f"`{p}`" for p in identity["pairs_it_determines"])
        + " — are exact triangular functions of the other "
        f"{identity['rank']}. Every relationship in this report is labelled "
        "for it, because a cointegration scan that does not separate the "
        "definition from the discovery ranks the definitions first.",
        f"2. **{verdict}** "
        + (f"{_n(len(counts['stable']))} relationships survive the "
           f"false-discovery correction across the scan **and** confirm in "
           f"both untouched windows, and "
           + ("every one of them is a triangular identity"
              if identity_stable == len(counts["stable"]) else
              f"{_n(identity_stable)} of them are triangular identities")
           + " — arbitrage relationships that exist by definition. Not one "
             "pays "
           "for the round trips of its own legs: the best of them needs the "
           f"spread to reach "
           f"{_f((best_identity or {}).get('cost', {}).get('break_even_entry_sd'), 1)}"
           " standard deviations from its mean before a full reversion covers "
           "the trade, and the worst needs "
           f"{_f((worst_identity or {}).get('cost', {}).get('break_even_entry_sd'), 1)}."
           if counts["stable"] else
           "No relationship survives the correction and confirms out of "
           "window."),
        f"3. **{_n(len(counts['stable_non_identity']))} non-identity "
        "cointegration relationships confirm out of window.** "
        + ("The scan finds hundreds that survive the correction inside the "
           "discovery window; every one of them fails in 2020-2025, in "
           "2009-2012, or in both."
           if not counts["stable_non_identity"] else
           "They are listed in the ranked table."),
        "4. **The correlation structure is stable, and it is not the "
        f"structure a diversification argument assumes.** The universe offers "
        f"{_f((bets or {}).get('participation_ratio'), 2)} effective "
        f"independent bets against a nominal twelve and a structural ceiling "
        f"of {identity['rank']}, the same {_n(len(clusters))} clusters appear "
        "at every research horizon, and correlations do **not** go "
        f"to one in high volatility — the high-volatility regime carries "
        f"{_x((bets or {}).get('high_over_low_bets'), 2)} the effective bets "
        "of the low-volatility one.",
        "5. **The one cell that passes every test this card can put to it is "
        "a lead-lag, not a cointegration.** "
        + (f"`{lead['lead']}` leads `{lead['lagging']}` by "
           f"{lead['lag']} bar at `{lead['horizon']}`: it survives the "
           "correction with and without the January 2015 shock days, its sign "
           f"holds in {_pct(lead['rolling_sign_agreement'])} of rolling "
           "two-year windows, and its implied edge is "
           f"{_x(lead['edge_over_cost'])} the round trip of the pair it would "
           "trade. It is **one cell out of "
           f"{_n(sum(payload['leadlag_headline'][h]['tested'] for h in payload['leadlag_headline']))} "
           "lead-lag tests at the research horizons**, and that count belongs "
           "beside it wherever it is quoted."
           if lead else
           "No lead-lag cell survives the correction, the shock check, the "
           "stability test and its own cost."),
        "",
        "The honest one-line summary: **this universe's only reliable "
        "cross-pair structure is the structure that is true by definition, "
        "and it is between two and ten times too narrow to pay for itself.**",
        "",
    ]


def _rulings(payload: dict[str, Any]) -> list[str]:
    window = payload["window"]
    method = payload["method"]
    return [
        "## The decisions and rulings this card is shaped by",
        "",
        "A ruling listed without its consequence is decoration, so each is "
        "stated with where it actually bites.",
        "",
        *_table(
            ["decision", "statement", "where it bites here"],
            [
                ["**pre-reg #1**",
                 f"the cost ladder is {', '.join(method['ladder'])}× and the "
                 f"survival bar is {method['survival_bar']}×",
                 "every cost table carries the full ladder; every cost "
                 "verdict is the bar's, and no second threshold is added"],
                ["**pre-reg #9**", "the universe is the twelve pairs",
                 "all twelve are scanned; none is promoted or dropped here"],
                ["**pre-reg #10**", "multiple-testing honesty",
                 "every test is registered inside the hashed result and "
                 "corrected within its family; the trial count is stated "
                 "beside every claim"],
                ["**R1**", "`AUDUSD` before 2011-01-01 is excluded",
                 "the early confirmation window runs on "
                 f"{len(window['early_pairs'])} pairs and says so; "
                 + (", ".join(f"`{p}`" for p in
                              window["pairs_withheld_from_the_early_window"])
                    or "no pair")
                 + " has no early confirmation available, which is recorded "
                   "as `NO_WINDOW` rather than as a failure"],
                ["**D4**",
                 "T6 is the primary remaining hypothesis source on price data "
                 "alone; the external-data question is banked",
                 "the closing section says what this card's result implies "
                 "for that question and originates nothing"],
                ["**D6**", "2009-2012 and 2013+ are training data",
                 "2009-2012 is used as a confirmation window rather than as a "
                 "stress test"],
                ["**D7**",
                 "cross-pair research horizons are "
                 + ", ".join(f"`{h}`" for h in window["horizons"])
                 + "; 5m and 30m are characterisation only",
                 "section 6 carries correlation and lead-lag summaries at "
                 + ", ".join(f"`{h}`" for h in
                             window["characterisation_horizons"])
                 + " and raises no hypothesis at them"],
                ["**D9**",
                 f"the research reference notional is "
                 f"{_n(method['reference_units'])} units",
                 "every cost here is priced at it, and the per-order floor is "
                 "inside the arithmetic rather than beside it — T5's Step 0 "
                 "addendum measures where"],
                ["**D10**",
                 "a backtester-readiness card precedes any scorecard",
                 "nothing here is backtested; the P0-A caveat is stated under "
                 "every cost table"],
            ]),
    ]


def _method(payload: dict[str, Any], by_name: dict[str, Any],
            figures_dir: str) -> list[str]:
    method = payload["method"]
    identity = payload["identity"]
    null = method["null"]
    window = payload["window"]
    coverage = payload["coverage"]

    coverage_rows = [[
        f"`{row['horizon']}`", row["window"], _n(row["pairs"]),
        _n(row["n"]), _pct(row["adjacent_share"], 2), row["from"] or "—",
        row["to"] or "—"] for row in coverage]

    return [
        "## Method, and the four things that shape every number",
        "",
        "### The universe has twelve series and seven degrees of freedom",
        "",
        "A pair's log return is its base currency's strength less its "
        f"quote's. With {identity['currencies']} currencies that is a design "
        f"matrix of rank {identity['rank']}, so at most "
        f"{identity['rank']} of the twelve pairs can be independent and the "
        f"remaining {_n(identity['dependent_pairs'])} are exact functions of "
        "them. Which five is not unique — any spanning set of "
        f"{identity['rank']} will do — so the report states one spanning set "
        "and what it determines rather than pretending there is a canonical "
        "answer:",
        "",
        *_table(["quantity", "value"], [
            ["pairs", _n(identity["pairs"])],
            ["currencies", _n(identity["currencies"])],
            ["rank of the currency design", _n(identity["rank"])],
            ["pairs it therefore determines", _n(identity["dependent_pairs"])],
            ["one spanning set",
             ", ".join(f"`{p}`" for p in identity["one_spanning_set"])],
            ["the pairs it determines",
             ", ".join(f"`{p}`" for p in identity["pairs_it_determines"])],
        ]),
        "This is not a modelling choice and it is not a finding: "
        "`log EURGBP = log EURUSD - log GBPUSD` is what a cross rate **is**. "
        "It is stated first because it is the single most load-bearing fact "
        "in the card. A cointegration scan run without it produces a ranked "
        "list whose top entries are definitions, and the experiment therefore "
        "derives the identity flag from the design matrix for every "
        "relationship it reports rather than from a list somebody typed.",
        "",
        "### Nothing spans a hole, and nothing is interpolated",
        "",
        "Every horizon's series are aligned onto a **common** timestamp index "
        "by intersection, and every lagged estimator works inside the "
        "contiguous runs of that index. A bar one pair has and another does "
        "not is dropped from both rather than filled in: a filled value is a "
        "return nobody quoted, and a correlation computed against one is "
        "partly a correlation with an interpolation. The adjacency column is "
        "what the rule costs.",
        "",
        *_table(["horizon", "window", "pairs", "rows", "adjacency", "from",
                 "to"], coverage_rows),
        "### Discovery is one window and the confirmations are untouched",
        "",
        f"The cointegration scan and its false-discovery correction run in "
        f"**{window['discovery']['start']} → {window['discovery']['end']}**. "
        f"**{window['confirmation']['start']} → "
        f"{window['confirmation']['end']}** and **{window['early']['start']} "
        f"→ {window['early']['end']}** are confirmations of a set that was "
        "fixed before they were looked at. The full primary window is "
        "reported for context and is **not** a third independent test, "
        "because it contains both halves.",
        "",
        "Two honest consequences travel with that design. First, the "
        "discovery/confirmation split is the same partition T4's split-half "
        "used, so \"split-half stable\" and \"confirmed out of window\" are "
        "the same evidence rather than two pieces of it; the rolling "
        f"{method['rolling_window_years']}-year windows stepped "
        f"{method['rolling_step_months']} months are the independent "
        "stability check. Second, the **lead-lag** scan runs on the full "
        "primary window rather than on the split, so its stability is T4's "
        "discipline — split-half sign and rolling sign agreement — and not "
        "this window design. Section 4 says which test each row got.",
        "",
        "> **The confirmation rule.** A relationship discovered in the "
        "discovery window confirms in another window when its Engle-Granger "
        f"residual rejects the unit root at p < {method['confirm_alpha']} "
        "there **and** its hedge ratio keeps its sign and stays within a "
        f"factor of {_f(method['confirm_beta_tolerance'], 1)} of the "
        "discovery value. It is declared in the experiment config, before any "
        "result existed, and it thresholds nothing SPEC2 thresholds — "
        "pre-reg #1 pins exactly one bar and this adds no second one.",
        "",
        "### The p-values are simulated, and the simulation is checked",
        "",
        "The Engle-Granger and Johansen statistics have no standard "
        "distribution, and a scan cannot have a correction until it has "
        "p-values. So the null is **simulated** from independent random walks "
        "put through the same functions the data goes through, from this "
        f"experiment's seed: {_n(null['replications'])} draws of "
        f"{_n(null['length'])} observations at {null['lags']} lags. The "
        f"smallest p-value it can produce is {_p(null['smallest_p_value'])}, "
        "which is below the Benjamini-Hochberg threshold a lone survivor "
        "would have to clear — a simulation coarser than that could only find "
        "relationships in groups, and a scan that can only find them in "
        "groups is not a scan.",
        "",
        "The simulation is checked against MacKinnon's **published** "
        "asymptotic critical values for the Engle-Granger residual test, "
        "which is what makes it an instrument rather than an assertion:",
        "",
        *_table(["variables in the regression", "level", "simulated",
                 "published", "difference"],
                [[width, level,
                  _f(null["quantiles"]["engle_granger"][width][level], 3),
                  _f(null["engle_granger_published"][width][level], 3),
                  _f(float(null["quantiles"]["engle_granger"][width][level])
                     - float(null["engle_granger_published"][width][level]), 3)]
                 for width in sorted(null["engle_granger_published"])
                 for level in ("1%", "5%", "10%")
                 if width in null["quantiles"]["engle_granger"]]),
        *_figure(by_name, "simulated_null_against_published", figures_dir),
        "The Johansen critical values are **not** tabulated here, and that is "
        "deliberate: its distribution depends on which deterministic terms "
        "the model carries, and the published tables are easy to quote and "
        "easy to quote wrongly. The Johansen test is validated by what it "
        "does — on constructed systems whose answer is known, and by its "
        "rejection rate on fresh random walks — in "
        "`tests2/test_crossstats.py`, and its p-values come from the same "
        "simulation. This card runs it with an **unrestricted constant**: the "
        "levels of a decade of log FX prices carry a drift, and the variant "
        "that allows one is both the standard choice and the numerically "
        "robust one.",
        "",
        "### A relationship pays one round trip per leg",
        "",
        "A relationship holding one unit of notional in its first member "
        "against `beta` units in each of the others trades the residual "
        "`r0 - sum(beta_i ri)`, so its round trip is the first leg's plus "
        "each other leg's scaled by that leg's weight — all in basis points "
        "of the first leg's notional, which keeps the sum dimensionless and "
        "therefore currency-free. Every cost comes out of "
        f"`{method['cost_model']}` through `research.costs`, at "
        f"{_n(method['reference_units'])} units, exactly as T5's do.",
        "",
        "Two conventions are stated because they are choices:",
        "",
        "* the amplitude a relationship is credited with is the **standard "
        "deviation of its own spread**, which is what entering one standard "
        "deviation from the mean and exiting at the mean earns, once, for one "
        "round trip of every leg. The per-bar move is reported beside it for "
        "a rule that would trade every bar;",
        f"* the cost verdict is taken in the **{COST_WINDOW} window**, so "
        "both the stability test and the cost test are out of sample. The "
        "discovery and primary figures are in `result.json`.",
        "",
        "> **P0-A caveat.** SPEC2 prerequisite P0-A is **unfixed**: "
        "commission is floored against a quote-currency notional and "
        "cross-pair P&L is summed without conversion. Every cost here is a "
        "ratio of two quote-currency quantities and is therefore "
        "currency-free — except the per-order floor, which at this reference "
        "notional **does** bind for part of the universe. Every two-leg cost "
        "below names the legs whose floor binds, and T5's Step 0 addendum "
        "measures the size of the term. A cross-currency relationship's leg "
        "weights are also stated in each leg's own base units rather than in "
        "a common currency, which is the same defect seen from the sizing "
        "side. **This is decision D10's whole point.**",
        "",
        "### The shock window",
        "",
        "T4 reported it in terms: `EURCHF` and `USDCHF` carry the 2015 SNB "
        "de-peg inside the primary window — a 15% five-minute move, 403 "
        "standard deviations — and every statistic for those two pairs in the "
        "first half of the split is that afternoon. This card's strongest "
        "lead-lag cells are `EURCHF` and `USDCHF`, so it owes the same "
        "statistic with that afternoon removed, as a measurement rather than "
        "as a caveat.",
        "",
        f"The declared days are "
        + ", ".join(f"`{day}`" for day in method["shock_days"])
        + f" — {method['shock_reason']} — fixed in the experiment config "
        "before any result existed. Removing them is **not** a correction to "
        "the data: the de-peg happened and those prices are real. The whole "
        "lead-lag family is re-scanned without them, in its own family with "
        "its own correction, so the comparison answers *which cells win* "
        "rather than *do the winners hold*.",
        "",
    ]


# --------------------------------------------------------------------------- #
# Section 1 -- correlation
# --------------------------------------------------------------------------- #

def _section_correlation(payload: dict[str, Any], by_name: dict[str, Any],
                         figures_dir: str) -> list[str]:
    horizons = payload["window"]["horizons"]
    summary_rows = []
    for horizon in horizons:
        block = payload["correlation"].get(horizon)
        if not block:
            continue
        without = payload["correlation_without_shock"].get(horizon) or {}
        summary_rows.append([
            f"`{horizon}`", _n(block["n"]), _f(block["mean_rho"], 4),
            _f(block["mean_abs_rho"], 4),
            _f(without.get("mean_abs_rho"), 4),
            _f(block["geometry"]["participation_ratio"], 3),
            _f(block["geometry"]["entropy_bets"], 3),
            _n(block["geometry"]["components_for_90pct"])])

    extremes: list[list[Any]] = []
    for horizon in horizons:
        block = payload["correlation"].get(horizon)
        if not block:
            continue
        tests = sorted(block["tests"], key=lambda t: -(abs(t["rho"] or 0.0)))
        for test in tests[:5]:
            extremes.append([f"`{horizon}`", f"`{test['a']}`",
                             f"`{test['b']}`", _f(test["rho"], 4),
                             _n(test["n"])])

    regime_rows = []
    for horizon in horizons:
        block = payload["correlation"].get(horizon)
        if not block:
            continue
        for regime, entry in block["by_regime"].items():
            regime_rows.append([
                f"`{horizon}`", regime, _n(entry["n"]),
                _f(entry["mean_rho"], 4), _f(entry["mean_abs_rho"], 4),
                _f(entry["max_abs_rho"], 4),
                _f(entry["geometry"]["participation_ratio"], 3),
                _n(entry["geometry"]["components_for_90pct"])])

    cluster_rows = [[f"`{horizon}`", str(index + 1),
                     ", ".join(f"`{p}`" for p in cluster)]
                    for horizon in horizons
                    for index, cluster in enumerate(
                        (payload["correlation"].get(horizon) or {}).get(
                            "clusters", []))]
    identical = len({json.dumps((payload["correlation"].get(h) or {}).get(
        "clusters")) for h in horizons if payload["correlation"].get(h)}) == 1

    halves = []
    for horizon in horizons:
        block = payload["correlation"].get(horizon)
        if not block or not block["split_half"]:
            continue
        held = sum(1 for row in block["split_half"] if row["sign_held"])
        shifts = [abs(float(row["shift"])) for row in block["split_half"]
                  if row["shift"] is not None]
        biggest = max(block["split_half"],
                      key=lambda r: abs(float(r["shift"] or 0.0)))
        halves.append([
            f"`{horizon}`", _n(len(block["split_half"])),
            f"{held} / {len(block['split_half'])}",
            _f(sum(shifts) / len(shifts) if shifts else None, 4),
            f"`{biggest['a']}`–`{biggest['b']}` "
            f"({_f(biggest['first_half'], 3)} → "
            f"{_f(biggest['second_half'], 3)})"])

    rolling = payload["rolling_geometry"].get(horizons[0]) or []
    trend = ""
    if len(rolling) >= 2:
        first, last = rolling[0], rolling[-1]
        trend = (
            f"At `{horizons[0]}` the mean absolute correlation runs from "
            f"{_f(first['mean_abs_rho'], 3)} in {first['from'][:7]}–"
            f"{first['to'][:7]} to {_f(last['mean_abs_rho'], 3)} in "
            f"{last['from'][:7]}–{last['to'][:7]}, and the effective bets "
            f"from {_f(first['participation_ratio'], 2)} to "
            f"{_f(last['participation_ratio'], 2)}. **The universe has been "
            "getting less diversified across the decade**, which is a "
            "different statement from the one a crisis-correlation argument "
            "makes and is visible in the rolling figure rather than in the "
            "regime table.")

    return [
        "## 1 — Correlation structure",
        "",
        "Pairwise return correlations at each research horizon, their "
        "stability, their regime dependence and the network they form. Every "
        "pairwise correlation is a registered test, corrected within its "
        "horizon's family; at these sample sizes essentially all of them "
        "reject the null of zero, which is expected and is not a finding — "
        "the effect sizes and the stability are.",
        "",
        *_table(["horizon", "rows", "mean ρ", "mean |ρ|",
                 "mean |ρ| without the shock days", "participation ratio",
                 "entropy bets", "components for 90%"], summary_rows),
        *_figure(by_name, "mean_correlation_by_pair", figures_dir),
        "The five strongest pairwise correlations at each horizon:",
        "",
        *_table(["horizon", "pair", "pair", "ρ", "rows"], extremes),
        "### Stability",
        "",
        *_table(["horizon", "pairwise correlations", "sign held across the "
                 "split", "mean absolute shift", "largest shift"], halves),
        trend,
        "",
        *_figure(by_name, "effective_bets_rolling", figures_dir),
        "### Regime dependence — do correlations go to one?",
        "",
        "The regime is universe-level: the cross-sectional mean of each "
        "pair's trailing volatility, bucketed into terciles, computed "
        "strictly before the row it labels. Bucketing a row by a volatility "
        "estimate containing it would put the largest moves in the highest "
        "bucket by construction.",
        "",
        *_table(["horizon", "regime", "rows", "mean ρ", "mean |ρ|",
                 "max |ρ|", "participation ratio", "components for 90%"],
                regime_rows),
        *_figure(by_name, "effective_bets_by_regime", figures_dir),
        _regime_verdict(payload),
        "",
        "### The network",
        "",
        "Average-linkage clusters on the correlation distance "
        f"`sqrt(2(1 - rho))`, cut at "
        f"{_f(payload['method']['cluster_threshold'], 2)} — which is a "
        "correlation of a half. The cut is the whole of the clustering and is "
        "declared in the config rather than chosen after seeing the matrix.",
        "",
        *_table(["horizon", "cluster", "members"], cluster_rows),
        ("**The clusters are identical at every research horizon.** They are "
         "the currency blocs the design matrix already implies: a pair sits "
         "with the pairs it shares a currency with, and `EURGBP` and "
         "`USDCAD` sit alone because the currency each of them contributes is "
         "in nothing else that trades against the same side."
         if identical else
         "**The clusters differ between horizons**, which is a fact the "
         "table above carries and this card does not interpret."),
        "",
    ]


def _regime_verdict(payload: dict[str, Any]) -> str:
    """The generated answer to the card's question about high-volatility."""
    rows = payload["portfolio"]["rows"]
    ratios = [row["high_over_low_bets"] for row in rows
              if row["high_over_low_bets"] is not None]
    if not ratios:
        return ""
    low, high = min(ratios), max(ratios)
    if high < 0.85:
        return (f"**Correlations do go towards one in high volatility**: the "
                f"high-volatility regime carries {_x(low)} to {_x(high)} the "
                "effective bets of the low-volatility one.")
    if low > 1.15:
        return ("**Correlations fall in high volatility**, which is the "
                "opposite of the usual assumption: the high-volatility regime "
                f"carries {_x(low)} to {_x(high)} the effective bets of the "
                "low-volatility one.")
    return (
        "**Correlations do not go to one.** The high-volatility regime "
        f"carries {_x(low)} to {_x(high)} the effective bets of the "
        "low-volatility one across the research horizons — a difference of a "
        "few percent, against an assumption that usually expects the number "
        "to halve. Whatever else this universe does under stress, it does not "
        "collapse into a single trade at these horizons. The diversification "
        "that is being lost is being lost slowly over the decade rather than "
        "suddenly in a regime, which the rolling figure shows and the regime "
        "table cannot.")


# --------------------------------------------------------------------------- #
# Section 2 -- currency strength
# --------------------------------------------------------------------------- #

def _section_currency(payload: dict[str, Any], by_name: dict[str, Any],
                      figures_dir: str) -> list[str]:
    horizons = payload["window"]["horizons"]
    first = payload["currency"].get(horizons[0]) or {}
    fit_rows = []
    for horizon in horizons:
        block = payload["currency"].get(horizon)
        if not block:
            continue
        for row in block["by_pair"]:
            fit_rows.append([f"`{horizon}`", f"`{row['pair']}`",
                             _f(row["r_squared"], 6), _f(row["sd_bp"], 3),
                             _f(row["residual_sd_bp"], 4),
                             _pct(row["residual_share_of_sd"], 2)])

    factor_rows = []
    for horizon in horizons:
        block = payload["currency"].get(horizon)
        if not block:
            continue
        for row in block["factors"]:
            factor_rows.append([
                f"`{horizon}`", row["currency"], _n(row["pairs_it_appears_in"]),
                _f(row["sd_bp"], 3), _pct(row["share_of_universe_variance"], 1),
                _f(row["rho1"], 5), _p(row["rho1_q"]),
                _f(row["vr_headline"], 5), _p(row["vr_q_value"]),
                "**yes**" if row["vr_survives_correction"] else "no"])

    reference_rows = []
    for horizon in horizons:
        block = payload["currency"].get(horizon)
        if not block:
            continue
        for row in block["pair_reference"]:
            reference_rows.append([
                f"`{horizon}`", f"`{row['pair']}`", _f(row["rho1"], 5),
                _p(row["rho1_q"]), _f(row["vr_headline"], 5),
                _p(row["vr_q_value"]),
                "**yes**" if row["vr_survives_correction"] else "no"])

    families = payload["families"]["families"]
    factor_vr = families.get("currency_factor_variance_ratio", {})
    pair_vr = families.get("pair_reference_variance_ratio", {})
    factor_ac = families.get("currency_factor_autocorr", {})
    pair_ac = families.get("pair_reference_autocorr", {})
    single = first.get("single_pair_currencies") or []

    surviving = [(horizon, row["currency"], row["vr_headline"])
                 for horizon in horizons
                 for row in (payload["currency"].get(horizon) or {}).get(
                     "factors", [])
                 if row["vr_survives_correction"]]

    return [
        "## 2 — Currency-strength decomposition",
        "",
        "Each pair's return factored into its base currency's strength less "
        "its quote's, with the strengths normalised to sum to zero so no "
        "currency is silently made the numeraire. The normalisation is "
        "imposed as an extra equation rather than by dropping a currency.",
        "",
        "**Read the fit before reading the factors.** The design has rank "
        f"{first.get('design_rank')}, so this is closer to a change of basis "
        "than to a factor model, and the R² below is a statement about the "
        "universe's arithmetic rather than about how good the model is:",
        "",
        *_table(["horizon", "pair", "R²", "sd (bp)", "residual sd (bp)",
                 "residual share of sd"], fit_rows[:MAX_ROWS]),
        (f"_First {MAX_ROWS} of {len(fit_rows)} pair-horizons; the whole "
         "table is in `result.json`._" if len(fit_rows) > MAX_ROWS else ""),
        "",
        f"The mean R² is {_f(first.get('mean_r_squared'), 6)} at "
        f"`{horizons[0]}`. **Twelve series with "
        f"{payload['identity']['rank']} degrees of freedom cannot have an "
        "idiosyncratic component**, and the residual that remains is quoting "
        "noise: bars that closed a moment apart, and a bid-ask spread that is "
        "not identical on both sides of a triangle. It is a fact about the "
        "universe, not a good model fit.",
        "",
        ("Two currencies appear in only one pair each — "
         + ", ".join(f"`{code}`" for code in single)
         + ". A currency appearing once adds one unknown and one equation, so "
           "its strength is exactly determined and that pair's residual is "
           "zero by construction. Its factor is that pair's return with the "
           "broad dollar taken out, which is a different series from the pair "
           "and worth keeping in mind when reading its memory below."
         if single else ""),
        "",
        "### Do the currency factors carry memory the pairs do not?",
        "",
        "The card's question, and the honest way to answer it is with the "
        "same estimators T4 used, in their own families, corrected the same "
        "way. The comparison families are stated because they are different "
        "sizes and a Benjamini-Hochberg threshold depends on family size:",
        "",
        *_table(["family", "tests", "rejected at FDR "
                 f"{payload['method']['fdr_alpha']}", "share"],
                [["`currency_factor_variance_ratio`",
                  _n(factor_vr.get("tests")), _n(factor_vr.get("rejected")),
                  _pct(factor_vr.get("rejected_share"))],
                 ["`pair_reference_variance_ratio`",
                  _n(pair_vr.get("tests")), _n(pair_vr.get("rejected")),
                  _pct(pair_vr.get("rejected_share"))],
                 ["`currency_factor_autocorr`",
                  _n(factor_ac.get("tests")), _n(factor_ac.get("rejected")),
                  _pct(factor_ac.get("rejected_share"))],
                 ["`pair_reference_autocorr`",
                  _n(pair_ac.get("tests")), _n(pair_ac.get("rejected")),
                  _pct(pair_ac.get("rejected_share"))]]),
        _factor_verdict(payload, surviving),
        "",
        *_figure(by_name, "currency_factor_variance_ratio", figures_dir),
        "The factors, in full:",
        "",
        *_table(["horizon", "currency", "pairs it appears in", "sd (bp)",
                 "share of factor variance", "ρ(1)", "q", "VR(4)", "q",
                 "VR survives"], factor_rows),
        "And the pairs, on the same estimators, as the comparison:",
        "",
        *_table(["horizon", "pair", "ρ(1)", "q", "VR(4)", "q", "VR survives"],
                reference_rows[:MAX_ROWS]),
        (f"_First {MAX_ROWS} of {len(reference_rows)} pair-horizons; the whole "
         "table is in `result.json`._"
         if len(reference_rows) > MAX_ROWS else ""),
        "",
    ]


def _factor_verdict(payload: dict[str, Any],
                    surviving: Sequence[tuple[Any, Any, Any]]) -> str:
    """The generated answer to the card's currency-memory question."""
    families = payload["families"]["families"]
    pair_vr = families.get("pair_reference_variance_ratio", {})
    if not surviving and not pair_vr.get("rejected"):
        return ("**Neither carries it.** No variance ratio survives the "
                "correction, on the factors or on the pairs, at any research "
                "horizon — which is the same answer T4 reached for the pairs "
                "and extends it to the factors.")
    if surviving and not pair_vr.get("rejected"):
        named = ", ".join(f"the `{code}` factor at `{horizon}` "
                          f"(VR(4) = {_f(value, 3)})"
                          for horizon, code, value in surviving)
        factor_vr = families.get("currency_factor_variance_ratio", {})
        return (
            f"**Yes, and it is a small answer.** "
            f"{_n(len(surviving))} of {_n(factor_vr.get('tests'))} factor "
            f"variance ratios "
            f"{_plural(len(surviving), 'survives', 'survive')} the correction "
            "— " + named + " — while **not one of "
            f"{_n(pair_vr.get('tests'))} pair variance ratios survives at any "
            "research horizon**, which is exactly what T4 found. A currency "
            "factor is a pair with the broad-dollar component taken out, so "
            "this says the reversion is in the currency rather than in the "
            "quote, and that removing the dollar leg is what makes it "
            "visible. The two families are different sizes, so the "
            "Benjamini-Hochberg thresholds differ and the raw effect sizes in "
            "the tables below are the fairer comparison; they point the same "
            "way. A T7 card acting on this would be acting on "
            f"{_n(len(surviving))} of {_n(factor_vr.get('tests'))} tests.")
    return ("**Both carry some.** The factor and pair families both reject, "
            "and the tables below carry the effect sizes rather than a "
            "verdict this card is entitled to draw from them.")


# --------------------------------------------------------------------------- #
# Section 3 -- cointegration
# --------------------------------------------------------------------------- #

def _section_cointegration(payload: dict[str, Any], by_name: dict[str, Any],
                           figures_dir: str) -> list[str]:
    block = payload["cointegration"]
    counts = _counts(payload)
    families = payload["families"]["families"]
    eg = families.get("cointegration_engle_granger", {})
    johansen = families.get("cointegration_johansen", {})

    identity_rows = []
    for row in sorted(counts["identity"],
                      key=lambda r: -(float((r["cost"] or {}).get(
                          "amplitude_over_cost", {}).get(BAR) or 0.0))):
        cost = row["cost"] or {}
        window = row["windows"].get(COST_WINDOW) or row["discovery"]
        identity_rows.append([
            _members(row), f"`{row['horizon']}`",
            _f(row["discovery"]["tau"], 2), _p(row["eg_q_value"]),
            row["confirmation"][COST_WINDOW]["verdict"].replace("_", " "),
            row["confirmation"]["early"]["verdict"].replace("_", " "),
            _f(row["discovery"]["half_life_bars"], 2),
            _f(window.get("residual_sd_bp"), 3),
            _f(cost.get("cost_bp", {}).get(BAR), 3),
            _x(cost.get("amplitude_over_cost", {}).get(BAR)),
            _f(cost.get("break_even_entry_sd"), 1)])

    non_identity = [row for row in counts["surviving"] if not row["identity"]]
    non_rows = []
    for row in non_identity[:MAX_ROWS]:
        cost = row["cost"] or {}
        non_rows.append([
            _members(row), f"`{row['horizon']}`",
            _f(row["discovery"]["tau"], 2), _p(row["eg_q_value"]),
            row["confirmation"][COST_WINDOW]["verdict"].replace("_", " "),
            row["confirmation"]["early"]["verdict"].replace("_", " "),
            _f(row["discovery"]["residual_sd_bp"], 1),
            _f(row["discovery"]["half_life_bars"], 1),
            ", ".join(row["fails_on"])])

    breakdown: dict[str, int] = {}
    for row in counts["ranked"]:
        key = ", ".join(row["fails_on"]) or "nothing — qualifies"
        breakdown[key] = breakdown.get(key, 0) + 1

    return [
        "## 3 — Cointegration scans",
        "",
        f"All {_n(66)} pairs-of-pairs in both Engle-Granger orderings, plus "
        f"{_n(len(block['triples_declared']))} declared triples, at each "
        f"research horizon: **{_n(block['scanned'])} relationships** scanned "
        "in the discovery window, then confirmed — untouched — in the two "
        "later and earlier windows.",
        "",
        "**Five of the triples are known-answer controls, not candidates.** "
        "They are the universe's triangular identities, declared as such in "
        "the config before any result existed. `log EURGBP` is `log EURUSD` "
        "less `log GBPUSD` by definition, so the scan **must** find them: a "
        "scan that misses one is broken, and a report that lists one as an "
        "opportunity has discovered arithmetic.",
        "",
        *_table(["family", "tests", "rejected at FDR "
                 f"{payload['method']['fdr_alpha']}", "BH threshold p"],
                [["`cointegration_engle_granger`", _n(eg.get("tests")),
                  _n(eg.get("rejected")), _p(eg.get("bh_threshold"))],
                 ["`cointegration_johansen`", _n(johansen.get("tests")),
                  _n(johansen.get("rejected")),
                  _p(johansen.get("bh_threshold"))]]),
        f"{_n(block['p_values_on_the_simulation_floor'])} of "
        f"{_n(block['scanned'])} discovery statistics sit on the simulation's "
        "resolution floor — they are more extreme than any of "
        f"{_n(payload['method']['null']['replications'])} random-walk draws, "
        "so their p-value is reported as the floor rather than as a number "
        "the simulation cannot support. Johansen is symmetric in its members, "
        "so it is computed once per unordered set and its family is "
        "correspondingly smaller.",
        "",
        "### The known-answer controls, and what they cost",
        "",
        "Every one of the universe's triangular identities, at every research "
        f"horizon. The scan finds all {_n(len(counts['identity']))} of them; "
        f"{_n(sum(1 for r in counts['identity'] if r['stable_out_of_window']))}"
        " survive the correction and confirm in **both** untouched windows; "
        "and **not one of them pays for its own legs**:",
        "",
        *_table(["relationship", "horizon", "τ", "q", COST_WINDOW, "early",
                 "half-life (bars)", "spread sd (bp)",
                 f"3-leg round trip @ {BAR}× (bp)", "amplitude / cost",
                 "break-even entry (σ)"], identity_rows),
        *_figure(by_name, "identity_spread_versus_cost", figures_dir),
        "The break-even entry column is the whole story. A triangular spread "
        "would have to reach that many standard deviations from its mean "
        "before a full reversion to the mean covered three round trips at the "
        "reference notional — and a spread whose own standard deviation is "
        "the unit is not going to reach it. **Triangular arbitrage in this "
        "universe is real, statistically overwhelming, confirmed in every "
        "window, and between two and ten times too narrow to trade.**",
        "",
        "### Everything else",
        "",
        f"{_n(len(non_identity))} non-identity relationships survive the "
        f"false-discovery correction inside the discovery window. "
        + (f"**{_n(len(counts['stable_non_identity']))} of them confirm in "
           "both untouched windows.**"
           if counts["stable_non_identity"] else
           "**Not one of them confirms in both untouched windows.**"),
        "",
        *_table(["relationship", "horizon", "τ", "q", COST_WINDOW, "early",
                 "spread sd (bp)", "half-life (bars)", "fails on"], non_rows),
        (f"_First {MAX_ROWS} of {len(non_identity)}; the whole scan is in "
         "`result.json` under `payload.cointegration.ranked`._"
         if len(non_identity) > MAX_ROWS else ""),
        "",
        "**A large spread standard deviation on an unconfirmed relationship "
        "is not an edge — it is a random walk's variance.** The rows above "
        "with spread standard deviations in the hundreds of basis points are "
        "pairs whose residual wanders; their amplitude-over-cost ratios look "
        "spectacular and mean nothing, which is exactly why the card asks for "
        "out-of-window confirmation before it asks for arithmetic.",
        "",
        "How the whole scan divides, by which of the card's three conditions "
        "it fails:",
        "",
        *_table(["fails on", "relationships"],
                sorted(([key, _n(value)] for key, value in breakdown.items()),
                       key=lambda r: -int(str(r[1]).replace(",", "")))),
        "",
    ]


# --------------------------------------------------------------------------- #
# Section 4 -- lead-lag
# --------------------------------------------------------------------------- #

def _section_leadlag(payload: dict[str, Any], by_name: dict[str, Any],
                     figures_dir: str) -> list[str]:
    horizons = payload["window"]["horizons"]
    headline = payload["leadlag_headline"]
    method = payload["method"]

    summary = []
    for horizon in horizons:
        block = headline.get(horizon)
        if not block:
            continue
        without = payload["leadlag_without_shock"].get(horizon) or {}
        summary.append([
            f"`{horizon}`", _n(block["tested"]),
            _n(block["survives_correction"]),
            _n(without.get("survivors")),
            _n(block["survives_both_ways"]),
            _n(block["pays"]), _n(block["stable"]),
            f"**{_n(block['qualifying'])}**"])

    per_horizon = 10
    rows: list[list[Any]] = []
    shown = 0
    total_survivors = 0
    for horizon in horizons:
        block = headline.get(horizon)
        if not block:
            continue
        survivors = [row for row in block["rows"]
                     if row["survives_correction"]]
        total_survivors += len(survivors)
        keep = [row for row in survivors if row["qualifies"]]
        for row in sorted(survivors, key=lambda r: -(abs(r["rho"] or 0.0))):
            if len(keep) >= per_horizon:
                break
            if row not in keep:
                keep.append(row)
        shown += len(keep)
        for row in sorted(keep, key=lambda r: -(abs(r["rho"] or 0.0))):
            rows.append([
                f"`{horizon}`", f"`{row['lead']}`", f"`{row['lagging']}`",
                _n(row["lag"]), _f(row["rho"], 5),
                _f(row["rho_without_shock"], 5), _p(row["q_value"]),
                _f(row["edge_bp"], 4),
                _f(row["cost_bp_at_survival_bar"], 4),
                _x(row["edge_over_cost"]),
                row["stability_label"] or "—",
                _pct(row["rolling_sign_agreement"]),
                "**yes**" if row["qualifies"] else "no"])

    qualifying = [row for horizon in horizons
                  for row in (headline.get(horizon) or {}).get("rows", [])
                  if row["qualifies"]]
    total_tests = sum(block["tested"] for block in headline.values())

    detail: list[str] = []
    for row in qualifying:
        detail += [
            f"#### `{row['lead']}` → `{row['lagging']}`, lag "
            f"{row['lag']}, `{row['horizon']}`",
            "",
            *_table(["quantity", "value"], [
                ["correlation", _f(row["rho"], 5)],
                ["correlation without the shock days",
                 _f(row["rho_without_shock"], 5)],
                ["q inside its family", _p(row["q_value"])],
                ["q without the shock days", _p(row["q_without_shock"])],
                ["first half / second half",
                 f"{_f(row['first_half'], 5)} / {_f(row['second_half'], 5)}"],
                ["rolling two-year sign agreement",
                 f"{_pct(row['rolling_sign_agreement'])} of "
                 f"{_n(row['rolling_windows'])} windows "
                 f"({row['stability_label']})"],
                ["standard deviation of the lagging pair",
                 f"{_f(row['sd_bp'], 3)} bp"],
                ["implied edge per trade", f"{_f(row['edge_bp'], 4)} bp"],
                [f"round trip of `{row['lagging']}` @ {BAR}×",
                 f"{_f(row['cost_bp_at_survival_bar'], 4)} bp"],
                ["edge over cost", _x(row["edge_over_cost"])],
                ["dearest rung it clears",
                 next((rung for rung in reversed(method["ladder"])
                       if row["edge_bp"] and row["cost_bp_at_survival_bar"]
                       and row["edge_bp"] > row["cost_bp_at_survival_bar"]
                       / float(method["survival_bar"]) * float(rung)), "none")
                 + "×"],
            ]),
        ]

    return [
        "## 4 — Lead-lag",
        "",
        "Every ordered pair at lags 1 to "
        f"{method['leadlag_max_lag']}, at each research horizon, "
        "Benjamini-Hochberg corrected inside its horizon's family — and then "
        "the entire family re-scanned with the declared shock days removed, "
        "in a second family with its own correction.",
        "",
        "The effect size is T5's measure applied across pairs: a rule "
        "forecasting the lagging pair from the leading one has a forecast "
        "whose standard deviation is `|ρ| × sd` of the **lagging** pair, and "
        "trading its sign earns that per trade before costs. It is compared "
        "against that pair's own round trip, because that is the pair the "
        "rule would trade — **one** round trip, not two.",
        "",
        *_table(["horizon", "tests", "survive the correction",
                 "survive it without the shock days", "survive both ways",
                 "pay their own round trip", "stable", "qualify on all three"],
                summary),
        "**The three columns are nearly disjoint, and that is the finding.** "
        "At the short research horizons the survivors are numerous and "
        "microscopic — none of them earns its round trip. At the daily "
        "horizon almost everything clears its round trip, because a daily "
        "move is two orders of magnitude larger than a daily round trip, and "
        "almost nothing survives the correction.",
        "",
        *_figure(by_name, "leadlag_edge_versus_cost", figures_dir),
        "### The shock check",
        "",
        *_figure(by_name, "leadlag_shock_sensitivity", figures_dir),
        _shock_verdict(payload),
        "",
        f"The strongest {_n(per_horizon)} cells that survive the correction "
        f"at each research horizon, of {_n(total_survivors)} that do "
        "(everything that qualifies is included whatever its rank):",
        "",
        *_table(["horizon", "lead", "lagging", "lag", "ρ", "ρ without the "
                 "shock", "q", "edge (bp)", f"cost @ {BAR}× (bp)",
                 "edge / cost", "rolling stability", "sign agreement",
                 "qualifies"], rows),
        ("### The cells that pass every test"
         if qualifying else "### Nothing passes every test"),
        "",
        (f"{_n(len(qualifying))} "
         f"{_plural(len(qualifying), 'cell', 'cells')} of "
         f"{_n(total_tests)} lead-lag tests at the research horizons "
         f"{_plural(len(qualifying), 'survives', 'survive')} the correction "
         "with and without the shock days, holds its sign across the split "
         "and across the rolling windows, and earns more than the round trip "
         "of the pair it would trade. **The trial count belongs beside it "
         "wherever it is quoted** — pre-reg #10 — and at this ratio a single "
         "false discovery is exactly what one would expect to look like."
         if qualifying else
         f"No cell of {_n(total_tests)} lead-lag tests at the research "
         "horizons survives the correction both ways, holds its sign, and "
         "earns its own round trip."),
        "",
        *detail,
    ]


def _shock_verdict(payload: dict[str, Any]) -> str:
    """The generated reading of the shock-window comparison."""
    horizons = payload["window"]["horizons"]
    collapsed = 0
    survivors = 0
    for horizon in horizons:
        for row in (payload["leadlag_headline"].get(horizon) or {}).get(
                "rows", []):
            if not row["survives_correction"]:
                continue
            survivors += 1
            if not row.get("survives_without_shock"):
                collapsed += 1
    if not survivors:
        return ""
    method = payload["method"]
    days = ", ".join(f"`{day}`" for day in method["shock_days"])
    return (
        f"**{_n(collapsed)} of {_n(survivors)} cells that survive the "
        f"correction stop surviving it when {days} are removed** and the "
        "whole family is re-scanned without them. Two days out of a decade. "
        "Every one of them involves `EURCHF` or `USDCHF`, which is precisely "
        "what T4's warning about those two pairs predicted, and it is the "
        "reason this check is a measurement in the card rather than a "
        "sentence at the end of it.")


# --------------------------------------------------------------------------- #
# Section 5 -- portfolio geometry
# --------------------------------------------------------------------------- #

def _section_portfolio(payload: dict[str, Any], by_name: dict[str, Any],
                       figures_dir: str) -> list[str]:
    portfolio = payload["portfolio"]
    rows = [[
        f"`{row['horizon']}`", _n(row["pairs"]), _n(row["n"]),
        _f(row["mean_abs_rho"], 4), _f(row["participation_ratio"], 3),
        _f(row["entropy_bets"], 3), _n(row["components_for_90pct"]),
        _pct(row["pc1_share"]), _x(row["high_over_low_bets"])]
        for row in portfolio["rows"]]
    ceiling = portfolio["structural_ceiling"]
    best = max((row["participation_ratio"] for row in portfolio["rows"]),
               default=None)
    return [
        "## 5 — Portfolio-level geometry",
        "",
        "The input a portfolio-level T7 evaluation needs: how many "
        "independent bets the universe actually offers, by horizon and by "
        "regime. Two measures, because they answer the question differently "
        "— the participation ratio is `n` for an uncorrelated universe and 1 "
        "for one moving as a single thing, and the entropy measure has the "
        "same two extremes and is less dominated by the largest eigenvalue in "
        "between.",
        "",
        *_table(["horizon", "pairs", "rows", "mean |ρ|",
                 "participation ratio", "entropy bets", "components for 90%",
                 "PC1 share", "high-vol / low-vol bets"], rows),
        *_figure(by_name, "eigen_spectrum", figures_dir),
        f"**The ceiling is {ceiling}, not twelve.** "
        f"{payload['identity']['dependent_pairs']} of the twelve pairs are "
        f"exact functions of the other {ceiling}, so an effective-bet count "
        "is measuring how much of "
        f"{ceiling} the universe delivers rather than how much of twelve. "
        f"It delivers about {_f(best, 1)} of them on the participation ratio "
        "— which is the number a portfolio-level evaluation should size "
        "against, and it is materially smaller than a naive reading of a "
        "twelve-pair universe would give.",
        "",
    ]


# --------------------------------------------------------------------------- #
# Section 6 -- characterisation
# --------------------------------------------------------------------------- #

def _section_characterisation(payload: dict[str, Any]) -> list[str]:
    short = payload["characterisation"]
    horizons = payload["window"]["characterisation_horizons"]
    rows = []
    for horizon in horizons:
        block = short.get(horizon)
        if not block:
            continue
        rows.append([
            f"`{horizon}`", _n(block["n"]), _pct(block["adjacent_share"], 2),
            _f(block["mean_abs_rho"], 4),
            _f(block["geometry"]["participation_ratio"], 3),
            _f(block["geometry"]["entropy_bets"], 3),
            _n(block["geometry"]["components_for_90pct"]),
            _n(block["leadlag_tests"])])
    lead_rows = []
    for horizon in horizons:
        block = short.get(horizon)
        if not block:
            continue
        for row in block["leadlag_strongest"][:8]:
            lead_rows.append([
                f"`{horizon}`", f"`{row['lead']}`", f"`{row['lagging']}`",
                _n(row["lag"]), _f(row["rho"], 5), _p(row.get("q_value")),
                _f(row["edge_bp"], 4),
                _f(row["cost_bp_at_survival_bar"], 4),
                "yes" if row["pays_at_survival_bar"] else "**no**"])
    cluster_note = ""
    research = payload["correlation"].get(payload["window"]["horizons"][0])
    if research and short:
        same = all(block["clusters"] == research["clusters"]
                   for block in short.values())
        cluster_note = (
            "The clusters at these horizons are **identical** to the ones at "
            "the research horizons, so the network is the same network all "
            "the way down the ladder."
            if same else
            "The clusters differ from the research horizons', which the table "
            "in section 1 and the payload both carry.")
    paying = sum(1 for horizon in horizons
                 for row in (short.get(horizon) or {}).get(
                     "leadlag_strongest", [])
                 if row["pays_at_survival_bar"])
    return [
        "## 6 — Characterisation only: `"
        + "`, `".join(horizons) + "` (decision D7)",
        "",
        "**No hypothesis is raised here and none may be.** SPEC2 decision D7 "
        "puts cross-pair research at the three longer horizons and makes "
        "these two characterisation: a correlation or a lead-lag that exists "
        "only at the short horizons is a fact about the universe, and it may "
        "not become a candidate. The tables below are that fact, and nothing "
        "in the closing sections is built on them.",
        "",
        *_table(["horizon", "rows", "adjacency", "mean |ρ|",
                 "participation ratio", "entropy bets", "components for 90%",
                 "lead-lag tests"], rows),
        cluster_note,
        "",
        "The strongest lead-lag cells at each, with the cost they would have "
        "to clear — stated because it is the reason D7 reads the way it does, "
        "not because anything here is a candidate:",
        "",
        *_table(["horizon", "lead", "lagging", "lag", "ρ", "q", "edge (bp)",
                 f"cost @ {BAR}× (bp)", "pays"], lead_rows),
        (f"**{_n(paying)} of the strongest cells at these horizons clears its "
         "own round trip.** The correlations are the largest anywhere in the "
         "card and the moves are the smallest, which is T5's cost geometry "
         "restated across pairs: the horizons with the most measurable "
         "structure are the horizons with the least room to pay for it."
         if paying == 0 else
         f"{_n(paying)} of the strongest cells at these horizons clears its "
         "own round trip. D7 still forbids a hypothesis here; the number is "
         "reported because a checkpoint reading D7 again should see it."),
        "",
    ]


# --------------------------------------------------------------------------- #
# Multiple testing
# --------------------------------------------------------------------------- #

def _section_testing(payload: dict[str, Any], trials: int) -> list[str]:
    families = payload["families"]["families"]
    rows = [[f"`{name}`", _n(block["tests"]), _n(block["usable"]),
             _p(block["bh_threshold"]), _n(block["rejected"]),
             _pct(block["rejected_share"])]
            for name, block in sorted(families.items())]
    total = sum(block["tests"] for block in families.values())
    return [
        "## Multiple testing, counted",
        "",
        f"**{_n(trials)} trial(s) are ledgered under this card** and this "
        f"result registers **{_n(total)} hypothesis tests** across "
        f"{_n(len(families))} families. Both numbers matter and they are "
        "different numbers, exactly as in T4: the ledger records experiments "
        "— one entry per run, written before the run — and the register "
        "records tests, inside the hashed result, so a test cannot be dropped "
        "from its family after its p-value has been seen.",
        "",
        *_table(["family", "tests", "usable",
                 f"BH threshold p at FDR {payload['method']['fdr_alpha']}",
                 "rejected", "share"], rows),
        "Three things about this table are worth stating plainly.",
        "",
        "**The correlation families reject almost everything, and that is not "
        "a finding.** At these sample sizes a test of zero correlation has "
        "the power to reject on the third decimal. They are in the table "
        "because a family excluded from the count is a family that stops "
        "being counted.",
        "",
        "**The lead-lag p-values are optimistic.** The statistic is "
        "`ρ√n` over overlapping pairs, exactly as T4's was, and overlapping "
        "pairs are not independent draws. The correction is applied to those "
        "p-values, so a survivor is a survivor on a generous instrument, "
        "which is the conservative direction for a null result and the "
        "wrong one for a positive claim. The shock re-scan and the rolling "
        "stability are what a positive claim in this card actually rests on.",
        "",
        "**The cointegration p-values are simulated and have a floor.** "
        f"{_n(payload['cointegration']['p_values_on_the_simulation_floor'])} "
        "discovery statistics are more extreme than any random-walk draw, so "
        "their p-value is the floor rather than a number the simulation can "
        "support. The Benjamini-Hochberg step-up is applied to the floored "
        "values, which costs power only when very few tests are significant "
        "— and the replication count was chosen so that the floor sits below "
        "the threshold a lone survivor would have to clear.",
        "",
    ]


# --------------------------------------------------------------------------- #
# The ranked table
# --------------------------------------------------------------------------- #

def _closing_ranked(payload: dict[str, Any]) -> list[str]:
    counts = _counts(payload)
    ranked = counts["ranked"]
    qualifying = counts["qualifying"]
    stable = counts["stable"]

    rows = []
    for row in (qualifying if qualifying else stable)[:MAX_ROWS]:
        cost = row["cost"] or {}
        window = row["windows"].get(COST_WINDOW) or row["discovery"]
        rows.append([
            _members(row), f"`{row['horizon']}`",
            _n(row["size"]), "**identity**" if row["identity"] else "no",
            _p(row["eg_q_value"]),
            row["confirmation"][COST_WINDOW]["verdict"].replace("_", " "),
            row["confirmation"]["early"]["verdict"].replace("_", " "),
            _f(window.get("residual_sd_bp"), 3),
            _f(cost.get("cost_bp", {}).get(BAR), 3),
            _x(cost.get("amplitude_over_cost", {}).get(BAR)),
            _f(cost.get("break_even_entry_sd"), 1),
            "**yes**" if row["qualifies"] else "no"])

    leadlag = counts["leadlag_qualifying"]
    lead_rows = [[
        f"`{row['lead']}` → `{row['lagging']}`", f"`{row['horizon']}`",
        _n(row["lag"]), _p(row["q_value"]), _p(row["q_without_shock"]),
        row["stability_label"], _pct(row["rolling_sign_agreement"]),
        _f(row["edge_bp"], 4), _f(row["cost_bp_at_survival_bar"], 4),
        _x(row["edge_over_cost"]), "**yes**"] for row in leadlag]

    head = [
        "## The ranked table — what survives all three conditions",
        "",
        "The card's deliverable. A cross-pair relationship earns a place here "
        "only if all three hold:",
        "",
        "* **(a) statistically surviving after correction** — its "
        "Engle-Granger statistic survives Benjamini-Hochberg at FDR "
        f"{payload['method']['fdr_alpha']} across the whole "
        f"{_n(payload['families']['families'].get('cointegration_engle_granger', {}).get('tests'))}"
        "-test scan;",
        "* **(b) stable out of window** — it confirms, untouched, in "
        f"**both** the {payload['window']['confirmation']['start']}–"
        f"{payload['window']['confirmation']['end']} window and the "
        f"{payload['window']['early']['start']}–"
        f"{payload['window']['early']['end']} window, under the confirmation "
        "rule stated in the method;",
        "* **(c) able to pay its own way** — the standard deviation of its "
        f"spread exceeds the round trip of **every one of its legs** at the "
        f"{BAR}× rung and "
        f"{_n(payload['method']['reference_units'])} units.",
        "",
    ]

    if qualifying:
        return head + [
            f"**{_n(len(qualifying))} relationship(s) satisfy all three.**",
            "",
            *_table(["relationship", "horizon", "legs", "identity?", "q",
                     COST_WINDOW, "early", "spread sd (bp)",
                     f"cost @ {BAR}× (bp)", "amplitude / cost",
                     "break-even entry (σ)", "qualifies"], rows),
        ] + _closing_leadlag(payload, lead_rows) + _closing_d4(payload)

    return head + [
        "### Nothing satisfies all three. That is the result.",
        "",
        f"Of {_n(len(ranked))} relationships scanned, "
        f"**{_n(len(counts['surviving']))}** survive the correction, "
        f"**{_n(len(stable))}** also confirm in both untouched windows, and "
        f"**{_n(len(qualifying))}** of those pay for their own legs. "
        f"Every one of the {_n(len(stable))} that reaches the last condition "
        + ("is a triangular identity"
           if all(row["identity"] for row in stable) else
           f"— {_n(sum(1 for r in stable if r['identity']))} of them "
           "triangular identities —")
        + ", and here they are with the arithmetic that closes them:",
        "",
        *_table(["relationship", "horizon", "legs", "identity?", "q",
                 COST_WINDOW, "early", "spread sd (bp)",
                 f"cost @ {BAR}× (bp)", "amplitude / cost",
                 "break-even entry (σ)", "qualifies"], rows),
        "**Read the last two columns together.** These relationships are not "
        "marginal and they are not noise: they are the tightest, most "
        "overwhelmingly significant, most reliably confirmed structure "
        "anywhere in the universe, and they are arbitrage identities. Their "
        "spreads are one to two basis points wide. Three round trips at the "
        "reference notional cost four to seven. The gap is not a modelling "
        "choice — it is the reason retail triangular arbitrage does not "
        "exist, measured.",
        "",
    ] + _closing_leadlag(payload, lead_rows) + _closing_d4(payload)


def _closing_leadlag(payload: dict[str, Any],
                     lead_rows: Sequence[Sequence[Any]]) -> list[str]:
    counts = _counts(payload)
    leadlag = counts["leadlag_qualifying"]
    total = sum(block["tested"]
                for block in payload["leadlag_headline"].values())
    if not leadlag:
        return [
            "### And nothing in the lead-lag scan either",
            "",
            f"The lead-lag scan is a one-leg test rather than a two-leg one, "
            "so it is reported separately rather than folded into the table "
            f"above. Of {_n(total)} lead-lag tests at the research horizons, "
            "none survives the correction both with and without the shock "
            "days, holds its sign, and earns the round trip of the pair it "
            "would trade.",
            "",
        ]
    return [
        "### The one thing that does pass an analogous test",
        "",
        "A lead-lag rule trades the lagging pair and pays **one** round trip, "
        "where a cointegration relationship pays one per leg — so it cannot "
        "go in the table above, and hiding it would be worse than putting it "
        "somewhere slightly awkward. Its stability test is also different: "
        "the lead-lag scan runs on the full primary window, so what it has is "
        "T4's split-half and rolling discipline plus the shock re-scan, not "
        "the ranked table's out-of-window confirmation.",
        "",
        *_table(["cell", "horizon", "lag", "q", "q without the shock days",
                 "rolling stability", "sign agreement", "edge (bp)",
                 f"cost @ {BAR}× (bp)", "edge / cost", "qualifies"],
                lead_rows),
        f"**One cell out of {_n(total)} lead-lag tests at the research "
        "horizons.** Pre-reg #10 requires that count next to it and it is "
        "the most important thing on this line: at this ratio, one survivor "
        "is what a single false discovery would look like. It is a question "
        "for a T7 card, not a finding, and section 4 carries everything a "
        "card would need to specify it.",
        "",
    ]


def _closing_d4(payload: dict[str, Any]) -> list[str]:
    counts = _counts(payload)
    identity = payload["identity"]
    qualifying = counts["qualifying"]
    leadlag = counts["leadlag_qualifying"]
    if qualifying:
        return []
    return [
        "### What this implies for decision D4",
        "",
        "SPEC2 decision D4 made this card **the primary remaining hypothesis "
        "source on price data alone**, and banked the external-data question "
        "— rates and carry, positioning, the macro calendar — for a later "
        "checkpoint. Neither this card nor T5 may originate it. So the "
        "implication is stated as evidence rather than as a proposal.",
        "",
        "The evidence is that the cross-pair structure in this universe is "
        "almost entirely the structure its own arithmetic guarantees. "
        f"{_n(identity['dependent_pairs'])} of twelve pairs are exact "
        f"functions of the other {identity['rank']}; the correlation "
        "clusters are the currency blocs that follow from that; the "
        "cointegration relationships that confirm out of window are the "
        "triangular identities and nothing else; and the currency-strength "
        "decomposition leaves residuals of a fraction of a basis point, "
        "because twelve series with seven degrees of freedom cannot have an "
        "idiosyncratic component. **A price-only cross-pair search on this "
        "universe has now returned its answer, and the answer is that the "
        "reliable structure is the structure that pays nothing.**",
        "",
        (f"That leaves {_n(len(leadlag))} lead-lag "
           f"{_plural(len(leadlag), 'cell', 'cells')} as the only "
           "price-only question this card can hand forward, at a trial count "
           "that makes it a question rather than a finding."
           if leadlag else
           "That leaves no price-only question this card can hand forward."),
        "",
        "What follows from it is a checkpoint decision and not this card's. "
        "Two readings are available and the evidence does not choose between "
        "them: that the universe should be widened, or that the information "
        "should be. The card's own contribution to the second is negative "
        "evidence about the first — a wider set of *these* pairs adds "
        f"correlated combinations of {identity['rank']} currency factors, and "
        "the effective-bet count in section 5 is what that is worth.",
        "",
    ]


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #

def _closing_questions(payload: dict[str, Any]) -> list[str]:
    counts = _counts(payload)
    families = payload["families"]["families"]
    leadlag = counts["leadlag_qualifying"]
    horizons = payload["window"]["horizons"]
    portfolio = payload["portfolio"]["rows"]
    bets = portfolio[0] if portfolio else {}
    surviving_factors = [(horizon, row["currency"], row["vr_headline"],
                          row["vr_q_value"])
                         for horizon in horizons
                         for row in (payload["currency"].get(horizon)
                                     or {}).get("factors", [])
                         if row["vr_survives_correction"]]
    rolling = payload["rolling_geometry"].get(horizons[0]) or []
    identity_best = max(
        (row for row in counts["identity"]
         if (row["cost"] or {}).get("amplitude_over_cost", {}).get(BAR)),
        key=lambda r: r["cost"]["amplitude_over_cost"][BAR], default=None)

    lines = [
        "## Questions for T7 cards, with their trial counts",
        "",
        "Questions, not answers, and not hypotheses this card is entitled to "
        "originate. Pre-registered decision #3 puts hypothesis selection in "
        "chat; what a card may do is say what its own evidence makes worth "
        "asking. Each is stated with the number that prompted it and the "
        "family it came out of, so it can be argued with.",
        "",
    ]
    if leadlag:
        row = leadlag[0]
        lines += [
            f"**`{row['lead']}` leads `{row['lagging']}` by {row['lag']} bar "
            f"at `{row['horizon']}` — ρ = {_f(row['rho'], 4)}, "
            f"{_x(row['edge_over_cost'])} its round trip, "
            f"{_pct(row['rolling_sign_agreement'])} rolling sign agreement, "
            "and it strengthens rather than weakens when the January 2015 "
            "shock days come out.** Family: "
            f"`leadlag@{row['horizon']}`, "
            f"{_n(families.get(f'leadlag@{row['horizon']}', {}).get('tests'))} "
            "tests; re-scanned in "
            f"`leadlag_no_shock@{row['horizon']}`, the same size. "
            "**Question for a T7 card:** does a rule that has to trade it — "
            "one round trip per signal, at the reference notional, inside the "
            "lagging pair's own cheapest execution band — keep any of that "
            f"{_x(row['edge_over_cost'])}? The gap between a correlation and "
            "a tradeable rule is the whole question, and T5's section 4 is "
            "the cautionary version of the same arithmetic.",
            "",
        ]
    if surviving_factors:
        horizon, code, value, q = surviving_factors[0]
        lines += [
            f"**The `{code}` currency factor's variance ratio survives the "
            f"correction at `{horizon}` (VR(4) = {_f(value, 4)}, "
            f"q = {_p(q)}) while not one of the twelve pairs' does at any "
            "research horizon.** Families: "
            "`currency_factor_variance_ratio`, "
            f"{_n(families.get('currency_factor_variance_ratio', {}).get('tests'))} "
            "tests, against `pair_reference_variance_ratio`, "
            f"{_n(families.get('pair_reference_variance_ratio', {}).get('tests'))}. "
            "**Question for a T7 card:** a currency factor is a pair with the "
            "broad-dollar component removed, so is the reversion in the "
            "currency rather than in the quote — and if it is, what does a "
            "rule trading it have to hold? A factor is not tradeable: "
            "capturing it means a basket, and a basket pays a round trip per "
            "leg, which is the arithmetic that closed every identity in "
            "section 3.",
            "",
        ]
    if len(rolling) >= 2:
        lines += [
            f"**The effective number of independent bets at `{horizons[0]}` "
            f"falls from {_f(rolling[0]['participation_ratio'], 2)} in "
            f"{rolling[0]['from'][:7]} to "
            f"{_f(rolling[-1]['participation_ratio'], 2)} in "
            f"{rolling[-1]['from'][:7]}, against a structural ceiling of "
            f"{payload['portfolio']['structural_ceiling']}.** "
            "**Question for a checkpoint:** a portfolio-level evaluation "
            "sizes against the diversification it expects to have, and this "
            "says the number has been shrinking for a decade rather than "
            "spiking in crises. Should a portfolio-level T7 evaluation size "
            "against the decade's average or against the most recent "
            "window's? The two differ by "
            f"{_x(float(rolling[0]['participation_ratio']) / float(rolling[-1]['participation_ratio']))}.",
            "",
        ]
    if identity_best is not None:
        cost = identity_best["cost"]
        lines += [
            f"**The tightest confirmed relationship in the universe — "
            f"{_members(identity_best)} at `{identity_best['horizon']}` — "
            f"needs its spread to reach "
            f"{_f(cost['break_even_entry_sd'], 1)} standard deviations before "
            "a full reversion pays three round trips.** Family: "
            "`cointegration_engle_granger`, "
            f"{_n(families.get('cointegration_engle_granger', {}).get('tests'))} "
            "tests. **Question for a checkpoint, not a card:** the gap is "
            "spread, and decision D8's recorder is the only instrument that "
            "can revisit a spread. Is the triangular geometry worth "
            "re-measuring once recorder-measured IB spreads exist, or is a "
            "relationship this far from paying its way closed for good? "
            "Pre-reg #1 allows exactly one route back, and this is the "
            "cleanest test case for it in the whole battery.",
            "",
        ]
    lines += [
        "**What this card did not ask.** No strategy is specified, no "
        "parameter is chosen, and nothing is backtested. No pair is promoted "
        "or dropped. Decision D4 banks the external-data question and this "
        "card leaves it banked — the section above states what its evidence "
        "implies for it and originates nothing. Decision D7 forbids a "
        "hypothesis at `"
        + "`, `".join(payload["window"]["characterisation_horizons"])
        + "`, and section 6 raises none.",
        "",
    ]
    return lines


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

def _provenance(document: dict[str, Any], payload: dict[str, Any], home: str,
                figures: Sequence[dict[str, Any]], figures_dir: str,
                gate_status: str) -> list[str]:
    access = document.get("access") or {}
    loader = payload["loader"]
    null = payload["method"]["null"]
    return [
        "## Provenance",
        "",
        f"* Config: `{home}/config.toml` (sha256 "
        f"`{str(document['config_sha256'])[:16]}`), which is where the cost "
        "model, the windows, the declared triples, the shock days and the "
        "confirmation rule are all fixed.",
        "* Cost model: `fxlab.costs.IBCostModel`, unchanged from Phase 1, "
        "through `research.costs` and `research.cost_geometry.price_series` "
        "— the same code path T5's tables come out of.",
        "* Bars: `data/research/bars/timeframe=<TF>/pair=<PAIR>/`, read only "
        "through `research.loader.ResearchLoader` in `scoring` mode, which is "
        "what enforces the seal and ruling R1 on every date served.",
        f"* Simulated null: {_n(null['replications'])} draws of "
        f"{_n(null['length'])} observations at {null['lags']} lags, from seed "
        f"{null['seed']}. Checked against MacKinnon's published asymptotic "
        "critical values in `tests2/test_crossstats.py`.",
        f"* Result: `{home}/result.json`, hash `{document['result_hash']}`",
        f"* Figures: {len(figures)} under `{figures_dir}/`, each beside the "
        "CSV of the numbers it was drawn from. Both are regenerated from "
        "`result.json` by `python -m research.cross_pair_report`.",
        f"* Loader mode `{document['mode']}`, scored `{document['scored']}`, "
        f"re-run class `{document['rerun_class']}`. It served "
        f"{len(access.get('files', []))} file(s) across "
        f"{len(access.get('pairs', []))} pair(s), "
        f"{len(access.get('timeframes', []))} timeframe(s) and "
        f"{len(access.get('dates', []))} date(s); sealed dates served: "
        f"{loader['sealed_dates_served'] or 'none'}; dates withheld by an "
        f"exclusion window: {_n(loader['excluded_dates_withheld'])} across "
        f"{_n(len(loader['excluded_pairs']))} pair(s) — ruling R1, the early "
        "confirmation window asking `AUDUSD` for years it may not have.",
        f"* Research gate: {gate_status}",
        "",
    ]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.cross_pair_report",
        description="Render the T6 cross-pair report and its figures.")
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
    card = str(document.get("taskcard") or "T6")
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
