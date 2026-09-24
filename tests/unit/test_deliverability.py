"""control/deliverability.py - the per-device N-1 check. RATIONALE.md s6c.

The check is only worth anything if it drains the fleet the way the allocator does and asks the
question the metric scores. Each test here pins one of those, or the seed-259 shape that made
the check necessary.
"""
from __future__ import annotations

import numpy as np
import pytest

from config import DEFAULTS, ct
from contracts.types import BindingReason, Product
from control.admission import Admission
from control.allocator import allocate
from control.deliverability import FleetArrays, waterfill, worst_shortfall
from control.estimator import estimate_device, scope_belief
from tests.unit.test_estimator import _static, _tel

CFG = DEFAULTS


def _energy(kw: float, start=ct(20, 0), end=ct(21, 0)) -> Admission:
    return Admission(claim_id="E", product=Product.ENERGY, requested_kw=kw, admitted_kw=kw,
                     reason=BindingReason.NONE, start_ts=start, end_ts=end)


def _hold(kw: float, start=ct(19, 0), end=ct(21, 0), deploy_h=1.0) -> Admission:
    return Admission(claim_id="AS", product=Product.CAPACITY, requested_kw=kw, admitted_kw=kw,
                     reason=BindingReason.NONE, start_ts=start, end_ts=end, max_deploy_h=deploy_h)


def _fleet(socs, e_max=39.2, p_max=11.5, regions=("R1", "R2", "R3", "R4")):
    t = ct(20, 0)
    return [estimate_device(_static(f"D{i}", region=regions[i % len(regions)], e_max=e_max,
                                    p_max=p_max), _tel(t, soc, dev_id=f"D{i}"), now=t,
                            last_setpoint_kw=0.0, lease_expiry_ts=t + 1e9, cfg=CFG)
            for i, soc in enumerate(socs)]


# ---------------------------------------------------------------- it drains like the allocator
@pytest.mark.parametrize("target_kw", [5.0, 60.0, 180.0, 1e6])
def test_waterfill_places_energy_exactly_where_the_allocator_does(target_kw):
    """The projection is a projection of THIS allocator or it is a projection of nothing."""
    rng = np.random.default_rng(7)
    ests = _fleet(list(rng.uniform(8.0, 39.0, 24)))
    now = ct(20, 5)
    adm = _energy(target_kw)
    plan = allocate(ests, [adm], now, {"F1": 1e9}, CFG, earmark_capacity=False)
    h = (adm.end_ts - now + 60.0) / 3600.0      # this tick included: control/allocator.py
    caps = np.array([min(e.kw_cap, e.kwh_above_floor_low / h) for e in ests])
    mine = waterfill(caps, target_kw)
    theirs = np.array([plan.setpoint_kw[e.device_id] for e in ests])
    assert mine == pytest.approx(theirs, abs=1e-6)


def test_waterfill_never_exceeds_a_cap_and_hits_the_target_when_it_can():
    caps = np.array([0.0, 1.0, 2.0, 10.0])
    out = waterfill(caps, 7.0)
    assert (out <= caps + 1e-12).all() and out.sum() == pytest.approx(7.0)
    assert waterfill(caps, 100.0) == pytest.approx(caps), "more asked than exists: every cap full"


# ---------------------------------------------------------------- the seed-259 shape
def test_empty_batteries_are_not_power_for_the_hold():
    """Seed 259 at 20:20, scaled down: most devices near their floor, a few large ones full.

    The scope's nameplate kW covers ENERGY + the hold easily. The kW that has energy behind it
    does not, once any one region is lost. The old aggregate reserve could not see this.
    """
    small = [CFG.floor_frac * 39.2 + 0.7] * 28          # ~empty 39.2 kWh units
    ests = _fleet(small) + _fleet([70.0] * 12, e_max=78.4, p_max=23.0)
    fleet = FleetArrays(scope_belief(ests, {"F1": 1e9}, "aggregation:A1", CFG))
    nameplate = sum(e.kw_cap for e in ests)
    energy, hold = _energy(90.0), _hold(150.0)
    assert nameplate > 90.0 + 150.0 * 2, "the aggregate view must say 'plenty' for this to mean anything"

    worst = worst_shortfall(fleet, [energy, hold], now=ct(19, 55), cfg=CFG)
    assert worst is not None and worst.margin_kw < 0, (
        f"losing a region should break the hold here, margin {worst.margin_kw if worst else None}")
    relieved = worst_shortfall(fleet, [_energy(0.0), hold], now=ct(19, 55), cfg=CFG)
    assert relieved.margin_kw > worst.margin_kw, "taking the junior ENERGY off must help the hold"


