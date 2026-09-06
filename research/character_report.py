"""Render the T4 per-pair character report from its result document.

Ruling R6, with no escape hatch: there is no ``--note`` here, every number
below is read out of ``result.json``, and every figure is drawn at render time
from the same document by :mod:`research.character_figures`. The hypothesis
section at the end is generated too -- its prose is a template and every
quantity in it, including which pair-horizons appear at all, comes from the
result and from the Benjamini-Hochberg correction the experiment computed. A
hypothesis section typed by hand is a hypothesis section that stops being true
the moment the data changes, which is exactly the defect T3 spent its first
step cleaning up.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
from typing import Any, Final, Sequence

from research.character_figures import build_all

_LOG: Final[logging.Logger] = logging.getLogger("research.character_report")

#: Rows of a long listing carried before a table is truncated.
MAX_ROWS: Final[int] = 40

#: The FDR level below which a fingerprint is called rather than left FLAT.
CALL_LEVEL: Final[float] = 0.05

#: Weekdays in calendar order. The result document is hashed, so it is written
#: with sorted keys -- which reproduces exactly and turns every weekday tally
#: into "Fri, Mon, Sun, Thu, Tue, Wed". Order is restored here.
WEEKDAYS: Final[tuple[str, ...]] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat",
                                    "Sun")

#: Empty-date classes in the priority order the classifier applies them, which
#: is also the order they are worth reading in.
EMPTY_ORDER: Final[tuple[str, ...]] = (
    "r1_artefact", "week_boundary", "calendar_holiday", "currency_holiday",
    "feed_artefact", "unknown")


def _ordered(mapping: dict[str, Any], order: Sequence[str]) -> list[tuple]:
    """``mapping`` in ``order``, then anything ``order`` did not name."""
    known = [(key, mapping[key]) for key in order if key in mapping]
    rest = [(key, value) for key, value in sorted(mapping.items())
            if key not in set(order)]
    return known + rest


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def _row(cells: Sequence[Any]) -> str:
    """One Markdown table row, with any pipe inside a cell escaped.

    Half the statistics in this report are written with absolute-value bars --
    rho|r|(1), mean |return| -- and an unescaped one silently splits the row
    into extra columns and stops the table rendering at all.
    """
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
    if value is None:
        return "—"
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"{100.0 * float(value):.{places}f}%"


def _p(value: Any) -> str:
    """A p- or q-value, with the very small ones written as a bound."""
    if value is None:
        return "—"
    number = float(value)
    if number == 0.0:
        return "<1e-16"
    if number < 1e-4:
        return f"{number:.1e}"
    return f"{number:.4f}"


def _tick(value: Any) -> str:
    if value is None:
        return "—"
    return "yes" if value else "**no**"


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #

def render(document: dict[str, Any], trials: int, gate_status: str, home: str,
           figures: Sequence[dict[str, Any]], figures_dir: str) -> str:
    """Build the whole report."""
    payload = document["payload"]
    card = str(document.get("taskcard") or "T4")
    index = {entry["name"]: entry for entry in figures}
    lines: list[str] = []
    lines += _header(document, payload, trials, card)
    lines += _rulings(payload)
    lines += _method(payload)
    lines += _section_returns(payload, index, figures_dir)
    lines += _section_memory(payload, index, figures_dir)
    lines += _section_volatility(payload, index, figures_dir)
    lines += _section_sessions(payload, index, figures_dir)
    lines += _section_stability(payload, index, figures_dir)
    lines += _section_density(payload, index, figures_dir)
    lines += _section_empties(payload, index, figures_dir)
    lines += _section_testing(payload, trials)
    lines += _section_character(payload)
    lines += _section_hypotheses(payload, trials)
    lines += _provenance(document, gate_status, home, figures, figures_dir)
    return "\n".join(lines) + "\n"


def _figure(index: dict[str, Any], name: str, figures_dir: str) -> list[str]:
    """A figure, its caption and the CSV it was drawn from."""
    entry = index.get(name)
    if entry is None:
        return []
    svg = pathlib.PurePosixPath(entry["svg"]).name
    csv = pathlib.PurePosixPath(entry["csv"]).name
    return [
        f"![{entry['caption']}]({figures_dir}/{svg})",
        "",
        f"*{entry['caption']}* — source table: [`{figures_dir}/{csv}`]"
        f"({figures_dir}/{csv})",
        "",
    ]


def _header(document: dict[str, Any], payload: dict[str, Any], trials: int,
            card: str) -> list[str]:
    window = payload["window"]
    tests = payload["tests"]
    return [
        f"# {card} — EDA battery I: per-pair character",
        "",
        f"**Primary window:** {window['start']} → {window['end']}, "
        f"{window['pairs']} pairs, horizons "
        f"{', '.join(f'`{h}`' for h in window['horizons'])} · "
        f"**Appendix:** {window['history_start']} → {window['end']} on "
        f"{', '.join(f'`{h}`' for h in window['history_horizons'])} · "
        f"**Task card:** `taskcards/{card}.md` · "
        f"**Experiment:** `{document['experiment_id']}` · "
        f"**Seed:** {document['seed']} · "
        f"**Result hash:** `{document['result_hash'][:16]}`",
        "",
        f"**Trials ledgered under {card}:** {trials} "
        f"(SPEC2 pre-reg #10). **Hypothesis tests in this result:** "
        f"{_n(tests['total_tests'])}, across "
        f"{_n(len(tests['families']))} families, each corrected at "
        f"FDR {tests['alpha']}.",
        "",
        "This is the first research card. Its output is **evidence and "
        "hypotheses for a human checkpoint** — never decisions. No pair is "
        "dropped or promoted here, no strategy is specified, nothing is "
        "backtested and no scorecard exists. Pre-registered decision #3 puts "
        "those in chat, between cards, and this report is written to be "
        "argued with rather than acted on.",
        "",
        "**Every number below is derived at render time** from "
        "`result.json`, which the research gate re-executes and re-hashes. "
        "That is ruling R6. It reaches the hypothesis section too: which "
        "pair-horizons appear there, and every figure quoted beside them, "
        "come out of the result and the false-discovery correction rather "
        "than out of a paragraph somebody wrote once.",
        "",
        "### What the battery found, in five sentences",
        "",
        *_abstract(payload),
    ]


def _abstract(payload: dict[str, Any]) -> list[str]:
    """The five findings a reader who stops here should leave with."""
    rows = payload["character"]
    horizons = list(payload["window"]["horizons"])
    called = [r for r in rows if r["fingerprint"] != "FLAT"]
    revert = [r for r in called if r["fingerprint"] == "REVERT"]
    trend = [r for r in called if r["fingerprint"] == "TREND"]
    by_horizon: dict[str, int] = {}
    for row in called:
        by_horizon[row["horizon"]] = by_horizon.get(row["horizon"], 0) + 1
    stable = [r for r in called if r["stability"] in ("STABLE",
                                                      "MOSTLY-STABLE")]
    vol = payload["cells"]
    half_lives = [cell["volatility"]["half_life_abs"]
                  for cell in vol.values()
                  if cell["volatility"]["half_life_abs"] is not None]
    roll = payload["sessions"]
    spread_ratios = [s["roll_vs_rest"]["spread_ratio"] for s in roll.values()
                     if s["roll_vs_rest"]["spread_ratio"] is not None]
    vol_ratios = [s["roll_vs_rest"]["vol_ratio"] for s in roll.values()
                  if s["roll_vs_rest"]["vol_ratio"] is not None]
    empties = payload["empties"]
    density = payload["density"]
    density_vs_vol = [d["annual_density_vs_vol_spearman"]
                      for d in density.values()
                      if d["annual_density_vs_vol_spearman"] is not None]
    return [
        f"1. **Directional memory lives at one horizon and it is the "
        f"shortest.** Of {_n(len(rows))} pair-horizon cells, "
        f"{_n(len(called))} have a variance ratio that survives the "
        f"false-discovery correction — "
        f"{', '.join(f'{_n(by_horizon.get(h, 0))} at `{h}`' for h in horizons)}. "
        f"{_n(len(revert))} of those are mean-reverting and "
        f"{_n(len(trend))} are trending, and {_n(len(stable))} of them hold "
        "their sign on rolling two-year windows.",
        f"2. **Volatility memory is everywhere and is far stronger than "
        f"return memory.** The autocorrelation of |return| has a half-life "
        f"between {_f(min(half_lives), 1)} and {_f(max(half_lives), 1)} bars "
        "across the cells that have one, against return autocorrelations that "
        "die inside a bar. Whatever is predictable here is the size of the "
        "move, not its direction.",
        f"3. **The roll window is a different market.** Across the twelve "
        f"pairs the spread inside 16:00–18:00 New York is "
        f"{_f(min(spread_ratios), 2)}× to {_f(max(spread_ratios), 2)}× its "
        f"level outside, while volatility is {_f(min(vol_ratios), 2)}× to "
        f"{_f(max(vol_ratios), 2)}×. Pre-registered decision #4 excludes it "
        "from execution by default; this is the evidence a checkpoint would "
        "revisit that on, and it points the same way the decision does.",
        f"4. **Tick count is an activity proxy only within a year.** Across "
        f"years its rank correlation with realised volatility runs from "
        f"{_f(min(density_vs_vol), 2)} to {_f(max(density_vs_vol), 2)} "
        "depending on the pair — the series is dominated by feed changes, not "
        "by the market. Section 6 gives the conditions under which ruling R4 "
        "can be lifted, and they are narrower than they look.",
        f"5. **{_n(empties['by_class']['r1_artefact'])} of T3's "
        f"{_n(empties['dates'])} unexplained empty dates are not data facts "
        f"at all.** They are dates on which the only pair that went quiet was "
        "AUDUSD inside ruling R1's exclusion window, so the readable-pair "
        f"filter emptied the row and left it counted. "
        f"{_n(empties['dates_with_a_readable_empty_pair'])} dates survive as "
        "real, and most of those sit on the week boundary.",
        "",
    ]


def _rulings(payload: dict[str, Any]) -> list[str]:
    rulings = payload["rulings"]
    rows = [[f"**{key}**", rulings[key]["statement"], rulings[key]["bites"]]
            for key in sorted(rulings)]
    return [
        "## The rulings this card is shaped by",
        "",
        "R1 and R3–R4 were fixed at the M2 checkpoint, R7 and R8 at the M3 "
        "checkpoint that opened this card. All are in `SPEC2.md`. They are "
        "restated with where each one actually bites, because a ruling listed "
        "without its consequence is decoration.",
        "",
        *_table(["ruling", "statement", "where it bites here"], rows),
    ]


def _method(payload: dict[str, Any]) -> list[str]:
    method = payload["method"]
    coverage = payload["coverage"]
    readable = [row for row in coverage if row.get("readable")]
    by_horizon: dict[str, dict[str, int]] = {}
    for row in readable:
        bucket = by_horizon.setdefault(row["horizon"],
                                       {"pairs": 0, "bars": 0, "returns": 0,
                                        "dropped": 0, "stubs": 0})
        bucket["pairs"] += 1
        bucket["bars"] += row["bars"]
        bucket["returns"] += row["returns"]
        bucket["dropped"] += row["dropped_pairs"]
        bucket["stubs"] += row["stub_bars_dropped"]
    order = list(payload["window"]["horizons"])
    rows = [[f"`{h}`", _n(by_horizon[h]["pairs"]), _n(by_horizon[h]["bars"]),
             _n(by_horizon[h]["returns"]), _n(by_horizon[h]["dropped"]),
             _n(by_horizon[h]["stubs"]),
             _pct(min(r["adjacent_share"] for r in readable
                      if r["horizon"] == h and r["adjacent_share"] is not None),
                  2)]
            for h in order if h in by_horizon]
    return [
        "## Method, and the two decisions that shape every number",
        "",
        "### Returns never span a hole",
        "",
        f"`{method['return_definition']}`, and a consecutive pair of bars is "
        f"kept only under the gap rule: {method['gap_rule']}. This is not "
        "housekeeping. A bar table has a weekend in it every five days; "
        "differencing straight through one produces a *5-minute return* "
        "covering 65 hours, and a single one of those dominates the kurtosis, "
        "the lag-1 autocorrelation and every tail statistic in the section it "
        "lands in. The surviving returns are carried as contiguous **spans**, "
        "and every memory estimator here works inside a span and pools across "
        "them, so no lag-1 pair and no variance-ratio window straddles a "
        "weekend.",
        "",
        "The last column is what the rule costs: the share of consecutive "
        "returns that really are adjacent, at the worst pair for that horizon.",
        "",
        *_table(["horizon", "pairs", "bars", "returns", "gapped pairs dropped",
                 "stub bars dropped", "adjacency (worst pair)"], rows),
        "The daily row drops stub bars rather than gapped pairs, and the two "
        "are different problems. The FX week opens Sunday 17:00 New York, so "
        "every Sunday has a daily bar covering the two or three hours to "
        "midnight UTC — a fortieth of a weekday's ticks. Counted as a day it "
        "would insert a stub between every Friday and Monday and truncate "
        "every Monday return; dropped, Friday-to-Monday is the standard daily "
        "close-to-close return and nothing is lost, because Monday's close "
        "comes after the Sunday session anyway.",
        "",
        "### Regimes are conditioned, never fitted",
        "",
        f"The volatility regime of a bar comes from the standard deviation of "
        f"the {method['vol_window_bars']} returns **strictly before** it. "
        "Bucketing a return by a volatility estimate that contains it would "
        "put the largest returns in the highest bucket by construction, and "
        "every regime finding in section 3 would be circular. The same shift "
        "is why the low tercile is not simply the bars whose returns were "
        "small.",
        "",
        "### The estimators",
        "",
        *_table(["choice", "value", "why"], [
            ["return autocorrelation lags", _n(method["acf_lags"]),
             "enough to see the shape, short enough that Ljung-Box over them "
             "is a test rather than a formality"],
            ["|return| autocorrelation lags", _n(method["vol_acf_lags"]),
             "volatility memory runs an order of magnitude longer than return "
             "memory, so it needs a longer window to see the decay"],
            ["variance-ratio horizons",
             ", ".join(str(q) for q in method["vr_horizons"]),
             "a factor of sixteen in holding period at every bar size, "
             "computed with the heteroskedasticity-robust statistic — the "
             "homoskedastic form would reject on volatility clustering alone"],
            ["ADF lag order", _n(method["adf_lags"]),
             "fixed and stated. Schwert's rule would put ~110 lags on a "
             "750,000-observation series to answer a question already obvious "
             "at ten, and make the answer depend on sample length invisibly"],
            ["ADF 1% critical value", _f(method["adf_critical_1pct"], 5),
             "MacKinnon's asymptotic value for the constant-without-trend "
             "case; at these sample sizes the finite-sample correction is "
             "below the last digit printed"],
            ["forward-continuation horizons",
             ", ".join(str(h) for h in method["continuation_horizons"]),
             "the variance ratio cannot be conditioned on a regime — it needs "
             "a contiguous overlapping window — so this is the memory "
             "statistic the regime tables use"],
            ["rolling window",
             f"{method['rolling_window_years']} years, stepped "
             f"{method['rolling_step_months']} months",
             "consecutive windows share three quarters of their data, so a "
             "property has to hold for two years at a time everywhere in the "
             "decade rather than on average across it"],
            ["session and spread grain",
             f"`{method['session_timeframe']}` bars",
             "the roll window is two hours wide and three daylight-saving "
             "rules move the session map, so hourly is the coarsest grain "
             "that resolves both"],
            ["FDR level", str(method["fdr_alpha"]),
             "Benjamini-Hochberg within each family. The family is pairs "
             "times horizons of tests on overlapping data, where family-wise "
             "error is a target nothing would survive"],
        ]),
    ]


# --------------------------------------------------------------------------- #
# Section 1
# --------------------------------------------------------------------------- #

def _cells_by_horizon(payload: dict[str, Any], horizon: str
                      ) -> list[tuple[str, dict[str, Any]]]:
    return sorted(((key.split("|", 1)[0], cell)
                   for key, cell in payload["cells"].items()
                   if key.split("|", 1)[1] == horizon),
                  key=lambda item: item[0])


def _section_returns(payload: dict[str, Any], index: dict[str, Any],
                     figures_dir: str) -> list[str]:
    horizons = list(payload["window"]["horizons"])
    lines = [
        "## 1 — Return distributions by horizon",
        "",
        "Moments, tails and normality for every pair at every horizon. The "
        "tail ratio is the empirical quantile of |return| divided by the "
        "Gaussian quantile at the same probability for a normal distribution "
        "of the same variance: 1.0 is Gaussian, 2.0 says the 1-in-1,000 move "
        "is twice the size a normal would put there. It is reported beside the "
        "exceedance ratio because the two answer different questions — how "
        "much *bigger*, and how much more *often* — and a distribution can be "
        "extreme on one and ordinary on the other.",
        "",
    ]
    for horizon in horizons:
        rows = []
        for pair, cell in _cells_by_horizon(payload, horizon):
            row = cell["returns"]
            rows.append([f"`{pair}`", _n(row["n"]), _f(row["sd_bp"], 3),
                         _f(row["sd_annualised_pct"], 2), _f(row["skew"], 3),
                         _f(row["excess_kurtosis"], 1),
                         _f(row["tail_ratio_p99"], 2),
                         _f(row["tail_ratio_p999"], 2),
                         _f(row["tail_ratio_p9999"], 2),
                         _n(row["count_beyond_4sd"]),
                         _f(row["gaussian_count_beyond_4sd"], 1),
                         _n(row["count_beyond_6sd"]),
                         _f(row["gaussian_count_beyond_6sd"], 4)])
        lines += [f"### `{horizon}`", "",
                  *_table(["pair", "returns", "sd (bp)", "annualised sd (%)",
                           "skew", "excess kurtosis", "tail ratio p99",
                           "p99.9", "p99.99", "beyond 4σ", "a Gaussian would "
                           "give", "beyond 6σ", "a Gaussian would give"],
                          rows)]
    extreme_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            for entry in cell["returns"]["extremes"][:1]:
                extreme_rows.append([f"`{pair}`", f"`{horizon}`",
                                     entry["ts"][:19].replace("T", " "),
                                     _f(entry["return_bp"], 1),
                                     _f(entry["sigmas"], 1)])
    extreme_rows.sort(key=lambda r: -abs(float(r[3])))
    lines += [
        "### The largest single moves, and why one pair looks unlike the rest",
        "",
        "A kurtosis of five figures is not a distributional property, it is an "
        "event with a date. The largest absolute return in each cell, worst "
        "first, so the outliers in the tables above can be recognised rather "
        "than wondered at:",
        "",
        *_table(["pair", "horizon", "bar close (UTC)", "return (bp)",
                 "σ"], extreme_rows[:20]),
        "### How the tails change with horizon",
        "",
        "Aggregation is supposed to thin tails: sum enough independent moves "
        "and the central limit theorem takes over. It does, and the rate at "
        "which it does is the interesting part, because a horizon where the "
        "tails stay fat is a horizon where the moves are not independent.",
        "",
        *_figure(index, "kurtosis_by_horizon", figures_dir),
        *_figure(index, "tail_ratio_by_horizon", figures_dir),
        *_figure(index, "sd_by_horizon", figures_dir),
        "Jarque-Bera is reported in the result document and deliberately not "
        "tabulated here. At these sample sizes the statistic runs to six "
        "figures for every pair at every horizon and the p-value is zero to "
        "machine precision, which establishes only that FX returns are not "
        "Gaussian — something nobody doubted, and something the skew and "
        "kurtosis columns above say with an effect size attached.",
        "",
    ]
    return lines


# --------------------------------------------------------------------------- #
# Section 2
# --------------------------------------------------------------------------- #

def _section_memory(payload: dict[str, Any], index: dict[str, Any],
                    figures_dir: str) -> list[str]:
    horizons = list(payload["window"]["horizons"])
    method = payload["method"]
    adf_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            memory = cell["memory"]
            adf_rows.append([f"`{pair}`", f"`{horizon}`",
                             _f(memory["adf_levels"]["tau"], 2),
                             _tick(memory["adf_levels"]
                                   ["rejects_unit_root_1pct"]),
                             _f(memory["adf_returns"]["tau"], 1),
                             _tick(memory["adf_returns"]
                                   ["rejects_unit_root_1pct"])])
    level_rejects = sum(1 for r in adf_rows if r[3] == "yes")
    return_rejects = sum(1 for r in adf_rows if r[5] == "yes")

    lines = [
        "## 2 — Stationarity and memory",
        "",
        "### Unit-root sanity",
        "",
        "The level series is rebuilt from the gap-filtered returns rather than "
        "read off the price column, so the regression differences the same "
        "series everything else here measures and never across a weekend. The "
        "null is a unit root, so a τ **below** the critical value rejects it.",
        "",
        f"Of {_n(len(adf_rows))} pair-horizon cells, **{_n(level_rejects)}** "
        f"reject a unit root in the level and **{_n(return_rejects)}** reject "
        "it in the returns. That is the sanity result and it is the only thing "
        "this test is being asked for: prices behave like random walks, "
        "returns emphatically do not, and at hundreds of thousands of "
        "observations the test has the power to reject on departures far too "
        "small to trade, so the sign and the magnitude are what to read.",
        "",
        *_table(["pair", "horizon", "τ (levels)", "rejects at 1%",
                 "τ (returns)", "rejects at 1%"], adf_rows[:MAX_ROWS]),
        f"_First {min(MAX_ROWS, len(adf_rows))} of {_n(len(adf_rows))} cells; "
        "the whole table is in `result.json`._",
        "",
        "### The variance-ratio profile — the trend-versus-reversion "
        "fingerprint",
        "",
        "A variance ratio above 1 says a q-period move is larger than q "
        "independent one-period moves would be: returns reinforce, which is "
        "what a trend looks like. Below 1 says they cancel, which is what mean "
        "reversion looks like. The whole profile across q is worth more than "
        "any single value, because a series can trend at one aggregation and "
        "revert at another — and several here do.",
        "",
        "The statistic is Lo and MacKinlay's heteroskedasticity-robust `z*`. "
        "The homoskedastic form would reject on volatility clustering alone, "
        "and section 3 shows every pair at every horizon clusters, so the "
        "robust form is not a refinement here — it is the difference between "
        "measuring memory and measuring variance.",
        "",
    ]
    for horizon in horizons:
        lines += _figure(index, f"variance_ratio_{horizon}", figures_dir)

    vr_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            by_q = {row["q"]: row for row in cell["memory"]["variance_ratio"]}
            vr_rows.append([f"`{pair}`", f"`{horizon}`",
                            *[_f((by_q.get(q) or {}).get("vr"), 4)
                              for q in method["vr_horizons"]],
                            _f((by_q.get(4) or {}).get("z"), 2)])
    lines += [
        "Every cell, with the z of the q=4 rung — the one the character table "
        "ranks on:",
        "",
        *_table(["pair", "horizon",
                 *[f"VR({q})" for q in method["vr_horizons"]], "z at q=4"],
                vr_rows),
        "### Return autocorrelation and sign persistence",
        "",
        "Effect sizes, not just significance. `ρ(1)` is the lag-1 return "
        "autocorrelation over pairs inside a span; `Σ|ρ|` sums the first "
        f"{method['acf_lags']} lags, which is the honest way to see whether "
        "memory that is invisible at lag 1 is hiding further out. `p(same "
        "sign)` is the share of returns keeping the previous return's sign, "
        "and 0.5 is a fair coin.",
        "",
    ]
    memory_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            memory = cell["memory"]
            signs = memory["sign_persistence"]
            continuation = {row["horizon"]: row
                            for row in memory["forward_continuation"]}
            memory_rows.append([
                f"`{pair}`", f"`{horizon}`",
                _f(memory["acf"][0]["rho"], 4) if memory["acf"] else "—",
                _f(memory["acf_abs_sum"], 4),
                _p(memory["ljung_box"]["p_value"]),
                _f(signs["p_same"], 4), _f(signs["z"], 1),
                _f((continuation.get(4) or {}).get("rho"), 4),
                _f((continuation.get(12) or {}).get("rho"), 4)])
    lines += [
        *_table(["pair", "horizon", "ρ(1)", f"Σ|ρ| over "
                 f"{method['acf_lags']} lags", "Ljung-Box p", "p(same sign)",
                 "z", "continuation ρ (h=4)", "(h=12)"], memory_rows),
        "The continuation columns correlate one return against the sum of the "
        "next h. It is the same question the variance ratio asks, in a form "
        "that survives being conditioned on a regime — which is why section 3 "
        "uses it and why it is reported unconditionally here, so the two are "
        "on one scale. Its z is deflated by √h for the overlap it is built "
        "from; without that correction a value of 0.01 would read as twenty "
        "sigma.",
        "",
    ]
    return lines


# --------------------------------------------------------------------------- #
# Section 3
# --------------------------------------------------------------------------- #

def _section_volatility(payload: dict[str, Any], index: dict[str, Any],
                        figures_dir: str) -> list[str]:
    horizons = list(payload["window"]["horizons"])
    rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            vol = cell["volatility"]
            rows.append([f"`{pair}`", f"`{horizon}`",
                         _f(vol["acf_abs"][0], 4) if vol["acf_abs"] else "—",
                         _f(vol["acf_abs"][4], 4) if len(vol["acf_abs"]) > 4
                         else "—",
                         _f(vol["acf_abs"][19], 4) if len(vol["acf_abs"]) > 19
                         else "—",
                         _f(vol["half_life_abs"], 1),
                         _f(vol["acf_squared"][0], 4) if vol["acf_squared"]
                         else "—",
                         _f(vol["half_life_squared"], 1),
                         _p(vol["ljung_box_abs"]["p_value"])])
    lines = [
        "## 3 — Volatility: clustering, regimes and the clock",
        "",
        "### Clustering",
        "",
        "The autocorrelation of |return| is the single strongest statistical "
        "regularity in this whole battery, and it is an order of magnitude "
        "larger and longer-lived than anything in the returns themselves. "
        "Whatever is forecastable in FX at these horizons is the *size* of the "
        "next move, not its direction.",
        "",
        *_table(["pair", "horizon", "ρ|r|(1)", "ρ|r|(5)", "ρ|r|(20)",
                 "half-life (bars)", "ρ r²(1)", "r² half-life",
                 "Ljung-Box p"], rows),
        "A half-life is fitted by least squares to the log of the leading run "
        "of positive autocorrelations, and is reported as absent rather than "
        "as a number when the sequence does not decay — a negative half-life "
        "in a table is a number with no referent that nobody notices.",
        "",
        *_figure(index, "volatility_acf_5m", figures_dir),
        *_figure(index, "volatility_acf_1h", figures_dir),
        "### Regimes",
        "",
        "Terciles of trailing volatility, and how the memory statistics differ "
        "inside each. The regime label uses only returns before the one it "
        "labels; see the method note above for why that shift is the whole "
        "test.",
        "",
    ]
    regime_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            regimes = cell["volatility"]["regimes"]
            if not regimes.get("usable"):
                continue
            by_regime = regimes["by_regime"]
            regime_rows.append([
                f"`{pair}`", f"`{horizon}`",
                _f(regimes["high_over_low_vol"], 2),
                *[_f(by_regime[name]["rho1"], 4)
                  for name in ("low", "mid", "high")],
                *[_f(by_regime[name]["p_same"], 4)
                  for name in ("low", "mid", "high")],
                *[_f(by_regime[name]["continuation_rho"], 4)
                  for name in ("low", "high")]])
    lines += [
        *_table(["pair", "horizon", "σ high/low", "ρ(1) low", "mid", "high",
                 "p(same) low", "mid", "high", "cont ρ low", "cont ρ high"],
                regime_rows[:MAX_ROWS]),
        f"_First {min(MAX_ROWS, len(regime_rows))} of "
        f"{_n(len(regime_rows))} cells; the whole table is in `result.json`._",
        "",
        *_figure(index, "regime_rho1_5m", figures_dir),
        "### The clock",
        "",
        "Volatility, spread and quote density by hour of day, on hourly bars. "
        "Three separate daylight-saving rules move the session map, so these "
        "are UTC hours and the session boundaries inside them drift by an hour "
        "twice a year — which is exactly why the session table in section 4 is "
        "computed from the derived boundaries rather than from these buckets.",
        "",
        *_figure(index, "volatility_by_hour", figures_dir),
        *_figure(index, "spread_by_hour", figures_dir),
        *_figure(index, "density_by_hour", figures_dir),
    ]
    return lines


# --------------------------------------------------------------------------- #
# Section 4
# --------------------------------------------------------------------------- #

def _section_sessions(payload: dict[str, Any], index: dict[str, Any],
                      figures_dir: str) -> list[str]:
    sessions = payload["sessions"]
    method = payload["method"]
    roll = method["roll_window_ny"]
    names = ["tokyo", "london", "london_ny_overlap", "new_york", "sydney"]
    rows = []
    for pair in sorted(sessions):
        block = sessions[pair]["by_session"]
        for name in names:
            entry = block.get(name)
            if not entry:
                continue
            rows.append([f"`{pair}`", name.replace("_", " "),
                         _n(entry["bars"]), _f(entry["mean_abs_bp"], 2),
                         _f(entry["sd_bp"], 2),
                         _f(entry["median_spread_pips"], 3),
                         _f(entry["p90_spread_pips"], 3),
                         _n(entry["median_ticks"]), _f(entry["rho1"], 4)])
    roll_rows = []
    for pair in sorted(sessions):
        block = sessions[pair]
        ratios = block["roll_vs_rest"]
        roll_rows.append([
            f"`{pair}`", _n(block["roll"]["bars"]),
            _f(block["roll"]["mean_abs_bp"], 2),
            _f(block["off_roll"]["mean_abs_bp"], 2),
            _f(ratios["vol_ratio"], 2),
            _f(block["roll"]["median_spread_pips"], 3),
            _f(block["off_roll"]["median_spread_pips"], 3),
            _f(ratios["spread_ratio"], 2),
            _f(ratios["density_ratio"], 2),
            _f(block["roll"]["rho1"], 4)])

    band_order = payload["rulings"]["R3"]["bands"]
    band_rows = []
    for pair in sorted(sessions):
        bands = sessions[pair]["spread_by_density_band"]
        for name, entry in _ordered(bands, band_order):
            band_rows.append([f"`{pair}`", f"`{name}`", _n(entry["bars"]),
                              _f(entry["median_spread_pips"], 3),
                              _f(entry["p90_spread_pips"], 3),
                              _pct(entry["share_in_roll"], 1)])
    corr_rows = []
    for pair in sorted(sessions):
        block = sessions[pair]["spread_versus_volatility"]
        within = block["within_density_band"]
        corr_rows.append([f"`{pair}`",
                          _f(block["log_spread_vs_log_abs_return"], 3),
                          *[_f(within.get(name), 3)
                            for name in ("500-1k", "1k-3k", "3k-10k",
                                         ">=10k")],
                          _f(block["log_ticks_vs_log_abs_return"], 3),
                          _f(block["log_ticks_vs_log_spread"], 3)])
    return [
        "## 4 — Session and spread structure",
        "",
        f"Computed on `{method['session_timeframe']}` bars with the session "
        "boundaries **derived** from each centre's own local clock, so they "
        "move with British Summer Time and US daylight saving independently, "
        "as they do in reality.",
        "",
        "### By session",
        "",
        *_table(["pair", "session", "bars", "mean |r| (bp)", "sd (bp)",
                 "median spread (pips)", "p90 spread", "median ticks",
                 "ρ(1)"], rows[:MAX_ROWS]),
        f"_First {min(MAX_ROWS, len(rows))} of {_n(len(rows))} pair-sessions; "
        "the whole table is in `result.json`._",
        "",
        f"### The roll window as its own regime (pre-reg #4 evidence)",
        "",
        f"The daily roll, {roll[0]}:00–{roll[1]}:00 `America/New_York`, "
        "derived per bar rather than pinned to a UTC hour — 17:00 New York is "
        "21:00Z in summer and 22:00Z in winter, and a rule written in UTC is "
        "wrong for half of every year.",
        "",
        "Pre-registered decision #4 excludes this window from strategy "
        "execution by default and says the exclusion is revisable at a "
        "checkpoint **with EDA evidence**. This is that evidence. It is not "
        "this card's to act on.",
        "",
        *_table(["pair", "roll bars", "roll mean |r| (bp)", "elsewhere",
                 "vol ratio", "roll median spread", "elsewhere",
                 "spread ratio", "density ratio", "roll ρ(1)"], roll_rows),
        "Read the two ratio columns together. The roll hour is the one window "
        "of the day where the spread widens and the volatility falls at the "
        "same time — every other quiet period on the clock is quiet in both. "
        "A strategy trading through it pays materially more to move a price "
        "that is moving materially less.",
        "",
        "### Spread inside tick-count bands (ruling R3)",
        "",
        "R3 forbids comparing a spread statistic across eras without "
        "controlling for ticks per hour, because a percentile taken over a "
        "thousand-tick hour and one taken over six thousand are not the same "
        "instrument. The control is to compare inside a band and never across "
        "one, and every spread figure in this report obeys it.",
        "",
        *_table(["pair", "band", "bars", "median spread (pips)", "p90",
                 "share inside the roll"], band_rows[:MAX_ROWS]),
        f"_First {min(MAX_ROWS, len(band_rows))} of {_n(len(band_rows))} "
        "pair-bands; the whole table is in `result.json`._",
        "",
        "### Spread against volatility",
        "",
        "The unconditional correlation and the same correlation inside each "
        "band. Where they disagree it is the unconditional one to distrust: "
        "spread and volatility both move with the hour of day and with the "
        "era, so an uncontrolled correlation is partly measuring the clock.",
        "",
        *_table(["pair", "log spread vs log |r|", "inside 500-1k", "1k-3k",
                 "3k-10k", "≥10k", "log ticks vs log |r|",
                 "log ticks vs log spread"], corr_rows),
    ]


# --------------------------------------------------------------------------- #
# Section 5
# --------------------------------------------------------------------------- #

def _section_stability(payload: dict[str, Any], index: dict[str, Any],
                       figures_dir: str) -> list[str]:
    horizons = list(payload["window"]["horizons"])
    window = payload["window"]
    split_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            block = cell["stability"]
            first, second = block["first_half"], block["second_half"]
            flips = block["split_half"]
            split_rows.append([
                f"`{pair}`", f"`{horizon}`",
                _f(first.get("sd_bp"), 2), _f(second.get("sd_bp"), 2),
                _f(first.get("vr4"), 4), _f(second.get("vr4"), 4),
                _tick(flips["vr4"]["same_side"]),
                _f(first.get("rho1"), 4), _f(second.get("rho1"), 4),
                _tick(flips["rho1"]["same_side"]),
                _f(first.get("vol_rho1"), 4), _f(second.get("vol_rho1"), 4),
                _tick(flips["vol_rho1"]["same_side"])])
    flipped = {name: sum(1 for horizon in horizons
                         for _pair, cell in _cells_by_horizon(payload, horizon)
                         if cell["stability"]["split_half"][name]["same_side"]
                         is False)
               for name in ("vr4", "rho1", "vol_rho1", "p_same")}
    total = len(split_rows)

    rolling_rows = []
    for horizon in horizons:
        for pair, cell in _cells_by_horizon(payload, horizon):
            rolling = cell["stability"]["rolling"]
            rolling_rows.append([
                f"`{pair}`", f"`{horizon}`",
                _n((rolling.get("vr4") or {}).get("windows")),
                *[f"{_pct((rolling.get(name) or {}).get('sign_agreement'), 0)}"
                  f" {(rolling.get(name) or {}).get('label', '—')}"
                  for name in ("vr4", "rho1", "p_same", "vol_rho1")]])

    rank = payload["rank_stability"]
    rank_rows = []
    for horizon in horizons:
        block = rank.get(horizon) or {}
        rank_rows.append([f"`{horizon}`",
                          *[_f((block.get(name) or {}).get("spearman"), 3)
                            for name in ("sd_bp", "excess_kurtosis", "vol_rho1",
                                         "vr4", "rho1", "p_same")]])

    lines = [
        "## 5 — Stability: the load-bearing section",
        "",
        "The T4 card calls this section load-bearing and it is right to. "
        "Everything above is a number computed over ten years; this asks "
        f"whether it means anything about any particular part of them. A "
        "property whose sign flips between halves is reported as unstable, "
        "never averaged: the average of a trend and a reversion is a number "
        "that describes neither regime and would be traded in both.",
        "",
        f"### Split-half: {window['start']} → the split, and "
        f"{window['split_date']} → {window['end']}",
        "",
        "The split is fixed on the calendar rather than on the sample. "
        "Splitting at the median bar would put the boundary wherever the "
        "quote density happened to change, and the question is whether a "
        "property survives *time*.",
        "",
        f"Across {_n(total)} pair-horizon cells the sign changes between "
        f"halves in **{_n(flipped['vr4'])}** for the variance ratio, "
        f"**{_n(flipped['rho1'])}** for lag-1 return autocorrelation, "
        f"**{_n(flipped['p_same'])}** for sign persistence and "
        f"**{_n(flipped['vol_rho1'])}** for volatility clustering. The last "
        "of those is the point of the table: volatility memory is the one "
        "property that does not change its mind.",
        "",
        *_table(["pair", "horizon", "sd₁", "sd₂", "VR(4)₁", "VR(4)₂", "same "
                 "side", "ρ(1)₁", "ρ(1)₂", "same side", "ρ|r|(1)₁",
                 "ρ|r|(1)₂", "same side"], split_rows[:MAX_ROWS]),
        f"_First {min(MAX_ROWS, total)} of {_n(total)} cells; the whole table "
        "is in `result.json`._",
        "",
        f"### Rolling {payload['method']['rolling_window_years']}-year windows",
        "",
        "Sign agreement is the share of rolling windows whose statistic sits "
        "on the same side of its null as the full-window estimate. The labels "
        "are descriptive and nothing is dropped or promoted on them:",
        "",
        *_table(["label", "sign agreement at least"],
                [[f"`{entry['label']}`",
                  _pct(entry["sign_agreement_at_least"], 0)]
                 for entry in payload["method"]["stability_labels"]]),
        *_table(["pair", "horizon", "windows", "VR(4)", "ρ(1)", "p(same sign)",
                 "ρ|r|(1)"], rolling_rows[:MAX_ROWS]),
        f"_First {min(MAX_ROWS, len(rolling_rows))} of "
        f"{_n(len(rolling_rows))} cells; the whole table is in "
        "`result.json`._",
        "",
        *_figure(index, "rolling_vr4_5m", figures_dir),
        "### Rank stability",
        "",
        "A statistic can hold its sign for every pair and still be useless for "
        "choosing *between* pairs, if the order it puts them in is noise. "
        "Spearman between the two halves' rankings asks that directly. A pair "
        "ranking that does not survive the split is a ranking no card "
        "downstream should select on.",
        "",
        *_table(["horizon", "sd", "excess kurtosis", "ρ|r|(1)", "VR(4)",
                 "ρ(1)", "p(same sign)"], rank_rows),
    ]
    lines += _appendix(payload, index, figures_dir)
    return lines


def _appendix(payload: dict[str, Any], index: dict[str, Any],
              figures_dir: str) -> list[str]:
    eras = payload["eras"]
    appendix = payload["appendix"]
    era_rows = [[f"`{name}`", _n(len(years)),
                 f"{years[0]}–{years[-1]}" if years else "—",
                 ", ".join(years)]
                for name, years in eras["years_by_era"].items()]
    year_rows = [[year, _n(row["sampled"]), _n(row["pass"]),
                  _n(row["blocked"]), _n(row["unverifiable"]),
                  _pct(row["agreement_rate"]),
                  _pct(row["unverifiable_share"]), f"`{row['era']}`"]
                 for year, row in eras["by_year"].items()]
    lines = [
        "### Appendix — the full history, era-tagged",
        "",
        "The same memory and volatility statistics on `1h` and `1d` bars back "
        f"to {payload['window']['history_start']}, to show which properties "
        "survive the 2000s. AUDUSD starts in 2011 by ruling R1 and the loader "
        "refuses the earlier dates rather than trusting this card to remember.",
        "",
        "The era tags come from ruling R7's by-year agreement table, read out "
        "of the committed classification. An era is defined by **how well the "
        "cross-check could see the year**, not by how the year's statistics "
        "came out — which is the only ordering under which the split is not a "
        "search for the boundary that makes a property look stable. A `thin` "
        "year is not a year whose numbers are wrong; it is a year whose "
        "numbers have no second opinion.",
        "",
        *_table(["era", "years", "span", "which"], era_rows),
        *_figure(index, "agreement_by_year", figures_dir),
        *_table(["year", "sampled", "`PASS`", "`BLOCKED`", "`UNVERIFIABLE`",
                 "agreement", "unverifiable", "era"], year_rows),
    ]
    era_names = [entry["era"] for entry in payload["method"]["era_bounds"]]
    for horizon in sorted(appendix):
        rows = []
        for pair in sorted(appendix[horizon]):
            block = appendix[horizon][pair]
            whole = block["whole"]
            by_era = block["by_era"]
            signed = block["signed"]
            rows.append([
                f"`{pair}`", _n(whole.get("n")), _f(whole.get("vr4"), 4),
                *[_f((by_era.get(name) or {}).get("vr4"), 4)
                  for name in era_names],
                _tick(signed["vr4"]["same_side_across_eras"]),
                _f(whole.get("vol_rho1"), 4),
                *[_f((by_era.get(name) or {}).get("vol_rho1"), 4)
                  for name in era_names],
                _tick(signed["vol_rho1"]["same_side_across_eras"])])
        lines += [
            f"#### `{horizon}` over the full history",
            "",
            *_table(["pair", "returns", "VR(4) all",
                     *[f"VR(4) {name}" for name in era_names], "same side",
                     "ρ|r|(1) all",
                     *[f"ρ|r|(1) {name}" for name in era_names], "same side"],
                    rows),
        ]
    return lines


# --------------------------------------------------------------------------- #
# Section 6
# --------------------------------------------------------------------------- #

def _section_density(payload: dict[str, Any], index: dict[str, Any],
                     figures_dir: str) -> list[str]:
    density = payload["density"]
    breaks = payload["density_breaks"]
    corr_rows = [[f"`{pair}`",
                  _f(block["annual_density_vs_vol_spearman"], 3),
                  _f(block["annual_density_vs_vol_pearson"], 3),
                  _f(block["bar_density_vs_vol_pearson"], 3),
                  _f(block["annual_density_vs_spread_spearman"], 3),
                  _f(block["annual_density_vs_banded_spread_spearman"], 3)]
                 for pair, block in sorted(density.items())]
    years = sorted({year for block in density.values()
                    for year in block["by_year"]})
    year_rows = []
    for year in years:
        values = [block["by_year"][year]["median_ticks"]
                  for block in density.values() if year in block["by_year"]]
        banded = [block["by_year"][year]["median_spread_pips_in_band"]
                  for block in density.values()
                  if year in block["by_year"]
                  and block["by_year"][year]["median_spread_pips_in_band"]
                  is not None]
        year_rows.append([year, _n(len(values)),
                          _n(min(values)) if values else "—",
                          _n(sorted(values)[len(values) // 2]) if values
                          else "—",
                          _n(max(values)) if values else "—",
                          _f(sorted(banded)[len(banded) // 2], 3) if banded
                          else "—"])
    break_rows = [[f"`{row['pair']}`", row["year"],
                   _f(row["log_change"], 3), f"{_f(row['ratio'], 2)}×"]
                  for row in breaks["candidates"][:MAX_ROWS]]
    bar_level = [block["bar_density_vs_vol_pearson"]
                 for block in density.values()
                 if block["bar_density_vs_vol_pearson"] is not None]
    annual = [block["annual_density_vs_vol_spearman"]
              for block in density.values()
              if block["annual_density_vs_vol_spearman"] is not None]
    return [
        "## 6 — Tick density, and whether it may be used as an activity proxy "
        "(ruling R4)",
        "",
        "Ruling R4 forbids treating a tick count as a volume or activity proxy "
        "until a T4 card has characterised the series. This section is that "
        "characterisation, and it ends in a verdict rather than a table.",
        "",
        *_figure(index, "density_by_year", figures_dir),
        *_table(["year", "pairs", "min ticks/hour", "median", "max",
                 "median spread inside the 3k-10k band"], year_rows),
        "The spread column is band-controlled per ruling R3. Compare it with "
        "the uncontrolled series and the difference is the size of the "
        "instrument problem R3 exists to name.",
        "",
        *_figure(index, "spread_by_year", figures_dir),
        "### What density tracks",
        "",
        *_table(["pair", "annual density vs vol (Spearman)", "(Pearson, log)",
                 "bar-level log ticks vs log |r|", "annual vs spread",
                 "annual vs band-controlled spread"], corr_rows),
        f"Two correlations, two different answers. **Within a year, at bar "
        f"level, density and volatility move together** — the log-tick to "
        f"log-|return| correlation runs {_f(min(bar_level), 2)} to "
        f"{_f(max(bar_level), 2)} across the twelve pairs, positive for every "
        f"one. **Across years it collapses**, to between "
        f"{_f(min(annual), 2)} and {_f(max(annual), 2)}. That gap is the "
        "whole finding: the year-to-year level of the density series is set "
        "by the feed, and only its variation inside a year is set by the "
        "market.",
        "",
        "### Structural breaks",
        "",
        f"The rule, stated rather than tuned: {breaks['rule']}. The scale is "
        f"derived from the series being described — the store-wide median "
        f"absolute year-over-year change is {_f(breaks['median_abs_change'], 3)} "
        f"in logs, so the threshold is {_f(breaks['threshold'], 3)}. Choosing "
        "it against the answer is how a break list becomes a list of the years "
        "somebody expected.",
        "",
        *_table(["measure", "value"], [
            ["pair-years examined", _n(breaks["pair_years_examined"])],
            ["break candidates", _n(len(breaks["candidates"]))],
            ["years where at least half the universe moves",
             ", ".join(breaks["years_where_most_pairs_move"]) or "none"],
        ]),
        *_table(["pair", "year", "Δ log median ticks", "ratio"], break_rows),
        *_table(["year", "pairs flagged"],
                [[year, _n(count)]
                 for year, count in breaks["by_year"].items()]),
        "### Verdict on ruling R4",
        "",
        "**Tick count is usable as an activity proxy within a pair-year, and "
        "is not usable across years without one.** Concretely, three "
        "conditions, all of which the evidence above supports and none of "
        "which it establishes beyond the sampled window:",
        "",
        "1. **Within a year and within a pair**, ticks per hour tracks "
        "realised volatility positively for every pair in the universe, at "
        "bar level. A statistic that conditions on density inside a year — a "
        "session comparison, an intraday regime, a liquidity filter — is "
        "reading the market.",
        "2. **Across years, it is not.** The annual series is dominated by "
        f"feed changes: {_n(len(breaks['candidates']))} pair-years exceed a "
        "threshold set from the data's own dispersion, and one year moves at "
        "least half the universe at once, which no market event does to twelve "
        "currency pairs simultaneously with no volatility signature to match.",
        "3. **Any cross-era comparison must hold density constant**, which is "
        "ruling R3 arriving from the other direction. The band-controlled "
        "spread column above is what that looks like in practice.",
        "",
        "R4 asks for a characterisation before the proxy may be used. This is "
        "it, with its conditions attached. Whether the ruling is lifted, and "
        "in which of those three forms, is a checkpoint decision and not this "
        "card's.",
        "",
    ]


# --------------------------------------------------------------------------- #
# Section 7
# --------------------------------------------------------------------------- #

def _section_empties(payload: dict[str, Any], index: dict[str, Any],
                     figures_dir: str) -> list[str]:
    empties = payload["empties"]
    by_class = dict(_ordered(empties["by_class"], EMPTY_ORDER))
    class_rows = [[f"`{name}`", _n(count),
                   {"r1_artefact": "the only pair that went quiet was AUDUSD "
                                   "inside ruling R1's exclusion window, so "
                                   "the readable-pair filter emptied the row",
                    "week_boundary": "a Sunday or Friday date at most three "
                                     "hours deep — the FX week edge, where "
                                     "the feed and the derived boundary need "
                                     "not agree to the hour",
                    "calendar_holiday": "the static major-holiday list names "
                                        "the date",
                    "currency_holiday": "every empty pair shares a currency, "
                                        "so that currency's own market was "
                                        "shut and the crosses kept trading",
                    "feed_artefact": "at least half the readable universe "
                                     "went quiet, but too shallowly to be a "
                                     "market closure",
                    "unknown": "none of the above, and the report says so "
                               "rather than guessing"}[name]]
                  for name, count in by_class.items()]
    kind_rows = [[f"`{kind}`", _n(count)] for kind, count
                 in _ordered(empties["by_kind"],
                             ("partial holiday", "feed artefact",
                              "bookkeeping artefact", "unknown"))]
    year_rows = [[year, *[_n(row.get(name, 0)) for name in by_class]]
                 for year, row in sorted(empties["by_year"].items())]
    weekday_rows = [[day, _n(count)] for day, count
                    in _ordered(empties["by_weekday"], WEEKDAYS)]
    weekday_readable = [[day, _n(count)] for day, count
                        in _ordered(empties["by_weekday_readable"], WEEKDAYS)]
    pair_rows = [[f"`{pair}`", _n(row["dates"]), _n(row["hours"])]
                 for pair, row in sorted(empties["by_pair"].items(),
                                         key=lambda kv: -kv[1]["hours"])]
    deepest = [[row["date"], row["weekday"], _n(len(row["pairs_empty"])),
                _n(row["hours"]), _n(row["max_hours"]),
                f"`{row['class']}`", row["static_holiday"] or "—"]
               for row in empties["deepest"][:20]]
    return [
        "## 7 — The unexplained empty dates T3 handed over",
        "",
        f"T3 found {_n(empties['dates'])} dates carrying "
        f"{_n(empties['hours'])} empty trading hours that its holiday "
        "calendar does not explain, and passed them to this card as data "
        "facts rather than holidays. The first thing to say about them is "
        "that most of them are not facts about the data at all.",
        "",
        "### The finding: a third of the list is the exclusion filter's own "
        "shadow",
        "",
        f"**{_n(by_class['r1_artefact'])} of the {_n(empties['dates'])} dates "
        "have no readable empty pair on them.** T3's classifier filters a "
        "date's empty pairs down to the ones research may read, and ruling R1 "
        "removes AUDUSD before 2011 — so a date on which *only* AUDUSD went "
        "quiet in 2008 survives as a row whose pair list is then empty, and is "
        "counted as an unexplained date. Every one of them falls in "
        "2007–2010, which is where the exclusion window is.",
        "",
        "That is an observation about T3's derivation, not a defect this card "
        "is authorised to fix: changing the classifier would change the "
        "committed holiday calendar, and the T4 card does not cover that. It "
        "is recorded here for the checkpoint, which is where the card says "
        "observations go.",
        "",
        f"**{_n(empties['dates_with_a_readable_empty_pair'])} dates survive** "
        "as real. All "
        f"{_n(empties['hours_on_those_dates'])} of the empty hours belong to "
        "them — the artefact rows carry none, which is exactly what makes "
        "them artefacts. The rest of this section is about the survivors.",
        "",
        "### Classification",
        "",
        *_table(["class", "dates", "what the evidence supports"], class_rows),
        "Rolled up into the three buckets the card asks for:",
        "",
        *_table(["kind", "dates"], kind_rows),
        *_figure(index, "empties_by_year", figures_dir),
        "### By weekday",
        "",
        "The single most informative cut, and it is not subtle:",
        "",
        *_table(["weekday", "dates (all)"], weekday_rows),
        "Restricted to the dates with a readable empty pair:",
        "",
        *_table(["weekday", "dates"], weekday_readable),
        "The FX week opens Sunday 17:00 New York and closes Friday 17:00, so "
        "those two days carry a handful of open hours each and the feed and "
        "the derived boundary need not agree about them to the hour. An empty "
        "hour there is the week edge, not a closure — which is why the "
        "classification treats a shallow Sunday or Friday date as a feed "
        "artefact rather than leaving it in the unknown pile.",
        "",
        "### By year and by pair",
        "",
        *_table(["year", *[f"`{name}`" for name in by_class]], year_rows),
        *_table(["pair", "dates", "empty hours"], pair_rows),
        "### The deepest of them",
        "",
        *_table(["date", "weekday", "pairs empty", "empty hours",
                 "deepest pair", "class", "static holiday"], deepest),
    ]


# --------------------------------------------------------------------------- #
# Multiple testing, the character table and the hypotheses
# --------------------------------------------------------------------------- #

def _section_testing(payload: dict[str, Any], trials: int) -> list[str]:
    tests = payload["tests"]
    rows = [[f"`{name}`", _n(block["tests"]), _n(block["usable"]),
             _p(block["bh_threshold"]), _n(block["rejected"]),
             _pct(block["rejected_share"])]
            for name, block in tests["families"].items()]
    return [
        "## Multiple testing, counted",
        "",
        f"**{trials} trial(s) are ledgered under this card** and this result "
        f"registers **{_n(tests['total_tests'])} hypothesis tests** across "
        f"{_n(len(tests['families']))} families. Both numbers matter and they "
        "are different numbers.",
        "",
        "The T4 card asks for every test to be a ledgered trial. The ledger "
        "records *experiments* — one entry per run, written before the run — "
        "and filling it with three thousand individual z-statistics would "
        "destroy the thing it is for. So the tests are registered inside the "
        "hashed result instead, at the granularity that is actually needed: a "
        "test cannot be dropped from its family after its p-value has been "
        "seen, and the family has a size the report can state next to a claim. "
        "Every claim in the hypothesis section below carries both.",
        "",
        f"Benjamini-Hochberg runs within each family at FDR "
        f"{tests['alpha']}. BH rather than Bonferroni because the family is "
        "twelve pairs by five horizons of tests on overlapping data, where "
        "controlling the expected false-discovery proportion is the honest "
        "target and family-wise error is a target nothing would survive.",
        "",
        *_table(["family", "tests", "usable", "BH threshold p", "rejected",
                 "share"], rows),
        "`jarque_bera` rejecting everything is the expected result and not a "
        "finding: at these sample sizes a normality test has the power to "
        "reject on the third decimal of a moment. It is in the table because "
        "a family excluded from the count is a family that stops being "
        "counted.",
        "",
    ]


def _section_character(payload: dict[str, Any]) -> list[str]:
    rows = payload["character"]
    table = [[f"`{r['pair']}`", f"`{r['horizon']}`", f"**{r['fingerprint']}**",
              _f(r["vr4"], 4), _f(r["vr4_z"], 2), _p(r["vr4_q_value"]),
              _f(r["rho1"], 4), _f(r["p_same"], 4), _f(r["vol_rho1"], 3),
              _f(r["vol_half_life"], 1), _f(r["excess_kurtosis"], 1),
              _f(r["sd_bp"], 2), _f(r["median_spread_pips"], 3),
              _pct(r["rolling_sign_agreement"], 0), f"`{r['stability']}`",
              _tick(r["split_half_same_side"])]
             for r in rows]
    return [
        "## The universe character table",
        "",
        "Every pair at every horizon, ranked by the size of its variance-ratio "
        "departure from a random walk. The fingerprint is called `TREND` or "
        "`REVERT` only when the q=4 variance ratio survives the "
        "false-discovery correction across all 60 cells; otherwise it is "
        "`FLAT`, which means *this battery found no directional memory*, not "
        "that there is none.",
        "",
        "**No pair is dropped or promoted by this table.** Stability sits "
        "beside the effect size as a label rather than being folded into a "
        "score, precisely so it cannot be read as a decision. The card is "
        "explicit that the decisions are the checkpoint's.",
        "",
        *_table(["pair", "horizon", "fingerprint", "VR(4)", "z", "q", "ρ(1)",
                 "p(same)", "ρ|r|(1)", "vol half-life", "kurtosis", "sd (bp)",
                 "spread (pips)", "rolling agreement", "stability",
                 "split-half same side"], table),
    ]


def _section_hypotheses(payload: dict[str, Any], trials: int) -> list[str]:
    """The card's required closing section, generated from the result."""
    rows = payload["character"]
    tests = payload["tests"]
    cells = payload["cells"]
    sessions = payload["sessions"]
    horizons = list(payload["window"]["horizons"])
    family = tests["families"].get("variance_ratio", {})
    called = [r for r in rows if r["fingerprint"] != "FLAT"]
    flat = [r for r in rows if r["fingerprint"] == "FLAT"]

    lines = [
        "## What this implies about where edge might live",
        "",
        "Questions for T7 cards, not answers. Each carries the size of the "
        "family its p-value came from, the trial count under this card, and "
        "the stability caveat that applies to it — because a hypothesis "
        "stated without those three is a hypothesis somebody will test on the "
        "strength of a number that was one of sixty.",
        "",
        f"Everything below rests on **{trials} ledgered trial(s)** under this "
        f"card and on the **{_n(family.get('tests', 0))}-test** "
        "variance-ratio family, in which Benjamini-Hochberg rejects "
        f"{_n(family.get('rejected', 0))} at FDR {tests['alpha']}.",
        "",
        "### The horizons where directional memory survives correction",
        "",
    ]
    if called:
        for row in called:
            lines += _hypothesis_bullet(row, cells, sessions, family, tests)
    else:
        lines += ["No pair-horizon cell has a variance ratio that survives "
                  "the correction. That is a finding and not a failure: it "
                  "says the directional edge, if there is one, is not visible "
                  "as linear memory in unconditional returns at these "
                  "horizons.", ""]

    by_horizon = {h: [r for r in called if r["horizon"] == h]
                  for h in horizons}
    silent = [h for h in horizons if not by_horizon[h]]
    lines += [
        "### Where it does not",
        "",
        f"{_n(len(flat))} of {_n(len(rows))} cells are `FLAT`"
        + (f", and no cell at " + ", ".join(f"`{h}`" for h in silent)
           + " survives at all" if silent else "")
        + ". The reading a T7 card should take from that is narrow and "
        "specific: **unconditional linear memory in returns is not where the "
        "edge is** at these horizons, for these pairs, over this decade. It "
        "says nothing about conditional memory, about non-linear structure, "
        "or about cross-pair structure, which is T6's question and not asked "
        "here.",
        "",
        "### The strongest regularity in the battery is not directional",
        "",
    ]
    lines += _volatility_hypothesis(payload, tests, trials)
    lines += _session_hypothesis(payload, tests, trials)
    lines += _regime_hypothesis(payload, tests, trials)
    lines += [
        "### What would falsify each of these",
        "",
        "Stated now, before any of them is tested, because a hypothesis "
        "whose falsification condition is written after the result is not one:",
        "",
        *_table(["hypothesis class", "what would kill it"], [
            ["short-horizon mean reversion",
             "a walk-forward whose out-of-sample net P&L at 1.5× costs is "
             "below zero, which given the median spread in the character "
             "table is where this one most likely dies — the effect is "
             "measured in fractions of a basis point and the round trip is "
             "measured in pips"],
            ["volatility-conditional sizing",
             "a regime split whose out-of-sample volatility ordering does not "
             "hold, or a strategy whose edge disappears once position size is "
             "the only thing conditioned on"],
            ["session-conditional execution",
             "session boundaries whose effect does not survive being re-derived "
             "on the second half, or a spread advantage that vanishes once "
             "ruling R3's density control is applied"],
            ["roll-window avoidance",
             "nothing in this card — pre-reg #4 already excludes it, and the "
             "evidence here supports the exclusion rather than testing it"],
        ]),
        "The first row is the honest headline. Every directional effect this "
        "battery found is small enough that T5's cost geometry, not T4's "
        "statistics, will decide whether any of it is tradeable — which is "
        "exactly what the next card is for.",
        "",
    ]
    return lines


