"""Slide 3's picture: what the honesty costs, across 1,000 seeded evenings.

Same two-step shape as `web/hero_chart.py`, for the same reason:

    batch_results.json  ->  ChartSpec  ->  PNG  /  JSON for the browser

`ChartSpec` is plain geometry, so it can be **asserted**. FINDING-7 is why: a chart that only
exists as pixels can only be checked by looking at it, and on 2026-09-16 one rendered clean,
correctly styled, entirely blank, and exited 0.

THE FORM, and why it is this one. The data's job here is **polarity**, not magnitude: the
interesting fact is not how big either number is, it is **which side of zero each controller
lands on**. Zero is not an origin, it is a claim - *exactly what a perfect-information
controller could have delivered on that evening*.

`reasonable` sits to the LEFT of it on **1,000 evenings out of 1,000** - it sells capacity that
does not exist, every single night. `headroom` sits to the right on **957 of 1,000**; the other
43 run to -3.9% at worst, and those bars are drawn rather than trimmed, because "entirely to the
right" was the first thing I wrote here and it was not true. A pair of distributions on one
shared axis says all of that in about three seconds from the back of a room, which is the whole
budget Slide 3 has.

COLOR. Two categorical identities, so two categorical slots in fixed order - slot 1 blue for
`headroom`, slot 2 orange for `reasonable`. The polarity lives on the AXIS, not in the hue, so
this is deliberately not a diverging palette. Validated, not eyeballed:

    node scripts/validate_palette.js "#2a78d6,#eb6834" --mode light
    ALL CHECKS PASS - CVD separation dE 24.7 (protan), normal-vision 33.6, contrast >= 3:1

Both series are also direct-labelled, so identity never rests on colour alone.
"""
from __future__ import annotations

import json
from pathlib import Path

from web.hero_chart import GRID, INK, INK_2, MUTED, SURFACE, Annotation, ChartSpec, Series

# categorical slots 1 and 2 of the reference palette (see module docstring)
HEADROOM, REASONABLE = "#2a78d6", "#eb6834"
SERIES_COLOR = {"headroom": HEADROOM, "reasonable": REASONABLE}

BIN_PCT = 1.0                    # one percentage point per bin
ZERO_LABEL = "0% = what the fleet could actually deliver  →"


def _bins(values: list[float], lo: float, hi: float) -> tuple[list[float], list[float]]:
    """Bin centres and counts. Centres, not edges, so a bar is drawn about its own value."""
    n = max(1, int(round((hi - lo) / BIN_PCT)))
    counts = [0.0] * n
    for v in values:
        k = int((v - lo) / BIN_PCT)
        counts[min(max(k, 0), n - 1)] += 1
    return [lo + BIN_PCT * (i + 0.5) for i in range(n)], counts


def build_batch_chart(results: dict) -> ChartSpec:
    """`prep/out/batch_results.json` -> geometry."""
    held = {c: [r[c]["held_back_pct"] for r in results["rows"]
                if r[c]["held_back_pct"] == r[c]["held_back_pct"]]        # drop NaN
            for c in ("headroom", "reasonable")}
    # A FIXED window, not one the outliers get to set. A handful of seeds run to -135%, and
    # letting them size the axis squashed the -30..+10 band - where 99% of the evenings and the
    # entire argument live - into the right-hand third. `_bins` clamps anything beyond the
    # window into the edge bin, and `clamped` below says out loud how many that was, so the
    # range is a reading decision rather than a quiet truncation.
    lo, hi = -35.0, 18.0
    clamped = sum(1 for vs in held.values() for v in vs if v < lo or v > hi)

    series, peaks = [], {}
    for name in ("headroom", "reasonable"):            # fixed order, never cycled
        x, y = _bins(held[name], lo, hi)
        peaks[name] = max(y, default=0.0)
        series.append(Series(name, x, y, "hist", SERIES_COLOR[name]))
    peak = max(peaks.values(), default=1.0)

    # Direct labels sit above their OWN distribution, so neither needs a leader line and they
    # cannot collide with each other or with the bars.
    agg = results["aggregate"]
    notes = [Annotation(x=-1.0, y=peak * 1.14, text=ZERO_LABEL, kind="zero")]
    for name in ("headroom", "reasonable"):
        med = agg[name]["held_back_pct_median"]
        notes.append(Annotation(
            x=med, y=peaks[name] * 1.06,
            text=f"{name}  {med:+.1f}%  ·  "
                 f"{agg[name]['silent_buckets_total']:,} silent buckets",
            kind=name))
    if clamped:
        notes.append(Annotation(x=lo, y=peak * 0.50, kind="clamped",
                                text=f"{clamped} of {results['n_seeds']:,} evenings fall beyond {lo:+.0f}% and are drawn in the edge bin"))
    return ChartSpec(
        title="What the honesty costs",
        subtitle=f"{results['n_seeds']:,} seeded evenings · {results['devices']:,} devices · "
                 f"held back vs an oracle ceiling that was measured, not assumed",
        xlim=(lo, hi), ylim=(0.0, peak * 1.18),
        series=series, annotations=notes,
        y_label="evenings",
        source="held back = (oracle admitted kWh - controller admitted kWh) / oracle admitted kWh",
    )


def render_png(spec: ChartSpec, path: Path, width: float = 11.0, height: float = 5.0) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(width, height), facecolor=SURFACE)

    # 2px surface gap between adjacent bars: draw at 0.92 of bin width, not flush.
    for s in [x for x in spec.series if x.kind == "hist"]:
        ax.bar(s.x, s.y, width=BIN_PCT * 0.92, color=s.color, edgecolor=SURFACE,
               linewidth=0.6, zorder=3, label=s.name, alpha=0.95)

    # the zero line IS the argument, so it is the heaviest thing on the plot
    ax.axvline(0.0, color=INK, lw=2.0, zorder=5)

    for a in spec.annotations:
        if a.kind == "zero":
            ax.text(a.x, a.y, a.text, fontsize=9.5, color=INK, va="bottom", ha="right", zorder=6)
        elif a.kind == "clamped":
            ax.text(a.x + 0.6, a.y, a.text, fontsize=8, color=MUTED, va="center",
                    ha="left", zorder=6, style="italic")
        else:
            ax.text(a.x, a.y, a.text, fontsize=10.5, color=SERIES_COLOR[a.kind],
                    va="bottom", ha="center", zorder=6, fontweight="bold")

    ax.set_title(spec.title + ("\n" + spec.subtitle if spec.subtitle else ""),
                 fontsize=13, color=INK, loc="left")
    ax.set_xlabel("held back vs the oracle ceiling  (negative = sold more than could be delivered)",
                  color=INK_2, fontsize=9)
    ax.set_ylabel(spec.y_label, color=INK_2, fontsize=9)
    ax.set_xlim(*spec.xlim)
    ax.set_ylim(*spec.ylim)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}%" if v else "0")
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.legend(frameon=False, fontsize=9, loc="upper left", labelcolor=INK_2)
    fig.text(0.008, 0.015, spec.source, fontsize=7.5, color=MUTED)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    src = root / "prep" / "out" / "batch_results.json"
    if not src.exists():
        print(f"FAIL: {src.relative_to(root)} missing - run `python run.py batch 1000` first")
        return 1
    spec = build_batch_chart(json.loads(src.read_text(encoding="utf-8")))
    out = render_png(spec, root / "prep" / "out" / "batch_where_we_lose.png")
    print(f"wrote {out.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
