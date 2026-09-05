"""Cross-check the stored feed against OANDA, on a sample per pair per year.

T3 Step 3, pre-registered decision #7. FX has no consolidated tape, so there is
no authority to check Dukascopy against -- only a second venue, whose
disagreement with the first is evidence about *both*. That asymmetry decides
what this module may conclude. A difference beyond threshold does not mean
Dukascopy is wrong; it means the stored data cannot be relied on until somebody
looks. Pre-reg #7 says so and the wording of the result says so.

What is compared: the mid of the first and last stored tick in an hour, against
the open and close of OANDA's H1 candle for the same hour. The last-tick mid is
the headline, because a candle close and a final quote are the same object
measured twice.

Three details that decide whether the comparison means anything:

* **The roll window is exempt** (pre-reg #4 and #7): 16:00-18:00
  ``America/New_York``, *derived per date* rather than pinned to a UTC hour,
  because it moves twice a year and a hardcoded hour is wrong for half of it.
  The sample deliberately includes an hour inside the window -- an exemption
  that is never exercised is an exemption nobody has tested.
* **The seal and ruling R1 apply to the sample.** A sampled date at or after
  the cutoff is refused outright rather than trusted to be absent, and an
  excluded pair-window is never requested. A cross-check is a read.
* **History availability is measured, not assumed.** OANDA does not reach 2005
  for every instrument. Asking each pair how far back it goes, and reporting
  it, is the difference between "the pairs agree" and "the pairs agree where
  both had data, which for this pair is a shorter window than you think".

Network work is checkpointed per pair-date and resumable, and the experiment
entry point reads the checkpoint back rather than re-running it. A judge that
needed a third-party API to be reachable would be judging the API.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import pathlib
import random
import time
import zoneinfo
from typing import Any, Final, Iterable, Sequence

import pyarrow.compute as pc
import pyarrow.parquet as pq

from fxlab.ingestion.oanda import Candle, OandaClient, OandaError
from fxlab.ingestion.pairs import pair_spec
from research.exclusions import is_excluded
from research.seal import as_date, assert_not_sealed, is_sealed

_LOG: Final[logging.Logger] = logging.getLogger("research.crosscheck_oanda")

#: The checkpoint, one JSON object per pair-date.
CROSSCHECK_NAME: Final[str] = "oanda.jsonl"

#: Per-pair history probe, written once, one record per pair.
#:
#: JSON Lines, and the extension matters rather than being a style choice: the
#: research gate treats every ``*.json`` in an experiment directory as a result
#: document to be ledgered and re-hashed. A checkpoint is not a result, and
#: naming it as one made the gate quite correctly refuse it.
AVAILABILITY_NAME: Final[str] = "oanda_availability.jsonl"

#: The exchange the roll window is expressed in (pre-reg #4).
NEW_YORK: Final[zoneinfo.ZoneInfo] = zoneinfo.ZoneInfo("America/New_York")

#: Seconds between requests. OANDA's practice tier allows far more; this is a
#: politeness floor, and the run is minutes either way.
REQUEST_INTERVAL: Final[float] = 0.25

#: Consecutive request failures before the sampler gives up and reports.
MAX_CONSECUTIVE_ERRORS: Final[int] = 12


def in_roll_window(when: dt.datetime, start_hour: int,
                   end_hour: int) -> bool:
    """True when ``when`` falls in the derived New York roll window.

    Derived per instant, never pinned to a UTC hour: 17:00 New York is 21:00
    UTC in summer and 22:00 UTC in winter, and a rule written in UTC is wrong
    for half of every year.
    """
    local = when.astimezone(NEW_YORK)
    return start_hour <= local.hour < end_hour


def sample_dates(pair: str, year: int, dates_per_year: int,
                 available: Sequence[str], seed: int) -> list[str]:
    """Pick sample dates for one pair-year, spread across the months.

    One date per month before any second date in a month, so a year's sample
    cannot pile into a quarter. Chosen with a seeded RNG keyed by pair and year
    so that two runs of the same config sample the same dates -- an unseeded
    sample would make the cross-check unreproducible in exactly the way ruling
    D5 exists to prevent.
    """
    by_month: dict[str, list[str]] = {}
    for date in available:
        if date[:4] != f"{year:04d}":
            continue
        by_month.setdefault(date[5:7], []).append(date)
    if not by_month:
        return []
    rng = random.Random(f"{seed}:{pair}:{year}")
    picked: list[str] = []
    months = sorted(by_month)
    while months and len(picked) < dates_per_year:
        for month in list(months):
            if len(picked) >= dates_per_year:
                break
            candidates = [d for d in by_month[month] if d not in picked]
            if not candidates:
                months.remove(month)
                continue
            picked.append(rng.choice(sorted(candidates)))
    return sorted(picked)


def stored_hour_mid(store: pathlib.Path, pair: str, date: str,
                    hour: int) -> dict[str, Any] | None:
    """First and last tick mid for one stored hour, or ``None`` if absent.

    Read from the ticks, which is what the card asks for. The bar tables carry
    the same two numbers by construction, but reading the ticks means the
    comparison tests the stored data itself rather than a resampling of it.
    """
    path = (store / "ticks" / f"pair={pair}" / f"date={date}"
            / f"{pair}_{date}_{hour:02d}h.parquet")
    if not path.is_file():
        return None
    table = pq.read_table(path, columns=["ts", "bid", "ask"])
    rows = table.num_rows
    if not rows:
        return None
    bid, ask = table["bid"], table["ask"]
    first = (bid[0].as_py() + ask[0].as_py()) / 2.0
    last = (bid[rows - 1].as_py() + ask[rows - 1].as_py()) / 2.0
    return {
        "ticks": rows,
        "mid_open": first,
        "mid_close": last,
        "spread_mean": float(pc.mean(pc.subtract(ask, bid)).as_py() or 0.0),
    }


def compare_hour(pair: str, date: str, hour: int, stored: dict[str, Any],
                 candle: Candle, threshold_pips: float,
                 roll: tuple[int, int]) -> dict[str, Any]:
    """One hour's comparison, in pips, with the roll exemption applied."""
    pip = pair_spec(pair).pip_size
    opens = dt.datetime.combine(as_date(date), dt.time(hour=hour),
                                tzinfo=dt.timezone.utc)
    open_diff = (stored["mid_open"] - candle.mid.open) / pip
    close_diff = (stored["mid_close"] - candle.mid.close) / pip
    exempt = in_roll_window(opens, roll[0], roll[1])
    worst = max(abs(open_diff), abs(close_diff))
    return {
        "pair": pair, "date": date, "hour": hour,
        "duka_ticks": int(stored["ticks"]),
        "oanda_volume": int(candle.volume),
        "duka_mid_open": round(stored["mid_open"], 7),
        "duka_mid_close": round(stored["mid_close"], 7),
        "oanda_mid_open": round(candle.mid.open, 7),
        "oanda_mid_close": round(candle.mid.close, 7),
        "open_diff_pips": round(open_diff, 4),
        "close_diff_pips": round(close_diff, 4),
        "abs_worst_pips": round(worst, 4),
        "roll_exempt": exempt,
        "beyond_threshold": bool(worst > threshold_pips and not exempt),
    }


