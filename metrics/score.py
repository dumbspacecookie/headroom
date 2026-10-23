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


def crash_unobserved(frames: list[dict], crash: dict | None) -> bool:
    """HEADROOM-1014-S4/AC-1: was anybody waiting on this crash?

    Let R be the ts of the first frame with `ts > crash_ts + crash_duration_s` (the raw,
    un-snapped crash end `crash_recovery_s` already uses). The crash is UNOBSERVED when every
    frame with `crash_ts < ts <= R` has `booked_kw <= 0` - i.e. nothing was ever booked during
    the outage, so there was no claim for recovery to restore. If no frame falls after the crash
    end, R does not exist and the window is every frame with `ts > crash_ts` instead (the
    vacuous case of a crash at/after the last simulated frame reads as unobserved, per the
    independent review).

    `False` when `crash` is `None` - there is no crash to be unobserved.
    """
    if crash is None:
        return False
    end_ts = crash["crash_ts"] + crash["crash_duration_s"]
    ordered = sorted(frames, key=lambda fr: fr["ts"])
    after_end = [f for f in ordered if f["ts"] > end_ts]
    r_ts = after_end[0]["ts"] if after_end else None
    if r_ts is None:
        window = [f for f in ordered if f["ts"] > crash["crash_ts"]]
    else:
        window = [f for f in ordered if crash["crash_ts"] < f["ts"] <= r_ts]
    return not any(f["booked_kw"] > 0 for f in window)


def crash_recovery_s(frames: list[dict], crash: dict | None,
                     cfg: Config = DEFAULTS) -> float | None:
    """S3's crash_recovery_s (SPEC §6.5, HEADROOM-0930-S1C/AC-1). Truth-based: crash end ->
    first tick where delivered is back within `cfg.tolerance_frac` of booked, measured against
    `frame["delivered_truth_kw"]` and `frame["booked_kw"]` - never the controller's belief.

    "Crash end" is `crash["crash_ts"] + crash["crash_duration_s"]`, the raw boundary S1B calls
    `restart_boundary_ts` - never snapped to the tick grid - per the independent review's reading
    of AC-1. "Within tolerance" reuses `energy_breach`'s one-sided test, `delivered_kw >=
    (1 - tolerance_frac) * booked_kw`, for consistency with the rest of Must-tier scoring.

    AC-2: a tick with `booked_kw <= 0` is skipped, never counted as recovered, however much is
    delivered. `energy_breach` calls an unbooked tick "ok" so an idle claim never reads as a
    breach - that is a statement about breaches, not about recovery, and reusing it naively here
    let stale pre-crash power at a booked-to-zero tick score as "recovered" (run 1's adversary).

    HEADROOM-1014-S4/AC-1: `None` also when nobody was waiting on this crash at all
    (`crash_unobserved`) - a quiet stretch with no booking during the outage must not report its
    idle time as recovery.

    `None` if `crash` is `None`, the crash is unobserved, or no tick with `booked_kw > 0` and
    delivered back in tolerance ever occurs after crash end (nothing to report).
    """
    if crash is None:
        return None
    if crash_unobserved(frames, crash):
        return None
    end_ts = crash["crash_ts"] + crash["crash_duration_s"]
    for f in sorted(frames, key=lambda fr: fr["ts"]):
        if f["ts"] <= end_ts:
            continue
        booked_kw = f["booked_kw"]
        if booked_kw <= 0:
            continue
        if f["delivered_truth_kw"] >= (1.0 - cfg.tolerance_frac) * booked_kw:
            return f["ts"] - end_ts
    return None
