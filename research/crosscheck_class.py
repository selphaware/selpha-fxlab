"""Ruling R7: the density-aware cross-check class of a sampled hour.

Pre-registered decision #7 thresholded every sampled hour at a flat 1.0 pip.
T3 ran that as pinned and then measured what it had done: 81% of hours holding
under 500 ticks disagreed with OANDA beyond a pip, against 5.7% of hours
holding 3k-10k, while the by-year median difference fell from 2.7 pip in 2005
to 0.15 in 2024. A flat pip threshold was therefore not one instrument applied
to twenty years -- it was twenty different instruments, because what it
measures in a thin hour is how far price moved between two prints minutes
apart, and what it measures in a dense hour is whether two venues agree.

Ruling R7 (M3 checkpoint, 2026-09-06) amends the threshold and nothing else:

* **>= 3,000 ticks** -- threshold 1.0 pip, exactly as pinned. A dense hour's
  two prints are close enough together that a pip of disagreement is about the
  data;
* **500-2,999 ticks** -- threshold ``1.0 pip + that hour's own median spread``.
  The hour's own spread is the resolution at which it can be read at all, so
  the threshold scales with the instrument rather than with the era;
* **< 500 ticks** -- ``UNVERIFIABLE``. No threshold, no verdict. A check that
  cannot see an hour should say so rather than fail it. These hours stay
  usable and stay tagged, and what to do with them before 2013 is a T5
  decision on the by-year agreement evidence;
* the **roll window stays exempt** (pre-reg #4 and #7), and an exempt hour gets
  no verdict either.

An hour failing the threshold that applies to it is ``BLOCKED``, which still
means exactly what pre-reg #7 said: out of research use until a checkpoint says
otherwise. R7 changed the instrument, not the consequence.

The classification of the whole sample is derived, written to
``config/crosscheck.toml``, and re-derived and compared on every run of the T3
experiment -- the same discipline ``config/calendar.toml`` is held to, for the
same reason: a tracked file anybody can edit is a file that will be edited.
:class:`CrosscheckClasses` is how a later scoring experiment asks the question,
through :meth:`research.loader.ResearchLoader.crosscheck_class`.
"""

from __future__ import annotations

import datetime as dt
import logging
import pathlib
import tomllib
from typing import Any, Final, Iterable, Mapping, Sequence

from fxlab.ingestion.pairs import pair_spec
from research.seal import as_date

_LOG: Final[logging.Logger] = logging.getLogger("research.crosscheck_class")

#: The hour agrees with the second venue inside the threshold that applies.
CLASS_PASS: Final[str] = "PASS"

#: Beyond the applicable threshold, outside the roll window. Pre-reg #7 blocks
#: it from research use until a checkpoint says otherwise.
CLASS_BLOCKED: Final[str] = "BLOCKED"

#: Under the density floor: the check cannot see the hour, so it returns no
#: verdict. Usable and tagged, per R7.
CLASS_UNVERIFIABLE: Final[str] = "UNVERIFIABLE"

#: Inside the derived roll window, exempt by pre-reg #4 and #7.
CLASS_ROLL_EXEMPT: Final[str] = "ROLL_EXEMPT"

#: Never sampled. The cross-check covers a sample, not the store, and the
#: difference between "checked and agreed" and "never checked" is the whole
#: reason this class exists rather than defaulting to PASS.
CLASS_UNSAMPLED: Final[str] = "UNSAMPLED"

#: Every class, in the order a report tabulates them.
CLASSES: Final[tuple[str, ...]] = (
    CLASS_PASS, CLASS_BLOCKED, CLASS_UNVERIFIABLE, CLASS_ROLL_EXEMPT,
    CLASS_UNSAMPLED)

#: One-letter codes, so the committed file stays small enough to read.
CODES: Final[dict[str, str]] = {
    CLASS_PASS: "P", CLASS_BLOCKED: "B", CLASS_UNVERIFIABLE: "U",
    CLASS_ROLL_EXEMPT: "R",
}
BY_CODE: Final[dict[str, str]] = {v: k for k, v in CODES.items()}

#: R7's density bands, in ticks per hour. Pinned by the ruling, not tunable.
DENSE_TICKS: Final[int] = 3000
UNVERIFIABLE_TICKS: Final[int] = 500

#: The committed classification, relative to the project root.
CLASSES_RELPATH: Final[str] = "config/crosscheck.toml"

