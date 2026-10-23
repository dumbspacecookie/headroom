"""The lease-vs-bytes chart: what piggybacking the lease on telemetry actually saves.

Same two-step shape as `web/batch_chart.py` and `web/hero_chart.py`, for the same reason
(PRACTICE-NOTES.md FINDING-7 - a chart that only exists as pixels can only be checked by
looking at it):

    config.Config  ->  metrics.lease.lease_cost  ->  ChartSpec  ->  PNG

Two bars, one controller lever: `cfg.lease_piggyback`. "piggyback" rides the lease on the
telemetry tick that is sent anyway; "standalone" adds a 20 s heartbeat (`metrics.lease.
HEARTBEAT_S`) priced at `cfg.msg_ack_bytes` each, on top of the same telemetry traffic. Both
bars hold every other knob fixed at the caller's `cfg` - this is a question about ONE lever,
not about re-deriving the whole config twice.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from config import Config, DEFAULTS
from metrics.lease import lease_cost
from web.hero_chart import GRID, INK, INK_2, MUTED, SURFACE, Annotation, ChartSpec, Series

PIGGYBACK, STANDALONE = "#2a78d6", "#eb6834"
SERIES_COLOR = {"piggyback": PIGGYBACK, "standalone": STANDALONE}
SERIES_NAMES = ("piggyback", "standalone")
SERIES_X = {"piggyback": 0.0, "standalone": 1.0}


def build_lease_chart(cfg: Config = DEFAULTS) -> ChartSpec:
    """Holds every field of `cfg` fixed except `lease_piggyback`, which is forced to each of
    its two values in turn, so the two bars isolate exactly that one lever."""
    values = {
        name: lease_cost(dataclasses.replace(cfg, lease_piggyback=(name == "piggyback")))[
            "bytes_per_device_day"]
        for name in SERIES_NAMES
    }
    series = [Series(name, [SERIES_X[name]], [values[name]], "bar", SERIES_COLOR[name])
              for name in SERIES_NAMES]

    extra = values["standalone"] - values["piggyback"]
    extra_pct = extra / values["piggyback"] * 100.0 if values["piggyback"] else 0.0
    annotations = [Annotation(
        x=SERIES_X["standalone"], y=values["standalone"],
        text=f"standalone heartbeat costs {extra_pct:+.1f}% more bytes/device/day",
        kind="standalone")]

    peak = max(values.values(), default=1.0)
    return ChartSpec(
        title="Lease delivery: piggyback vs standalone heartbeat",
        subtitle="bytes per device-day, telemetry traffic held fixed",
        xlim=(-0.5, 1.5),
        ylim=(0.0, peak * 1.15),
        series=series,
        annotations=annotations,
        y_label="bytes / device / day",
        source="metrics.lease.lease_cost - telemetry every t_tel_s, "
               "standalone heartbeat every 20 s",
    )


def render_png(spec: ChartSpec, path: Path, width: float = 7.0, height: float = 5.0) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(width, height), facecolor=SURFACE)

    for s in [x for x in spec.series if x.kind == "bar"]:
        ax.bar(s.x, s.y, width=0.6, color=s.color, edgecolor=SURFACE,
               linewidth=0.6, zorder=3, label=s.name)

    for a in spec.annotations:
        ax.text(a.x, a.y * 1.02, a.text, fontsize=9, color=INK, va="bottom",
                ha="center", zorder=6)

    ax.set_title(spec.title + ("\n" + spec.subtitle if spec.subtitle else ""),
                 fontsize=12, color=INK, loc="left")
    ax.set_ylabel(spec.y_label, color=INK_2, fontsize=9)
    ax.set_xlim(*spec.xlim)
    ax.set_ylim(*spec.ylim)
    ax.set_xticks([SERIES_X[n] for n in SERIES_NAMES])
    ax.set_xticklabels(list(SERIES_NAMES), color=INK_2)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK_2, labelsize=9)
    fig.text(0.012, 0.015, spec.source, fontsize=7.5, color=MUTED)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    spec = build_lease_chart(DEFAULTS)
    out = render_png(spec, root / "prep" / "out" / "lease_bytes.png")
    print(f"wrote {out.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
