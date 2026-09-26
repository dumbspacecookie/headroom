"""Replay one evening in the terminal: what went dark, what was booked, what arrived.

    python run.py show 333                  # headroom, regional draw
    python run.py show 333 headroom_no_n1   # any controller or variant
    python run.py show s2                   # the demo's tower evening

One row per five-minute bucket. The page (web/explore.html) replays 46 chosen evenings; this
replays any of them, from the same run_scenario the sweep scores.
"""
from __future__ import annotations

import os
import sys

from config import ct
from runner.run import CONTROLLERS, VARIANTS, RegionOutage, run_scenario
from sim.chaos import A1_REGIONS, describe, draw_faults

COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
if COLOR and os.name == "nt":
    os.system("")                       # switches the Windows console into ANSI mode
REGION_COLORS = (205, 214, 99, 84, 75)


def paint(text: str, code: int | str) -> str:
    return f"\x1b[38;5;{code}m{text}\x1b[0m" if COLOR else text


def hhmm(ts: float) -> str:
    return f"{int(ts // 3600) % 24:02d}:{int(ts % 3600) // 60:02d}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    demo = argv[0].lower() == "s2"
    seed = 42 if demo else int(argv[0])
    rule = argv[1] if len(argv) > 1 else "headroom"
    if rule not in CONTROLLERS and rule not in VARIANTS:
        print(f"unknown rule {rule!r}; try one of: {', '.join(['headroom', *VARIANTS])}")
        return 2
    faults = ((RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),) if demo else draw_faults(seed))
    r = run_scenario(rule, seed=seed, faults=faults)
    m = r.metrics
    title = "S2, the tower evening" if demo else f"evening #{seed}"
    print(f"\n{paint(title, 51)}  {paint(rule, 213)}   outages: {describe(faults)}")
    print(f"silent min {paint(m['silent_breach_buckets'], 197 if m['silent_breach_buckets'] else 84)}"
          f"   late min {paint(m['late_breach_buckets'], 208 if m['late_breach_buckets'] else 84)}"
          f"   notices {m['notices']}   delivered {m['delivered_kwh']:,.0f} of "
          f"{m['committed_kwh']:,.0f} kWh booked\n")
    print("        " + " ".join(A1_REGIONS) + "   booked (magenta) vs delivered (cyan), kW")

    frames, notices = r.frames, [e for e in r.events if e["kind"] == "notice"]
    peak = max(1.0, max(f["booked_kw"] for f in frames), max(f["delivered_truth_kw"] for f in frames))
    width = 44
    prev_s = prev_l = 0
    for k in range(0, len(frames), 5):
        chunk = frames[k:k + 5]
        t = chunk[-1]["ts"]
        dark = set().union(*(f["dark"].keys() for f in chunk))
        lanes = " ".join(paint("##", REGION_COLORS[j]) if reg in dark else paint("..", 238)
                         for j, reg in enumerate(A1_REGIONS))
        booked = max(f["booked_kw"] for f in chunk)
        got = min(f["delivered_truth_kw"] for f in chunk)
        nb, nd = round(width * booked / peak), round(width * got / peak)
        bar = paint("=" * min(nb, nd), 51) + paint("-" * max(0, nb - nd), 197) + " " * (width - max(nb, nd))
        s, l = chunk[-1]["silent_breaches"], chunk[-1]["late_breaches"]
        marks = (paint(" SILENT", 197) if s > prev_s else "") + (paint(" LATE", 208) if l > prev_l else "")
        prev_s, prev_l = s, l
        said = [e for e in notices if chunk[0]["ts"] - 60 < e["ts"] <= t]
        if booked == 0 and not dark and not marks and not said:
            continue                    # a quiet, unbooked bucket says nothing
        print(f"{hhmm(t)}   {lanes}   {bar} {booked:6,.0f} {got:6,.0f}{marks}")
        for e in said:
            print(" " * 27 + paint("> " + e["text"], 191))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
