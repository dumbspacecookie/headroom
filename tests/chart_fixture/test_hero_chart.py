"""The hero chart is on the never-cut list, so it gets a test. SPEC §11 (v0.4), FINDING-7.

On 2026-09-16 a chart plotted minutes-from-15:00 against a clock-hour axis. Matplotlib produced
a clean, correctly styled, ENTIRELY BLANK figure and exited 0. Nothing failed. The slide would
have gone up empty.

So these tests assert geometry, not that a file appeared.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from web.hero_chart import MATERIAL_CROSSING_KWH, build_hero_chart, render_png

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "frames_s1.json"


@pytest.fixture(scope="module")
def frames() -> list[dict]:
    if not FIXTURE.exists():
        pytest.fail("tests/fixtures/frames_s1.json missing - run tests/fixtures/make_frames_s1.py")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["frames"]


@pytest.mark.parametrize("controller", ["headroom", "reasonable"])
def test_truth_line_is_inside_the_axes(frames, controller):
    """THE assertion FINDING-7 exists for. Not 'the PNG was written'."""
    spec = build_hero_chart(frames, controller)
    inside = spec.fraction_inside("truth")
    assert inside >= 0.9, (
        f"{controller}: only {inside:.0%} of the truth line is inside the axes. The chart will "
        f"render clean, styled and empty, and exit 0."
    )


@pytest.mark.parametrize("controller", ["headroom", "reasonable"])
def test_truth_line_actually_moves(frames, controller):
    """A flat line is inside the axes too. An energy-left chart whose energy never changes is
    not showing an evening."""
    spec = build_hero_chart(frames, controller)
    assert spec.y_range("truth") > 100.0, "truth line is flat - nothing is being discharged"


@pytest.mark.parametrize("controller", ["headroom", "reasonable"])
def test_belief_is_never_above_truth(frames, controller):
    """The pessimistic edge is a floor under what may be sold. If it rises above truth, the
    chart is claiming the controller believes it has MORE than it does, which inverts the pitch."""
    spec = build_hero_chart(frames, controller)
    low, truth = spec.line("belief_low"), spec.line("truth")
    worst = max(lo - tr for lo, tr in zip(low.y, truth.y))
    assert worst <= 1e-6, f"{controller}: belief exceeded truth by {worst:,.1f} kWh"


def test_commitments_are_stacked_by_claim_not_summed(frames):
    """SPEC §12: 'future committed kWh stacked BY CLAIM'. One merged block cannot show the co-op
    squeezing ADER_ENERGY, which is the whole of Beat A."""
    spec = build_hero_chart(frames, "headroom")
    stacks = [s.name for s in spec.series if s.kind == "stack"]
    assert set(stacks) >= {"COOP_PEAK", "ADER_AS", "ADER_ENERGY"}, f"got {stacks}"


def test_reasonable_crosses_and_the_crossing_is_annotated(frames):
    """FINDING-10: the crossing IS the thesis and at S1's sizing it is a few pixels tall.
    It must carry a marker and the kWh number, not be left to the projector."""
    spec = build_hero_chart(frames, "reasonable")
    crossings = [a for a in spec.annotations if a.kind == "crossing"]
    assert crossings, "reasonable over-promises but the chart does not say where"
    assert "kWh" in crossings[0].text, "the crossing annotation must carry the number"

    # The first version of this test asserted only the two lines above, and passed while the
    # chart annotated "15:15 promised 0 kWh more than it has" - a real annotation, at the wrong
    # moment, carrying a meaningless number, caught by LOOKING at the render. Assert substance.
    import re
    magnitude = float(re.search(r"promised ([\d,]+) kWh", crossings[0].text).group(1).replace(",", ""))
    assert magnitude >= MATERIAL_CROSSING_KWH, (
        f"annotated a {magnitude} kWh crossing - that is float noise dressed as a finding"
    )
    truth = spec.line("truth")
    assert crossings[0].x > truth.x[0], (
        "the crossing is annotated at the FIRST frame, before anything has been delivered - "
        "the detector is firing on equality, not on an over-promise"
    )
    worst = max(sum(f["committed_future_kwh"].values()) - f["energy_left_truth_kwh"]
                for f in frames if f["controller"] == "reasonable")
    assert magnitude == pytest.approx(worst, abs=1.0), (
        f"annotated {magnitude:,.0f} kWh but the worst over-promise is {worst:,.0f} kWh - "
        f"the chart is pointing at a crossing, just not the one that matters"
    )


def test_headroom_does_not_cross(frames):
    """The other half of the comparison. If headroom crossed too there would be no argument."""
    spec = build_hero_chart(frames, "headroom")
    assert not [a for a in spec.annotations if a.kind == "crossing"], \
        "headroom over-promised - the ledger is admitting beyond the band"


def test_headroom_never_admits_past_the_pessimistic_edge(frames):
    """The ledger must never promise into the haircut it set aside.

    This test previously asserted the tightest margin EQUALS the haircut, and passed - because
    the fixture frames were a projection built from the same admissions the assertion compared
    against. Both sides came from one source, so the equality was a tautology wearing an
    invariant's clothes. The moment the frames came from a REAL run it failed (681.9 vs 153.5),
    which is the test doing its job at last.

    The real, safety-relevant claim is the inequality: the truth margin never falls BELOW the
    haircut. Being above it is the ledger being conservative, which is allowed and expected once
    reconcile and water-fill are in the loop.
    """
    spec = build_hero_chart(frames, "headroom")
    truth = spec.line("truth")
    stacks = [s for s in spec.series if s.kind == "stack"]
    owed_top = stacks[-1].y if stacks else [0.0] * len(truth.y)
    margin = min(t - o for t, o in zip(truth.y, owed_top))
    haircut = min(t - b for t, b in zip(truth.y, spec.line("belief_low").y))
    assert margin >= haircut - 1.0, (
        f"tightest truth margin {margin:,.1f} kWh is BELOW the haircut {haircut:,.1f} kWh - "
        f"the ledger has promised into the reserve it set aside"
    )


# ---------------------------------------------------------------- C7
def test_C7_shifting_the_x_data_must_fail_this_lens(frames):
    """Control C7 (docs/GATE.md). Plant FINDING-7's exact bug - x data in the wrong frame of
    reference - and the inside-the-axes assertion must catch it. A control that scores 0 means
    the lens is decoration."""
    one_window = 6.5 * 3600.0
    spec = build_hero_chart(frames, "headroom", _plant_x_shift_s=one_window)
    # the shift moves the data, not the limits the caller would have used
    good = build_hero_chart(frames, "headroom")
    spec.xlim = good.xlim
    assert spec.fraction_inside("truth") < 0.9, (
        "C7 scored 0: the truth line was shifted by a whole window and the lens still passed. "
        "The inside-the-axes assertion is not doing anything."
    )


def test_renders_a_png_that_is_not_trivially_small(tmp_path, frames):
    """Last line of defence, and deliberately last: this is the weak assertion, which is why it
    is not the only one."""
    out = render_png(build_hero_chart(frames, "reasonable"), tmp_path / "hero.png")
    assert out.exists() and out.stat().st_size > 20_000