def test_a_locked_bucket_is_drained_through_but_never_scored():
    """Scoring a bucket already in delivery is how the prototype 'cut' a finished claim."""
    ests = _fleet([12.0] * 16)
    fleet = FleetArrays(scope_belief(ests, {"F1": 1e9}, "aggregation:A1", CFG))
    hold = _hold(1e4, start=ct(20, 0), end=ct(20, 10))        # absurd: fails in every bucket
    now = ct(20, 0)
    both = {round(ct(20, 5)), round(ct(20, 10))}
    assert worst_shortfall(fleet, [hold], now, frozenset(), CFG).margin_kw < 0
    assert worst_shortfall(fleet, [hold], now, frozenset(both), CFG) is None, (
        "every bucket is locked, so nothing may be scored")
    only_first = worst_shortfall(fleet, [hold], now, frozenset({round(ct(20, 5))}), CFG)
    assert only_first is not None and only_first.bucket_ts == pytest.approx(ct(20, 10))


# ---------------------------------------------------------------- the ledger's use of it
def test_s1_the_energy_term_still_says_928_and_the_reserve_says_763():
    """Two stages, two answers, both pinned. D1 (tests/known_answer) is stage 1 alone."""
    from control.ledger import Ledger
    from scenarios.s1 import build_s1

    s1 = build_s1()
    stage1 = Ledger(s1.belief, now=s1.t0, horizon_ts=s1.horizon_ts)
    stage1.admit_priority(s1.claims)
    ader1 = stage1.admitted_by_id("ADER_ENERGY")
    assert ader1.admitted_kw == pytest.approx(928.0, abs=10.0)
    assert ader1.reason is BindingReason.KWH_SLACK

    both = Ledger(s1.belief, now=s1.t0, horizon_ts=s1.horizon_ts)
    both.admit_all(s1.claims)
    ader = both.admitted_by_id("ADER_ENERGY")
    assert ader.admitted_kw == pytest.approx(763.0, abs=10.0)
    assert ader.reason is BindingReason.RESERVE
    assert both.admitted_by_id("ADER_AS").admitted_kw == pytest.approx(1500.0), (
        "the SENIOR hold must not be the one that yields while a junior award has kW to give")


def test_the_reserve_never_rewrites_a_claim_that_has_finished():
    """The prototype cut COOP_PEAK (15:30-18:30) at 20:15 on S2. That is a lie in the record."""
    from runner.run import RegionOutage, run_scenario

    r = run_scenario("headroom", faults=(RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),))
    coop = next(a for a in r.admissions if a.claim_id == "COOP_PEAK")
    assert coop.admitted_kw == pytest.approx(3000.0) and coop.reason is BindingReason.NONE


def test_deliverability_off_means_no_stage_2_at_all():
    """The flat de-rate baselines and the -N-1 ablation depend on this."""
    from dataclasses import replace

    from control.ledger import Ledger
    from scenarios.s1 import build_s1

    cfg = replace(CFG, haircut="independent", deliverability="off")
    s1 = build_s1(cfg=cfg)
    a = Ledger(s1.belief, now=s1.t0, cfg=cfg, horizon_ts=s1.horizon_ts)
    a.admit_all(s1.claims)
    b = Ledger(s1.belief, now=s1.t0, cfg=cfg, horizon_ts=s1.horizon_ts)
    b.admit_priority(s1.claims)
    assert [x.admitted_kw for x in a.admitted] == [x.admitted_kw for x in b.admitted]