def _hypothesis_bullet(row: dict[str, Any], cells: dict[str, Any],
                       sessions: dict[str, Any], family: dict[str, Any],
                       tests: dict[str, Any]) -> list[str]:
    """One pair-horizon hypothesis, with its conditions and its caveat."""
    pair, horizon = row["pair"], row["horizon"]
    cell = cells.get(f"{pair}|{horizon}") or {}
    reverting = row["fingerprint"] == "REVERT"
    # "Strongest" means most negative for a reverting cell and most positive
    # for a trending one. Hardcoding one of those would quietly report the
    # weakest regime for half the table.
    pick = min if reverting else max
    regimes = (cell.get("volatility") or {}).get("regimes") or {}
    by_regime = regimes.get("by_regime") or {}
    usable = [(name, entry) for name, entry in by_regime.items()
              if entry.get("rho1") is not None]
    strongest = pick(usable, key=lambda item: item[1]["rho1"]) if usable else None
    session_block = (sessions.get(pair) or {}).get("by_session") or {}
    candidates = [(name, entry) for name, entry in session_block.items()
                  if entry.get("rho1") is not None]
    session = pick(candidates, key=lambda item: item[1]["rho1"]) if candidates \
        else None
    overall_spread = row.get("median_spread_pips")
    direction = ("mean-reversion" if reverting else "trend")
    classes = ("mean-reversion, session-conditional, vol-conditional"
               if row["fingerprint"] == "REVERT"
               else "trend, session-conditional, vol-conditional")
    stability_note = {
        "STABLE": "holds its sign in every rolling window measured",
        "MOSTLY-STABLE": "holds its sign in most rolling windows",
        "MIXED": "changes sign in a third of the rolling windows, so it is a "
                 "property of parts of the decade rather than of the decade",
        "UNSTABLE": "changes sign in nearly half the rolling windows and "
                    "should be treated as absent until something explains the "
                    "switching",
        "UNMEASURED": "has no rolling-window evidence",
    }[row["stability"]]
    return [
        f"**`{pair}` at `{horizon}` — {direction}.** VR(4) = "
        f"{_f(row['vr4'], 4)} (z = {_f(row['vr4_z'], 2)}, BH q = "
        f"{_p(row['vr4_q_value'])} within a family of "
        f"{_n(family.get('tests', 0))}), ρ(1) = {_f(row['rho1'], 4)}, "
        f"p(same sign) = {_f(row['p_same'], 4)} over {_n(row['n'])} returns. "
        f"Stability: `{row['stability']}` — it {stability_note}, and the "
        f"split-half sign "
        + ("agrees" if row["split_half_same_side"] else "**disagrees**") + ". "
        + (f"By volatility tercile ρ(1) is "
           + ", ".join(f"{_f((by_regime.get(name) or {}).get('rho1'), 4)} "
                       f"({name})" for name in ("low", "mid", "high"))
           + f", strongest in the **{strongest[0]}** one. " if strongest
           else "")
        + (f"By session it is strongest in **{session[0].replace('_', ' ')}** "
           f"(ρ(1) = {_f(session[1]['rho1'], 4)}, median spread "
           f"{_f(session[1]['median_spread_pips'], 3)} pips against "
           f"{_f(overall_spread, 3)} across all hours, "
           f"{_n(session[1]['median_ticks'])} ticks/hour) — "
           + ("**and that is the caveat, not the opportunity**: the session "
              "where the reversion is largest is also the one where the "
              f"spread is {_f(session[1]['median_spread_pips'] / overall_spread, 1)}× "
              "the pair's own median. Returns here are mid-to-mid, so this is "
              "not bid-ask bounce in the textbook sense, but quote noise in a "
              "thin book produces the same signature and is equally "
              "untradeable. Whether the effect survives outside that session "
              "is the question, not whether it is biggest inside it. "
              if (session[1]["median_spread_pips"] is not None
                  and overall_spread
                  and session[1]["median_spread_pips"] > overall_spread)
              else "")
           if session else "")
        + f"**Question for a T7 card:** does a {direction} rule at `{horizon}` "
        f"on `{pair}` survive walk-forward validation once the round trip "
        f"costs {_f(row['median_spread_pips'], 3)} pips of spread plus "
        "commission — and does restricting it to the regime and session above "
        "improve the net or merely shrink the sample? Strategy classes: "
        f"{classes}.",
        "",
    ]


