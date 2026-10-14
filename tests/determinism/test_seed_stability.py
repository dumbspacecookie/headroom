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


# REQ: HEADROOM-0930-S1A/AC-3
def test_default_path_events_are_unchanged_by_the_epoch_fencing_addition():
    """sim/fleet.py gained `apply_command` (epoch/seq command fencing, SPEC 5/6.5), but nothing
    calls it from `runner/run.py` yet - the clarifications call this out explicitly, since
    wiring it in is out of this ticket's scope. So the no-fault S1 run and the S2 tower-down
    fault run must still produce exactly the events they did before that addition.

    These hashes were captured by running the unmodified, base-commit code in this worktree,
    before `apply_command` was added - they are not recomputed from the current build.
    """
    from config import ct
    from runner.run import RegionOutage, run_scenario

    s2_fault = (RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),)

    headroom = run_scenario("headroom")
    assert headroom.events_sha256 == (
        "374c99b558a5815025497f980d329089f83952a34b6b42531d8324ede675292b")

    s2 = run_scenario("headroom", faults=s2_fault)
    assert s2.events_sha256 == (
        "9e0a58f7ec4f6e8c3782f142eb952687bc30c46ab787a25a072d205e6f37c684")


# REQ: HEADROOM-0930-S1B/AC-4
def test_crash_none_leaves_these_same_literals_unchanged():
    """HEADROOM-0930-S1B added an optional `crash=` keyword to `run_scenario` for the S3
    controller-crash window. `crash=None` - what every existing caller passes, since none of
    runner/batch.py, compare.py or show.py was touched - must still produce the exact same
    `events_sha256` literals pinned above by S1A, on the same two runs."""
    from config import ct
    from runner.run import RegionOutage, run_scenario

    s2_fault = (RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),)

    headroom = run_scenario("headroom", crash=None)
    assert headroom.events_sha256 == (
        "374c99b558a5815025497f980d329089f83952a34b6b42531d8324ede675292b")

    s2 = run_scenario("headroom", faults=s2_fault, crash=None)
    assert s2.events_sha256 == (
        "9e0a58f7ec4f6e8c3782f142eb952687bc30c46ab787a25a072d205e6f37c684")
