"""I1 and friends under hypothesis. SPEC §11.

THE POINT OF THIS FILE: assert the GUARANTEE, not the formula.

It would be easy - and worthless - to write `admitted_kw == min(requested, kw_room, kwh_term)`.
That is the implementation restated, and it passes for any implementation including a wrong one.
What follows instead asserts the thing §6.3 exists to make true:

    at no moment in the evening does the sum of everything the ledger has promised exceed the
    energy the fleet is believed to have.

If the admission formula is subtly wrong, that fails. That is the difference between a property
test and an expensive tautology.
"""
from __future__ import annotations

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from config import DEFAULTS, ct
from contracts.types import BindingReason, Claim, ClaimSource, Hardness, Product
from control.estimator import ScopeBelief
from control.ledger import Ledger

CFG = DEFAULTS
SETTINGS = settings(max_examples=200, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow])

T0, HORIZON = ct(15, 0), ct(21, 30)


def belief(e0: float, kw: float, reserve_kw: float, drain_kw: float) -> ScopeBelief:
    return ScopeBelief(scope="aggregation:A1", E0_kwh=e0, KW_kw=kw,
                       kw_reserve_kw=reserve_kw, kwh_reserve_kwh=reserve_kw * CFG.outage_h,
                       drain_kw=drain_kw, estimates=())


@st.composite
def claims(draw, n_min=1, n_max=4):
    out = []
    for i in range(draw(st.integers(n_min, n_max))):
        start = draw(st.sampled_from([ct(15, 30), ct(17, 0), ct(19, 0), ct(20, 0)]))
        dur_h = draw(st.sampled_from([0.5, 1.0, 2.0, 3.0]))
        end = min(start + dur_h * 3600.0, HORIZON)
        assume(end > start)
        product = draw(st.sampled_from([Product.ENERGY, Product.CAPACITY]))
        out.append(Claim(
            claim_id=f"C{i}", source=ClaimSource.ADER_ENERGY, product=product,
            priority=draw(st.integers(1, 6)), hardness=Hardness.FIRM, scope="aggregation:A1",
            start_ts=start, end_ts=end,
            power_kw=draw(st.floats(10.0, 6000.0, allow_nan=False)),
            max_deploy_h=draw(st.sampled_from([0.5, 1.0, 2.0])) if product is Product.CAPACITY else 0.0,
            tolerance_frac=CFG.tolerance_frac, created_ts=T0,
        ))
    return out


beliefs = st.builds(
    belief,
    e0=st.floats(0.0, 30_000.0, allow_nan=False),
    kw=st.floats(0.0, 8_000.0, allow_nan=False),
    reserve_kw=st.floats(0.0, 2_000.0, allow_nan=False),
    drain_kw=st.floats(0.0, 100.0, allow_nan=False),
)


def _grid() -> list[float]:
    t, out = T0 + CFG.bucket_s, []
    while t <= HORIZON:
        out.append(t)
        t += CFG.bucket_s
    return out


# ---------------------------------------------------------------- I1, both units
@SETTINGS
@given(b=beliefs, cs=claims())
def test_I1_energy_the_ledger_promised_never_exceeds_the_energy_it_believes_it_has(b, cs):
    """The guarantee, not the formula.

    At every bucket: everything promised so far (ENERGY consumed to date + every CAPACITY
    reservation still open) must fit inside E0 minus what has drained minus the N-1 reserve.
    """
    led = Ledger(b, now=T0, cfg=CFG, horizon_ts=HORIZON)
    led.admit_all(cs)
    assume(any(a.admitted_kw > 0 for a in led.admitted))

    for t in _grid():
        promised = sum(led.consumed_cum(a, t) for a in led.admitted)
        promised += led.capacity_reservation(t)
        inside_a_window = any(a.start_ts <= t <= a.end_ts for a in led.admitted)
        available = b.E0_kwh - led.drain_hi(t) - (b.kwh_reserve_kwh if inside_a_window else 0.0)
        # A belief already below its own reserve makes `available` slightly negative; the
        # ledger correctly promises 0.0, and `0.0 <= -0.08` fails on a degenerate case this
        # assertion never meant to cover. Hypothesis found it on a LATER run than the one that
        # first went green - a property test passing yesterday is not proof, because it draws
        # different examples every time.
        assert promised <= max(0.0, available) + 1e-6, (
            f"at {t/3600:.2f}h the ledger has promised {promised:,.1f} kWh but believes it has "
            f"{available:,.1f} kWh. §6.3's admission did not hold the line it exists to hold."
        )


@SETTINGS
@given(b=beliefs, cs=claims())
def test_I1_kw_held_in_any_bucket_never_exceeds_kw_room(b, cs):
    led = Ledger(b, now=T0, cfg=CFG, horizon_ts=HORIZON)
    led.admit_all(cs)
    ceiling = b.KW_kw - b.kw_reserve_kw
    for t in _grid():
        held = sum(led.hold_kw(a, t) for a in led.admitted)
        assert held <= max(0.0, ceiling) + 1e-6, (
            f"at {t/3600:.2f}h holds total {held:,.1f} kW against a ceiling of {ceiling:,.1f} kW"
        )


