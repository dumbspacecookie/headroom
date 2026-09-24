"""Per-device link model: the flaky backhaul SPEC 6.6 specified and the runner never had.

Why this exists
---------------
The runner's comms are perfect except for scripted region outages, and it ticks every 60 s while
a device counts as unreachable after 10 s (`reach_k` 5 x `t_tel` 2 s). So a device the controller
can see is always 0 s old, and the SoC band's "trust old data less" half has never been
exercised: RATIONALE.md s6a found `headroom_eps_only` identical to headroom on 1,000 of 1,000
evenings.

Small latencies cannot fix that - at a 60 s tick a 3 s delay is invisible. What the band is for is
the case in between: a device that has gone quiet for minutes. A controller can drop it after a
timeout (the yes/no rule Tesla describes publicly) or keep counting it at a discount that grows
with its silence. This model produces those minutes-long gaps, so the two policies can be
compared; `runner/run.py` does not use it yet.

The model
---------
Each device's link is a three-state Markov chain, GOOD -> DEGRADED -> DOWN, with the rates in
ASSUMPTIONS.md s5 (per hour): G->D 0.5, D->G 6, D->DOWN 0.3, DOWN->G 2. Stepped per tick with
exact transition probabilities for the tick length (matrix exponential of the rate matrix), so a
60 s tick and ten 6 s ticks describe the same chain.

A device is HEARD in a tick unless it is DOWN. DEGRADED loses 15% of messages, but with telemetry
every 2 s a 60 s tick still carries ~30 of them, so at this resolution DEGRADED only matters as the
road to DOWN. Store-and-forward means a device coming back is heard at once with its latest
reading (the estimator keeps the max-seq message), so a gap ends cleanly.

`region_bias` optionally makes links in the same region fail together, for the case between
"independent" and "a whole region goes dark". 0 is independent.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

GOOD, DEGRADED, DOWN = 0, 1, 2


@dataclass(frozen=True)
class LinkRates:
    """Transition rates per hour. Defaults are ASSUMPTIONS.md s5."""
    good_to_degraded: float = 0.5
    degraded_to_good: float = 6.0
    degraded_to_down: float = 0.3
    down_to_good: float = 2.0

    def generator(self) -> np.ndarray:
        q = np.zeros((3, 3))
        q[GOOD, DEGRADED] = self.good_to_degraded
        q[DEGRADED, GOOD] = self.degraded_to_good
        q[DEGRADED, DOWN] = self.degraded_to_down
        q[DOWN, GOOD] = self.down_to_good
        np.fill_diagonal(q, -q.sum(axis=1))
        return q / 3600.0                              # per second

    def stationary(self) -> np.ndarray:
        """Long-run share of time in each state: pi Q = 0, sum(pi) = 1."""
        q = self.generator()
        a = np.vstack([q.T, np.ones(3)])
        b = np.array([0.0, 0.0, 0.0, 1.0])
        return np.linalg.lstsq(a, b, rcond=None)[0]


def _expm(m: np.ndarray, terms: int = 30) -> np.ndarray:
    """exp(m) for a small matrix by scaling and squaring - no scipy dependency for a 3x3."""
    k = max(0, int(np.ceil(np.log2(max(np.abs(m).sum(axis=1).max(), 1e-12)))) + 1)
    a = m / (2 ** k)
    out, term = np.eye(m.shape[0]), np.eye(m.shape[0])
    for i in range(1, terms):
        term = term @ a / i
        out = out + term
    for _ in range(k):
        out = out @ out
    return out


class LinkModel:
    """Vectorised links for n devices. Deterministic for a given seed."""

    def __init__(self, n: int, seed: int, rates: LinkRates = LinkRates(),
                 region_of: np.ndarray | None = None, region_bias: float = 0.0,
                 start_state: int = GOOD) -> None:
        self.n = n
        self.rates = rates
        self.state = np.full(n, start_state, dtype=np.int8)
        # A stream of its own, so adding links never shifts the chaos draw (sim/chaos.py).
        self.rng = np.random.default_rng([seed, 7])
        self.region_of = region_of
        self.region_bias = float(region_bias)
        self._p_cache: dict[float, np.ndarray] = {}

    def _p(self, dt_s: float) -> np.ndarray:
        if dt_s not in self._p_cache:
            p = _expm(self.rates.generator() * dt_s)
            self._p_cache[dt_s] = np.clip(p / p.sum(axis=1, keepdims=True), 0.0, 1.0)
        return self._p_cache[dt_s]

    def step(self, dt_s: float) -> np.ndarray:
        """Advance every link by dt_s. Returns a bool array: was the device heard this tick?"""
        p = self._p(dt_s)
        u = self.rng.random(self.n)
        if self.region_of is not None and self.region_bias > 0.0:
            # One shared draw per region, blended in: bias 1 means a region's links move together.
            regions = np.unique(self.region_of)
            shared = dict(zip(regions, self.rng.random(regions.size)))
            u = (1.0 - self.region_bias) * u + self.region_bias * np.array(
                [shared[r] for r in self.region_of])
        cum = np.cumsum(p[self.state], axis=1)
        self.state = (u[:, None] > cum[:, :-1]).sum(axis=1).astype(np.int8)
        return self.state != DOWN
