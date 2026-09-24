"""N-1 deliverability: can every CAPACITY hold survive losing any one region, device by device?

Why this module exists
----------------------
Until 2026-09-24 the N-1 reserve was one number: the largest reachable region's nameplate kW,
subtracted from the scope's nameplate kW. That is an aggregate answer to a per-device question,
and the 1,000-seed sweep showed where it breaks.

After the co-op's three-hour shave, S1's small 39.2 kWh units are nearly empty - water-filling
gives every device the same kW, so the small ones hit their floor first. By 20:20 on seed 259
the scope still reported 4,784 kW, but 224 of the 320 reachable devices held 0.69 kWh each. The
kW that actually had energy behind it was 96 large units x 23 kW = 2,208 kW, and that had to
carry ADER_ENERGY (928 kW) and the ADER_AS hold (1,500 kW) at once. 2,428 > 2,208. The hold went
undeliverable the moment a region dropped, and nothing had warned about it.

The ledger could not see this because an empty battery still contributes its full `kw_cap` to
`KW(scope)`. Power with no energy behind it is not power you can sell for an hour.

What it does
------------
Walk forward from now, bucket by bucket, draining a per-device copy of the fleet exactly the way
`control/allocator.py` will (water-fill, per-device kWh cap over the claim's remaining hours).
At every CAPACITY bucket that has not started yet, drop each reachable region in turn, re-place
ENERGY on the survivors, and ask whether the holds are still deliverable:

    sum over surviving devices of min(kw_cap - energy_kw, kwh_above_floor / max_deploy_h)

That expression is deliberately the same one `metrics.score.capacity_deliverable` scores against.
If the ledger and the metric define "deliverable" differently, the ledger can be right by its own
definition and still produce silent breaches - which is the exact shape of most findings in this
repo.

What it is not
--------------
- Not a forecast of truth. It projects from the pessimistic band (`kwh_above_floor_low`), with
  no ramp limits, no home load and no re-earmarking. It mirrors the allocator, not the world.
- Not a solver. It checks one admission profile; `Ledger.admit_all` decides who yields.
- It protects against losing ONE more region while one may already be dark. Whether the reserve
  should relax once a region is already out is an open owner decision (RATIONALE.md s2, SPEC s18).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from config import DEFAULTS, Config
from contracts.types import Product
from control.admission import Admission
from control.estimator import ScopeBelief


def waterfill(caps: np.ndarray, target_kw: float) -> np.ndarray:
    """Equal kW per device, each capped at its own limit, summing to `target_kw` where possible.

    Vectorised twin of the loop in `control/allocator.py` (FINDING-13's water-fill).
    `tests/unit/test_deliverability.py` pins the two against each other, because a projection
    that drains the fleet differently from the allocator is a projection of a different fleet.
    """
    if target_kw <= 0.0 or caps.size == 0:
        return np.zeros_like(caps)
    if caps.sum() <= target_kw:
        return caps.copy()
    s = np.sort(caps)
    n = s.size
    below = np.concatenate(([0.0], np.cumsum(s)[:-1]))
    level = (target_kw - below) / (n - np.arange(n))
    return np.minimum(caps, level[int(np.argmax(level <= s))])


@dataclass(frozen=True)
class Shortfall:
    margin_kw: float        # worst (deliverable - booked) found; negative means a breach
    bucket_ts: float        # the bucket end where it was found
    region_id: str          # the region whose loss causes it; "none" (N-0) or "energy"


class FleetArrays:
    """The reachable devices as arrays, built once per ledger pass rather than once per probe."""

    def __init__(self, belief: ScopeBelief):
        self.aux_reserved = belief.aux_reserved
        live = [e for e in belief.estimates if e.reachable and e.kw_cap > 0]
        self.kw = np.array([e.kw_cap for e in live], dtype=float)
        self.kwh = np.array([e.kwh_above_floor_low for e in live], dtype=float)
        region = np.array([e.region_id for e in live])
        self.regions = [str(r) for r in np.unique(region)]
        self.survivors = [region != r for r in self.regions]

    @property
    def empty(self) -> bool:
        return self.kw.size == 0


def worst_shortfall(fleet: FleetArrays, admitted: list[Admission], now: float,
                    locked: frozenset[int] = frozenset(), cfg: Config = DEFAULTS,
                    drop_regions: bool = True,
                    only: frozenset[int] | None = None,
                    skip: frozenset[int] = frozenset()) -> Shortfall | None:
    """The worst per-device margin over every bucket the ledger may still decide. None if none.

    Two questions per bucket, both about kW that has energy behind it:

    - ENERGY: can the reachable fleet place the booked kW, after the CAPACITY earmarks, with
      each device capped over the claim's remaining hours? Asked of the fleet as it is (no
      region removed). ENERGY is not a reserve product: if a region drops mid-delivery the
      reconcile cuts the tail with a Notice and the bucket in flight gets a late one. What this
      catches is the miss nobody could announce - seed 19, where the co-op's last bucket needed
      3,000 kW and the only devices with energy left were 96 large units, 96 x 23 = 2,208 kW.
    - CAPACITY: can every hold still be called, given what ENERGY is drawing? With
      `drop_regions`, after losing any one reachable region (N-1, headroom); without it, on the
      whole reachable fleet (N-0, the oracle).

    `locked` bucket ends are drained through but not scored: they cannot be re-decided, and a
    shortfall in one is the late Notice's job (runner, FINDING-22). Scoring them is what made the
    prototype "cut" COOP_PEAK at 20:15, a claim that had finished at 18:30. `only`, if given,
    limits scoring to those bucket ends - the ledger uses it so a claim is only ever trimmed for
    buckets it is actually in.
    """
    if fleet.empty:
        return None
    holds = [a for a in admitted if a.product is Product.CAPACITY]
    energy = [a for a in admitted if a.product is Product.ENERGY]
    ends = [a.end_ts for a in holds + energy if a.admitted_kw > 0.0 or a.locked_kw]
    if not ends or max(ends) <= now:
        return None
    last = max(ends)
    step_h = cfg.bucket_h
    # Standby drain per bucket - unless the estimates already reserved it to the horizon.
    aux_kwh = 0.0 if fleet.aux_reserved else cfg.aux_kw * step_h
    kw_all = fleet.kw
    kwh = fleet.kwh.copy()
    cases = (list(zip(fleet.regions, fleet.survivors)) if drop_regions
             else [("none", np.ones(kw_all.size, dtype=bool))])

    worst: Shortfall | None = None

    def note(margin: float, b: float, why: str) -> None:
        nonlocal worst
        if worst is None or margin < worst.margin_kw:
            worst = Shortfall(margin, b, why)

    # First bucket end strictly after `now`: the one being delivered, if now is mid-window.
    b = now - (now - cfg.t0) % cfg.bucket_s + cfg.bucket_s
    while b <= last + 1e-9:
        start = b - cfg.bucket_s
        energy_kw, energy_h = 0.0, step_h
        for a in energy:
            kw = a.kw_at(b)
            if kw > 0.0:
                energy_kw += kw
                # the allocator's per-device cap divides by the claim's remaining hours
                energy_h = max((a.end_ts - start) / 3600.0, step_h)
        active_holds = [a for a in holds if a.kw_at(b) > 0.0]
        held_kw = sum(a.kw_at(b) for a in active_holds)
        held_kwh = sum(a.kw_at(b) * a.max_deploy_h for a in active_holds)
        scored = (round(b) not in locked and round(b) not in skip
                  and (only is None or round(b) in only))

        if scored and energy_kw > 0.0:
            # the allocator earmarks CAPACITY first, proportionally, then places ENERGY
            f_kw = min(1.0, held_kw / kw_all.sum()) if kw_all.sum() > 0 else 1.0
            f_kwh = min(1.0, held_kwh / kwh.sum()) if kwh.sum() > 0 else 1.0
            caps = np.minimum(kw_all * (1.0 - f_kw), kwh * (1.0 - f_kwh) / energy_h)
            note(float(caps.sum()) - energy_kw, b, "energy")

        if scored and held_kw > 0.0:
            deploy_h = max(a.max_deploy_h for a in active_holds)
            for region_id, alive in cases:
                kw_s, kwh_s = kw_all[alive], kwh[alive]
                used = waterfill(np.minimum(kw_s, kwh_s / energy_h), energy_kw)
                note(float(np.minimum(kw_s - used, kwh_s / deploy_h).sum()) - held_kw, b,
                     region_id)

        # Drain the whole reachable fleet through this bucket, as the allocator would.
        used = waterfill(np.minimum(kw_all, kwh / energy_h), energy_kw)
        kwh = np.maximum(0.0, kwh - used * step_h - aux_kwh)
        b += cfg.bucket_s
    return worst


def in_flight_kw(fleet: FleetArrays, admitted: list[Admission], claim_id: str, t: float,
                 cfg: Config = DEFAULTS) -> float:
    """What the reachable fleet can deliver for `claim_id` in the bucket being delivered at `t`.

    The late Notice needs this and not the counterfactual's figure. The counterfactual admits one
    flat kW for a claim's whole remaining window, so on S2 it said "only 209 kW is now
    deliverable" while the fleet delivered the in-flight bucket in full: a correct answer to
    "would I sell the rest of this window at 763?" quoted as if it answered "is this bucket
    short?". The operator reads the second question.

    Mirrors the allocator's order: CAPACITY earmarks first, proportionally; ENERGY on what is
    left, each device capped over the claim's remaining hours. For a CAPACITY claim it is the
    metric's own question: the kW the hold could call on now, given what ENERGY is drawing.
    """
    me = next(a for a in admitted if a.claim_id == claim_id)
    if fleet.empty:
        return 0.0
    k, e = fleet.kw, fleet.kwh
    holds = [a for a in admitted if a.product is Product.CAPACITY and a.kw_at(t) > 0.0]
    energy = [a for a in admitted if a.product is Product.ENERGY and a.kw_at(t) > 0.0]
    tick_h = 60.0 / 3600.0
    if me.product is Product.ENERGY:
        hold_kw = sum(a.kw_at(t) for a in holds)
        hold_kwh = sum(a.kw_at(t) * a.max_deploy_h for a in holds)
        f_kw = min(1.0, hold_kw / k.sum()) if k.sum() > 0 else 1.0
        f_kwh = min(1.0, hold_kwh / e.sum()) if e.sum() > 0 else 1.0
        h = max(me.end_ts - t, 0.0) / 3600.0 + tick_h      # this tick included, as the allocator
        others = sum(a.kw_at(t) for a in energy if a.claim_id != claim_id)
        return max(0.0, float(np.minimum(k * (1 - f_kw), e * (1 - f_kwh) / h).sum()) - others)
    energy_kw = sum(a.kw_at(t) for a in energy)
    energy_h = max(max((a.end_ts for a in energy), default=t) - t, 0.0) / 3600.0 + tick_h
    used = waterfill(np.minimum(k, e / energy_h), energy_kw)
    others = sum(a.kw_at(t) for a in holds if a.claim_id != claim_id)
    return max(0.0, float(np.minimum(k - used, e / me.max_deploy_h).sum()) - others)
