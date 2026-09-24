"""The perfect-information ceiling. SPEC 7 (`oracle` row) and 10 (`capacity_held_back_vs_oracle_pct`).

**This is the one file in `control/` allowed to import `sim/`**, and the boundary test names it
explicitly (`tests/boundary/test_control_does_not_import_sim.py`). Everything else in `control/`
may only know what the network delivered; this one reads the truth directly, on purpose, because
its whole job is to answer "what was actually available?" so the honest controller's caution can
be priced against it.

**What makes it an oracle, precisely - three things and no more:**

  1. **It sees true SoC**, not the pessimistic band. No `eps`, no staleness widening, no
     mode-aware drain guesswork.
  2. **It holds no N-1 reserve.** `haircut = "independent"`, so `kw_reserve` and
     `kwh_reserve` are both zero. It does not insure against a region it has not lost yet.
  3. **It still obeys the ledger.** It runs the *same* SPEC 6.3 admission, the same allocator
     and the same per-device kWh cap as `headroom`. It is a controller with better information,
     not a controller with different rules.

**What it is NOT: a controller that can promise anything it likes.** A "ceiling" that breaches
its own promises is not a ceiling, it is a bigger number - and every held-back percentage
measured against it would be inflated by exactly the amount it cheated. So the ceiling carries
a control: `tests/perf` asserts the oracle finishes the batch with **zero silent breaches and
zero floor breaches**. If that ever fails, the oracle is over-admitting and
`capacity_held_back_vs_oracle_pct` is meaningless until it is fixed - the test says so in those
words rather than reporting a percentage nobody can defend.

It deliberately does **not** get foresight of faults. SPEC 7 says "knows future faults", and an
oracle that pre-positions around an outage it has not observed would fold two different
advantages - better present information, and prophecy - into one number, leaving "what does our
caution cost?" unanswerable. Perfect present knowledge is the ceiling this build can defend, and
the gap it measures is exactly the price of the band plus the reserve. Recorded in DECISIONS.md
rather than quietly narrowed.
"""
from __future__ import annotations

from dataclasses import replace

from config import Config
from contracts.types import Estimate
from control.estimator import ScopeBelief, scope_belief
from sim.fleet import FleetState                # the import that makes this file the exception

NAME = "oracle"


def truth_estimates(estimates: list[Estimate], fleet: FleetState, idx: dict[str, int],
                    cfg: Config) -> list[Estimate]:
    """The same devices, re-described from fleet truth instead of from telemetry.

    Reachability is left exactly as the estimator found it: a dark device is still dark. The
    oracle knows what every battery holds; it does not get a radio the others do not have.
    """
    out = []
    for est in estimates:
        i = idx[est.device_id]
        soc = float(fleet.soc_kwh[i])
        above = max(0.0, soc - float(fleet.floor_kwh[i]))
        out.append(replace(
            est,
            soc_low_kwh=soc, soc_high_kwh=soc,
            kwh_above_floor_low=above if est.reachable else 0.0,
            kw_cap=float(fleet.p_max[i]) if est.reachable else 0.0,
        ))
    return out


def oracle_view(estimates: list[Estimate], fleet: FleetState, idx: dict[str, int],
                feeder_caps: dict[str, float], scope: str,
                cfg: Config, scale: float = 1.0,
                now: float | None = None, tick_s: float = 60.0) -> tuple[list[Estimate], ScopeBelief]:
    """The oracle's whole world view: truth-fed estimates AND the belief built from them.

    Returned together from one call, because the ledger admits against the belief and the
    allocator places against the estimates. Hand those out separately and they can be built
    from different inputs - the ledger admitting on truth while the allocator still places on
    the pessimistic band would under-deliver every booking the oracle made and quietly lower
    the ceiling.

    Reusing `scope_belief` rather than writing a second aggregation is the other half: the
    feeder clip, the per-region share and the drain rate must be computed the SAME way for the
    ceiling and for the thing measured against it, or the gap between them is partly an
    artefact of two implementations. Only the inputs and the haircut differ.
    """
    ests = truth_estimates(estimates, fleet, idx, cfg)
    belief = scope_belief(ests, feeder_caps, scope, replace(cfg, haircut="independent"))
    if now is not None:
        # Perfect information includes the oracle's own standby draw. Without this it planned
        # every device to its exact floor, aux then pushed devices under it (349,440 device-
        # seconds on seed 2), and the ceiling search could only shrink everything uniformly -
        # which is how headroom came to "beat the ceiling". Applied to what the ALLOCATOR sees
        # only: the ledger's slack already charges aux over time (`drain_hi`), and charging it
        # in the belief too would double-count ~130 kWh and lower the ceiling the other way.
        # ... to the horizon PLUS the tick at the horizon, which still runs for tick_s: without it
        # 224 small units ended the evening 0.0008 kWh (one minute of aux) under their floor.
        aux_left = cfg.aux_kw * (max(0.0, cfg.horizon_ts - now) + tick_s) / 3600.0
        ests = [replace(e, kwh_above_floor_low=max(0.0, e.kwh_above_floor_low - aux_left))
                for e in ests]
        # And the SAME per-device numbers go to the ledger's stage-2 projection. The first
        # version gave them to the allocator only: the projection then believed each small unit
        # held ~0.15 kWh the allocator would not spend, and on seven evenings the oracle booked
        # a co-op bucket it could not place (seed 19: 3,000 kW sold, 2,208 delivered). E0 stays
        # truth - `drain_hi` already charges aux over time in the aggregate.
        belief = replace(belief, estimates=tuple(ests), aux_reserved=True)
    if scale < 1.0:
        # The knob the ceiling search turns (`runner/batch.py:measure_ceiling`). Scaling the
        # belief down scales what the ledger will admit down with it, monotonically, which is
        # all a bisection needs. It is NOT a fudge factor applied to a result: the search
        # reports the largest scale that still delivers everything it promised, and that
        # measured point IS the ceiling.
        belief = replace(belief, E0_kwh=belief.E0_kwh * scale, KW_kw=belief.KW_kw * scale)
    return ests, belief


def is_a_valid_ceiling(metrics: dict) -> tuple[bool, str]:
    """Did the oracle keep every promise it made? If not, it is not a ceiling.

    Returned as (ok, why) so the caller can put the reason in the failure message instead of a
    bare False - a control that says only "no" gets argued with.
    """
    silent = metrics.get("silent_breach_buckets", 0)
    floor = metrics.get("floor_breach_dev_s", 0.0)
    if silent:
        return False, (f"the oracle itself had {silent} SILENT breach buckets - it admitted more "
                       f"than it could keep, so every held-back % measured against it is "
                       f"inflated by however much it over-promised")
    if floor > 0:
        return False, (f"the oracle crossed the backup floor for {floor:,.0f} device-seconds - "
                       f"a ceiling that spends the homeowner's reserve is not a ceiling")
    return True, "kept every promise"


def held_back_pct(subject_kwh: float, oracle_kwh: float) -> float:
    """`capacity_held_back_vs_oracle_pct` (SPEC 10): the price of the band and the reserve.

    How much less energy a controller *committed* than the perfect-information ceiling
    committed on the same seed. Measured on ADMITTED kWh rather than delivered, because the
    question the slide asks is "what did our caution refuse to sell?" - a booking never made is
    the cost, and it never shows up in delivery.
    """
    if oracle_kwh <= 0:
        return float("nan")
    return 100.0 * (oracle_kwh - subject_kwh) / oracle_kwh
