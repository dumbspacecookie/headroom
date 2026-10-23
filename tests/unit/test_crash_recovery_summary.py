"""HEADROOM-1014-S4: crash_unobserved (metrics/score.py) and summarise_crash_recovery
(runner/batch.py) - unit level, no full scenario run required.

AC-1's scenario-level pin (`scenarios.s3.run_s3(crash_ts=config.ct(19, 30))`) lives in
tests/scenarios/test_s3_crash_recovery.py per the approved design; this file covers
`crash_unobserved` directly against synthetic frames, plus AC-3's `summarise_crash_recovery`.
"""
from __future__ import annotations

import math

from metrics.score import crash_unobserved
from runner.batch import summarise_crash_recovery


# ---------------------------------------------------------------- AC-1 (crash_unobserved, unit)
# REQ: HEADROOM-1014-S4/AC-1
def test_crash_unobserved_true_when_nothing_booked_up_to_r():
    """R is the ts of the first frame after crash end (1090.0): here that is 1120.0. Every
    frame with 1000.0 < ts <= 1120.0 has booked_kw <= 0, so the crash is unobserved even though
    a later frame (1180.0) is booked."""
    crash = {"crash_ts": 1000.0, "crash_duration_s": 90.0}
    frames = [
        {"ts": 1030.0, "booked_kw": 0.0, "delivered_truth_kw": 763.0},
        {"ts": 1120.0, "booked_kw": 0.0, "delivered_truth_kw": 763.0},
        {"ts": 1180.0, "booked_kw": 100.0, "delivered_truth_kw": 96.0},
    ]
    assert crash_unobserved(frames, crash) is True


# REQ: HEADROOM-1014-S4/AC-1
def test_crash_unobserved_false_when_something_is_booked_in_the_window():
    """Same frames, but the window-defining frame (1120.0, which is R) is now booked: somebody
    was waiting on this crash."""
    crash = {"crash_ts": 1000.0, "crash_duration_s": 90.0}
    frames = [
        {"ts": 1030.0, "booked_kw": 0.0, "delivered_truth_kw": 763.0},
        {"ts": 1120.0, "booked_kw": 50.0, "delivered_truth_kw": 0.0},
        {"ts": 1180.0, "booked_kw": 100.0, "delivered_truth_kw": 96.0},
    ]
    assert crash_unobserved(frames, crash) is False


# REQ: HEADROOM-1014-S4/AC-1
def test_crash_unobserved_false_for_crash_is_none():
    assert crash_unobserved([{"ts": 0.0, "booked_kw": 0.0, "delivered_truth_kw": 0.0}], None) is False


# REQ: HEADROOM-1014-S4/AC-1
def test_crash_unobserved_no_frame_after_crash_end_uses_every_frame_after_crash_ts():
    """No frame falls after crash end (1090.0): R does not exist, so the window is every frame
    with ts > crash_ts instead. All such frames are booked_kw <= 0 here -> unobserved."""
    crash = {"crash_ts": 1000.0, "crash_duration_s": 90.0}
    frames = [
        {"ts": 1020.0, "booked_kw": 0.0, "delivered_truth_kw": 0.0},
        {"ts": 1050.0, "booked_kw": 0.0, "delivered_truth_kw": 0.0},
    ]
    assert crash_unobserved(frames, crash) is True


# ---------------------------------------------------------------- AC-3
# REQ: HEADROOM-1014-S4/AC-3
def test_summarise_crash_recovery_pinned_example():
    """crash_recovery_s [30.0, None, 10.0, 50.0, None] with flags [0, 1, 0, 0, 1]: median over
    the 3 observed values {30.0, 10.0, 50.0} is 30.0, crashes_observed is 3, crashes_unobserved
    is 2."""
    metrics_list = [
        {"crash_recovery_s": 30.0, "crashes_unobserved": 0},
        {"crash_recovery_s": None, "crashes_unobserved": 1},
        {"crash_recovery_s": 10.0, "crashes_unobserved": 0},
        {"crash_recovery_s": 50.0, "crashes_unobserved": 0},
        {"crash_recovery_s": None, "crashes_unobserved": 1},
    ]
    result = summarise_crash_recovery(metrics_list)
    assert result == {
        "crash_recovery_s_median": 30.0,
        "crashes_observed": 3,
        "crashes_unobserved": 2,
    }


# REQ: HEADROOM-1014-S4/AC-3
def test_summarise_crash_recovery_all_none_does_not_raise_and_is_nan():
    metrics_list = [
        {"crash_recovery_s": None, "crashes_unobserved": 1},
        {"crash_recovery_s": None, "crashes_unobserved": 1},
        {"crash_recovery_s": None, "crashes_unobserved": 0},  # e.g. a no-crash run
    ]
    result = summarise_crash_recovery(metrics_list)
    v = result["crash_recovery_s_median"]
    assert v != v  # NaN
    assert result["crashes_observed"] == 0
    assert result["crashes_unobserved"] == 2


# REQ: HEADROOM-1014-S4/AC-3
def test_summarise_crash_recovery_exact_keys():
    metrics_list = [{"crash_recovery_s": 12.0, "crashes_unobserved": 0}]
    result = summarise_crash_recovery(metrics_list)
    assert set(result.keys()) == {"crash_recovery_s_median", "crashes_observed", "crashes_unobserved"}
    assert not math.isnan(result["crash_recovery_s_median"])
