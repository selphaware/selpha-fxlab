"""Every figure in the T4 report, drawn from the result document.

Ruling R6 in its strongest form: nothing here reads data, opens a bar table or
recomputes a statistic. Each function takes the payload the experiment already
hashed and turns part of it into a picture, and writes the numbers it drew from
next to the picture as a CSV -- which is what the T4 card means by "figures
saved under ``reports/T4/`` with their source tables". A figure whose numbers
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

#: Pairs, in the order SPEC2 pre-reg #9 lists the universe, so a colour means
#: the same pair in every figure of the report.
def _colour(pairs: Sequence[str], pair: str) -> str:
    """A stable colour for a pair across every figure."""
    return PALETTE[list(pairs).index(pair) % len(PALETTE)]


def _categorical(labels: Sequence[str]) -> list[tuple[float, str]]:
    """Tick positions for a categorical x-axis."""
    return [(float(i), str(label)) for i, label in enumerate(labels)]


def build_all(payload: dict[str, Any], out_dir: pathlib.Path
              ) -> list[dict[str, Any]]:
    """Draw every figure and return a manifest the report links to."""
    pairs = sorted({row["pair"] for row in payload["character"]})
    horizons = list(payload["window"]["horizons"])
    manifest: list[dict[str, Any]] = []

    def record(entry: dict[str, str], name: str, caption: str) -> None:
        manifest.append({"name": name, "caption": caption, **entry})

    record(_horizon_line(payload, pairs, horizons,
                         out_dir / "kurtosis_by_horizon.svg",
                         "excess_kurtosis",
                         "Excess kurtosis by horizon (log10 scale)",
                         "log10 excess kurtosis", transform="log10"),
           "kurtosis_by_horizon",
           "Excess kurtosis of log returns at each horizon, one line per pair, "
           "on a log10 axis. Linear, one pair's SNB-de-peg outlier is four "
           "orders of magnitude above the rest and flattens every other line "
           "onto the floor. The CSV carries the untransformed values.")
    record(_horizon_line(payload, pairs, horizons,
                         out_dir / "tail_ratio_by_horizon.svg",
                         "tail_ratio_p999",
                         "Tail ratio at the 99.9th percentile, by horizon",
                         "empirical / Gaussian quantile", reference=1.0,
                         reference_caption="Gaussian"),
           "tail_ratio_by_horizon",
           "How much larger the 1-in-1,000 move is than a Gaussian of the same "
           "variance would put there. 1.0 is Gaussian.")
    record(_horizon_line(payload, pairs, horizons,
                         out_dir / "sd_by_horizon.svg", "sd_bp",
                         "Return standard deviation by horizon (log10 scale)",
                         "log10 standard deviation (basis points)",
                         transform="log10"),
           "sd_by_horizon",
           "Return standard deviation in basis points at each horizon, log10. "
           "Under square-root-of-time scaling these lines would be straight "
           "and parallel, since the horizon ladder is close to geometric; "
           "where a pair bends, its variance is not accumulating linearly. "
           "The CSV carries the untransformed values.")

    for horizon in horizons:
        record(_vr_profile(payload, pairs, horizon, out_dir),
               f"variance_ratio_{_slug(horizon)}",
               f"Variance-ratio profile at the {horizon} horizon. Above 1 is "
               "trending, below 1 is reverting, and the dashed line is the "
               "random walk.")

    for horizon in ("5m", "1h"):
        if horizon in horizons:
            record(_vol_acf(payload, pairs, horizon, out_dir),
                   f"volatility_acf_{_slug(horizon)}",
                   f"Autocorrelation of |return| by lag at the {horizon} "
                   "horizon -- the volatility-clustering signature.")

    record(_clock(payload, pairs, out_dir / "volatility_by_hour.svg",
                  "mean_abs_bp", "Mean |return| by hour of day (1h bars)",
                  "mean |return| (basis points)"),
           "volatility_by_hour",
           "Mean absolute hourly return by UTC hour, one line per pair.")
    record(_clock(payload, pairs, out_dir / "spread_by_hour.svg",
                  "median_spread_pips",
                  "Median spread by hour of day (1h bars)",
                  "median spread (pips)"),
           "spread_by_hour",
           "Median quoted spread by UTC hour. The roll window is the spike.")
    record(_clock(payload, pairs, out_dir / "density_by_hour.svg",
                  "median_ticks", "Median ticks per hour, by hour of day",
                  "ticks per hour"),
           "density_by_hour",
           "Median tick count by UTC hour -- the density series R4 asks to "
           "characterise, at its finest published grain.")

    record(_density_by_year(payload, pairs, out_dir),
           "density_by_year",
           "Median ticks per hour by calendar year, full history. AUDUSD "
           "starts in 2011 by ruling R1.")
    record(_spread_by_year(payload, pairs, out_dir),
           "spread_by_year",
           "Median spread by year measured only inside the 3k-10k ticks-per-"
           "hour band, which is ruling R3's control: a spread compared across "
           "eras must be compared at constant quote density.")
    record(_agreement_by_year(payload, out_dir),
           "agreement_by_year",
           "Ruling R7's by-year cross-check agreement and the share of hours "
           "it could not verify. The era tags in the appendix come from the "
           "second series.")
    record(_rolling(payload, pairs, out_dir),
           "rolling_vr4_5m",
           "Variance ratio at q=4 on rolling two-year windows of 5-minute "
           "returns. The dashed line is the random walk; a series that crosses "
           "it is a property that changed sign inside the decade.")
    record(_regimes(payload, pairs, out_dir),
           "regime_rho1_5m",
           "Lag-1 return autocorrelation inside each trailing-volatility "
           "tercile, 5-minute bars. The regime label uses only returns before "
           "the one it labels.")
    record(_empties(payload, out_dir),
           "empties_by_year",
           "Unexplained empty dates by year and class -- the 312 dates T3 "
           "handed to this card.")
    return manifest


def _slug(text: str) -> str:
    """A filename-safe form of a horizon label."""
    return str(text).replace(".", "").replace("/", "")


# --------------------------------------------------------------------------- #
# Sections 1 and 2
# --------------------------------------------------------------------------- #

def _horizon_line(payload: dict[str, Any], pairs: Sequence[str],
                  horizons: Sequence[str], path: pathlib.Path, field: str,
                  title: str, y_label: str, reference: float | None = None,
                  reference_caption: str = "",
                  transform: str | None = None) -> dict[str, str]:
    """One line per pair across the horizon ladder.

    ``transform="log10"`` plots the base-10 logarithm while the CSV keeps the
    raw value, which is the only honest way to draw a quantity whose range
    spans four orders of magnitude because one pair had one bad afternoon.
    """
    lookup = {(key.split("|", 1)[0], key.split("|", 1)[1]): cell["returns"]
              for key, cell in payload["cells"].items()}
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        points: list[tuple[float, float]] = []
        for index, horizon in enumerate(horizons):
            row = lookup.get((pair, horizon))
            value = None if row is None else row.get(field)
            table.append([pair, horizon, value])
            if value is None:
                continue
            plotted = float(value)
            if transform == "log10":
                if plotted <= 0.0:
                    continue
                plotted = math.log10(plotted)
            points.append((float(index), plotted))
            values.append(plotted)
        series[pair] = points
    figure = Figure(title, x_label="horizon", y_label=y_label,
                    x_range=(0.0, float(len(horizons) - 1)),
                    y_range=data_range(values),
                    x_ticks=_categorical(horizons))
    if reference is not None:
        figure.reference(reference, reference_caption)
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
        figure.dots(series[pair], _colour(pairs, pair))
    return write(figure, path, table, ["pair", "horizon", field])


def _vr_profile(payload: dict[str, Any], pairs: Sequence[str], horizon: str,
                out_dir: pathlib.Path) -> dict[str, str]:
    """Variance ratio against q, one line per pair, at one horizon."""
    horizons = [int(q) for q in payload["method"]["vr_horizons"]]
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        cell = payload["cells"].get(f"{pair}|{horizon}")
        points: list[tuple[float, float]] = []
        if cell:
            by_q = {row["q"]: row for row in cell["memory"]["variance_ratio"]}
            for index, q in enumerate(horizons):
                row = by_q.get(q) or {}
                table.append([pair, q, row.get("vr"), row.get("z"),
                              row.get("p_value")])
                if row.get("vr") is None:
                    continue
                points.append((float(index), float(row["vr"])))
                values.append(float(row["vr"]))
        series[pair] = points
    values.append(1.0)
    figure = Figure(f"Variance-ratio profile, {horizon} returns",
                    x_label="q (bars aggregated)", y_label="VR(q)",
                    x_range=(0.0, float(len(horizons) - 1)),
                    y_range=data_range(values),
                    x_ticks=_categorical([str(q) for q in horizons]))
    figure.reference(1.0, "random walk")
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
        figure.dots(series[pair], _colour(pairs, pair))
    return write(figure, out_dir / f"variance_ratio_{_slug(horizon)}.svg",
                 table, ["pair", "q", "vr", "z", "p_value"])


def _vol_acf(payload: dict[str, Any], pairs: Sequence[str], horizon: str,
             out_dir: pathlib.Path) -> dict[str, str]:
    """Autocorrelation of |return| against lag, one line per pair."""
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    lags = 0
    for pair in pairs:
        cell = payload["cells"].get(f"{pair}|{horizon}")
        points: list[tuple[float, float]] = []
        if cell:
            acf = cell["volatility"]["acf_abs"]
            lags = max(lags, len(acf))
            for lag, value in enumerate(acf, 1):
                table.append([pair, lag, value])
                if value is None:
                    continue
                points.append((float(lag), float(value)))
                values.append(float(value))
        series[pair] = points
    figure = Figure(f"Autocorrelation of |return| by lag, {horizon} bars",
                    x_label="lag (bars)", y_label="autocorrelation of |r|",
                    x_range=(1.0, float(max(lags, 2))),
                    y_range=data_range(values + [0.0]))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / f"volatility_acf_{_slug(horizon)}.svg",
                 table, ["pair", "lag", "acf_abs"])


# --------------------------------------------------------------------------- #
# Sections 3, 4 and 6
# --------------------------------------------------------------------------- #

def _clock(payload: dict[str, Any], pairs: Sequence[str], path: pathlib.Path,
           field: str, title: str, y_label: str) -> dict[str, str]:
    """One line per pair across the 24 UTC hours."""
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        clock = (payload["clock"].get(pair) or {}).get("by_hour_utc") or {}
        points: list[tuple[float, float]] = []
        for hour in range(24):
            row = clock.get(f"{hour:02d}") or {}
            value = row.get(field)
            table.append([pair, hour, value])
            if value is None:
                continue
            points.append((float(hour), float(value)))
            values.append(float(value))
        series[pair] = points
    figure = Figure(title, x_label="UTC hour", y_label=y_label,
                    x_range=(0.0, 23.0), y_range=data_range(values),
                    x_ticks=[(float(h), f"{h:02d}") for h in range(0, 24, 2)])
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, path, table, ["pair", "utc_hour", field])


def _density_by_year(payload: dict[str, Any], pairs: Sequence[str],
                     out_dir: pathlib.Path) -> dict[str, str]:
    """Median ticks per hour by year, one line per pair."""
    years = sorted({year for profile in payload["density"].values()
                    for year in profile["by_year"]})
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        profile = payload["density"].get(pair) or {"by_year": {}}
        points: list[tuple[float, float]] = []
        for index, year in enumerate(years):
            row = profile["by_year"].get(year) or {}
            value = row.get("median_ticks")
            table.append([pair, year, value, row.get("hours"),
                          row.get("realised_vol_bp")])
            if value is None:
                continue
            points.append((float(index), float(value)))
            values.append(float(value))
        series[pair] = points
    figure = Figure("Median ticks per hour, by year",
                    x_label="year", y_label="ticks per hour",
                    x_range=(0.0, float(max(1, len(years) - 1))),
                    y_range=data_range(values), x_ticks=_categorical(years))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "density_by_year.svg", table,
                 ["pair", "year", "median_ticks", "hours", "realised_vol_bp"])


def _spread_by_year(payload: dict[str, Any], pairs: Sequence[str],
                    out_dir: pathlib.Path) -> dict[str, str]:
    """Median spread by year inside the R3 reference band."""
    years = sorted({year for profile in payload["density"].values()
                    for year in profile["by_year"]})
    band = next((profile.get("reference_band")
                 for profile in payload["density"].values()), "3k-10k")
    table: list[list[Any]] = []
    values: list[float] = []
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        profile = payload["density"].get(pair) or {"by_year": {}}
        points: list[tuple[float, float]] = []
        for index, year in enumerate(years):
            row = profile["by_year"].get(year) or {}
            value = row.get("median_spread_pips_in_band")
            table.append([pair, year, value, row.get("hours_in_reference_band"),
                          row.get("median_spread_pips")])
            if value is None:
                continue
            points.append((float(index), float(value)))
            values.append(float(value))
        series[pair] = points
    figure = Figure(f"Median spread by year, inside the {band} ticks/hour band",
                    x_label="year", y_label="median spread (pips)",
                    x_range=(0.0, float(max(1, len(years) - 1))),
                    y_range=data_range(values), x_ticks=_categorical(years))
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "spread_by_year.svg", table,
                 ["pair", "year", "median_spread_pips_in_band",
                  "hours_in_band", "median_spread_pips_uncontrolled"])


def _agreement_by_year(payload: dict[str, Any],
                       out_dir: pathlib.Path) -> dict[str, str]:
    """R7's agreement rate and unverifiable share, by year."""
    by_year = payload["eras"]["by_year"]
    years = sorted(by_year)
    table = [[year, by_year[year]["sampled"], by_year[year]["pass"],
              by_year[year]["blocked"], by_year[year]["unverifiable"],
              by_year[year]["agreement_rate"],
              by_year[year]["unverifiable_share"], by_year[year]["era"]]
             for year in years]
    agreement = [(float(i), float(by_year[y]["agreement_rate"]))
                 for i, y in enumerate(years)
                 if by_year[y]["agreement_rate"] is not None]
    unverifiable = [(float(i), float(by_year[y]["unverifiable_share"]))
                    for i, y in enumerate(years)
                    if by_year[y]["unverifiable_share"] is not None]
    figure = Figure("Cross-check agreement and blindness, by year (ruling R7)",
                    x_label="year", y_label="share of sampled hours",
                    x_range=(0.0, float(max(1, len(years) - 1))),
                    y_range=(0.0, 1.0), x_ticks=_categorical(years))
    figure.line(agreement, PALETTE[0], "agreement among verifiable hours")
    figure.dots(agreement, PALETTE[0])
    figure.line(unverifiable, PALETTE[1], "unverifiable share", dashed=True)
    figure.dots(unverifiable, PALETTE[1])
    return write(figure, out_dir / "agreement_by_year.svg", table,
                 ["year", "sampled", "pass", "blocked", "unverifiable",
                  "agreement_rate", "unverifiable_share", "era"])


