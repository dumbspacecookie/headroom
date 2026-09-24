"""The S1 belief built from §6.2 must reproduce §8.1's hand arithmetic.

This is a control, not a unit test: §8.1 was worked out by hand and prep/size_s1.py computes it
in closed form. Three independent routes to the same numbers. When they agree the numbers are
probably right; when they disagree, ONE of them is wrong and the job is to find out which -
never to edit whichever is easiest to reach.
"""
import pytest

from scenarios.s1 import build_s1

# SPEC §8.1, 400 devices at 80% SoC, before the drain and reserve terms the LEDGER applies.
EXPECT_E0_KWH = 12_026.6      # nameplate 20,384 * 0.60 - 1% eps
EXPECT_KW = 4_784.0           # 400 * 14.95 * 0.80 feeder cap
EXPECT_N1_KW = 956.8          # largest region: 80 devices
EXPECT_KW_ROOM = 3_827.2      # KW - N-1


def test_s1_belief_reproduces_spec_8_1():
    b = build_s1().belief
    assert b.E0_kwh == pytest.approx(EXPECT_E0_KWH, abs=1.0)
    assert b.KW_kw == pytest.approx(EXPECT_KW, abs=1.0)
    assert b.kw_reserve_kw == pytest.approx(EXPECT_N1_KW, abs=1.0)
    assert b.kwh_reserve_kwh == pytest.approx(EXPECT_N1_KW * 0.5, abs=1.0)
    assert b.KW_kw - b.kw_reserve_kw == pytest.approx(EXPECT_KW_ROOM, abs=1.0)


def test_a1_is_400_devices_and_the_fleet_has_more():
    """T2/T3 exist to show the fleet holds kWh the aggregation cannot use. If A1 were the whole
    fleet, S1's scarcity would be a fleet-size artifact rather than an ADER scope constraint."""
    from sim.topology import build_topology
    topo = build_topology()
    assert len(topo.in_scope("aggregation:A1")) == 400
    assert len(topo.devices) == 1000
    assert len(topo.in_scope("territory:T1")) == 400


def test_coop_and_ader_contend_for_the_same_devices():
    """If territory:T1 and aggregation:A1 resolved to different device sets there would be no
    collision, and S1 would be telling a story about nothing."""
    from sim.topology import build_topology
    topo = build_topology()
    assert {d.device_id for d in topo.in_scope("territory:T1")} == \
           {d.device_id for d in topo.in_scope("aggregation:A1")}
