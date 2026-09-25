"""Seeded fault composition. SPEC S6 ("chaos monkey: random fault composition, batch only").

WHY THIS EXISTS, stated plainly: until 2026-09-17 the `seed` argument did nothing.

`run_scenario(seed=...)` carried a seed into `RunResult` and into `metrics.json`, and **nothing
in `sim/` ever read it** - there is no RNG anywhere in the fleet, the topology or the tick loop.
Every "seed" produced a byte-identical run. Two things were resting on that:

  * `tests/determinism` asserted I6 ("same seed -> same events_sha256") against a constant. It
    would have passed just as green if the seed had been deleted from the signature.
  * SPEC 11's `perf` lens asks for **1k devices x 1000 seeds**, and `DEMO.md` Slide 3 says
    "across N seeded chaos runs we hold back X% of what a perfect oracle could promise".
    A thousand identical runs is one run reported a thousand times, and the sentence on the
    slide would have been false in the way that is hardest to spot: every number in it real.

So the seed has to move something, and the thing it must move is **faults** - the reason
`headroom` exists at all. A scenario where nothing ever goes dark cannot distinguish a
controller that holds a reserve from one that does not.

OPT-IN, DELIBERATELY. `run_scenario`'s `faults` argument still defaults to `()`, so S1, S2, the
demo bake and every committed `events_sha256` are untouched. Only `runner/batch.py` asks for a
draw. A chaos layer that switched itself on would have silently rewritten the demo.
"""
from __future__ import annotations

import numpy as np

from config import DEFAULTS, Config
from runner.run import RegionOutage

# [A] The fault distribution. These are assumptions, not measurements, and they are here rather
# than in config.py because they describe a TEST HARNESS, not the product - nothing in the Must
# scope reads them, and moving one cannot change a demo number.
#
# Chosen so that a meaningful fraction of seeds are quiet: a batch in which every run is a
# disaster measures disaster recovery, not the ordinary evening, and would flatter the reserve.
N_FAULTS_P = (0.25, 0.40, 0.25, 0.10)          # P(0), P(1), P(2), P(3) simultaneous outages
DURATIONS_MIN = (10.0, 20.0, 30.0, 45.0, 60.0)
A1_REGIONS = ("R1", "R2", "R3", "R4", "R5")
LEAD_IN_S = 3600.0                              # no fault in the first hour: nothing is booked
TAIL_S = 900.0                                  # nor in the last 15 min: nothing left to notice