def _rolling(payload: dict[str, Any], pairs: Sequence[str],
             out_dir: pathlib.Path) -> dict[str, str]:
    """Rolling two-year VR(4) on 5-minute returns, one line per pair."""
    horizon = "5m"
    starts: list[str] = []
    for pair in pairs:
        cell = payload["cells"].get(f"{pair}|{horizon}")
        if cell:
            starts = [w["start"] for w in cell["stability"]["rolling_windows"]]
            break
    table: list[list[Any]] = []
    values: list[float] = [1.0]
    series: dict[str, list[tuple[float, float]]] = {}
    for pair in pairs:
        cell = payload["cells"].get(f"{pair}|{horizon}")
        points: list[tuple[float, float]] = []
        if cell:
            for index, window in enumerate(cell["stability"]["rolling_windows"]):
                value = window["stats"].get("vr4")
                table.append([pair, window["start"], window["end"], value,
                              window["stats"].get("n")])
                if value is None:
                    continue
                points.append((float(index), float(value)))
                values.append(float(value))
        series[pair] = points
    figure = Figure("VR(4) on rolling two-year windows, 5-minute returns",
                    x_label="window start", y_label="VR(4)",
                    x_range=(0.0, float(max(1, len(starts) - 1))),
                    y_range=data_range(values),
                    x_ticks=_categorical([s[:7] for s in starts]))
    figure.reference(1.0, "random walk")
    for pair in pairs:
        figure.line(series[pair], _colour(pairs, pair), pair)
    return write(figure, out_dir / "rolling_vr4_5m.svg", table,
                 ["pair", "window_start", "window_end", "vr4", "n"])