def check_pair_date(store: pathlib.Path, client: OandaClient, pair: str,
                    date: str, hours: Sequence[int], threshold_pips: float,
                    roll: tuple[int, int]) -> dict[str, Any]:
    """Compare the sampled hours of one pair-date. One request."""
    assert_not_sealed(date, f"crosscheck({pair})")
    if is_excluded(pair, date):
        raise ValueError(f"{pair} {date} is inside an exclusion window")
    start = dt.datetime.combine(as_date(date), dt.time(0),
                                tzinfo=dt.timezone.utc)
    candles = client.candles(pair, "H1", start=start,
                             end=start + dt.timedelta(days=1))
    by_hour = {c.ts.astimezone(dt.timezone.utc).hour: c
               for c in candles if c.complete}
    rows: list[dict[str, Any]] = []
    missing: list[int] = []
    for hour in hours:
        stored = stored_hour_mid(store, pair, date, hour)
        candle = by_hour.get(hour)
        if stored is None or candle is None:
            missing.append(hour)
            continue
        rows.append(compare_hour(pair, date, hour, stored, candle,
                                 threshold_pips, roll))
    return {"pair": pair, "date": date, "hours": rows,
            "missing_hours": missing,
            "oanda_candles": len(candles)}


def history_start(client: OandaClient, pair: str) -> dict[str, Any]:
    """How far back OANDA serves this instrument.

    Asked rather than assumed. "Both feeds agree" means much less for a pair
    whose second feed only starts in 2010, and reporting the window each
    comparison actually had is the difference between a result and a slogan.
    """
    probe = dt.datetime(2003, 1, 1, tzinfo=dt.timezone.utc)
    try:
        candles = client.candles(pair, "H1", start=probe, count=1)
    except OandaError as exc:
        return {"pair": pair, "available": False, "detail": str(exc)[:200]}
    if not candles:
        return {"pair": pair, "available": False,
                "detail": "no candles returned from the earliest probe"}
    first = candles[0].ts.astimezone(dt.timezone.utc)
    return {"pair": pair, "available": True,
            "first_candle": first.isoformat(),
            "first_date": first.date().isoformat()}


