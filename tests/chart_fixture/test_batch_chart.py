"""Slide 3's picture gets the same treatment as the hero chart. SPEC §11, FINDING-7.

A chart that only exists as pixels can only be checked by looking at it, and this build has
already shipped one that rendered clean, correctly styled and entirely blank. So these assert
geometry - and, more importantly, they assert that the geometry still makes the ARGUMENT the
slide says it makes. A histogram can be beautiful and say nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from web.batch_chart import build_batch_chart, render_png

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "prep" / "out" / "batch_results.json"


@pytest.fixture(scope="module")
def results() -> dict:
    if not RESULTS.exists():
        pytest.fail("prep/out/batch_results.json missing - run `python run.py batch 1000`")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec(results):
    return build_batch_chart(results)


def test_every_evening_is_drawn_somewhere(spec, results):
    """Bins clamp out-of-window values into the edge bin. They must never DROP one."""
    for s in spec.series:
        assert sum(s.y) == results["n_seeds"], (
            f"{s.name} has {sum(s.y):,.0f} evenings on the chart and the sweep ran "
            f"{results['n_seeds']:,}. A binning bug that loses evenings would make the picture "
            f"a subset nobody chose.")


def test_zero_is_on_the_chart(spec):
    """Zero is not an origin here, it is the claim the whole slide rests on: what the fleet
    could actually have delivered. If it ever falls outside the window the argument is
    invisible and the chart is decoration."""
    assert spec.xlim[0] < 0.0 < spec.xlim[1]


def test_the_bars_are_inside_the_axes(spec):
    for s in spec.series:
        assert s.x and s.y, f"{s.name} drew nothing"
        assert min(s.x) >= spec.xlim[0] and max(s.x) <= spec.xlim[1]
        assert max(s.y) <= spec.ylim[1], f"{s.name}'s tallest bar is clipped by ylim"
        assert max(s.y) > 0, f"{s.name} is flat - a blank chart that exits 0"


def test_the_picture_still_makes_the_argument(results):
    """The one that matters. Slide 3 says the baseline sells what it cannot deliver and we do
    not. That is a claim about which SIDE of zero each distribution falls on, and it is checked
    here rather than admired in the render.
    """
    held = {c: [r[c]["held_back_pct"] for r in results["rows"]
                if r[c]["held_back_pct"] == r[c]["held_back_pct"]]
            for c in ("headroom", "reasonable")}

    over = [v for v in held["reasonable"] if v >= 0]
    assert not over, (
        f"`reasonable` held back >= 0 on {len(over)} evenings. The slide says it books past the "
        f"ceiling on every one of them; if that stops being true, re-read Slide 3.")

    right_side = sum(1 for v in held["headroom"] if v > 0)
    assert right_side >= 0.95 * len(held["headroom"]), (
        f"headroom is on the safe side of zero on only {right_side} of {len(held['headroom'])} "
        f"evenings. The chart's claim is 'almost always', not 'always' - but not this.")


def test_both_series_are_directly_labelled(spec):
    """Identity must never rest on colour alone - a projector, a colourblind judge, a
    photocopy. The legend is not enough on its own, so each series carries its own label."""
    kinds = {a.kind for a in spec.annotations}
    assert {"headroom", "reasonable"} <= kinds, f"missing direct labels; have {kinds}"
    for a in spec.annotations:
        if a.kind in ("headroom", "reasonable"):
            assert a.kind in a.text and "%" in a.text


def test_the_medians_on_the_chart_are_the_medians_in_the_sweep(spec, results):
    """The labels are the numbers Slide 3 reads aloud. They come from the aggregate, and this
    is what stops them drifting from it the way `0.61 MW` did."""
    agg = results["aggregate"]
    for a in spec.annotations:
        if a.kind in ("headroom", "reasonable"):
            assert a.x == pytest.approx(agg[a.kind]["held_back_pct_median"])


def test_it_renders(spec, tmp_path):
    """Not the assertion - the assertions are above. But a spec that cannot be drawn is still
    a slide nobody sees, and matplotlib raises on plenty of valid-looking geometry."""
    out = render_png(spec, tmp_path / "batch.png")
    assert out.exists() and out.stat().st_size > 10_000