#: An hour whose median spread was never measured. Not a class -- a defect in
#: the inputs, raised rather than absorbed, because silently treating an
#: unmeasured hour as dense would quietly re-apply the flat threshold R7 exists
#: to replace.
class SpreadNotMeasured(Exception):
    """A middle-band hour reached the classifier without its median spread."""


def band_of(ticks: int) -> str:
    """Which of R7's three density bands an hour's tick count falls in.

    Returns:
        ``"dense"``, ``"middle"`` or ``"thin"``.
    """
    count = int(ticks)
    if count >= DENSE_TICKS:
        return "dense"
    if count >= UNVERIFIABLE_TICKS:
        return "middle"
    return "thin"


def threshold_for(ticks: int, base_pips: float,
                  median_spread_pips: float | None) -> float | None:
    """The threshold R7 applies to one hour, in pips.

    Args:
        ticks: Ticks stored in the hour.
        base_pips: The pinned pre-reg #7 threshold, 1.0 pip.
        median_spread_pips: The hour's own median spread, needed only for the
            middle band.

    Returns:
        The threshold in pips, or ``None`` for a thin hour, which R7 gives no
        threshold at all.

    Raises:
        SpreadNotMeasured: For a middle-band hour with no measured spread.
    """
    band = band_of(ticks)
    if band == "thin":
        return None
    if band == "dense":
        return float(base_pips)
    if median_spread_pips is None:
        raise SpreadNotMeasured(
            f"an hour holding {int(ticks)} ticks falls in R7's middle band, "
            "whose threshold is 1.0 pip + the hour's own median spread, and no "
            "median spread was measured for it. Run "
            "`python -m research.crosscheck_spreads` first.")
    return float(base_pips) + float(median_spread_pips)


def classify(row: Mapping[str, Any], base_pips: float,
             median_spread_pips: float | None) -> dict[str, Any]:
    """Classify one stored comparison row under R7.

    Args:
        row: A row from ``oanda.jsonl``'s ``hours`` list -- it must carry
            ``duka_ticks``, ``abs_worst_pips`` and ``roll_exempt``.
        base_pips: The pinned threshold, 1.0 pip.
        median_spread_pips: The hour's own median spread in pips, or ``None``.

    Returns:
        The row's fields plus ``r7_class``, ``r7_band``, ``r7_threshold_pips``
        and ``median_spread_pips``. The original ``beyond_threshold`` field is
        left untouched: it records what the flat threshold decided, and the
        re-issue reports both so the amendment is visible rather than applied
        in place.
    """
    ticks = int(row.get("duka_ticks", 0))
    worst = float(row.get("abs_worst_pips", 0.0))
    band = band_of(ticks)
    if row.get("roll_exempt"):
        verdict, threshold = CLASS_ROLL_EXEMPT, None
    elif band == "thin":
        verdict, threshold = CLASS_UNVERIFIABLE, None
    else:
        threshold = threshold_for(ticks, base_pips, median_spread_pips)
        verdict = (CLASS_BLOCKED if threshold is not None and worst > threshold
                   else CLASS_PASS)
    out = dict(row)
    out["r7_band"] = band
    out["r7_class"] = verdict
    out["r7_threshold_pips"] = (None if threshold is None
                                else round(float(threshold), 4))
    out["median_spread_pips"] = (None if median_spread_pips is None
                                 else round(float(median_spread_pips), 4))
    return out


def median_spread_pips(pair: str, spread_price: float) -> float:
    """A spread measured in price units, expressed in pips for ``pair``."""
    return float(spread_price) / pair_spec(pair).pip_size


def key_of(date: str, hour: int) -> str:
    """The compact ``YYYY-MM-DD HH`` key one classified hour is stored under."""
    return f"{as_date(date).isoformat()} {int(hour):02d}"


# --------------------------------------------------------------------------- #
# The re-issued verdict
# --------------------------------------------------------------------------- #

def _stats(values: Sequence[float]) -> dict[str, Any]:
    """Count, mean, median, p95 and max of an absolute-difference sample.

    Deliberately identical in shape to :func:`research.crosscheck_oanda._stats`
    so the re-issued tables and the pinned ones can be read side by side
    without a reader having to check whether "p95" means the same thing twice.
    """
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(float(v) for v in values)
    n = len(ordered)

    def quantile(q: float) -> float:
        return ordered[min(n - 1, max(0, int(round(q * (n - 1)))))]

    return {"n": n, "mean": round(sum(ordered) / n, 4),
            "median": round(quantile(0.5), 4),
            "p95": round(quantile(0.95), 4), "max": round(ordered[-1], 4)}


