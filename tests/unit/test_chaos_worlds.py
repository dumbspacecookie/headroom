"""The fault-SHAPE worlds behind `python run.py compare N scattered|fragmented`. RATIONALE.md s8.

These worlds exist to test premise P2 (outages come by region) without changing HOW MUCH goes
dark. If the amount drifted between worlds, a difference in the comparison could be the amount
and not the shape, and the test would answer a question nobody asked.
"""
from __future__ import annotations

from collections import Counter

import pytest

from sim.chaos import WORLDS, dark_device_seconds, draw_faults, draw_faults_world
from sim.topology import build_topology

SEEDS = range(200)


def test_regional_world_is_the_recorded_draw_byte_for_byte():
    for s in SEEDS:
        assert draw_faults_world(s, "regional") == draw_faults(s)


@pytest.mark.parametrize("world", ["scattered", "fragmented"])
def test_every_world_goes_exactly_as_dark_as_the_regional_draw(world):
    differed = 0
    for s in SEEDS:
        assert dark_device_seconds(draw_faults_world(s, world)) == pytest.approx(
            dark_device_seconds(draw_faults(s))), f"seed {s}: {world} changed the AMOUNT of darkness"
        differed += draw_faults_world(s, world) != draw_faults(s)
    # the control: a world that equals the regional draw everywhere tests nothing
    assert differed > 100, f"{world} matched the regional draw on {200 - differed} of 200 seeds"


@pytest.mark.parametrize("world", ["scattered", "fragmented"])
def test_no_device_is_in_two_outages_on_the_same_evening(world):
    for s in SEEDS:
        ids = [i for f in draw_faults_world(s, world) for i in f.device_ids]
        assert len(ids) == len(set(ids)), f"seed {s}: overlap would shrink the real darkness"


def test_scattered_outages_actually_cross_region_boundaries():
    """The whole point. A 'scattered' outage that lands in one region is a regional outage."""
    region = {d.device_id: d.region_id
              for d in build_topology().in_scope("aggregation:A1")}
    spans = [len(Counter(region[i] for i in f.device_ids))
             for s in SEEDS for f in draw_faults_world(s, "scattered")]
    assert spans and min(spans) >= 3, f"a scattered outage touched only {min(spans)} region(s)"


def test_unknown_world_is_an_error_not_a_silent_regional_run():
    with pytest.raises(KeyError):
        draw_faults_world(1, "scatered")
    assert "regional" in WORLDS
