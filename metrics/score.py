"""What counts as a breach. SPEC §10.

These definitions are ash's, not a lane's, for one reason: a wrong metric does not fail - it
recruits defenders. Every green run that used it meant less than it appeared to, and by the time
anyone notices, several decisions have been made on top of it.

The definition that matters most on S1: **a silent breach is energy OR capacity.** `reasonable`
keeps ~100% of its ENERGY promise on S1 and loses the ancillary-service hold instead. Count only
the energy kind and `reasonable` scores 0 silent, which would fail §11's own control on a
CORRECT implementation (PRACTICE-NOTES.md FINDING-5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config import DEFAULTS, Config


@dataclass
class Scoreboard:
    committed_kwh: float = 0.0
    delivered_kwh: float = 0.0
    silent_energy_buckets: int = 0
    silent_capacity_buckets: int = 0
    late_breach_buckets: int = 0
    capacity_buckets: int = 0
    capacity_ok_buckets: int = 0
    floor_breach_dev_s: float = 0.0
    feeder_breach_kw_s: float = 0.0
    notices: int = 0
    lead_times_s: list[float] = field(default_factory=list)
    downgrades: int = 0

    @property
    def silent_breach_buckets(self) -> int:
        return self.silent_energy_buckets + self.silent_capacity_buckets

    @property
    def promise_kept_pct(self) -> float:
        return 100.0 * self.delivered_kwh / self.committed_kwh if self.committed_kwh else 100.0

    @property
    def capacity_availability_pct(self) -> float:
        if not self.capacity_buckets:
            return float("nan")
        return 100.0 * self.capacity_ok_buckets / self.capacity_buckets

    @property
    def median_lead_time_s(self) -> float:
        if not self.lead_times_s:
            return 0.0
        xs = sorted(self.lead_times_s)
        return xs[len(xs) // 2]

    def as_dict(self, scenario: str, seed: int, controller: str, ablation: str,
                events_sha256: str, runtime_s: float) -> dict:
        return {
            "scenario": scenario, "seed": seed, "controller": controller, "ablation": ablation,
            "promise_kept_pct": round(self.promise_kept_pct, 3),
            "capacity_availability_pct": round(self.capacity_availability_pct, 3),
            "committed_kwh": round(self.committed_kwh, 1),
            "delivered_kwh": round(self.delivered_kwh, 1),
            "silent_breach_buckets": self.silent_breach_buckets,
            "silent_energy_buckets": self.silent_energy_buckets,
            "silent_capacity_buckets": self.silent_capacity_buckets,
            "late_breach_buckets": self.late_breach_buckets,
            "downgrades": self.downgrades,
            "median_lead_time_s": self.median_lead_time_s,
            "notices": self.notices,
            "floor_breach_dev_s": round(self.floor_breach_dev_s, 3),
            "feeder_breach_kw_s": round(self.feeder_breach_kw_s, 3),
            "events_sha256": events_sha256,
            "runtime_s": round(runtime_s, 3),
        }


def energy_breach(delivered_kw: float, admitted_kw: float, had_prior_notice: bool,
                  cfg: Config = DEFAULTS) -> str:
    """-> "ok" | "silent" | "late". §10: below tolerance with no PRIOR notice is silent."""
    if admitted_kw <= 0:
        return "ok"
    if delivered_kw >= (1.0 - cfg.tolerance_frac) * admitted_kw:
        return "ok"
    return "late" if had_prior_notice else "silent"


def capacity_deliverable(soc_kwh, floor_kwh, p_max_kw, in_use_kw, reachable, hold_kw: float,
                         max_deploy_h: float) -> bool:
    """TRUTH-based, no deployment needed (§10, v0.3).

    Could the reachable devices, given what ENERGY is already taking from them, hold `hold_kw`
    for `max_deploy_h` out of what sits above the floor? Computed on truth so it survives
    CAPACITY deployments being cut from scope.
    """
    spare = 0.0
    for soc, floor, pmax, used, live in zip(soc_kwh, floor_kwh, p_max_kw, in_use_kw, reachable):
        if not live:
            continue
        margin_kwh = max(0.0, soc - floor)
        spare += max(0.0, min(pmax - used, margin_kwh / max_deploy_h))
    # bool(), not the bare comparison: `spare` accumulates numpy scalars, so this returned a
    # numpy.bool_ that rode into every Frame as `cap_ok` and made the frames un-serialisable.
    # Found 2026-09-17 by the first thing that ever tried to write a frame to JSON.
    return bool(spare >= hold_kw - 1e-9)
