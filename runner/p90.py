"""The outage odds a P90 reserve admits against. RATIONALE.md s5 (A1, A4) and s6d.

A chance-constrained reserve needs a distribution, and `control/` may not read `sim/` (boundary
lens). So the runner builds the distribution from the fault model and hands the controller a
table, the way an operator would hand it outage statistics from history. The table is the
controller's BELIEF about how often regions go dark; the world it runs in may disagree, and the
miscalibration runs are exactly that disagreement.

    table[m][i][j] = regions to hold at bucket j, planning at bucket i, with m regions dark now

It is the smallest k with P(more than k of the regions lit at i are dark somewhere in bucket j
| m dark at i) <= alpha. "Dark somewhere in bucket j", not at its end: a bucket is short if the
region drops at any point inside it.

What it is calibrated on, stated because a calibrated P90 is the BEST case for P90:
- 20,000 evenings from seeds 1,000,000 up, disjoint from the 0..999 every result is scored on.
- The fault model's time profile, including its artefacts: no outage in the first hour or the
  last 15 minutes (sim/chaos.py LEAD_IN_S, TAIL_S). A calibrated P90 knows those hours are safe
  and holds nothing there; a real one would not know that.
- Conditioning only on how many regions are dark now, not on the evening's history.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from config import DEFAULTS, Config
from sim.chaos import A1_REGIONS, draw_faults_rate

CALIB_SEED0 = 1_000_000
CALIB_N = 20_000
MIN_SAMPLES = 30        # fewer than this at (m, i) and the table holds one region, as N-1 does


def n_buckets(cfg: Config = DEFAULTS) -> int:
    return int(round((cfg.horizon_ts - cfg.t0) / cfg.bucket_s))


def _dark_matrices(faults, cfg: Config, nb: int) -> tuple[np.ndarray, np.ndarray]:
    """(dark at the instant starting bucket i, dark anywhere in bucket j), both (nb, regions)."""
    t_now = cfg.t0 + cfg.bucket_s * np.arange(nb)
    b_end = t_now + cfg.bucket_s
    now_d = np.zeros((nb, len(A1_REGIONS)), dtype=bool)
    in_b = np.zeros((nb, len(A1_REGIONS)), dtype=bool)
    for f in faults:
        r = A1_REGIONS.index(f.region_id)
        now_d[:, r] |= (f.start_ts < t_now) & (t_now <= f.end_ts)     # as runner/run.py dark()
        in_b[:, r] |= (f.start_ts < b_end) & (f.end_ts > b_end - cfg.bucket_s)
    return now_d, in_b


@lru_cache(maxsize=16)
def odds(rate: float, n: int = CALIB_N, cfg: Config = DEFAULTS) -> tuple[np.ndarray, np.ndarray]:
    """(count[m, i], exceed[m, i, j, k]) with exceed = evenings where >= k+1 newly dark."""
    nb, nr = n_buckets(cfg), len(A1_REGIONS)
    count = np.zeros((nr + 1, nb), dtype=np.int64)
    exceed = np.zeros((nr + 1, nb, nb, nr), dtype=np.int64)
    rows = np.arange(nb)
    for s in range(CALIB_SEED0, CALIB_SEED0 + n):
        faults = draw_faults_rate(s, rate, cfg)
        if not faults:
            count[0] += 1
            continue
        now_d, in_b = _dark_matrices(faults, cfg, nb)
        m = now_d.sum(axis=1)
        # newly dark in bucket j, relative to what was lit at i
        new = (~now_d).astype(np.int64) @ in_b.T.astype(np.int64)
        count[m, rows] += 1
        for k in range(nr):
            exceed[m, rows, :, k] += new >= k + 1
    return count, exceed


@lru_cache(maxsize=16)
def table(rate: float, alpha: float, cfg: Config = DEFAULTS) -> tuple:
    count, exceed = odds(rate, cfg=cfg)
    nr1, nb = count.shape
    out = np.ones((nr1, nb, nb), dtype=np.int64)
    for m in range(nr1):
        for i in range(nb):
            c = count[m, i]
            if c < MIN_SAMPLES:
                continue
            p = exceed[m, i] / c                        # (nb j, k): P(>= k+1 newly dark)
            ok = p <= alpha
            # smallest k whose tail P(> k) is within alpha; ok[:, k] is P(>= k+1) <= alpha
            out[m, i] = np.where(ok.any(axis=1), ok.argmax(axis=1), p.shape[1])
            out[m, i, :i] = 0                           # the past holds nothing
    return tuple(tuple(tuple(int(x) for x in row) for row in plane) for plane in out)
