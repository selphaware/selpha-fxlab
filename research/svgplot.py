"""A small, deterministic SVG plotter for report figures.

Written rather than imported, and the reason is narrower than the one behind
:mod:`research.stats`. ``SPEC2.md`` permits installing matplotlib when a card
needs it, and this card does want figures. What it also wants -- ruling R6 --
is that every figure is *derived at render time* from the result document and
that the whole deliverable is diffable. matplotlib's SVG output carries a
creation timestamp and font-hash salt by default, so two renders of an
unchanged result produce different bytes, and a reviewer diffing the report
directory sees every figure as changed on every run.

What comes out of here is plain, deterministic SVG text: identical input gives
identical bytes, forever, and ``git diff`` on a figure shows the data points
that moved. Each figure is written next to a CSV of the numbers that produced
it, so a reader who distrusts the picture can check the table -- which the card
asks for explicitly.

The chart types are the four this battery needs and nothing more: a line chart
over an ordered x-axis, a grouped bar chart over categories, a scatter, and a
horizon profile with a reference line. Anything a fifth chart type would buy is
better spent on the table underneath it.

Every figure defines both light and dark colours through a ``prefers-color-
scheme`` block, because a figure that is legible only against the background it
was drawn on is a figure half the readers cannot read.
"""

from __future__ import annotations

import html
import math
import pathlib
from typing import Any, Final, Iterable, Sequence

#: Canvas geometry. Fixed rather than configurable: a report whose figures are
#: all the same size is one a reader can scan, and every caller here wants the
#: same shape.
WIDTH: Final[int] = 880
HEIGHT: Final[int] = 420
MARGIN_LEFT: Final[int] = 78
MARGIN_RIGHT: Final[int] = 26
MARGIN_TOP: Final[int] = 40
MARGIN_BOTTOM: Final[int] = 74

#: A categorical palette that stays distinguishable in both themes and in
#: greyscale. Twelve entries because the universe has twelve pairs.
PALETTE: Final[tuple[str, ...]] = (
    "#2f6f9f", "#c0603a", "#4b8b5a", "#8d5aa8", "#b08b28", "#3f8f97",
    "#a8496a", "#6b7f3a", "#7a6bbf", "#9a6b3f", "#4a8fbf", "#8f4a4a",
)

_STYLE: Final[str] = """
  .bg { fill: #ffffff; }
  .frame { stroke: #b9bec6; fill: none; stroke-width: 1; }
  .grid { stroke: #e3e6ea; stroke-width: 1; }
  .zero { stroke: #8a9099; stroke-width: 1; stroke-dasharray: 4 3; }
  .ref { stroke: #8a9099; stroke-width: 1.2; stroke-dasharray: 6 4; }
  text { font-family: "DejaVu Sans", "Segoe UI", system-ui, sans-serif;
         font-size: 11px; fill: #2a2e34; }
  text.title { font-size: 14px; font-weight: 600; }
  text.axis { font-size: 11px; fill: #565c65; }
  .series { fill: none; stroke-width: 1.8; stroke-linejoin: round; }
  .dot { stroke: none; }
  @media (prefers-color-scheme: dark) {
    .bg { fill: #14171b; }
    .frame { stroke: #4a5058; }
    .grid { stroke: #262b31; }
    .zero, .ref { stroke: #6d747d; }
    text { fill: #d8dce1; }
    text.axis { fill: #9aa1a9; }
  }
"""