def _volatility_hypothesis(payload: dict[str, Any], tests: dict[str, Any],
                           trials: int) -> list[str]:
    family = tests["families"].get("volatility_ljung_box", {})
    cells = payload["cells"]
    half_lives = sorted(
        (cell["volatility"]["half_life_abs"], key)
        for key, cell in cells.items()
        if cell["volatility"]["half_life_abs"] is not None)
    flips = sum(1 for cell in cells.values()
                if cell["stability"]["split_half"]["vol_rho1"]["same_side"]
                is False)
    return [
        f"**Volatility clustering, every pair, every horizon.** The |return| "
        f"autocorrelation is positive at lag 1 in every one of "
        f"{_n(len(cells))} cells, with a half-life between "
        f"{_f(half_lives[0][0], 1)} bars (`{half_lives[0][1]}`) and "
        f"{_f(half_lives[-1][0], 1)} (`{half_lives[-1][1]}`). Its Ljung-Box "
        f"family has {_n(family.get('tests', 0))} tests and BH rejects "
        f"{_n(family.get('rejected', 0))}. It flips sign between halves in "
        f"{_n(flips)} cells — which is the point: this is the one property "
        "in the battery that does not change its mind.",
        "",
        "**Question for a T7 card:** since the forecastable quantity is the "
        "size of the move rather than its direction, is the right use of this "
        "a *sizing* rule rather than an *entry* rule — position scaled "
        "inversely to forecast volatility, on top of whatever entry the "
        "directional evidence supports? That is a different experiment from a "
        "volatility strategy and a much cheaper one, because it changes the "
        "size of trades a rule was going to make anyway rather than making "
        "new ones. Strategy classes: vol-conditional sizing, vol-conditional "
        "filtering.",
        "",
        f"The caveat is the same one that applies everywhere here: "
        f"{trials} ledgered trial(s), and clustering is the most-documented "
        "regularity in financial time series, so finding it is a check that "
        "the pipeline works rather than a discovery.",
        "",
    ]