def _regimes(payload: dict[str, Any], pairs: Sequence[str],
             out_dir: pathlib.Path) -> dict[str, str]:
    """Lag-1 autocorrelation inside each volatility tercile, 5-minute bars."""
    horizon = "5m"
    names = ("low", "mid", "high")
    table: list[list[Any]] = []
    values: list[float] = [0.0]
    bars: list[tuple[str, int, str, float]] = []
    for index, pair in enumerate(pairs):
        cell = payload["cells"].get(f"{pair}|{horizon}")
        regimes = (cell or {}).get("volatility", {}).get("regimes", {})
        by_regime = regimes.get("by_regime") or {}
        for slot, name in enumerate(names):
            row = by_regime.get(name) or {}
            value = row.get("rho1")
            table.append([pair, name, value, row.get("rho1_p_value"),
                          row.get("bars"), row.get("p_same"),
                          row.get("continuation_rho")])
            if value is None:
                continue
            bars.append((pair, index, name, float(value)))
            values.append(float(value))
    figure = Figure("Lag-1 return autocorrelation by volatility regime, "
                    "5-minute bars",
                    x_label="pair", y_label="lag-1 autocorrelation",
                    x_range=(-0.5, float(len(pairs)) - 0.5),
                    y_range=data_range(values),
                    x_ticks=[(float(i), p) for i, p in enumerate(pairs)])
    shades = {"low": PALETTE[0], "mid": PALETTE[4], "high": PALETTE[1]}
    width = 0.26
    for pair, index, name, value in bars:
        slot = names.index(name)
        left = index - 0.42 + slot * width
        figure.bar(left, left + width * 0.9, value, shades[name],
                   f"{name} volatility")
    return write(figure, out_dir / "regime_rho1_5m.svg", table,
                 ["pair", "regime", "rho1", "rho1_p_value", "bars", "p_same",
                  "continuation_rho"])


def _empties(payload: dict[str, Any], out_dir: pathlib.Path) -> dict[str, str]:
    """Unexplained empty dates by year, split by class."""
    by_year = payload["empties"]["by_year"]
    years = sorted(by_year)
    classes = ["r1_artefact", "week_boundary", "calendar_holiday",
               "currency_holiday", "feed_artefact", "unknown"]
    table = [[year, *[by_year[year].get(name, 0) for name in classes]]
             for year in years]
    values: list[float] = [0.0]
    figure = Figure("Unexplained empty dates by year and class",
                    x_label="year", y_label="dates",
                    x_range=(-0.5, float(max(1, len(years))) - 0.5),
                    y_range=(0.0, max([sum(row[1:]) for row in table] + [1])),
                    x_ticks=_categorical(years))
    width = 0.9 / max(1, len(classes))
    for index, year in enumerate(years):
        for slot, name in enumerate(classes):
            value = float(by_year[year].get(name, 0))
            values.append(value)
            if value <= 0:
                continue
            left = index - 0.45 + slot * width
            figure.bar(left, left + width * 0.85, value,
                       PALETTE[slot], name.replace("_", " "))
    return write(figure, out_dir / "empties_by_year.svg", table,
                 ["year", *classes])