# ---------------------------------------------------------------- admission hygiene
@SETTINGS
@given(b=beliefs, cs=claims())
def test_admitted_is_bounded_by_zero_and_the_request(b, cs):
    led = Ledger(b, now=T0, cfg=CFG, horizon_ts=HORIZON)
    for a in led.admit_all(cs):
        assert 0.0 <= a.admitted_kw <= a.requested_kw + 1e-9


@SETTINGS
@given(b=beliefs, cs=claims())
def test_binding_reason_agrees_with_what_happened(b, cs):
    """A PARTIAL with reason NONE, or a full admission blamed on a constraint, is a UI that lies.
    The binding reason is read aloud in the demo - it has to mean something."""
    led = Ledger(b, now=T0, cfg=CFG, horizon_ts=HORIZON)
    for a in led.admit_all(cs):
        if a.is_full:
            assert a.reason is BindingReason.NONE, f"{a.claim_id} got everything but blames {a.reason}"
        else:
            assert a.reason is not BindingReason.NONE, f"{a.claim_id} was cut but blames nothing"


@SETTINGS
@given(b=beliefs, cs=claims(n_min=2))
def test_priority_order_is_what_decides_who_gets_cut(b, cs):
    """A claim admitted FIRST never does worse than the same claim admitted later. If that ever
    fails, the ladder is decorative and 'priority is just who paid' becomes unanswerable."""
    ranked = sorted(cs, key=lambda c: (c.priority, c.created_ts))
    first = ranked[0]

    led_all = Ledger(b, now=T0, cfg=CFG, horizon_ts=HORIZON)
    led_all.admit_all(cs)
    got_in_order = led_all.admitted_by_id(first.claim_id).admitted_kw

    led_alone = Ledger(b, now=T0, cfg=CFG, horizon_ts=HORIZON)
    led_alone.admit(first)
    got_alone = led_alone.admitted_by_id(first.claim_id).admitted_kw

    assert got_in_order >= got_alone - 1e-6, (
        f"{first.claim_id} is top priority but got {got_in_order:,.1f} kW alongside others vs "
        f"{got_alone:,.1f} kW alone - something lower down took from it"
    )


@SETTINGS
@given(b=beliefs, cs=claims())
def test_a_ledger_with_nothing_to_give_admits_nothing(b, cs):
    """The zero case. A ledger that quietly admits against an empty fleet would make every
    'silent breach' metric in the build meaningless."""
    empty = belief(0.0, b.KW_kw, b.kw_reserve_kw, b.drain_kw)
    led = Ledger(empty, now=T0, cfg=CFG, horizon_ts=HORIZON)
    for a in led.admit_all(cs):
        assert a.admitted_kw == 0.0, f"{a.claim_id} admitted {a.admitted_kw} kW from an empty fleet"


# ---------------------------------------------------------------- the register
DEFERRED = {
    "I2": "truth floor never breached in GRID/SAFE_HOLD with the guard OFF. Needs the runner to "
          "drive sim/fleet.py over a scenario; sim/fleet.py exists, the runner does not.",
    "I3": "proxy feeder cap never exceeded. Needs the ALLOCATOR - the ledger admits in aggregate "
          "and the feeder clip happens per device at dispatch.",
    "I4": "every silent breach attributable. Needs metrics/score.py and a truth run.",
    "I5": "duplicate/reorder resolve to max (epoch, seq). Needs sim/network.py.",
    "I6": "same seed -> same events_sha256. Needs the event log. A partial determinism check on "
          "the fleet already runs in tests/determinism.",
    "I6b": "fork with no fault is continuous. Needs runner/fork.py.",
    "I8": "ENERGY never consumes CAPACITY earmarks. Needs the ALLOCATOR's per-device earmarks - "
          "the ledger half (a CAPACITY hold reserving kW in every bucket) is control C2.",
    "I9": "per-device setpoint <= its kWh cap over the remaining window. Needs the ALLOCATOR.",
}


def test_every_invariant_is_implemented_or_deferred_with_a_reason():
    """Self-audit A1's discipline, applied to this lens.

    A property lens that passes while 8 of 9 invariants quietly do not exist is a gate reporting
    coverage it does not have. Every I-number is either exercised here or named above with the
    specific thing it is waiting on.
    """
    implemented = {"I1"}
    expected = {"I1", "I2", "I3", "I4", "I5", "I6", "I6b", "I8", "I9"}
    missing = expected - (implemented | set(DEFERRED))
    assert not missing, f"invariants unaccounted for: {sorted(missing)}"
    assert not (implemented & set(DEFERRED)), "an invariant is both implemented and deferred"
    for name, reason in DEFERRED.items():
        assert len(reason) > 40, f"{name} deferred without a real reason - that is a TODO in disguise"