def read_checkpoint(path: pathlib.Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Every pair-date already compared, keyed by ``(pair, date)``."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not pathlib.Path(path).is_file():
        return out
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            _LOG.warning("dropping a truncated checkpoint line")
            continue
        out[(str(row.get("pair")), str(row.get("date")))] = row
    return out


def _append(path: pathlib.Path, row: dict[str, Any]) -> None:
    """Append one checkpoint record and flush it to the OS."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def stored_dates(store: pathlib.Path, pair: str, hours: Sequence[int],
                 start: dt.date, end: dt.date) -> list[str]:
    """Dates where every sampled hour is present for this pair.

    Sampling a date the store has nothing for would measure the sampler rather
    than the data, so eligibility is decided before the sample is drawn. The
    seal and ruling R1 are applied here too: a date research may not read is
    not eligible to be checked.
    """
    root = store / "ticks" / f"pair={pair}"
    if not root.is_dir():
        return []
    lo, hi = start.isoformat(), end.isoformat()
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("date="):
            continue
        date = entry.name.split("=", 1)[1]
        if not (lo <= date <= hi) or is_sealed(date) or is_excluded(pair, date):
            continue
        if all((entry / f"{pair}_{date}_{h:02d}h.parquet").is_file()
               for h in hours):
            out.append(date)
    return out


def run_sample(store: pathlib.Path, client: OandaClient, pairs: Sequence[str],
               start: dt.date, end: dt.date, *, hours: Sequence[int],
               dates_per_year: int, threshold_pips: float,
               roll: tuple[int, int], seed: int,
               checkpoint: pathlib.Path) -> dict[str, Any]:
    """Walk the sample, resuming where a previous run stopped."""
    done = read_checkpoint(checkpoint)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    plan: list[tuple[str, str]] = []
    for pair in pairs:
        eligible = stored_dates(store, pair, hours, start, end)
        for year in range(start.year, end.year + 1):
            for date in sample_dates(pair, year, dates_per_year, eligible,
                                     seed):
                plan.append((pair, date))
    todo = [item for item in plan if item not in done]
    _LOG.info("%d pair-date(s) planned, %d already checkpointed, %d to fetch",
              len(plan), len(done), len(todo))

    errors = 0
    for index, (pair, date) in enumerate(todo, 1):
        try:
            row = check_pair_date(store, client, pair, date, hours,
                                  threshold_pips, roll)
            errors = 0
        except Exception as exc:  # noqa: BLE001 - recorded, then retried later
            errors += 1
            _LOG.warning("%s %s failed (%d in a row): %s", pair, date, errors,
                         type(exc).__name__)
            if errors >= MAX_CONSECUTIVE_ERRORS:
                _LOG.error("giving up after %d consecutive failures", errors)
                break
            time.sleep(min(30.0, 2.0 ** min(errors, 5)))
            continue
        _append(checkpoint, row)
        if index % 100 == 0:
            _LOG.info("%d/%d pair-dates compared", index, len(todo))
        time.sleep(REQUEST_INTERVAL)
    return summarise(read_checkpoint(checkpoint).values(), threshold_pips)


#: Tick-density buckets the difference distribution is stratified by.
#:
#: The single most important cut in this whole step. Two independent quote
#: streams are compared by their last print before the same instant, and in a
#: thin hour those prints can be minutes apart -- so the difference measures
#: how far price moved in between, not whether the venues agree about price.
#: The resolving power of a fixed pip threshold therefore depends on how many
#: ticks the hour holds, which is the same instrument problem ruling R3 states
#: about spread percentiles, arriving here in a different statistic.
DENSITY_BUCKETS: Final[tuple[tuple[str, int, int], ...]] = (
    ("<500", 0, 500),
    ("500-1k", 500, 1000),
    ("1k-3k", 1000, 3000),
    ("3k-10k", 3000, 10000),
    (">=10k", 10000, 10 ** 9),
)


def density_bucket(ticks: int) -> str:
    """Which density bucket an hour's tick count falls in."""
    for name, low, high in DENSITY_BUCKETS:
        if low <= ticks < high:
            return name
    return DENSITY_BUCKETS[-1][0]


def summarise(rows: Iterable[dict[str, Any]],
              threshold_pips: float) -> dict[str, Any]:
    """Fold the checkpoint into the distribution a report states."""
    per_pair: dict[str, dict[str, Any]] = {}
    per_pair_year: dict[str, dict[str, list[float]]] = {}
    per_year: dict[str, list[float]] = {}
    per_density: dict[str, dict[str, Any]] = {}
    opens: list[float] = []
    closes: list[float] = []
    flagged: list[dict[str, Any]] = []
    exempt = compared = missing = 0
    for row in rows:
        pair = str(row.get("pair"))
        bucket = per_pair.setdefault(pair, {"compared": 0, "exempt": 0,
                                            "beyond": 0, "diffs": []})
        missing += len(row.get("missing_hours") or [])
        for hour in row.get("hours") or []:
            compared += 1
            bucket["compared"] += 1
            worst = float(hour["abs_worst_pips"])
            if hour.get("roll_exempt"):
                exempt += 1
                bucket["exempt"] += 1
            else:
                year = str(hour["date"])[:4]
                bucket["diffs"].append(worst)
                per_pair_year.setdefault(pair, {}).setdefault(
                    year, []).append(worst)
                per_year.setdefault(year, []).append(worst)
                opens.append(abs(float(hour["open_diff_pips"])))
                closes.append(abs(float(hour["close_diff_pips"])))
                name = density_bucket(int(hour.get("duka_ticks", 0)))
                dense = per_density.setdefault(
                    name, {"diffs": [], "beyond": 0})
                dense["diffs"].append(worst)
                if hour.get("beyond_threshold"):
                    dense["beyond"] += 1
            if hour.get("beyond_threshold"):
                bucket["beyond"] += 1
                flagged.append(hour)
    for bucket in per_pair.values():
        bucket.update(_stats(bucket.pop("diffs")))
    for bucket in per_density.values():
        diffs = bucket.pop("diffs")
        bucket.update(_stats(diffs))
        bucket["beyond_share"] = (round(bucket["beyond"] / len(diffs), 4)
                                  if diffs else 0.0)
    flagged.sort(key=lambda h: (-float(h["abs_worst_pips"]), h["pair"],
                                h["date"], h["hour"]))
    return {
        "threshold_pips": threshold_pips,
        "hours_compared": compared,
        "hours_roll_exempt": exempt,
        "hours_missing": missing,
        "hours_beyond_threshold": len(flagged),
        "by_pair": {k: per_pair[k] for k in sorted(per_pair)},
        "by_pair_year": {
            pair: {year: _stats(values) for year, values in sorted(years.items())}
            for pair, years in sorted(per_pair_year.items())},
        "by_year": {year: _stats(values)
                    for year, values in sorted(per_year.items())},
        # Reported side by side rather than instead of each other. They turn
        # out to behave almost identically, which is itself worth showing: an
        # hour's two boundaries disagree for the same reason and by the same
        # amount, so neither is the noisy one.
        "open_vs_close": {"open_abs": _stats(opens),
                          "close_abs": _stats(closes)},
        "by_density": {name: per_density[name]
                       for name, _, _ in DENSITY_BUCKETS
                       if name in per_density},
        "flagged": flagged,
    }


def _stats(values: Sequence[float]) -> dict[str, Any]:
    """Count, mean, median, p95 and max of an absolute-difference sample."""
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    n = len(ordered)

    def quantile(q: float) -> float:
        return ordered[min(n - 1, max(0, int(round(q * (n - 1)))))]

    return {
        "n": n,
        "mean": round(sum(ordered) / n, 4),
        "median": round(quantile(0.5), 4),
        "p95": round(quantile(0.95), 4),
        "max": round(ordered[-1], 4),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.crosscheck_oanda",
        description="Cross-check stored hours against OANDA H1 candles.")
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the sample and print what it found."""
    from research.experiment import load_config
    from research.loader import project_root

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z")
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base else project_root())
    config = load_config(args.config)
    params = config.params
    pairs = [str(p) for p in params["pairs"]]
    store = base / str(params.get("data_dir", "data/research"))
    experiment_dir = base / str(params["experiment_dir"])
    roll = (int(params["crosscheck_roll_start_hour_ny"]),
            int(params["crosscheck_roll_end_hour_ny"]))

    client = OandaClient()
    if not client.has_token:
        print("OANDA_TOKEN_MISSING", flush=True)
        return 1
    _LOG.info("cross-checking against %s", client.host)

    availability_path = experiment_dir / AVAILABILITY_NAME
    if not availability_path.is_file():
        availability_path.parent.mkdir(parents=True, exist_ok=True)
        for pair in pairs:
            row = history_start(client, pair)
            row["host"] = client.host
            _append(availability_path, row)
            time.sleep(REQUEST_INTERVAL)
        _LOG.info("wrote %s", availability_path)

    summary = run_sample(
        store, client, pairs,
        as_date(str(params["start_date"])), as_date(str(params["end_date"])),
        hours=[int(h) for h in params["crosscheck_hours"]],
        dates_per_year=int(params["crosscheck_dates_per_year"]),
        threshold_pips=float(params["crosscheck_threshold_pips"]),
        roll=roll, seed=int(config.seed),
        checkpoint=experiment_dir / CROSSCHECK_NAME)
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("flagged", "by_pair_year")}, indent=2))
    if summary["hours_beyond_threshold"]:
        print(f"CROSSCHECK_BEYOND_THRESHOLD "
              f"{summary['hours_beyond_threshold']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