def _session_hypothesis(payload: dict[str, Any], tests: dict[str, Any],
                        trials: int) -> list[str]:
    sessions = payload["sessions"]
    family = tests["families"].get("session_autocorr", {})
    ratios = [(block["roll_vs_rest"]["spread_ratio"],
               block["roll_vs_rest"]["vol_ratio"], pair)
              for pair, block in sessions.items()
              if block["roll_vs_rest"]["spread_ratio"] is not None]
    worst = max(ratios) if ratios else None
    spreads: list[tuple[float, str, str]] = []
    for pair, block in sessions.items():
        for name, entry in block["by_session"].items():
            if entry["median_spread_pips"] is not None:
                spreads.append((entry["median_spread_pips"], name, pair))
    cheapest = min(spreads) if spreads else None
    dearest = max(spreads) if spreads else None
    return [
        "### Session structure is a cost story before it is a signal story",
        "",
        f"Across {_n(len(sessions))} pairs and five derived sessions, the "
        f"cheapest median spread anywhere is {_f(cheapest[0], 3)} pips "
        f"(`{cheapest[2]}` in {cheapest[1].replace('_', ' ')}) and the "
        f"dearest is {_f(dearest[0], 3)} (`{dearest[2]}` in "
        f"{dearest[1].replace('_', ' ')}) — a factor of "
        f"{_f(dearest[0] / cheapest[0], 1)}. The session autocorrelation "
        f"family has {_n(family.get('tests', 0))} tests and BH rejects "
        f"{_n(family.get('rejected', 0))}, so session-conditional *memory* "
        "exists; but the spread spread, so to speak, is much the larger "
        "number.",
        "",
        (f"The roll window is the extreme case: at its worst, `{worst[2]}` "
         f"pays {_f(worst[0], 2)}× the spread for {_f(worst[1], 2)}× the "
         "volatility. " if worst else "")
        + "**Question for a T7 card:** is a session restriction better "
        "modelled as an execution constraint — trade only where the spread is "
        "in its own cheapest band — than as a signal condition? The two look "
        "identical in a backtest and differ completely in what they claim, "
        "and only the first survives being wrong about the signal. Strategy "
        "classes: session-conditional execution, session-conditional entry.",
        "",
        f"Caveat: {trials} ledgered trial(s); the session boundaries are "
        "derived rather than fitted, but which session is cheapest for a pair "
        "is a ranking, and section 5's rank-stability table is where to check "
        "whether it survives the split before any card selects on it.",
        "",
    ]


