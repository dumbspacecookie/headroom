"""I6 (partial): the fleet is deterministic. The full events_sha256 check lands with the runner.

This exists so lens 4 actually EVALUATES something now instead of being an all-skip that reads
green. A lens whose tests all skip has checked nothing.
"""
import numpy as np

from config import DEFAULTS
from sim.fleet import build_fleet, step
from sim.topology import build_topology


def _run(seed_frac: float) -> np.ndarray:
    topo = build_topology(DEFAULTS)
    fs = build_fleet(topo, DEFAULTS, soc_frac=seed_frac)
    for i in range(40):
        fs.setpoint_kw = fs.p_max * (0.3 + 0.01 * (i % 7))
        step(fs, now=float(i) * 300.0, dt_s=300.0,
             home_load_kw=np.zeros(fs.n), cfg=DEFAULTS)
    return fs.soc_kwh.copy()


def test_same_inputs_same_trajectory():
    assert np.array_equal(_run(0.80), _run(0.80))


def test_different_start_soc_gives_a_different_trajectory():
    """A determinism test that passes for a run that ignores its inputs is not a determinism
    test. This is the control on the control."""
    assert not np.array_equal(_run(0.80), _run(0.60))


def test_topology_is_deterministic():
    a, b = build_topology(DEFAULTS), build_topology(DEFAULTS)
    assert [d.device_id for d in a.devices] == [d.device_id for d in b.devices]
    assert [d.p_max_kw for d in a.devices] == [d.p_max_kw for d in b.devices]
