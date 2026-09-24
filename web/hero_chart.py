"""The hero chart: A1 energy left, belief vs truth, with future commitments stacked by claim.

SPEC §12 ("the band is the promise") and §13. On the never-cut list.

The chart is built in two steps on purpose:

    frames  ->  ChartSpec  ->  PNG  /  JSON for the browser

`ChartSpec` is plain geometry - series, limits, annotations - so it can be ASSERTED. A chart that
only exists as pixels can only be checked by looking at it, and on 2026-09-16 a chart rendered
clean, correctly styled, entirely blank, and exited 0 (PRACTICE-NOTES.md FINDING-7). "The PNG was
written" is not a test. "Every point of the truth line is inside the axes" is.

The slide deck and the CLI fallback both render from the same spec, so the thing on the projector
is the thing the test checked.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# dataviz reference palette, shared with prep/slide1_chart.py
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
TRUTH, BELIEF, CROSSING = "#0b0b0b", "#2a78d6", "#c2352a"
CLAIM_FILL = {"COOP_PEAK": "#f3c7b3", "ADER_AS": "#bfe3d4", "ADER_ENERGY": "#c3d9f2"}
CLAIM_EDGE = {"COOP_PEAK": "#eb6834", "ADER_AS": "#1baf7a", "ADER_ENERGY": "#2a78d6"}
CLAIM_ORDER = ("COOP_PEAK", "ADER_AS", "ADER_ENERGY")

# A crossing smaller than this is float noise, not an over-promise. Stated as a constant so it
# is a decision someone made rather than an accident of `>`.
MATERIAL_CROSSING_KWH = 5.0


@dataclass
class Series:
    name: str
    x: list[float]                  # seconds CT
    y: list[float]                  # kWh
    kind: str                       # "line" | "stack"
    color: str = INK


@dataclass
class Annotation:
    x: float
    y: float
    text: str
    kind: str = "note"              # "note" | "crossing"


@dataclass
class ChartSpec:
    title: str
    subtitle: str
    xlim: tuple[float, float]
    ylim: tuple[float, float]
    series: list[Series]
    annotations: list[Annotation] = field(default_factory=list)
    y_label: str = "kWh above the backup floor"
    source: str = ""

    # ---- the assertions the chart_fixture lens makes
    def line(self, name: str) -> Series:
        for s in self.series:
            if s.name == name:
                return s
        raise KeyError(f"no series {name!r}; have {[s.name for s in self.series]}")

    def fraction_inside(self, name: str) -> float:
        s = self.line(name)
        if not s.x:
            return 0.0
        (x0, x1), (y0, y1) = self.xlim, self.ylim
        inside = sum(1 for x, y in zip(s.x, s.y) if x0 <= x <= x1 and y0 <= y <= y1)
        return inside / len(s.x)

    def y_range(self, name: str) -> float:
        s = self.line(name)
        return (max(s.y) - min(s.y)) if s.y else 0.0

    def to_json(self) -> str:
        return json.dumps({
            "title": self.title, "subtitle": self.subtitle, "y_label": self.y_label,
            "xlim": list(self.xlim), "ylim": list(self.ylim), "source": self.source,
            "series": [{"name": s.name, "x": s.x, "y": s.y, "kind": s.kind, "color": s.color}
                       for s in self.series],
            "annotations": [{"x": a.x, "y": a.y, "text": a.text, "kind": a.kind}
                            for a in self.annotations],
        }, indent=1)


def _hhmm(ts: float) -> str:
    return f"{int(ts // 3600):02d}:{int(ts % 3600 // 60):02d}"


def build_hero_chart(frames: list[dict], controller: str, subtitle: str = "",
                     _plant_x_shift_s: float = 0.0) -> ChartSpec:
    """frames: dicts matching contracts.types.Frame. `_plant_x_shift_s` exists ONLY for control C7."""
    rows = [f for f in frames if f["controller"] == controller]
    if not rows:
        raise ValueError(f"no frames for controller {controller!r}")
    rows.sort(key=lambda f: f["ts"])

    xs = [f["ts"] + _plant_x_shift_s for f in rows]
    truth = Series("truth", xs, [f["energy_left_truth_kwh"] for f in rows], "line", TRUTH)
    belief = Series("belief_low", xs, [f["energy_left_low_kwh"] for f in rows], "line", BELIEF)

    # committed_future_kwh is stacked by claim: what is still OWED after this moment.
    stack, base = [], [0.0] * len(rows)
    for cid in CLAIM_ORDER:
        vals = [f["committed_future_kwh"].get(cid, 0.0) for f in rows]
        if not any(vals):
            continue
        top = [b + v for b, v in zip(base, vals)]
        stack.append(Series(cid, xs, top, "stack", CLAIM_FILL.get(cid, MUTED)))
        base = top
    commitment_top = base

    # FINDING-10: the crossing IS the thesis, and at S1's sizing it is a ~40 kWh gap on a
    # 1,500 kWh axis. Annotate it with the number; do not leave it to the projector.
    #
    # Two things the first version got wrong, caught by LOOKING at the render on 2026-09-16
    # after the tests passed:
    #   * `owed > have` fires on a floating-point hair. At 15:15 the stack and the truth line
    #     are equal to within 1e-10 and it annotated "promised 0 kWh more than it has" - a real
    #     annotation, at the wrong moment, carrying a meaningless number.
    #   * it took the FIRST crossing. The interesting moment is the WORST one.
    # A test that only asks "is there a crossing annotation containing the string kWh" passes
    # both bugs. Materiality is now part of the definition, not a matter of taste.
    annotations: list[Annotation] = []
    gaps = [(owed - have, i) for i, (owed, have) in enumerate(zip(commitment_top, truth.y))]
    worst_kwh, worst_i = max(gaps, default=(0.0, 0))
    if worst_kwh > MATERIAL_CROSSING_KWH:
        annotations.append(Annotation(
            x=xs[worst_i], y=commitment_top[worst_i],
            text=f"{_hhmm(rows[worst_i]['ts'])}  promised {worst_kwh:,.0f} kWh more than it has",
            kind="crossing"))

    ymax = max([*truth.y, *belief.y, *commitment_top, 1.0]) * 1.08
    return ChartSpec(
        title=f"A1 energy left — {controller}",
        subtitle=subtitle,
        xlim=(min(xs), max(xs)),
        ylim=(0.0, ymax),
        series=[*stack, belief, truth],
        annotations=annotations,
        source="ERCOT 2026-07-22, EIA-930 hourly; fleet sim truth vs controller belief",
    )


def render_png(spec: ChartSpec, path: Path, width: float = 11.0, height: float = 5.4) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    fig, ax = plt.subplots(figsize=(width, height), facecolor=SURFACE)
    base = [0.0] * len(spec.line("truth").x)
    for s in [x for x in spec.series if x.kind == "stack"]:
        ax.fill_between(s.x, base, s.y, facecolor=s.color,
                        edgecolor=CLAIM_EDGE.get(s.name, MUTED), linewidth=0.8, zorder=1)
        mid = len(s.x) // 8
        if max(s.y) - max(base) > 40:
            ax.text(s.x[mid], (base[mid] + s.y[mid]) / 2, s.name.replace("_", " "),
                    fontsize=8, color=INK_2, va="center", zorder=4)
        base = list(s.y)

    for s in [x for x in spec.series if x.kind == "line"]:
        ax.plot(s.x, s.y, color=s.color, lw=2.0 if s.name == "truth" else 1.6,
                ls="-" if s.name == "truth" else "--", zorder=3,
                label="truth" if s.name == "truth" else "belief, pessimistic edge")

    for a in spec.annotations:
        ax.scatter([a.x], [a.y], s=42, color=CROSSING, zorder=6)
        # The crossing usually lands late in the evening, so a right-hand label runs off the
        # axes and clips on a projector. Flip it inward past the two-thirds mark.
        span = spec.xlim[1] - spec.xlim[0] or 1.0
        late = (a.x - spec.xlim[0]) / span > 0.66
        ax.annotate(a.text, (a.x, a.y),
                    xytext=(-14 if late else 14, 20), textcoords="offset points",
                    ha="right" if late else "left", fontsize=9, color=CROSSING, zorder=6,
                    arrowprops=dict(arrowstyle="-", color=CROSSING, lw=0.9))

    ax.set_title(spec.title + ("\n" + spec.subtitle if spec.subtitle else ""),
                 fontsize=12, color=INK, loc="left")
    ax.set_ylabel(spec.y_label, color=INK_2, fontsize=9)
    ax.set_xlim(*spec.xlim)
    ax.set_ylim(*spec.ylim)
    ticks = [t for t in range(15 * 3600, 22 * 3600, 3600) if spec.xlim[0] <= t <= spec.xlim[1]]
    ax.set_xticks(ticks)
    ax.set_xticklabels([_hhmm(t) for t in ticks], color=INK_2)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=INK_2)
    fig.text(0.011, 0.015, spec.source, fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)
    return path
