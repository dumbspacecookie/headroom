"""Metamorphic relations. SPEC §11.

These do not ask "is the number right?" - nothing here knows the right number. They ask
"does the number move the right WAY when the world changes?", which catches a whole class of
bug that known-answer tests cannot: a ledger that is consistently, plausibly wrong.

Each relation is a sentence a Base engineer would nod at. If one fails, either the code is wrong
or the sentence is - and finding out which is the useful conversation.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from config import DEFAULTS, ct
from contracts.types import DeviceMode, Telemetry
from control.estimator import estimate_device, scope_belief
from control.ledger import Ledger
from scenarios.s1 import build_s1

CFG = DEFAULTS


def _ader_kw(cfg=CFG, soc_frac=None, **ledger_kw) -> float:
    s1 = build_s1(cfg=cfg, soc_frac=soc_frac)
    led = Ledger(s1.belief, now=s1.t0, cfg=cfg, **ledger_kw)
    led.admit_all(s1.claims)
    return led.admitted_by_id("ADER_ENERGY").admitted_kw


def _belief(cfg=CFG, soc_frac=None, age_s=0.0):
    """Rebuild A1's belief with telemetry aged by `age_s`."""
    s1 = build_s1(cfg=cfg, soc_frac=soc_frac)
    now = s1.t0
    frac = cfg.start_soc_frac if soc_frac is None else soc_frac
    ests = []
    for st in s1.statics:
        tel = Telemetry(device_id=st.device_id, seq=1, device_ts=now - age_s,
                        soc_kwh=frac * st.e_max_kwh, p_kw=0.0, home_load_kw=0.0,
                        mode=DeviceMode.GRID, last_applied_epoch=0, last_applied_seq=0,
                        recv_ts=now - age_s)
        ests.append(estimate_device(st, tel, now, 0.0, now + cfg.lease_s, cfg))
    from sim.topology import build_topology
    caps = {f.feeder_id: f.export_cap_kw for f in build_topology(cfg).feeders}
    return scope_belief(ests, caps, "aggregation:A1", cfg)


# ---------------------------------------------------------------- the six relations
def test_more_staleness_never_buys_more_room():
    """Older data must never let you promise MORE. If it does, the band is upside down and the
    whole 'staleness shrinks what you may promise' claim is backwards."""
    prev = None
    for age in (0.0, 4.0, 9.0, 11.0, 60.0):
        e0 = _belief(age_s=age).E0_kwh
        if prev is not None:
            assert e0 <= prev + 1e-6, f"E0 rose from {prev:,.1f} to {e0:,.1f} kWh as data aged {age}s"
        prev = e0


def test_more_devices_never_gives_less_room():
    small = replace(CFG, n_devices_a1=200, n_devices_total=1000)
    big = replace(CFG, n_devices_a1=400, n_devices_total=1000)
    assert _belief(cfg=big).E0_kwh > _belief(cfg=small).E0_kwh
    assert _belief(cfg=big).KW_kw > _belief(cfg=small).KW_kw


def test_a_lower_feeder_cap_never_gives_more_kw():
    loose = _belief(cfg=replace(CFG, feeder_cap_frac=0.95))
    tight = _belief(cfg=replace(CFG, feeder_cap_frac=0.40))
    assert tight.KW_kw < loose.KW_kw


def test_a_higher_backup_floor_never_gives_more_energy():
    """The homeowner's reserve is the first call on the battery, always."""
    low = _belief(cfg=replace(CFG, floor_frac=0.10))
    high = _belief(cfg=replace(CFG, floor_frac=0.40))
    assert high.E0_kwh < low.E0_kwh
    assert _ader_kw(cfg=replace(CFG, floor_frac=0.40)) < _ader_kw(cfg=replace(CFG, floor_frac=0.10))


def test_an_earlier_energy_booking_lowers_later_slack():
    """The whole thesis in one relation: energy spent at 5pm is not available at 9pm."""
    s1 = build_s1()
    ader = next(c for c in s1.claims if c.claim_id == "ADER_ENERGY")

    alone = Ledger(s1.belief, now=s1.t0)
    alone.admit(ader)
    slack_alone = min(alone.slack(t, ader) for t in alone.buckets(ader.start_ts, ader.end_ts))

    after_coop = Ledger(s1.belief, now=s1.t0)
    after_coop.admit_all(s1.claims)
    a = after_coop.admitted_by_id("ADER_ENERGY")
    slack_after = min(after_coop.slack(t, a) for t in after_coop.buckets(a.start_ts, a.end_ts))

    assert slack_after < slack_alone, (
        "booking the co-op's afternoon shave did not reduce the 8pm slack - the ledger is not "
        "carrying energy across time, which is the only thing it is for"
    )


def test_moving_a_device_out_of_the_aggregation_costs_the_aggregation_and_not_the_fleet():
    """ADER's ALR constraint, as a relation: the kWh can exist in the fleet but not in the
    aggregation that holds the award [S]. Shrink A1 and A1's energy must fall while the fleet's
    total stays put."""
    from sim.topology import build_topology
    full, shrunk = CFG, replace(CFG, n_devices_a1=300)
    fleet_kwh_full = sum(d.e_max_kwh for d in build_topology(full).devices)
    fleet_kwh_shrunk = sum(d.e_max_kwh for d in build_topology(shrunk).devices)

    assert _belief(cfg=shrunk).E0_kwh < _belief(cfg=full).E0_kwh, "A1 did not shrink"
    assert fleet_kwh_shrunk == pytest.approx(fleet_kwh_full, rel=0.02), (
        f"the FLEET changed size too ({fleet_kwh_full:,.0f} -> {fleet_kwh_shrunk:,.0f} kWh). "
        f"Then this test is measuring a smaller fleet, not a smaller aggregation, and S1's "
        f"scarcity could be blamed on fleet size."
    )


# ---------------------------------------------------------------- relations on the admission
def test_a_bigger_co_op_shave_never_leaves_ader_energy_better_off():
    prev = None
    for hours in (1.0, 2.0, 3.0, 4.0):
        kw = _ader_kw(cfg=replace(CFG, coop_shave_h=hours))
        if prev is not None:
            assert kw <= prev + 1e-6, f"a {hours} h shave admitted MORE than the shorter one"
        prev = kw


def test_a_fuller_fleet_never_admits_less():
    prev = None
    for frac in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        kw = _ader_kw(soc_frac=frac)
        if prev is not None:
            assert kw >= prev - 1e-6, f"starting at {frac:.0%} SoC admitted LESS than the emptier fleet"
        prev = kw


def test_a_longer_assumed_outage_never_admits_more():
    """The N-1 reserve is sized by how long you think the tower stays dark. Assuming worse must
    never let you sell more."""
    prev = None
    for h in (0.25, 0.5, 1.0, 2.0):
        kw = _ader_kw(cfg=replace(CFG, outage_h=h))
        if prev is not None:
            assert kw <= prev + 1e-6, f"a {h} h assumed outage admitted MORE than the shorter one"
        prev = kw