def summarise(rows: Sequence[Mapping[str, Any]],
              base_pips: float) -> dict[str, Any]:
    """The re-issued cross-check verdict, as the T4 card's Step 0 asks for it.

    Counts per class per pair per year, difference distributions by density
    band, the by-year agreement table the appendix era-tags against, and the
    final blocked-hour list. The pinned pre-reg #7 verdict is carried alongside
    rather than replaced -- an amendment that erases what it amended leaves a
    reader unable to see what changed.
    """
    counts = {c: 0 for c in CLASSES[:4]}
    by_pair: dict[str, dict[str, Any]] = {}
    by_pair_year: dict[str, dict[str, dict[str, int]]] = {}
    by_year: dict[str, dict[str, int]] = {}
    by_band: dict[str, dict[str, Any]] = {}
    blocked: list[dict[str, Any]] = []
    pinned_blocked = 0
    changed_to_pass = 0
    changed_to_unverifiable = 0
    newly_blocked = 0
    spread_span: list[float] = []

    for row in rows:
        verdict = str(row["r7_class"])
        pair, year = str(row["pair"]), str(row["date"])[:4]
        worst = float(row.get("abs_worst_pips", 0.0))
        band = str(row["r7_band"])
        counts[verdict] = counts.get(verdict, 0) + 1

        pair_bucket = by_pair.setdefault(
            pair, {c: 0 for c in CLASSES[:4]} | {"diffs": []})
        pair_bucket[verdict] += 1
        year_bucket = by_pair_year.setdefault(pair, {}).setdefault(
            year, {c: 0 for c in CLASSES[:4]})
        year_bucket[verdict] += 1
        overall_year = by_year.setdefault(year, {c: 0 for c in CLASSES[:4]})
        overall_year[verdict] += 1

        was_beyond = bool(row.get("beyond_threshold"))
        if was_beyond:
            pinned_blocked += 1
        if verdict == CLASS_BLOCKED:
            blocked.append({"pair": pair, "date": str(row["date"]),
                            "hour": int(row["hour"]),
                            "abs_worst_pips": worst,
                            "threshold_pips": row.get("r7_threshold_pips"),
                            "ticks": int(row.get("duka_ticks", 0)),
                            "band": band})
            if not was_beyond:
                newly_blocked += 1
        elif was_beyond and verdict == CLASS_PASS:
            changed_to_pass += 1
        elif was_beyond and verdict == CLASS_UNVERIFIABLE:
            changed_to_unverifiable += 1

        if verdict != CLASS_ROLL_EXEMPT:
            pair_bucket["diffs"].append(worst)
            band_bucket = by_band.setdefault(
                band, {"diffs": [], "thresholds": [],
                       "blocked": 0, "unverifiable": 0, "passed": 0})
            band_bucket["diffs"].append(worst)
            if row.get("r7_threshold_pips") is not None:
                band_bucket["thresholds"].append(
                    float(row["r7_threshold_pips"]))
            if verdict == CLASS_BLOCKED:
                band_bucket["blocked"] += 1
            elif verdict == CLASS_UNVERIFIABLE:
                band_bucket["unverifiable"] += 1
            else:
                band_bucket["passed"] += 1
        spread = row.get("median_spread_pips")
        if spread is not None:
            spread_span.append(float(spread))

    for bucket in by_pair.values():
        bucket.update(_stats(bucket.pop("diffs")))
    for name, bucket in by_band.items():
        diffs = bucket.pop("diffs")
        thresholds = bucket.pop("thresholds")
        bucket.update(_stats(diffs))
        bucket["threshold"] = _stats(thresholds)
        bucket["blocked_share"] = (round(bucket["blocked"] / len(diffs), 4)
                                   if diffs else 0.0)

    blocked.sort(key=lambda b: (-float(b["abs_worst_pips"]), b["pair"],
                                b["date"], b["hour"]))
    verifiable = counts[CLASS_PASS] + counts[CLASS_BLOCKED]
    return {
        "rules": {"base_pips": base_pips, "dense_ticks": DENSE_TICKS,
                  "unverifiable_ticks": UNVERIFIABLE_TICKS},
        "counts": counts,
        "hours_classified": sum(counts.values()),
        "verifiable_hours": verifiable,
        "agreement_rate": (round(counts[CLASS_PASS] / verifiable, 4)
                           if verifiable else None),
        "verdict": CLASS_BLOCKED if counts[CLASS_BLOCKED] else "CLEAR",
        "by_pair": {k: by_pair[k] for k in sorted(by_pair)},
        "by_pair_year": {p: {y: by_pair_year[p][y]
                             for y in sorted(by_pair_year[p])}
                         for p in sorted(by_pair_year)},
        "by_year": _with_agreement(by_year),
        "by_band": {name: by_band[name]
                    for name in ("dense", "middle", "thin")
                    if name in by_band},
        "blocked": blocked,
        "blocked_by_pair_year": _rollup(blocked),
        "median_spread_pips": _stats(spread_span),
        "against_pinned": {
            "pinned_beyond_threshold": pinned_blocked,
            "r7_blocked": counts[CLASS_BLOCKED],
            "unblocked_to_pass": changed_to_pass,
            "unblocked_to_unverifiable": changed_to_unverifiable,
            "newly_blocked": newly_blocked,
        },
    }


