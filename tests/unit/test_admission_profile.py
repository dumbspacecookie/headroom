"""The per-bucket profile, on its own. SPEC 6.3; PRACTICE-NOTES.md FINDING-14/17.

`Admission` stopped being a number on 2026-09-17 and became a step function over buckets. Three
things about it are easy to get wrong and expensive to find later, so they are pinned here.
"""
from __future__ import annotations

import pytest

from config import Config, ct
from contracts.types import BindingReason, Product
from control.admission import Admission, bucket_ends
from control.ledger import Ledger
from scenarios.s1 import build_s1


def _adm(start, end, kw, bucket_s=300.0, locked=None) -> Admission:
    return Admission(claim_id="X", product=Product.ENERGY, requested_kw=kw, admitted_kw=kw,
                     reason=BindingReason.NONE, start_ts=start, end_ts=end,
                     bucket_s=bucket_s, locked_kw=dict(locked or {}))


# ---------------------------------------------------------------- the grid travels with the cfg
def test_a_swept_bucket_s_reaches_the_admission():
    """`Admission.bucket_s` has a default, and a default is a second place a value can live.

    If the ledger ever stops passing it, every profile silently re-grids to 5 minutes while
    `cfg.bucket_s` says something else - and the sweep would report a result for a bucket size
    it never actually ran. The default exists for ergonomics; this is what keeps it honest.
    """
    cfg = Config(bucket_s=600.0)
    s1 = build_s1(cfg=cfg)
    led = Ledger(s1.belief, now=s1.t0, cfg=cfg)
    for a in led.admit_all(s1.claims):
        assert a.bucket_s == 600.0, f"{a.claim_id} was built on a {a.bucket_s}s grid, cfg says 600"


# ---------------------------------------------------------------- the half-open convention
def test_adjacent_windows_do_not_both_claim_their_shared_instant():
    """FINDING-17's shape, now living inside `kw_at`.

    Two windows meeting at 19:00: the instant belongs to the one that is ENDING. Every window
    test in this build answers this the same way, and this is the assertion that keeps the
    newest one in line with its neighbours.
    """
    a, b = _adm(ct(17, 0), ct(19, 0), 10.0), _adm(ct(19, 0), ct(19, 30), 10.0)
    assert a.kw_at(ct(19, 0)) == 10.0, "the ending window gave up its own last instant"
    assert b.kw_at(ct(19, 0)) == 0.0, "the starting window claimed an instant it does not own"
    assert a.kw_at(ct(17, 0)) == 0.0 and b.kw_at(ct(19, 30)) == 10.0


def test_kw_at_resolves_any_instant_to_its_bucket_not_just_bucket_ends():
    """The runner asks at 60 s ticks; the ledger asks at bucket ends. One answer for both."""
    a = _adm(ct(20, 0), ct(21, 0), 900.0, locked={ct(20, 20): 900.0, ct(20, 25): 124.0})
    assert a.kw_at(ct(20, 16)) == 900.0 and a.kw_at(ct(20, 20)) == 900.0
    assert a.kw_at(ct(20, 21)) == 124.0 and a.kw_at(ct(20, 25)) == 124.0
    assert a.kw_at(ct(20, 26)) == 900.0, "an unlocked bucket must read the live decision"


# ---------------------------------------------------------------- the integrator
def test_a_flat_profile_integrates_to_kw_times_hours():
    a = _adm(ct(20, 0), ct(21, 0), 900.0)
    assert a.kwh_between(ct(20, 0), ct(21, 0)) == pytest.approx(900.0)
    assert a.kwh_between(ct(20, 30), ct(21, 0)) == pytest.approx(450.0)
    assert a.kwh_between(ct(19, 0), ct(22, 0)) == pytest.approx(900.0), "clipping to the window"


def test_a_split_profile_integrates_to_the_sum_of_its_pieces():
    """The number the chart reads as 'still owed' after a cut."""
    locked = {ct(20, 5): 900.0, ct(20, 10): 900.0}
    a = _adm(ct(20, 0), ct(21, 0), 120.0, locked=locked)
    expect = 900.0 * (10 / 60) + 120.0 * (50 / 60)
    assert a.kwh_between(ct(20, 0), ct(21, 0)) == pytest.approx(expect)


def test_a_window_that_is_not_a_whole_number_of_buckets_keeps_its_last_minutes():
    """A 12-minute window on a 5-minute grid is 5 + 5 + 2, not 5 + 5.

    `bucket_ends` rounds, so the leftover 2 minutes have no bucket of their own. Dropping them
    would quietly under-count energy on any claim whose window is not bucket-aligned - and
    nothing in S1 is misaligned, which is exactly why it would not be noticed.
    """
    a = _adm(0.0, 720.0, 60.0)
    assert bucket_ends(0.0, 720.0, 300.0) == [300.0, 600.0], "the grid itself changed shape"
    assert a.kwh_between(0.0, 720.0) == pytest.approx(60.0 * 720.0 / 3600.0), (
        "the short final bucket was dropped from the integral")
