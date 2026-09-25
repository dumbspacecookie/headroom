"""The P90 reserve (RATIONALE.md s6d): the switch is live, and it is N-1 when the odds say so.

The comparison is only worth publishing if "p90" differs from headroom in exactly one thing - how
many regions the reserve covers - so the two ends of the table are pinned to rules we already
trust: k = 1 everywhere must BE headroom, event for event, and k = 0 everywhere must be the
same ledger with no reserve and the per-device check kept.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from config import DEFAULTS
from control.estimator import ScopeBelief
from control.reserve import regions_to_hold
from runner.p90 import n_buckets, odds, table
from runner.run import run_scenario
from sim.chaos import draw_faults, draw_faults_rate

SEEDS = (60, 76, 259)          # a region down mid-delivery; two dark at once; the seed-259 hold


def _flat(k: int) -> tuple:
    nb = n_buckets()
    return tuple(tuple(tuple(k for _ in range(nb)) for _ in range(nb)) for _ in range(6))


@pytest.mark.parametrize("seed", SEEDS)
def test_holding_one_region_everywhere_is_headroom(seed):
    f = draw_faults(seed)
    cfg = replace(DEFAULTS, reserve_rule="p90", p90_table=_flat(1))
    a = run_scenario("headroom", seed=seed, faults=f)
    b = run_scenario("headroom", seed=seed, faults=f, cfg=cfg)
    assert a.events_sha256 == b.events_sha256


@pytest.mark.parametrize("seed", SEEDS)
def test_holding_nothing_everywhere_is_the_no_reserve_ledger_with_the_device_check(seed):
    f = draw_faults(seed)
    p90 = replace(DEFAULTS, reserve_rule="p90", p90_table=_flat(0))
    n0 = replace(DEFAULTS, haircut="independent", deliverability="n0")
    a = run_scenario("headroom", seed=seed, faults=f, cfg=n0)
    b = run_scenario("headroom", seed=seed, faults=f, cfg=p90)
    assert a.events_sha256 == b.events_sha256


def test_the_calibrated_variants_change_what_is_admitted():
    """A flag without behaviour would publish headroom four times under four names."""
    seeds = (1, 2, 3, 60)
    base = [run_scenario("headroom", seed=s, faults=draw_faults(s)).metrics["committed_kwh"]
            for s in seeds]
    for name in ("headroom_p90_r050", "headroom_p90_r100"):
        got = [run_scenario(name, seed=s, faults=draw_faults(s)).metrics["committed_kwh"]
               for s in seeds]
        assert got != base, f"{name} admitted exactly what headroom did on {seeds}"


def test_the_table_is_the_smallest_k_whose_tail_is_within_alpha():
    # Re-derived from the raw counts, not from the code that built the table.
    count, exceed = odds(1.0)
    t = np.array(table(1.0, 0.10))
    checked = 0
    for m in range(count.shape[0]):
        for i in range(0, count.shape[1], 7):
            c = count[m, i]
            if c < 30:
                assert (t[m, i] == 1).all()
                continue
            for j in range(i, count.shape[1]):
                p = exceed[m, i, j] / c
                k = next((k for k in range(p.size) if p[k] <= 0.10), p.size)
                assert t[m, i, j] == k
                checked += 1
            assert (t[m, i, :i] == 0).all()
    assert checked > 1000


def test_the_hand_derived_ends_of_the_sweep():
    # From 5,000 evenings drawn by hand before this was built: at 0.5x no bucket ever has a
    # 10% chance of a new outage, so P90 holds nothing; at 1x the delivery windows do.
    nb = n_buckets()
    at_1600, at_2000 = 12, 60
    assert max(table(0.5, 0.10)[0][at_1600]) == 0
    assert table(1.0, 0.10)[0][at_1600][at_2000] == 1
    assert max(table(1.0, 0.10)[0][0][:12]) == 0      # planned at 15:00, the lead-in hour is safe
    assert max(table(1.0, 0.10)[0][0][12:]) == 1      # ... and the evening after it is not
    assert len(table(1.0, 0.10)[0]) == nb


def test_rate_worlds_keep_the_published_draw_at_one_and_scale_around_it():
    for s in range(50):
        base = draw_faults(s)
        assert draw_faults_rate(s, 1.0) == base
        assert set(draw_faults_rate(s, 0.5)) <= set(base)
        assert set(base) <= set(draw_faults_rate(s, 2.0))
    n = [sum(len(draw_faults_rate(s, r)) for s in range(2000)) for r in (0.5, 1.0, 2.0)]
    assert 0.4 < n[0] / n[1] < 0.6 and 1.8 < n[2] / n[1] < 2.2


def test_the_rule_is_explicit():
    b = ScopeBelief("aggregation:A1", 0.0, 0.0, 0.0, 0.0, 0.0, ())
    assert regions_to_hold(DEFAULTS, DEFAULTS.t0, b) is None
    with pytest.raises(ValueError):
        regions_to_hold(replace(DEFAULTS, reserve_rule="p90"), DEFAULTS.t0, b)
    with pytest.raises(ValueError):
        regions_to_hold(replace(DEFAULTS, reserve_rule="p95"), DEFAULTS.t0, b)