def _with_agreement(by_year: Mapping[str, Mapping[str, int]]
                    ) -> dict[str, dict[str, Any]]:
    """Per year, the class counts plus the agreement rate among verifiables.

    The agreement rate is over ``PASS + BLOCKED`` and not over every sampled
    hour, because an ``UNVERIFIABLE`` hour is not a disagreement -- it is a
    year in which the check could not see. Dividing by the whole sample would
    make the early years look corroborated in proportion to how blind the
    check was there, which is the opposite of the truth.
    """
    out: dict[str, dict[str, Any]] = {}
    for year in sorted(by_year):
        counts = dict(by_year[year])
        verifiable = counts[CLASS_PASS] + counts[CLASS_BLOCKED]
        sampled = sum(counts.values())
        out[year] = dict(counts)
        out[year]["verifiable"] = verifiable
        out[year]["sampled"] = sampled
        out[year]["agreement_rate"] = (round(counts[CLASS_PASS] / verifiable, 4)
                                       if verifiable else None)
        out[year]["unverifiable_share"] = (
            round(counts[CLASS_UNVERIFIABLE] / sampled, 4) if sampled else None)
    return out


def _rollup(blocked: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Blocked hours folded to one row per pair-year."""
    counts: dict[tuple[str, str], int] = {}
    for row in blocked:
        key = (str(row["pair"]), str(row["date"])[:4])
        counts[key] = counts.get(key, 0) + 1
    return [{"pair": p, "year": y, "hours": n}
            for (p, y), n in sorted(counts.items())]


# --------------------------------------------------------------------------- #
# The committed classification
# --------------------------------------------------------------------------- #

def render_toml(document: Mapping[str, Any]) -> str:
    """The classification as a tracked, diffable TOML file.

    One line per classified hour, ``"YYYY-MM-DD HH C"``, grouped by pair. The
    single-letter code is what keeps a twelve-thousand-hour classification to a
    file a person can open; the legend is in the header, and
    :func:`load_classes` is the only thing that has to read it.
    """
    rules = document["rules"]
    window = document["window"]
    counts = document["counts"]
    hours: Mapping[str, Mapping[str, str]] = document["hours"]
    lines = [
        "# config/crosscheck.toml -- the OANDA cross-check class of every",
        "# sampled hour, under SPEC2 ruling R7.",
        "#",
        "# Generated by the T3 experiment (`python -m research.run --config",
        "# experiments/T3-quality/config.toml`). Do not hand-edit: the",
        "# experiment re-derives this file on every run and refuses to agree",
        "# with a copy that does not match, exactly as it does for",
        "# config/calendar.toml.",
        "#",
        "# R7 amends pre-reg #7's flat 1.0 pip threshold to a density-aware",
        "# one. Per sampled hour, by the ticks the store holds for it:",
        "#",
        f"#   >= {rules['dense_ticks']} ticks   threshold {rules['base_pips']} pip",
        f"#   {rules['unverifiable_ticks']}-{rules['dense_ticks'] - 1} ticks"
        f"   threshold {rules['base_pips']} pip + that hour's own median spread",
        f"#   < {rules['unverifiable_ticks']} ticks    UNVERIFIABLE -- no threshold, no verdict",
        "#",
        "# Codes:  P = PASS   B = BLOCKED   U = UNVERIFIABLE   R = ROLL_EXEMPT",
        "#",
        "# An hour absent from this file was never sampled. That is a distinct",
        "# state from PASS and the reader returns UNSAMPLED for it: the",
        "# cross-check covers a sample of about 11,800 hours, not the",
        "# 1,495,740 the store holds.",
        "",
        "[crosscheck]",
        'derived_from = "SPEC2 ruling R7, applied to experiments/T3-quality/oanda.jsonl"',
        f'window_start = "{window["start"]}"',
        f'window_end = "{window["end"]}"',
        f'base_threshold_pips = {rules["base_pips"]}',
        f'dense_ticks = {rules["dense_ticks"]}',
        f'unverifiable_ticks = {rules["unverifiable_ticks"]}',
        f'roll_window_ny = [{rules["roll_start_ny"]}, {rules["roll_end_ny"]}]',
        f'hours_classified = {counts["classified"]}',
        f'passed = {counts[CLASS_PASS]}',
        f'blocked = {counts[CLASS_BLOCKED]}',
        f'unverifiable = {counts[CLASS_UNVERIFIABLE]}',
        f'roll_exempt = {counts[CLASS_ROLL_EXEMPT]}',
        "",
        "# One entry per sampled hour: \"YYYY-MM-DD HH C\".",
        "[crosscheck.hours]",
    ]
    for pair in sorted(hours):
        entries = hours[pair]
        lines.append(f"{pair} = [")
        for key in sorted(entries):
            lines.append(f'  "{key} {entries[key]}",')
        lines.append("]")
    lines.append("")
    return "\n".join(lines)


class CrosscheckClasses:
    """The committed classification, queryable.

    A scoring experiment that wants to know whether an hour was corroborated
    asks here rather than re-reading a result document, for the same reason
    :class:`research.calendar_build.Calendar` exists: a derived fact nobody can
    query is a text file.

    What this deliberately does not do is filter. It answers a question; the
    caller decides what to do with the answer, and says so in its report. A
    tagger that quietly dropped ``BLOCKED`` hours would make every downstream
    number depend on a decision nobody recorded.
    """

    __slots__ = ("_hours", "window", "rules", "counts")

    def __init__(self, hours: Mapping[str, Mapping[str, str]],
                 window: tuple[str, str], rules: Mapping[str, Any],
                 counts: Mapping[str, int]) -> None:
        self._hours = {p: dict(v) for p, v in hours.items()}
        self.window = window
        self.rules = dict(rules)
        self.counts = dict(counts)

    def classify(self, pair: str, date: object, hour: int) -> str:
        """The class of one hour, or ``UNSAMPLED`` if it was never checked."""
        entries = self._hours.get(str(pair))
        if not entries:
            return CLASS_UNSAMPLED
        code = entries.get(key_of(str(as_date(date)), int(hour)))
        return BY_CODE.get(code or "", CLASS_UNSAMPLED)

    def sampled_pairs(self) -> list[str]:
        """Every pair carrying at least one classified hour."""
        return sorted(self._hours)

    def hours_in_class(self, wanted: str,
                       pair: str | None = None) -> list[tuple[str, str, int]]:
        """Every ``(pair, date, hour)`` in one class, sorted."""
        code = CODES.get(wanted)
        if code is None:
            return []
        out: list[tuple[str, str, int]] = []
        for name, entries in sorted(self._hours.items()):
            if pair is not None and name != pair:
                continue
            for key, value in sorted(entries.items()):
                if value == code:
                    date, _, hh = key.partition(" ")
                    out.append((name, date, int(hh)))
        return out

    def classify_many(self, pair: str,
                      stamps: Iterable[dt.datetime]) -> list[str]:
        """Classify a sequence of bar timestamps for one pair.

        The vectorised form an experiment actually uses. Bar timestamps are
        bar **open** times, so an hourly bar's timestamp is the hour the
        cross-check sampled; a finer bar is classified by the hour it falls in,
        which is the only granularity the check ever had.
        """
        return [self.classify(pair, s.date(), s.hour) for s in stamps]


def load_classes(path: pathlib.Path) -> CrosscheckClasses:
    """Read the committed classification from TOML.

    Raises:
        FileNotFoundError: If it has not been derived. Deliberately not an
            empty classification: "nothing was checked" and "no file" are
            different claims, and returning the first for the second would make
            every hour read as ``UNSAMPLED`` and every check quietly pass.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"no cross-check classification at {path}; it is derived by the T3 "
            "experiment -- an absent classification is not an empty one")
    block = (tomllib.loads(path.read_text(encoding="utf-8"))
             .get("crosscheck") or {})
    hours: dict[str, dict[str, str]] = {}
    for pair, entries in (block.get("hours") or {}).items():
        bucket = hours.setdefault(str(pair), {})
        for entry in entries:
            date, _, rest = str(entry).partition(" ")
            hour, _, code = rest.partition(" ")
            bucket[f"{date} {hour}"] = code
    return CrosscheckClasses(
        hours=hours,
        window=(str(block.get("window_start", "")),
                str(block.get("window_end", ""))),
        rules={
            "base_pips": float(block.get("base_threshold_pips", 0.0)),
            "dense_ticks": int(block.get("dense_ticks", 0)),
            "unverifiable_ticks": int(block.get("unverifiable_ticks", 0)),
            "roll_window_ny": [int(v) for v in
                               (block.get("roll_window_ny") or [0, 0])],
        },
        counts={
            "classified": int(block.get("hours_classified", 0)),
            CLASS_PASS: int(block.get("passed", 0)),
            CLASS_BLOCKED: int(block.get("blocked", 0)),
            CLASS_UNVERIFIABLE: int(block.get("unverifiable", 0)),
            CLASS_ROLL_EXEMPT: int(block.get("roll_exempt", 0)),
        })


