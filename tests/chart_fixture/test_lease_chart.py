"""The lease-vs-bytes chart gets the same treatment as the hero and batch charts. FINDING-7.

A chart that only exists as pixels can only be checked by looking at it. These assert the
GEOMETRY `build_lease_chart` produces, and that it matches `metrics.lease.lease_cost` directly
rather than drifting from it.
"""
from __future__ import annotations

import dataclasses

import pytest

from config import DEFAULTS
from metrics.lease import lease_cost
from web.lease_chart import build_lease_chart, render_png


# REQ: HEADROOM-1014-S3/AC-1
def test_series_names_and_order():
    spec = build_lease_chart(DEFAULTS)
    assert [s.name for s in spec.series] == ["piggyback", "standalone"]
    for s in spec.series:
        assert len(s.y) == 1


# REQ: HEADROOM-1014-S3/AC-1
def test_values_match_lease_cost_for_defaults():
    spec = build_lease_chart(DEFAULTS)
    piggyback = lease_cost(dataclasses.replace(DEFAULTS, lease_piggyback=True))[
        "bytes_per_device_day"]
    standalone = lease_cost(dataclasses.replace(DEFAULTS, lease_piggyback=False))[
        "bytes_per_device_day"]
    assert piggyback == pytest.approx(2073600.0)
    assert standalone == pytest.approx(2211840.0)
    assert spec.line("piggyback").y[0] == pytest.approx(piggyback)
    assert spec.line("standalone").y[0] == pytest.approx(standalone)


# REQ: HEADROOM-1014-S3/AC-1
def test_values_are_the_same_whatever_the_cfgs_own_lease_piggyback_is():
    """build_lease_chart always builds BOTH bars from lease_piggyback forced to True and
    False in turn - the cfg's own setting of that field must not change either bar."""
    spec_true = build_lease_chart(DEFAULTS)
    spec_false = build_lease_chart(dataclasses.replace(DEFAULTS, lease_piggyback=False))
    for name in ("piggyback", "standalone"):
        assert spec_true.line(name).y[0] == pytest.approx(spec_false.line(name).y[0])


# REQ: HEADROOM-1014-S3/AC-2
def test_fraction_inside_is_one_for_both_series_on_defaults():
    spec = build_lease_chart(DEFAULTS)
    assert spec.fraction_inside("piggyback") == 1.0
    assert spec.fraction_inside("standalone") == 1.0


# REQ: HEADROOM-1014-S3/AC-2
def test_fraction_inside_is_one_when_msg_ack_bytes_changes_the_data():
    cfg = dataclasses.replace(DEFAULTS, msg_ack_bytes=480)
    spec = build_lease_chart(cfg)
    assert spec.line("standalone").y[0] == pytest.approx(4147200.0)
    assert spec.fraction_inside("piggyback") == 1.0
    assert spec.fraction_inside("standalone") == 1.0


# REQ: HEADROOM-1014-S3/AC-3
def test_annotation_states_the_standalone_extra_as_a_signed_percent():
    spec = build_lease_chart(DEFAULTS)
    assert any("+6.7%" in a.text for a in spec.annotations), (
        f"expected an annotation containing '+6.7%'; have {[a.text for a in spec.annotations]}")


# REQ: HEADROOM-1014-S3/AC-3
def test_it_renders(tmp_path):
    spec = build_lease_chart(DEFAULTS)
    out = render_png(spec, tmp_path / "lease.png")
    assert out.exists() and out.stat().st_size > 10_000
