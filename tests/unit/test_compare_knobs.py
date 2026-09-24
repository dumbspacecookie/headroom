"""The comparison knobs behind `runner/compare.py`. RATIONALE.md s8.

`reasonable_plus` and the ablations are built from headroom's own ledger with parts switched
off. If a switch silently did nothing, the comparison would report headroom against itself and
call it a baseline - so each test here checks the switch changes what it claims to, and that the
defaults still ARE headroom.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from config import DEFAULTS
from control.estimator import estimate_device, scope_belief
from runner.run import CONTROLLERS, VARIANTS, run_scenario
from tests.unit.test_estimator import _static, _tel


def test_defaults_are_headroom():
    assert DEFAULTS.band is True and DEFAULTS.flat_derate_frac == 0.0


def test_band_off_promises_the_reported_reading_and_band_on_does_not():
    s, t0 = _static(), 1000.0
    tel = _tel(t0, soc=30.0, p_kw=5.0)
    args = dict(now=t0 + 8.0, last_setpoint_kw=5.0, lease_expiry_ts=t0 + 60.0)
    point = estimate_device(s, tel, cfg=replace(DEFAULTS, band=False), **args)
    band = estimate_device(s, tel, cfg=DEFAULTS, **args)
    assert point.soc_low_kwh == 30.0
    assert band.soc_low_kwh < point.soc_low_kwh, "band=True no longer widens anything"


def test_band_off_still_drops_a_device_after_the_timeout():
    """Standard practice (Tesla, publicly) excludes a site it has not heard from. Keep that."""
    s, t0 = _static(), 1000.0
    est = estimate_device(s, _tel(t0, soc=30.0), now=t0 + 3600.0, last_setpoint_kw=0.0,
                          lease_expiry_ts=t0 + 60.0, cfg=replace(DEFAULTS, band=False))
    assert not est.reachable and est.kwh_above_floor_low == 0.0


def test_flat_derate_holds_that_share_of_both_units():
    t0 = 1000.0
    cfg = replace(DEFAULTS, haircut="independent", flat_derate_frac=0.10)
    ests = [estimate_device(_static(f"D{i}"), _tel(t0, 35.0, dev_id=f"D{i}"), now=t0,
                            last_setpoint_kw=0.0, lease_expiry_ts=t0 + 1e9, cfg=cfg)
            for i in range(4)]
    b = scope_belief(ests, {"F1": 1e9}, "aggregation:A1", cfg)
    assert b.kw_reserve_kw == pytest.approx(0.10 * b.KW_kw)
    assert b.kwh_reserve_kwh == pytest.approx(0.10 * b.E0_kwh)


def test_every_variant_changes_the_outcome_and_none_aliases_a_controller():
    """A variant that commits exactly what headroom commits is headroom under another name."""
    assert not set(VARIANTS) & set(CONTROLLERS)
    base = run_scenario("headroom", seed=42).metrics["committed_kwh"]
    got = {}
    for name in VARIANTS:
        m = run_scenario(name, seed=42).metrics
        assert m["controller"] == name
        got[name] = m["committed_kwh"]
        if name != "headroom_eps_only":
            assert got[name] != base, f"{name} committed exactly what headroom did"
    # headroom_eps_only IS allowed to equal headroom, and today it does on 1,000 of 1,000
    # evenings: the runner's 60 s tick and 10 s reachability timeout make staleness binary, so
    # the band's age-widening never engages (RATIONALE.md s6a point 3). Its switch must still be
    # live - it has to differ from having no margin at all.
    assert got["headroom_eps_only"] != got["headroom_no_band"], "point_eps switch does nothing"


def test_age_widening_is_still_unexercised_by_the_runner():
    """Pins RATIONALE.md s6a point 3. When a stale-telemetry model lands, this SHOULD fail:
    that is the signal to re-run `python run.py compare` and rewrite the claim about the band."""
    from sim.chaos import draw_faults
    for seed in (42, 52, 60):
        f = draw_faults(seed)
        a = run_scenario("headroom", seed=seed, faults=f).metrics["committed_kwh"]
        b = run_scenario("headroom_eps_only", seed=seed, faults=f).metrics["committed_kwh"]
        assert a == b, (f"seed {seed}: the band's age-widening now changes admission "
                        f"({a:,.1f} vs {b:,.1f} kWh). Re-run the comparison; RATIONALE s6a is stale.")
