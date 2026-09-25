"""C1-C7: planted bugs that MUST turn a metric non-zero. docs/GATE.md is the register.

A planted bug that scores 0 means the metric cannot see the damage. That fails the build - the
metric is decoration and every green run it was part of meant less than it appeared to.

Written on the same day as the ledger, deliberately. The 2026-09-16 self-audit found a D1
constant that was defined and never asserted (A3): a test can look like it checks something and
not, and the only thing that reliably catches that is breaking the code on purpose and confirming
the test screams.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from config import DEFAULTS
from contracts.types import BindingReason, Product
from control.ledger import Ledger
from scenarios.s1 import build_s1

CFG = DEFAULTS


def _admit(belief=None, claims=None, cfg=CFG, **kw):
    """Stage 1 only. These controls plant bugs in SPEC 6.3's arithmetic; on S1 the per-device
    N-1 stage binds first (763 kW) and would hide them behind a smaller number. C8 covers it."""
    s1 = build_s1(cfg=cfg)
    led = Ledger(belief or s1.belief, now=s1.t0, cfg=cfg, **kw)
    led.admit_priority(claims or s1.claims)
    return led


def _baseline_ader_kw() -> float:
    return _admit().admitted_by_id("ADER_ENERGY").admitted_kw


# ---------------------------------------------------------------- C6 (the one prep earned)
def test_C6_stopping_the_kwh_term_at_end_of_window_over_admits():
    """SPEC §6.3 v0.4 / PRACTICE-NOTES.md FINDING-3.

    Plant the natural misreading - evaluate slack only to end(W) - and COOP_PEAK stops seeing
    the ADER_AS reservation at any t inside its own window. It is then admitted at a level that
    eats the hold's kWh, and the ledger reports nothing wrong.
    """
    good = _admit(slack_upper="horizon")
    bad = _admit(slack_upper="window")

    coop_good = good.admitted_by_id("COOP_PEAK")
    coop_bad = bad.admitted_by_id("COOP_PEAK")

    # At S1's default sizing the co-op fits either way, so compare the HEADROOM each reading
    # leaves, not the admitted kW - the damage is invisible until the co-op is bigger.
    assert coop_bad.kwh_slack_at_decision > coop_good.kwh_slack_at_decision, (
        "C6 planted nothing: the window reading must see MORE slack than the horizon reading, "
        "because it never looks at the times where the CAPACITY reservation applies."
    )

    # Now make the damage real: a co-op big enough that the difference decides admission.
    cfg2 = replace(CFG, coop_shave_h=3.0)
    s1 = build_s1(cfg=cfg2)
    big = [replace(c, power_kw=4600.0) if c.claim_id == "COOP_PEAK" else c for c in s1.claims]

    good_big = Ledger(s1.belief, now=s1.t0, cfg=cfg2, slack_upper="horizon")
    good_big.admit_priority(big)
    bad_big = Ledger(s1.belief, now=s1.t0, cfg=cfg2, slack_upper="window")
    bad_big.admit_priority(big)

    assert bad_big.admitted_by_id("COOP_PEAK").admitted_kw > \
        good_big.admitted_by_id("COOP_PEAK").admitted_kw + 1.0, (
        "C6 scored 0: the planted misreading did not over-admit COOP_PEAK. Either the plant is "
        "not wired or the horizon bound is no longer doing anything."
    )


def test_C6_control_the_two_readings_agree_at_S1_default_sizing():
    """The control on the control. If the two readings differed even at the default sizing, C6
    would be detecting a difference that is always there rather than the bug."""
    assert _admit(slack_upper="horizon").admitted_by_id("ADER_ENERGY").admitted_kw == \
        pytest.approx(_admit(slack_upper="window").admitted_by_id("ADER_ENERGY").admitted_kw)


# ---------------------------------------------------------------- C1, C2, C4 (ledger-level)
def test_C1_dropping_the_kwh_over_time_term_lets_everything_in():
    """Drop `consumed_cum` - the ledger stops carrying commitments across time and becomes
    `reasonable`. ADER_ENERGY must go from PARTIAL to full, and D1's cut to zero."""
    led = _admit()
    base = led.admitted_by_id("ADER_ENERGY")
    assert base.reason is BindingReason.KWH_SLACK

    class Broken(Ledger):
        def consumed_cum(self, adm, t):                     # the plant
            return 0.0

    s1 = build_s1()
    broken = Broken(s1.belief, now=s1.t0)
    broken.admit_priority(s1.claims)
    bad = broken.admitted_by_id("ADER_ENERGY")
    assert bad.admitted_kw > base.admitted_kw + 100.0, (
        "C1 scored 0: dropping the across-time term changed nothing, so nothing in S1 is "
        "actually testing that commitments accumulate."
    )
    assert bad.is_full, "C1: without across-time accounting ADER_ENERGY should be admitted whole"


def test_C2_dropping_the_capacity_kw_hold_frees_room_it_should_not():
    """A CAPACITY hold reserves kW in EVERY bucket of its window, called or not."""
    led = _admit()
    ader = led.admitted_by_id("ADER_ENERGY")

    class Broken(Ledger):
        def hold_kw(self, adm, bucket_ts):
            if adm.product is Product.CAPACITY:
                return 0.0                                  # the plant
            return super().hold_kw(adm, bucket_ts)

    s1 = build_s1()
    broken = Broken(s1.belief, now=s1.t0)
    broken.admit_priority(s1.claims)
    assert broken.admitted_by_id("ADER_ENERGY").kw_room_at_decision > \
        ader.kw_room_at_decision + 100.0, (
        "C2 scored 0: the CAPACITY hold was not reserving kW, so I8 is unexercised."
    )