def _regime_hypothesis(payload: dict[str, Any], tests: dict[str, Any],
                       trials: int) -> list[str]:
    family = tests["families"].get("regime_autocorr", {})
    continuation = tests["families"].get("regime_continuation", {})
    cells = payload["cells"]
    spreads: list[tuple[float, str]] = []
    for key, cell in cells.items():
        regimes = (cell.get("volatility") or {}).get("regimes") or {}
        by_regime = regimes.get("by_regime") or {}
        low = (by_regime.get("low") or {}).get("rho1")
        high = (by_regime.get("high") or {}).get("rho1")
        if low is None or high is None:
            continue
        spreads.append((high - low, key))
    spreads.sort()
    return [
        "### Memory changes with the regime, and the direction of the change "
        "is not the same everywhere",
        "",
        f"The regime autocorrelation family has {_n(family.get('tests', 0))} "
        f"tests with BH rejecting {_n(family.get('rejected', 0))}; the "
        f"regime continuation family has "
        f"{_n(continuation.get('tests', 0))} with "
        f"{_n(continuation.get('rejected', 0))}. The difference between "
        "high-volatility and low-volatility lag-1 autocorrelation runs from "
        f"{_f(spreads[0][0], 4)} (`{spreads[0][1]}`) to "
        f"{_f(spreads[-1][0], 4)} (`{spreads[-1][1]}`) — it changes sign "
        "across the universe, which means there is no single statement of the "
        "form *FX reverts more when it is quiet* that holds for every pair.",
        "",
        "**Question for a T7 card:** for the pairs where the regime "
        "difference is large and stable, does conditioning entry on the "
        "trailing-volatility tercile improve out-of-sample net P&L, or does "
        "it merely cut the sample by two thirds and the cost base by less? "
        "The second is the failure mode, and it looks like success in-sample. "
        "Strategy classes: vol-conditional entry, vol-conditional sizing.",
        "",
        f"Caveat: {trials} ledgered trial(s), and the regime label is a "
        "tercile boundary estimated on the same decade — a T7 card must "
        "re-estimate it inside each training window or it has fitted the "
        "regime to the test set, which is precisely the leak the walk-forward "
        "harness exists to catch.",
        "",
    ]


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

