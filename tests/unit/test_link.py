"""sim/link.py - the per-device link chain. It has to be the chain ASSUMPTIONS.md s5 describes,
independent of tick length, and reproducible, or any result built on it describes nothing."""
from __future__ import annotations

import numpy as np
import pytest

from sim.link import DOWN, GOOD, LinkModel, LinkRates, _expm


def test_the_transition_matrix_is_a_proper_stochastic_matrix():
    p = LinkModel(1, seed=0)._p(60.0)
    assert (p >= 0).all() and p.sum(axis=1) == pytest.approx(np.ones(3))


def test_tick_length_does_not_change_the_chain():
    """One 60 s step and ten 6 s steps must be the same process."""
    m = LinkModel(1, seed=0)
    assert m._p(60.0) == pytest.approx(np.linalg.matrix_power(m._p(6.0), 10), abs=1e-9)


def test_expm_matches_a_known_answer():
    """exp of a 2-state chain with rates a, b has a closed form."""
    a, b, t = 0.3, 0.7, 2.0
    q = np.array([[-a, a], [b, -b]]) * t
    e = np.exp(-(a + b) * t)
    want = np.array([[b + a * e, a - a * e], [b - b * e, a + b * e]]) / (a + b)
    assert _expm(q) == pytest.approx(want, abs=1e-10)


def test_long_run_share_of_time_down_matches_the_rates():
    rates = LinkRates()
    pi = rates.stationary()
    m = LinkModel(4000, seed=1, rates=rates)
    for _ in range(600):                        # burn in ~10 h of 60 s ticks
        m.step(60.0)
    down = np.mean([np.mean(~m.step(60.0)) for _ in range(600)])
    assert down == pytest.approx(pi[DOWN], abs=0.004), (down, pi[DOWN])
    assert pi[DOWN] > 0.0, "the control: a chain that never goes down tests nothing"


def test_down_episodes_last_as_long_as_the_rate_says():
    """Mean DOWN -> GOOD dwell = 1 / 2 per hour = 30 min. These gaps are what the band is for."""
    m = LinkModel(3000, seed=2, start_state=DOWN)
    ticks = 0
    alive = np.ones(m.n, bool)
    lengths = np.zeros(m.n)
    while alive.any() and ticks < 1000:
        heard = m.step(60.0)
        ticks += 1
        newly = alive & heard
        lengths[newly] = ticks
        alive &= ~heard
    assert lengths.mean() == pytest.approx(30.0, rel=0.08)


def test_same_seed_same_links_and_different_seed_different_links():
    runs = []
    for seed in (5, 5, 6):
        m = LinkModel(200, seed=seed, start_state=GOOD)
        runs.append(np.array([m.step(60.0) for _ in range(300)]))
    assert (runs[0] == runs[1]).all()
    assert (runs[0] != runs[2]).any(), "the control: a seed that changes nothing is not a seed"


def test_region_bias_makes_a_region_fail_together():
    regions = np.repeat(np.arange(5), 80)
    corr = []
    for bias in (0.0, 1.0):
        m = LinkModel(400, seed=3, region_of=regions, region_bias=bias)
        down = np.array([~m.step(60.0) for _ in range(2000)])
        within = np.mean([np.corrcoef(down[:, i], down[:, i + 1])[0, 1]
                          for i in range(0, 400, 80) if down[:, i].std() > 0 and down[:, i + 1].std() > 0])
        corr.append(within)
    assert corr[1] > corr[0] + 0.3, corr