def test_C4_dropping_the_n_minus_1_reserve_frees_kW_and_kWh():
    """Both units. A reserve that only shows up in one is half a reserve."""
    s1 = build_s1()
    with_reserve = Ledger(s1.belief, now=s1.t0)
    with_reserve.admit_priority(s1.claims)

    no_reserve = replace(s1.belief, kw_reserve_kw=0.0, kwh_reserve_kwh=0.0)   # the plant
    without = Ledger(no_reserve, now=s1.t0)
    without.admit_priority(s1.claims)

    a, b = with_reserve.admitted_by_id("ADER_ENERGY"), without.admitted_by_id("ADER_ENERGY")
    assert b.kw_room_at_decision > a.kw_room_at_decision + 100.0, "C4 scored 0 in kW"
    assert b.admitted_kw > a.admitted_kw + 100.0, "C4 scored 0 in kWh"


def test_C5_dropping_the_drain_term_frees_energy_that_was_going_to_evaporate():
    s1 = build_s1()
    base = Ledger(s1.belief, now=s1.t0)
    base.admit_priority(s1.claims)

    class Broken(Ledger):
        def drain_hi(self, t):                              # the plant
            return 0.0

    broken = Broken(s1.belief, now=s1.t0)
    broken.admit_priority(s1.claims)
    delta = (broken.admitted_by_id("ADER_ENERGY").admitted_kw
             - base.admitted_by_id("ADER_ENERGY").admitted_kw)
    assert delta == pytest.approx(120.0, abs=5.0), (
        f"C5: dropping the drain freed {delta:,.1f} kWh; 400 devices x 50 W x 6.0 h = 120 kWh. "
        f"If this is 130, the drain is being charged over 6.5 h again (FINDING-9)."
    )


# ---------------------------------------------------------------- the register itself
DEFERRED = {
    # C7 landed 2026-09-16 in tests/chart_fixture/test_hero_chart.py, where the lens it plants
    # against lives. Kept named here so the register stays the single place C1-C7 are tracked.
}
IMPLEMENTED_ELSEWHERE = {
    "C3": "tests/controls/test_C3_per_device_cap.py::test_C3_planting_the_bug_over_commits_that_device",
    "C7": "tests/chart_fixture/test_hero_chart.py::test_C7_shifting_the_x_data_must_fail_this_lens",
}


def test_C8_skipping_the_per_device_n1_check_brings_back_the_silent_hold(monkeypatch):
    """Stage 2's control (2026-09-24, RATIONALE.md s6c).

    Plant the obvious regression - enforce_n1 does nothing - and a hold that stage 2 protects
    must go silent again. If it does not, the seed no longer exercises the stage and the control
    is dead: pick another.

    It was seed 259 until the one-tick fix (same day). Most of what stage 2 had been "preventing"
    was that off-by-one; after it, a search of 400 seeded evenings found ONE where switching
    stage 2 off still causes a silent miss - seed 60. That thinness is a finding, not a flaw in
    the control: RATIONALE.md s6c prices what stage 2 buys with `headroom_no_stage2`.

    And seed 60 was itself an artefact (FINDING-26): its outage started at 21:00, the closing
    tick of both ADER windows, which the old [start, end) fault test blacked out. With faults on
    (start, end] and starting 0-4 min into their slot, stage 2 has real work - the minutes before
    the next re-plan notices an outage - and switching it off misses silently on 55 of 1,000
    evenings. Seed 22 (R4 dark 20:51 for 10 min) is the strongest: 9 silent ticks without it.
    """
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    seed = 22
    faults = draw_faults(seed)
    good = run_scenario("headroom", seed=seed, faults=faults).metrics
    monkeypatch.setattr(Ledger, "enforce_n1", lambda self, order: None)
    bad = run_scenario("headroom", seed=seed, faults=faults).metrics
    assert good["silent_breach_buckets"] == 0, f"the stage no longer protects seed {seed}"
    assert bad["silent_breach_buckets"] > 0, (
        f"C8 planted nothing: with enforce_n1 disabled seed {seed} should miss silently")


def test_every_control_is_accounted_for():
    """The lens-count discipline, applied INSIDE the lens.

    A controls lens that passes while only 5 of 7 controls exist is the same failure as a gate
    that passes while missing 4 of its lenses (self-audit A1, 2026-09-16). Every control is
    either implemented here or explicitly deferred with a reason and a due date.
    """
    implemented = {name.split("_")[1] for name in globals()
                   if name.startswith("test_C") and "control" not in name}
    accounted = implemented | set(DEFERRED) | set(IMPLEMENTED_ELSEWHERE)
    expected = {f"C{i}" for i in range(1, 9)}
    missing = expected - accounted
    assert not missing, (
        f"controls unaccounted for: {sorted(missing)}. Implement them or add them to DEFERRED "
        f"with a reason - silence is how a control register rots."
    )
    assert not (set(DEFERRED) & implemented), "a control is both implemented and deferred"
    assert not (set(DEFERRED) & set(IMPLEMENTED_ELSEWHERE)), "a control is both deferred and done"
    # A pointer to a test that does not exist is worse than no pointer: it reads as coverage.
    for cid, where in IMPLEMENTED_ELSEWHERE.items():
        path, _, name = where.partition("::")
        src = Path(__file__).resolve().parents[2] / path
        assert src.exists(), f"{cid} points at {path}, which does not exist"
        assert name in src.read_text(encoding="utf-8"), f"{cid} points at {name}, which is not in {path}"
