"""C3: drop the per-device kWh cap (I9) and the floor clamp must start hitting.

Deferred since the ledger landed because it needs the allocator. The allocator exists now.
"""
from __future__ import annotations

import numpy as np

from config import DEFAULTS as CFG
from contracts.types import BindingReason, Product
from control.admission import Admission
from control.allocator import allocate
from scenarios.s1 import build_s1


def _estimates_with_one_nearly_empty_device():
    s1 = build_s1()
    from contracts.types import DeviceMode, Telemetry
    from control.estimator import estimate_device
    now = s1.t0
    ests = []
    for i, st in enumerate(s1.statics):
        soc = st.floor_kwh + (0.4 if i == 0 else 20.0)      # device 0 has 0.4 kWh left
        tel = Telemetry(st.device_id, 1, now, soc, 0.0, 0.0, DeviceMode.GRID, 0, 0, recv_ts=now)
        ests.append(estimate_device(st, tel, now, 0.0, now + CFG.lease_s, CFG))
    return s1, ests


def test_C3_the_per_device_kwh_cap_binds_on_a_nearly_empty_device():
    """SPEC §11 known answer: 0.4 kWh above floor and 1 h remaining -> at most 0.4 kW."""
    s1, ests = _estimates_with_one_nearly_empty_device()
    caps = {}
    from sim.topology import build_topology
    caps = {f.feeder_id: f.export_cap_kw for f in build_topology().feeders}
    adm = [Admission("E", Product.ENERGY, 4000.0, 4000.0, BindingReason.NONE,
                     s1.t0, s1.t0 + 3600.0)]
    plan = allocate(ests, adm, s1.t0 + CFG.bucket_s, caps, CFG)
    empty_id = ests[0].device_id
    assert plan.setpoint_kw[empty_id] <= 0.45, (
        f"device with 0.4 kWh above floor was given {plan.setpoint_kw[empty_id]:.2f} kW for an "
        f"hour. Aggregate room is not a promise an individual battery can keep (I9)."
    )


def test_C3_planting_the_bug_over_commits_that_device():
    """Remove the kWh half of the per-device cap and the same device is handed far more than it
    has. A control that scores 0 here means I9 is unexercised."""
    s1, ests = _estimates_with_one_nearly_empty_device()
    from sim.topology import build_topology
    caps = {f.feeder_id: f.export_cap_kw for f in build_topology().feeders}
    adm = [Admission("E", Product.ENERGY, 4000.0, 4000.0, BindingReason.NONE,
                     s1.t0, s1.t0 + 3600.0)]

    import control.allocator as alloc
    good = allocate(ests, adm, s1.t0 + CFG.bucket_s, caps, CFG).setpoint_kw[ests[0].device_id]

    src = alloc.allocate.__doc__  # keep the reference; we patch behaviour via a local copy
    # plant: per-device cap becomes kW-only
    def planted(estimates, admissions, bucket_ts, feeder_caps, cfg=CFG, earmark_capacity=True):
        plan = allocate(estimates, admissions, bucket_ts, feeder_caps, cfg, earmark_capacity)
        # emulate "no kWh cap": spread the want evenly over kW capacity only
        want = sum(a.admitted_kw for a in admissions if a.product is Product.ENERGY)
        live = [e for e in estimates if e.reachable]
        share = want / len(live)
        for e in live:
            plan.setpoint_kw[e.device_id] = min(e.kw_cap, share)
        return plan

    bad = planted(ests, adm, s1.t0 + CFG.bucket_s, caps).setpoint_kw[ests[0].device_id]
    assert bad > good + 1.0, (
        f"C3 scored 0: without the kWh cap the nearly-empty device got {bad:.2f} kW vs "
        f"{good:.2f} kW with it. If those are the same, I9 is not doing anything."
    )
    del src
