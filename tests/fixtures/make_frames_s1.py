"""Generate tests/fixtures/frames_s1.json - FIXTURE frames, not a run.

The chart surface is built against these so it is never blocked on the backend. They are derived from the real ledger admissions, so the shapes and the
magnitudes are right, but the truth line is a projection, not a simulated fleet:

    truth(t)  = E0 - drain(t) - (energy actually delivered by t, assuming full delivery)
    belief(t) = truth(t) - the eps/reserve haircut the estimator already applied
    committed_future_kwh[claim] = kWh still OWED after t

WHEN THE ALLOCATOR AND THE SIM LAND, THIS FILE IS DELETED and the fixtures come from a real run.
Until then every chart rendered from it is labelled a fixture, so nobody reads a number off it.

Run: .venv/Scripts/python.exe tests/fixtures/make_frames_s1.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config import DEFAULTS                                  # noqa: E402
from contracts.types import Product                          # noqa: E402
from control.baselines import Reasonable                     # noqa: E402
from control.ledger import Ledger                            # noqa: E402
from scenarios.s1 import build_s1                            # noqa: E402

OUT = Path(__file__).resolve().parent / "frames_s1.json"
CFG = DEFAULTS


def _frames_for(controller_name: str, admissions, belief, t0: float, horizon: float) -> list[dict]:
    frames = []
    t = t0 + CFG.bucket_s
    while t <= horizon:
        delivered_kwh = 0.0
        owed: dict[str, float] = {}
        for a in admissions:
            if a.admitted_kw <= 0:
                continue
            if a.product is Product.ENERGY:
                elapsed_h = min(max(t - a.start_ts, 0.0), a.end_ts - a.start_ts) / 3600.0
                delivered_kwh += a.admitted_kw * elapsed_h
                remaining_h = max(0.0, (a.end_ts - max(t, a.start_ts)) / 3600.0)
                owed[a.claim_id] = a.admitted_kw * remaining_h
            else:
                # a CAPACITY hold owes its whole reservation for as long as the window is open
                owed[a.claim_id] = a.admitted_kw * a.max_deploy_h if t <= a.end_ts else 0.0

        drain = belief.drain_kw * (t - t0) / 3600.0
        # truth has no eps haircut and no N-1 set aside - that is the point of the two lines
        truth = belief.E0_kwh + belief.kwh_reserve_kwh - drain - delivered_kwh
        low = belief.E0_kwh - drain - delivered_kwh - belief.kwh_reserve_kwh

        booked = sum(a.admitted_kw for a in admissions
                     if a.product is Product.ENERGY and a.start_ts < t <= a.end_ts)
        frames.append({
            "ts": t, "controller": controller_name,
            "booked_kw": booked, "delivered_truth_kw": booked,
            "belief_low_kw": 0.0, "belief_high_kw": 0.0, "kw_room": 0.0,
            "energy_left_low_kwh": low, "energy_left_truth_kwh": truth,
            "committed_future_kwh": owed, "oracle_kw_room": 0.0,
            "floor_breach_dev_s": 0.0, "feeder_breach_kw_s": 0.0,
            "silent_breaches": 0, "notices": 0,
            "region_freshness": {}, "log_seq_range": [0, 0],
        })
        t += CFG.bucket_s
    return frames


def main() -> None:
    s1 = build_s1()
    head = Ledger(s1.belief, now=s1.t0)
    head.admit_all(s1.claims)

    reas = Reasonable(s1.belief, now=s1.t0)
    for c in sorted(s1.claims, key=lambda c: c.priority):
        reas.admit(c)

    frames = (_frames_for("headroom", head.admitted, s1.belief, s1.t0, s1.horizon_ts)
              + _frames_for("reasonable", reas.admitted, s1.belief, s1.t0, s1.horizon_ts))
    OUT.write_text(json.dumps({"scenario": "S1", "seed": 42, "fixture": True,
                               "note": "derived from ledger admissions; NOT a simulated run",
                               "frames": frames}, indent=1), encoding="utf-8")
    print(f"wrote {OUT.relative_to(OUT.parents[2])}  ({len(frames)} frames)")
    for name in ("headroom", "reasonable"):
        rows = [f for f in frames if f["controller"] == name]
        worst = max((sum(f["committed_future_kwh"].values()) - f["energy_left_truth_kwh"]
                     for f in rows), default=0.0)
        print(f"  {name:<11} max over-promise {worst:>9,.1f} kWh"
              + ("   <- crosses" if worst > 0 else "   (never crosses)"))


if __name__ == "__main__":
    main()
