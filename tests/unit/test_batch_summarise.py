"""Unit tests for runner.batch.summarise's capacity-availability median.

REQ: HEADROOM-1014-S1
"""
from __future__ import annotations

from runner.batch import _pct, summarise

SUBJECTS = ("reasonable", "headroom")

# Finite, otherwise-unremarkable per-subject series used across tests below. These never
# contain NaN so they can stand in for "every other value is finite".
_HELD_BACK = {"reasonable": [5.0, 6.0, 4.0, 3.0], "headroom": [10.0, 12.0, 8.0, 9.0]}
_SILENT = {"reasonable": [0, 0, 0, 0], "headroom": [0, 0, 0, 0]}
_FLOOR_S = {"reasonable": [0.0, 0.0, 0.0, 0.0], "headroom": [0.0, 0.0, 0.0, 0.0]}
_REASONABLE_CAP = [80.0, 90.0, 70.0, 60.0]  # finite throughout, per the clarifications


def _make_rows(headroom_cap: list[float], reasonable_cap: list[float] = _REASONABLE_CAP) -> list[dict]:
    """Build four rows (seeds 0..3, n_faults=0) for both subjects, varying only the
    capacity_availability_pct series so each test isolates that one value."""
    caps = {"reasonable": reasonable_cap, "headroom": headroom_cap}
    rows = []
    for i, seed in enumerate((0, 1, 2, 3)):
        row: dict = {"seed": seed, "n_faults": 0}
        for c in SUBJECTS:
            row[c] = {
                "held_back_pct": _HELD_BACK[c][i],
                "capacity_availability_pct": caps[c][i],
                "silent": _SILENT[c][i],
                "floor_s": _FLOOR_S[c][i],
            }
        rows.append(row)
    return rows


# REQ: HEADROOM-1014-S1/AC-1
def test_nan_capacity_row_does_not_corrupt_the_median():
    rows = _make_rows(headroom_cap=[100.0, float("nan"), 0.0, 50.0])
    result = summarise(rows)
    assert result["aggregate"]["headroom"]["capacity_availability_pct_median"] == 50.0


# REQ: HEADROOM-1014-S1/AC-2
def test_all_nan_capacity_returns_nan_without_raising():
    rows = _make_rows(headroom_cap=[float("nan")] * 4)
    result = summarise(rows)
    v = result["aggregate"]["headroom"]["capacity_availability_pct_median"]
    assert v != v  # NaN, same contract as held_back_pct_median when all inputs are NaN


# REQ: HEADROOM-1014-S1/AC-3
def test_all_finite_capacity_median_unchanged():
    headroom_cap = [40.0, 55.0, 95.0, 20.0]
    rows = _make_rows(headroom_cap=headroom_cap)
    result = summarise(rows)
    for c, cap_values in (("reasonable", _REASONABLE_CAP), ("headroom", headroom_cap)):
        expected = round(_pct(cap_values, 0.50), 2)
        assert result["aggregate"][c]["capacity_availability_pct_median"] == expected


# REQ: HEADROOM-1014-S1/AC-3
def test_other_aggregates_unaffected_by_nan_vs_finite_swap():
    rows_nan = _make_rows(headroom_cap=[100.0, float("nan"), 0.0, 50.0])
    rows_75 = _make_rows(headroom_cap=[100.0, 75.0, 0.0, 50.0])
    result_nan = summarise(rows_nan)
    result_75 = summarise(rows_75)
    for c in SUBJECTS:
        for key in ("held_back_pct_median", "held_back_pct_p90", "held_back_pct_mean"):
            assert result_nan["aggregate"][c][key] == result_75["aggregate"][c][key]
    assert result_nan["where_we_lose"] == result_75["where_we_lose"]
