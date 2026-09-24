"""I6: same seed -> same `events_sha256`.

This file was green and testing nothing, in two independent ways, until 2026-09-17:

  1. It called `run_scenario("S1", seed=42)`. The first positional argument is **`controller`**,
     not `scenario` - so it asserted the determinism of a controller named `"S1"`, which does
     not exist. It fell through to the non-`reasonable` branch and ran headroom's code under a
     name nothing else in the build uses.
  2. **The seed did nothing.** There was no RNG anywhere in `sim/`; every seed produced a
     byte-identical run. "Same seed -> same hash" is not an achievement when *different* seeds
     also give the same hash. The assertion could not have failed.

`sim/chaos.py` now makes the seed choose a fault composition, so I6 has something to be true
about, and the tests below can distinguish a deterministic system from a constant one.
"""
from __future__ import annotations

import pytest


def test_same_seed_same_hash():
    """I6 proper: the same controller, the same seed, the same faults -> the same events."""
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    faults = draw_faults(42)
    a = run_scenario("headroom", seed=42, faults=faults)
    b = run_scenario("headroom", seed=42, faults=faults)
    assert a.events_sha256 == b.events_sha256


def test_different_seeds_give_different_runs():
    """The control on the test above. Without this, a build that ignored the seed entirely
    would pass I6 perfectly - which is exactly what happened here for two days.

    Seeds are searched rather than hard-coded because ~22% of draws are deliberately quiet
    (`sim/chaos.py`), and two quiet evenings SHOULD be identical. The claim is that the seed
    axis is live, not that every pair of seeds differs.
    """
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    hashes = {}
    for seed in range(8):
        faults = draw_faults(seed)
        hashes.setdefault(run_scenario("headroom", seed=seed, faults=faults).events_sha256,
                          []).append(seed)
    assert len(hashes) > 1, (
        "every seed in 0..7 produced the same events hash. The seed axis is inert again - "
        "a batch over 1,000 of them would be one run reported a thousand times, and Slide 3's "
        "'across N seeded chaos runs' would be false with every number in it real."
    )


def test_the_controller_argument_is_a_controller():
    """The bug that hid inside this file: `run_scenario`'s first argument is the CONTROLLER.

    A scenario name passed there does not raise - it just is not `"reasonable"`, so it runs
    headroom's path under a fictional name and every assertion downstream still passes.
    """
    import inspect

    from runner.run import run_scenario

    params = list(inspect.signature(run_scenario).parameters)
    assert params[0] == "controller", (
        f"run_scenario's first positional parameter is now {params[0]!r}. Every caller that "
        f"passes a bare string needs re-reading, starting with this file.")

    with pytest.raises(KeyError):
        # `reasonable` and `oracle` are real; an unknown name must not silently mean "headroom".
        run_scenario("S1", seed=0)
