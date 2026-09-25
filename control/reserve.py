"""How many reachable regions the reserve protects against losing, bucket by bucket.

"n1" (headroom): one, always. That is the deterministic rule and this module is not consulted.
"p90" (RATIONALE.md s6d): as many as `cfg.p90_table` says keep P(more go dark) <= p90_alpha,
given how many regions are dark now. The table is built by `runner/p90.py` from the fault
model and handed in through the config, because control/ may not read sim/.

The controller sees "dark" as a region with no reachable kW. That is what the table conditions
on, so the two agree in the regional world. In a world where outages ignore region boundaries
they do not, which is one reason the P90 runs are regional only.
"""
from __future__ import annotations

from collections.abc import Callable

from config import Config
from control.estimator import ScopeBelief


def regions_to_hold(cfg: Config, now: float, belief: ScopeBelief) -> Callable[[float], int] | None:
    """k(bucket_end) for the P90 rule, or None for the deterministic N-1 rule."""
    if cfg.reserve_rule == "n1":
        return None
    if cfg.reserve_rule != "p90":
        raise ValueError(f"reserve_rule must be n1 or p90, not {cfg.reserve_rule!r}")
    if not cfg.p90_table:
        raise ValueError("reserve_rule p90 needs cfg.p90_table (runner/p90.py builds it)")
    regions = {e.region_id for e in belief.estimates}
    lit = {e.region_id for e in belief.estimates if e.reachable and e.kw_cap > 0}
    plane = cfg.p90_table[min(len(regions - lit), len(cfg.p90_table) - 1)]
    nb = len(plane)
    i = min(max(int((now - cfg.t0) // cfg.bucket_s), 0), nb - 1)
    row = plane[i]
    n_lit = len(lit)

    def k_at(bucket_end: float) -> int:
        j = int(round((bucket_end - cfg.t0) / cfg.bucket_s)) - 1
        return min(row[j], n_lit) if 0 <= j < nb else 0

    return k_at