def test_the_oracle_never_runs_the_n1_stage_through_the_runner():
    """The guard above tests the config path; the oracle never takes it. This goes through
    `run_scenario("oracle")`, which is where the stage actually leaked on 2026-09-24 and moved
    the ceiling every held-back number is measured against."""
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    for seed in (3, 259):
        r = run_scenario("oracle", seed=seed, faults=draw_faults(seed))
        assert not any(a.reason is BindingReason.RESERVE for a in r.admissions), (
            f"seed {seed}: the oracle's admission was bound by the N-1 reserve it does not hold")


def test_n0_drops_no_region_and_n1_drops_each():
    """The oracle's check is the whole reachable fleet; headroom's is the fleet minus any region."""
    small = [CFG.floor_frac * 39.2 + 0.7] * 28
    ests = _fleet(small) + _fleet([70.0] * 12, e_max=78.4, p_max=23.0)
    fleet = FleetArrays(scope_belief(ests, {"F1": 1e9}, "aggregation:A1", CFG))
    held = [_energy(90.0), _hold(150.0)]
    n0 = worst_shortfall(fleet, held, now=ct(19, 55), cfg=CFG, drop_regions=False)
    n1 = worst_shortfall(fleet, held, now=ct(19, 55), cfg=CFG, drop_regions=True)
    assert n0.region_id == "none" and n1.region_id in fleet.regions
    assert n0.margin_kw > n1.margin_kw, "losing a region must never leave MORE deliverable"


def test_every_no_reserve_variant_says_off_explicitly():
    """Inferring stage 2 from `haircut` is what leaked it into the oracle. Say it, per variant."""
    from runner.run import VARIANTS

    for name, knobs in VARIANTS.items():
        if knobs.get("haircut") == "independent":
            assert knobs.get("deliverability") == "off", f"{name} leaves stage 2 to inference"


def test_the_oracle_is_a_ceiling_again_on_the_evening_that_broke_it():
    """One of the 87 evenings where headroom booked past the old ceiling (oracle scale 0.84-0.86).
    With the N-0 check the oracle's full-information bookings keep their promises unscaled."""
    from control.oracle import held_back_pct
    from runner.batch import measure_ceiling
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    # Pinned, not read from the current batch: after the fix there is no such evening left in
    # it to find, which is the point. Seed 19 was one of the 87 (oracle scale 0.836).
    seed = 19
    faults = draw_faults(seed)
    _, oracle_m, _ = measure_ceiling(seed, faults, CFG)
    head = run_scenario("headroom", seed=seed, faults=faults).metrics
    assert held_back_pct(head["committed_kwh"], oracle_m["committed_kwh"]) >= -0.5, seed


def test_an_energy_booking_needs_kw_with_energy_behind_it():
    """Seed 19, scaled down: the co-op's last bucket needed 3,000 kW, and the only devices with
    energy left were the large units - 96 x 23 kW = 2,208. Nameplate said plenty."""
    small = [CFG.floor_frac * 39.2 + 0.2] * 30                    # near-empty 39.2 kWh units
    ests = _fleet(small) + _fleet([60.0] * 6, e_max=78.4, p_max=23.0)
    fleet = FleetArrays(scope_belief(ests, {"F1": 1e9}, "aggregation:A1", CFG))
    backed = 6 * 23.0
    too_big = _energy(backed + 60.0, start=ct(18, 0), end=ct(18, 30))
    ok = _energy(backed - 20.0, start=ct(18, 0), end=ct(18, 30))
    assert sum(e.kw_cap for e in ests) > too_big.admitted_kw * 2, "nameplate must look ample"
    w = worst_shortfall(fleet, [too_big], now=ct(17, 55), cfg=CFG, drop_regions=False)
    assert w is not None and w.region_id == "energy" and w.margin_kw < 0, w
    w_ok = worst_shortfall(fleet, [ok], now=ct(17, 55), cfg=CFG, drop_regions=False)
    assert w_ok is None or w_ok.margin_kw >= 0, w_ok