def build_and_write(base: pathlib.Path, config_path: pathlib.Path) -> dict[str, Any]:
    """Derive the classification from an experiment's checkpoints and write it.

    The counterpart to ``python -m research.calendar_build``: cheap,
    deterministic, and re-run by the experiment on every gate pass so the
    committed file cannot drift away from the sample that justified it.
    """
    from research.experiment import load_config
    from research.quality import reissue_under_r7
    from research.crosscheck_oanda import read_checkpoint, CROSSCHECK_NAME

    config = load_config(config_path)
    params = config.params
    experiment_dir = base / str(params["experiment_dir"])
    threshold = float(params["crosscheck_threshold_pips"])
    rows = read_checkpoint(experiment_dir / CROSSCHECK_NAME)
    reissue = reissue_under_r7(experiment_dir, rows, params, threshold)
    document = derive(
        reissue["_classified"], base_pips=threshold,
        window=(str(params["start_date"]), str(params["end_date"])),
        roll=(int(params["crosscheck_roll_start_hour_ny"]),
              int(params["crosscheck_roll_end_hour_ny"])))
    path = base / str(params.get("crosscheck_classes_path", CLASSES_RELPATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(document), encoding="utf-8")
    _LOG.info("wrote %s (%d hour(s) classified)", path,
              document["counts"]["classified"])
    return document


def parse_args(argv: list[str] | None = None) -> Any:
    """Parse the command line."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m research.crosscheck_class",
        description="Derive the R7 cross-check classification of every "
                    "sampled hour.")
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Derive the classification and write it."""
    from research.loader import project_root

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z")
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base else project_root())
    document = build_and_write(base, args.config)
    counts = document["counts"]
    print(f"classified {counts['classified']} hour(s): "
          f"{counts[CLASS_PASS]} PASS, {counts[CLASS_BLOCKED]} BLOCKED, "
          f"{counts[CLASS_UNVERIFIABLE]} UNVERIFIABLE, "
          f"{counts[CLASS_ROLL_EXEMPT]} ROLL_EXEMPT")
    return 0