def draw_faults(seed: int, cfg: Config = DEFAULTS,
                regions: tuple[str, ...] = A1_REGIONS) -> tuple[RegionOutage, ...]:
    """A deterministic fault composition for `seed`. Same seed -> same faults, always.

    Comms outages only. A grid outage (ISLANDED) is S4's, and it changes what the fleet can
    physically do rather than what the controller can see - mixing the two into one batch would
    make "where we lose" unattributable.
    """
    rng = np.random.default_rng(seed)
    n = int(rng.choice(len(N_FAULTS_P), p=N_FAULTS_P))
    if n == 0:
        return ()

    lo = cfg.t0 + LEAD_IN_S
    hi = cfg.horizon_ts - TAIL_S
    picked = rng.choice(len(regions), size=min(n, len(regions)), replace=False)

    out = []
    for r in picked:
        dur = float(rng.choice(DURATIONS_MIN)) * 60.0
        # Snap to the bucket grid. A fault starting mid-bucket would make the lead time depend
        # on where in the bucket it landed, which is a property of the draw and not of the
        # controller - and it is the controller this batch is measuring.
        n_buckets = int((hi - dur - lo) // cfg.bucket_s)
        if n_buckets <= 0:
            continue
        start = lo + cfg.bucket_s * int(rng.integers(0, n_buckets + 1))
        out.append(RegionOutage(regions[r], start, min(start + dur, hi), "comms"))
    return tuple(sorted(out, key=lambda f: (f.start_ts, f.region_id)))


def describe(faults: tuple[RegionOutage, ...]) -> str:
    if not faults:
        return "quiet"
    return " · ".join(
        f"{f.region_id} {int(f.start_ts // 3600) % 24:02d}:{int(f.start_ts % 3600) // 60:02d}"
        f"+{int((f.end_ts - f.start_ts) / 60)}m" for f in faults)


# ---------------------------------------------------------------- fault SHAPE (RATIONALE s8 item 2)
# The batch above only ever darkens whole regions, which builds premise P2 ("outages come by
# tower/region") into the test that is supposed to measure the N-1 region reserve. These worlds
# keep each evening's darkness IDENTICAL in device-seconds and change only its shape:
#   regional   - the draw above, unchanged
#   scattered  - each region outage becomes the same number of devices, same window, drawn at
#                random across the whole aggregation
#   fragmented - each region outage becomes FRAG_GROUPS groups of equal size, same duration,
#                each with its own random start: many small independent dropouts
WORLDS = ("regional", "scattered", "fragmented")
FRAG_GROUPS = 8


def draw_faults_world(seed: int, world: str = "regional",
                      cfg: Config = DEFAULTS) -> tuple[RegionOutage, ...]:
    base = draw_faults(seed, cfg)
    if world == "regional" or not base:
        return base
    if world not in WORLDS:
        raise KeyError(f"unknown world {world!r}; have {WORLDS}")

    from sim.topology import build_topology
    a1 = build_topology(cfg).in_scope("aggregation:A1")
    ids = np.array(sorted(d.device_id for d in a1))
    size = {r: sum(1 for d in a1 if d.region_id == r) for r in A1_REGIONS}
    # A separate stream, so the regional draw above is byte-identical whatever this does.
    rng = np.random.default_rng([seed, 1 + WORLDS.index(world)])
    lo, hi = cfg.t0 + LEAD_IN_S, cfg.horizon_ts - TAIL_S

    # One permutation for the whole evening, sliced: no device is in two outages, exactly as no
    # two regional outages share a region. Otherwise overlap would quietly shrink the darkness.
    pool = iter(rng.permutation(ids).tolist())

    def take(k: int) -> frozenset[str]:
        return frozenset(next(pool) for _ in range(k))

    out = []
    for f in base:
        n = size[f.region_id]
        if world == "scattered":
            pick = take(n)
            out.append(RegionOutage(f"scatter<{f.region_id}", f.start_ts, f.end_ts, "comms", pick))
            continue
        dur = f.end_ts - f.start_ts
        per = n // FRAG_GROUPS
        n_buckets = int((hi - dur - lo) // cfg.bucket_s)
        for g in range(FRAG_GROUPS):
            pick = take(per)
            start = lo + cfg.bucket_s * int(rng.integers(0, max(0, n_buckets) + 1))
            out.append(RegionOutage(f"frag{g}<{f.region_id}", start, min(start + dur, hi),
                                    "comms", pick))
    return tuple(sorted(out, key=lambda f: (f.start_ts, f.region_id)))


def dark_device_seconds(faults: tuple[RegionOutage, ...], cfg: Config = DEFAULTS) -> float:
    """Total darkness, so a test can assert the worlds differ in SHAPE and not in amount."""
    from sim.topology import build_topology
    a1 = build_topology(cfg).in_scope("aggregation:A1")
    per_region = {r: sum(1 for d in a1 if d.region_id == r) for r in A1_REGIONS}
    return sum((f.end_ts - f.start_ts) * (len(f.device_ids) or per_region[f.region_id])
               for f in faults)


# ---------------------------------------------------------------- outage RATE (RATIONALE s6d)
# The P90 reserve's answer depends on how often regions go dark, and the rate above is an [A]
# nobody measured. These worlds scale it and keep everything else. 1.0 is `draw_faults` exactly.
# Below 1, each outage is kept with probability `rate`. Above 1, a second, independent evening's
# outages are added, thinned to `rate - 1`; a region can then be dark twice, and where the two
# overlap the darkness is counted once, so 2x is slightly less than twice the darkness.
RATE_WORLDS = {"rate050": 0.5, "rate075": 0.75, "rate100": 1.0, "rate200": 2.0}


def draw_faults_rate(seed: int, rate: float, cfg: Config = DEFAULTS) -> tuple[RegionOutage, ...]:
    if not 0.0 <= rate <= 2.0:
        raise ValueError(f"rate must be in [0, 2], not {rate}")
    base = draw_faults(seed, cfg)
    if rate == 1.0:
        return base
    # Separate streams, so the base draw is byte-identical whatever the rate.
    rng = np.random.default_rng([seed, 101])
    if rate < 1.0:
        out = [f for f in base if rng.random() < rate]
    else:
        extra_seed = int(np.random.default_rng([seed, 202]).integers(0, 2**31))
        out = list(base) + [f for f in draw_faults(extra_seed, cfg) if rng.random() < rate - 1.0]
    return tuple(sorted(out, key=lambda f: (f.start_ts, f.region_id)))