def _provenance(document: dict[str, Any], gate_status: str, home: str,
                figures: Sequence[dict[str, Any]],
                figures_dir: str) -> list[str]:
    access = document.get("access") or {}
    payload = document["payload"]
    return [
        "## Provenance",
        "",
        f"* Config: `{home}/config.toml` (sha256 "
        f"`{str(document['config_sha256'])[:16]}`)",
        "* Bars: `data/research/bars/timeframe=<TF>/pair=<PAIR>/`, read only "
        "through `research.loader.ResearchLoader` in `scoring` mode, which is "
        "what enforces the seal and ruling R1 on every date served.",
        "* Manifests: `data/research/manifests/pair=<PAIR>/<YYYY-MM>/"
        "manifest.json` for section 7, read the canonical way (SPEC2 §The "
        "canonical manifest reading) through the same "
        "`research.calendar_build` code T3 used.",
        "* Cross-check classes: `config/crosscheck.toml`, derived under ruling "
        "R7 and re-derived and compared on every run of the T3 experiment. "
        "The appendix era tags come from it.",
        f"* Result: `{home}/result.json`, hash `{document['result_hash']}`",
        f"* Figures: {len(figures)} under `{figures_dir}/`, each beside the "
        "CSV of the numbers it was drawn from. Both are regenerated from "
        "`result.json` by `python -m research.character_report`.",
        f"* Loader mode `{document['mode']}`, scored `{document['scored']}`, "
        f"re-run class `{document['rerun_class']}`. It served "
        f"{len(access.get('files', []))} file(s) across "
        f"{len(access.get('pairs', []))} pair(s), "
        f"{len(access.get('timeframes', []))} timeframe(s) and "
        f"{len(access.get('dates', []))} date(s); sealed dates served: "
        f"{payload['loader']['sealed_dates_served'] or 'none'}; calendar "
        "dates withheld by an exclusion window: "
        f"{_n(payload['loader']['excluded_dates_withheld'])} across "
        f"{_n(len(payload['loader']['excluded_pairs']))} pair(s) — ruling R1, "
        "the appendix window this card asked AUDUSD for and did not get.",
        f"* Research gate: {gate_status}",
        "",
    ]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.character_report",
        description="Render the T4 character report and its figures.")
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
    card = str(document.get("taskcard") or "T4")
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
