"""Estimator unit tests. SPEC §6.2, §11.

Every test here is trying to break a promise the band makes, not to confirm it works.
"""
from __future__ import annotations

import pytest

from config import DEFAULTS
from contracts.types import DeviceMode, SafeHoldBehavior, Telemetry
from control.estimator import DeviceStatic, estimate_device, scope_belief

CFG = DEFAULTS


def _static(dev_id="D1", region="R1", feeder="F1", e_max=39.2, p_max=11.5,
            shb=SafeHoldBehavior.IDLE) -> DeviceStatic:
    return DeviceStatic(device_id=dev_id, region_id=region, feeder_id=feeder,
                        aggregation_id="A1", territory="T1", e_max_kwh=e_max, p_max_kw=p_max,
                        floor_kwh=CFG.floor_frac * e_max, aux_kw=CFG.aux_kw,
                        safe_hold_behavior=shb)


def _tel(now, soc, p_kw=0.0, mode=DeviceMode.GRID, dev_id="D1") -> Telemetry:
    return Telemetry(device_id=dev_id, seq=1, device_ts=now, soc_kwh=soc, p_kw=p_kw,
                     home_load_kw=0.0, mode=mode, last_applied_epoch=0, last_applied_seq=0,
                     recv_ts=now)


def test_band_is_monotonic_in_staleness():
    """Older data must never allow a LARGER promise. This is the band's whole job."""
    s, t0 = _static(), 1000.0
    tel = _tel(t0, soc=30.0)
    prev = None
    for age in (0.0, 5.0, 60.0, 600.0, 3600.0):
        est = estimate_device(s, tel, now=t0 + age, last_setpoint_kw=0.0,
                              lease_expiry_ts=t0 + 1e9, cfg=CFG)
        if prev is not None:
            assert est.soc_low_kwh <= prev + 1e-9, f"soc_low rose with age at {age}s"
        prev = est.soc_low_kwh


def test_promises_come_off_the_pessimistic_edge_only():
    # NB: reachable means age <= reach_k * t_tel = 5 * 2 s = 10 s. With 2-second telemetry,
    # ten seconds of silence really is a long time. Use a fresh stamp or this device is dark.
    s, t0 = _static(), 1000.0
    est = estimate_device(s, _tel(t0, soc=30.0), now=t0 + 5, last_setpoint_kw=5.0,
                          lease_expiry_ts=t0 + 1e9, cfg=CFG)
    assert est.reachable
    assert est.soc_low_kwh < est.soc_high_kwh
    assert est.kwh_above_floor_low == pytest.approx(max(0.0, est.soc_low_kwh - s.floor_kwh))


def test_unreachable_device_contributes_zero_kw_AND_zero_kwh():
    """Both units, or the reserve gets subtracted twice against a device already at zero."""
    s, t0 = _static(), 1000.0
    stale = CFG.reach_k * CFG.t_tel_s + 1.0
    est = estimate_device(s, _tel(t0, soc=35.0), now=t0 + stale, last_setpoint_kw=0.0,
                          lease_expiry_ts=t0 + 1e9, cfg=CFG)
    assert not est.reachable
    assert est.kw_cap == 0.0
    assert est.kwh_above_floor_low == 0.0


def test_islanded_is_never_dispatchable_however_full_it_is():
    s, t0 = _static(), 1000.0
    est = estimate_device(s, _tel(t0, soc=39.0, mode=DeviceMode.ISLANDED), now=t0,
                          last_setpoint_kw=0.0, lease_expiry_ts=t0 + 1e9, cfg=CFG)
    assert not est.reachable and est.kw_cap == 0.0


def test_load_term_is_off_for_a_comms_dark_grid_device_by_default():
    """The 'your Notice came from invented house load' attack (SPEC §14) aims here."""
    s, t0 = _static(), 1000.0
    est = estimate_device(s, _tel(t0, soc=30.0), now=t0 + 600, last_setpoint_kw=0.0,
                          lease_expiry_ts=t0 + 1e9, cfg=CFG)
    assert est.load_term_on is False
    aux_only = 30.0 - CFG.aux_kw * 600 / 3600 - CFG.eps_frac * s.e_max_kwh
    assert est.soc_low_kwh == pytest.approx(aux_only, abs=1e-6)


def test_self_consume_widens_the_band_after_lease_expiry():
    s, t0 = _static(shb=SafeHoldBehavior.SELF_CONSUME), 1000.0
    idle = estimate_device(_static(), _tel(t0, soc=30.0), now=t0 + 600, last_setpoint_kw=0.0,
                           lease_expiry_ts=t0 + 60, cfg=CFG)
    sc = estimate_device(s, _tel(t0, soc=30.0), now=t0 + 600, last_setpoint_kw=0.0,
                         lease_expiry_ts=t0 + 60, cfg=CFG)
    assert sc.load_term_on and not idle.load_term_on
    assert sc.soc_low_kwh < idle.soc_low_kwh


def test_feeder_cap_clips_the_scope_kw():
    t0 = 1000.0
    ests = [
        estimate_device(_static(f"D{i}", feeder="F1"), _tel(t0, 35.0, dev_id=f"D{i}"),
                        now=t0, last_setpoint_kw=0.0, lease_expiry_ts=t0 + 1e9, cfg=CFG)
        for i in range(10)
    ]
    uncapped = scope_belief(ests, {"F1": 1e9}, "aggregation:A1", CFG)
    capped = scope_belief(ests, {"F1": 50.0}, "aggregation:A1", CFG)
    assert uncapped.KW_kw == pytest.approx(10 * 11.5)
    assert capped.KW_kw == pytest.approx(50.0)


def test_n_minus_1_reserve_covers_the_largest_REACHABLE_region_only():
    """A dark region already contributes 0. It must not also be the thing we reserve against -
    that would subtract the same loss twice and hold back capacity for nothing."""
    t0 = 1000.0
    stale = CFG.reach_k * CFG.t_tel_s + 1.0
    fresh = [estimate_device(_static(f"A{i}", region="R1", feeder="F1"),
                             _tel(t0, 35.0, dev_id=f"A{i}"), now=t0, last_setpoint_kw=0.0,
                             lease_expiry_ts=t0 + 1e9, cfg=CFG) for i in range(4)]
    # R2 is bigger, but it is dark
    dark = [estimate_device(_static(f"B{i}", region="R2", feeder="F2"),
                            _tel(t0, 35.0, dev_id=f"B{i}"), now=t0 + stale, last_setpoint_kw=0.0,
                            lease_expiry_ts=t0 + 1e9, cfg=CFG) for i in range(9)]
    b = scope_belief(fresh + dark, {"F1": 1e9, "F2": 1e9}, "aggregation:A1", CFG)
    assert b.KW_kw == pytest.approx(4 * 11.5), "dark devices leaked into scope kW"
    assert b.kw_reserve_kw == pytest.approx(4 * 11.5), "reserve was taken against the DARK region"


def test_haircut_independent_holds_no_reserve():
    from dataclasses import replace
    t0 = 1000.0
    ests = [estimate_device(_static(f"D{i}"), _tel(t0, 35.0, dev_id=f"D{i}"), now=t0,
                            last_setpoint_kw=0.0, lease_expiry_ts=t0 + 1e9, cfg=CFG)
            for i in range(4)]
    cfg2 = replace(CFG, haircut="independent")
    b = scope_belief(ests, {"F1": 1e9}, "aggregation:A1", cfg2)
    assert b.kw_reserve_kw == 0.0 and b.kwh_reserve_kwh == 0.0