def derive(rows: Sequence[Mapping[str, Any]], *, base_pips: float,
           window: tuple[str, str], roll: tuple[int, int]) -> dict[str, Any]:
    """Build the committed-classification document from classified hours.

    Args:
        rows: Rows already through :func:`classify`.
        base_pips: The pinned pre-reg #7 threshold.
        window: ``(start, end)`` of the window the sample was drawn over.
        roll: The derived roll window in New York hours.
    """
    hours: dict[str, dict[str, str]] = {}
    counts = {CLASS_PASS: 0, CLASS_BLOCKED: 0, CLASS_UNVERIFIABLE: 0,
              CLASS_ROLL_EXEMPT: 0}
    for row in rows:
        verdict = str(row["r7_class"])
        code = CODES.get(verdict)
        if code is None:
            continue
        pair = str(row["pair"])
        hours.setdefault(pair, {})[key_of(str(row["date"]),
                                          int(row["hour"]))] = code
        counts[verdict] += 1
    counts_out = dict(counts)
    counts_out["classified"] = sum(counts.values())
    return {
        "rules": {"base_pips": base_pips, "dense_ticks": DENSE_TICKS,
                  "unverifiable_ticks": UNVERIFIABLE_TICKS,
                  "roll_start_ny": roll[0], "roll_end_ny": roll[1]},
        "window": {"start": window[0], "end": window[1]},
        "counts": counts_out,
        "hours": hours,
    }


if __name__ == "__main__":
    raise SystemExit(main())