def _fmt(value: float) -> str:
    """A short, stable decimal string, so identical data gives identical bytes."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _tidy(value: float) -> str:
    """An axis label with a sensible number of digits for its magnitude."""
    magnitude = abs(value)
    if magnitude == 0:
        return "0"
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    if magnitude >= 0.1:
        return f"{value:.2f}"
    return f"{value:.3g}"


def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    """Round tick positions spanning ``[low, high]``.

    The usual 1/2/5 progression. Deterministic, and it keeps an axis from
    labelling itself 0.0731, 0.1462, 0.2193.
    """
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return [low]
    raw = (high - low) / max(1, count)
    exponent = math.floor(math.log10(raw))
    base = raw / (10 ** exponent)
    step = (1.0 if base <= 1.0 else 2.0 if base <= 2.0
            else 5.0 if base <= 5.0 else 10.0) * (10 ** exponent)
    first = math.ceil(low / step) * step
    ticks: list[float] = []
    value = first
    while value <= high + step * 1e-9 and len(ticks) < 32:
        ticks.append(round(value, 12))
        value += step
    return ticks or [low, high]


class Figure:
    """One SVG canvas with a linear x and y mapping.

    Args:
        title: Drawn at the top left.
        x_label, y_label: Axis captions.
        x_range, y_range: Data ranges; padded and rounded for the axes.
        x_ticks: Explicit ``(position, label)`` pairs for a categorical axis.
    """

    def __init__(self, title: str, *, x_label: str = "", y_label: str = "",
                 x_range: tuple[float, float] = (0.0, 1.0),
                 y_range: tuple[float, float] = (0.0, 1.0),
                 x_ticks: Sequence[tuple[float, str]] | None = None) -> None:
        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self.x_low, self.x_high = _pad(*x_range)
        self.y_low, self.y_high = _pad(*y_range)
        self.x_ticks = list(x_ticks) if x_ticks is not None else None
        self._body: list[str] = []
        self._legend: list[tuple[str, str]] = []

    # -- coordinate mapping ------------------------------------------------

    def px(self, x: float) -> float:
        """Data x to canvas x."""
        span = self.x_high - self.x_low or 1.0
        return MARGIN_LEFT + (x - self.x_low) / span * self._plot_width()

    def py(self, y: float) -> float:
        """Data y to canvas y, inverted so larger values sit higher."""
        span = self.y_high - self.y_low or 1.0
        return (HEIGHT - MARGIN_BOTTOM
                - (y - self.y_low) / span * self._plot_height())

    def _plot_width(self) -> float:
        return WIDTH - MARGIN_LEFT - MARGIN_RIGHT

    def _plot_height(self) -> float:
        return HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    # -- marks -------------------------------------------------------------

    def line(self, points: Sequence[tuple[float, float]], colour: str,
             label: str = "", dashed: bool = False) -> None:
        """A polyline through ``points``, skipping non-finite values."""
        run: list[str] = []
        for x, y in points:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            run.append(f"{_fmt(self.px(x))},{_fmt(self.py(y))}")
        if not run:
            return
        dash = ' stroke-dasharray="5 3"' if dashed else ""
        self._body.append(
            f'<polyline class="series" stroke="{colour}"{dash} '
            f'points="{" ".join(run)}" />')
        if label:
            self._legend.append((label, colour))

    def dots(self, points: Sequence[tuple[float, float]], colour: str,
             label: str = "", radius: float = 2.4) -> None:
        """Filled circles at ``points``."""
        drawn = False
        for x, y in points:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            self._body.append(
                f'<circle class="dot" cx="{_fmt(self.px(x))}" '
                f'cy="{_fmt(self.py(y))}" r="{_fmt(radius)}" fill="{colour}" />')
            drawn = True
        if label and drawn:
            self._legend.append((label, colour))

    def bar(self, x_left: float, x_right: float, y: float, colour: str,
            label: str = "") -> None:
        """One bar from the zero line (or the axis floor) to ``y``."""
        if not math.isfinite(y):
            return
        base = self.py(max(self.y_low, min(0.0, self.y_high)))
        top = self.py(y)
        left, right = self.px(x_left), self.px(x_right)
        self._body.append(
            f'<rect x="{_fmt(min(left, right))}" y="{_fmt(min(base, top))}" '
            f'width="{_fmt(abs(right - left))}" '
            f'height="{_fmt(abs(base - top))}" fill="{colour}" '
            f'fill-opacity="0.85" />')
        if label:
            self._legend.append((label, colour))

    def reference(self, y: float, caption: str = "") -> None:
        """A horizontal reference line, for a null value such as VR = 1."""
        if not (self.y_low <= y <= self.y_high):
            return
        position = self.py(y)
        self._body.append(
            f'<line class="ref" x1="{MARGIN_LEFT}" y1="{_fmt(position)}" '
            f'x2="{WIDTH - MARGIN_RIGHT}" y2="{_fmt(position)}" />')
        if caption:
            self._body.append(
                f'<text class="axis" x="{WIDTH - MARGIN_RIGHT - 4}" '
                f'y="{_fmt(position - 4)}" text-anchor="end">'
                f'{html.escape(caption)}</text>')

    # -- rendering ---------------------------------------------------------

    def render(self) -> str:
        """The whole figure as SVG text."""
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'role="img" aria-label="{html.escape(self.title)}">',
            f"<style>{_STYLE}</style>",
            f'<rect class="bg" x="0" y="0" width="{WIDTH}" height="{HEIGHT}" />',
            f'<text class="title" x="{MARGIN_LEFT}" y="24">'
            f'{html.escape(self.title)}</text>',
        ]
        parts += self._axes()
        parts += self._body
        parts += self._legend_marks()
        parts.append("</svg>")
        return "\n".join(parts) + "\n"

    def _axes(self) -> list[str]:
        """Frame, gridlines, ticks and captions."""
        out: list[str] = []
        for value in _nice_ticks(self.y_low, self.y_high):
            y = self.py(value)
            out.append(f'<line class="grid" x1="{MARGIN_LEFT}" y1="{_fmt(y)}" '
                       f'x2="{WIDTH - MARGIN_RIGHT}" y2="{_fmt(y)}" />')
            out.append(f'<text class="axis" x="{MARGIN_LEFT - 8}" '
                       f'y="{_fmt(y + 4)}" text-anchor="end">'
                       f'{html.escape(_tidy(value))}</text>')
        if self.y_low < 0.0 < self.y_high:
            zero = self.py(0.0)
            out.append(f'<line class="zero" x1="{MARGIN_LEFT}" y1="{_fmt(zero)}" '
                       f'x2="{WIDTH - MARGIN_RIGHT}" y2="{_fmt(zero)}" />')

        ticks = (self.x_ticks if self.x_ticks is not None
                 else [(v, _tidy(v)) for v in _nice_ticks(self.x_low,
                                                          self.x_high, 7)])
        baseline = HEIGHT - MARGIN_BOTTOM
        rotate = any(len(label) > 5 for _v, label in ticks) and len(ticks) > 8
        for value, label in ticks:
            x = self.px(value)
            out.append(f'<line class="grid" x1="{_fmt(x)}" y1="{MARGIN_TOP}" '
                       f'x2="{_fmt(x)}" y2="{_fmt(baseline)}" />')
            if rotate:
                out.append(
                    f'<text class="axis" x="{_fmt(x)}" y="{_fmt(baseline + 6)}" '
                    f'text-anchor="end" transform="rotate(-45 {_fmt(x)} '
                    f'{_fmt(baseline + 6)})">{html.escape(label)}</text>')
            else:
                out.append(
                    f'<text class="axis" x="{_fmt(x)}" y="{_fmt(baseline + 16)}" '
                    f'text-anchor="middle">{html.escape(label)}</text>')

        out.append(f'<rect class="frame" x="{MARGIN_LEFT}" y="{MARGIN_TOP}" '
                   f'width="{_fmt(self._plot_width())}" '
                   f'height="{_fmt(self._plot_height())}" />')
        if self.x_label:
            out.append(f'<text class="axis" x="{WIDTH / 2}" '
                       f'y="{HEIGHT - 6}" text-anchor="middle">'
                       f'{html.escape(self.x_label)}</text>')
        if self.y_label:
            out.append(f'<text class="axis" x="14" y="{HEIGHT / 2}" '
                       f'text-anchor="middle" transform="rotate(-90 14 '
                       f'{HEIGHT / 2})">{html.escape(self.y_label)}</text>')
        return out

    def _legend_marks(self) -> list[str]:
        """A single-row legend under the plot, de-duplicated in first-seen order."""
        seen: dict[str, str] = {}
        for label, colour in self._legend:
            seen.setdefault(label, colour)
        if not seen:
            return []
        out: list[str] = []
        x = MARGIN_LEFT
        y = HEIGHT - 28
        for label, colour in seen.items():
            out.append(f'<rect x="{_fmt(x)}" y="{_fmt(y - 8)}" width="10" '
                       f'height="10" fill="{colour}" />')
            out.append(f'<text class="axis" x="{_fmt(x + 14)}" y="{_fmt(y)}">'
                       f'{html.escape(label)}</text>')
            x += 22 + 6.6 * len(label)
            if x > WIDTH - 120:
                x = MARGIN_LEFT
                y += 14
        return out


def _pad(low: float, high: float) -> tuple[float, float]:
    """Widen a data range slightly so marks do not sit on the frame."""
    if not (math.isfinite(low) and math.isfinite(high)):
        return 0.0, 1.0
    if high <= low:
        span = abs(low) or 1.0
        return low - 0.5 * span, high + 0.5 * span
    pad = 0.06 * (high - low)
    return low - pad, high + pad


def data_range(values: Iterable[float]) -> tuple[float, float]:
    """The finite min and max of ``values``, or ``(0, 1)`` if there are none."""
    finite = [float(v) for v in values
              if v is not None and math.isfinite(float(v))]
    if not finite:
        return 0.0, 1.0
    return min(finite), max(finite)


def write(figure: Figure, path: pathlib.Path, table: Sequence[Sequence[Any]],
          header: Sequence[str]) -> dict[str, str]:
    """Write the figure and the table it was drawn from, side by side.

    The T4 card asks for figures "with their source tables". They are written
    together here rather than by two callers, so a figure cannot reach the
    report without the numbers behind it.

    Returns:
        ``{"svg": ..., "csv": ...}`` with both paths, project-relative as
        given.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(figure.render(), encoding="utf-8")
    csv_path = path.with_suffix(".csv")
    lines = [",".join(_csv_cell(c) for c in header)]
    lines += [",".join(_csv_cell(c) for c in row) for row in table]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"svg": path.as_posix(), "csv": csv_path.as_posix()}


def _csv_cell(value: Any) -> str:
    """One CSV cell: quoted when it has to be, never locale-dependent."""
    if value is None:
        return ""
    if isinstance(value, float):
        text = repr(round(value, 10))
    else:
        text = str(value)
    if any(ch in text for ch in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text
