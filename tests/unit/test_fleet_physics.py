"""Fleet truth. SPEC §6.1 step 6."""
import numpy as np
import pytest

from config import DEFAULTS
from contracts.types import GridState
from sim.fleet import build_fleet, step
from sim.topology import build_topology

CFG = DEFAULTS


def _small(n=4):
    topo = build_topology(CFG)
    fs = build_fleet(topo, CFG)
    for name in ("soc_kwh", "p_actual_kw", "setpoint_kw", "lease_expiry_ts", "last_epoch",
                 "last_seq", "e_max", "p_max", "floor_kwh", "aux_kw"):
        setattr(fs, name, getattr(fs, name)[:n])
    for name in ("mode", "grid_state", "guard_on"):
        setattr(fs, name, getattr(fs, name)[:n])
    return fs


def test_guard_on_means_the_floor_is_never_crossed():
    fs = _small()
    fs.soc_kwh = fs.floor_kwh + 0.1
    fs.setpoint_kw = fs.p_max.copy()
    for _ in range(50):
        step(fs, now=0.0, dt_s=300.0, home_load_kw=np.zeros(fs.n), cfg=CFG)
    assert np.all(fs.soc_kwh >= -1e-9)


def test_aux_drains_even_when_nothing_is_dispatched():
    """The guard clamps the GRID setpoint. It does not stop standby draw - which is why a quiet
    device's band widens, and why I2 is asserted with the guard OFF."""
    fs = _small()
    fs.setpoint_kw = np.zeros(fs.n)
    before = fs.soc_kwh.copy()
    step(fs, now=0.0, dt_s=3600.0, home_load_kw=np.zeros(fs.n), cfg=CFG)
    assert np.all(fs.soc_kwh < before)
    assert np.allclose(before - fs.soc_kwh, fs.aux_kw, atol=1e-9)


def test_islanded_device_serves_the_house_and_does_not_export():
    fs = _small()
    fs.grid_state = np.array([GridState.OUTAGE.value] * fs.n, dtype=object)
    fs.setpoint_kw = fs.p_max.copy()
    before = fs.soc_kwh.copy()
    step(fs, now=0.0, dt_s=3600.0, home_load_kw=np.full(fs.n, 2.0), cfg=CFG)
    assert np.all(fs.p_actual_kw == 0.0), "islanded device exported to a dead feeder"
    assert np.allclose(before - fs.soc_kwh, 2.0 + fs.aux_kw, atol=1e-9)


def test_stepping_is_deterministic():
    a, b = _small(), _small()
    for _ in range(20):
        for fs in (a, b):
            fs.setpoint_kw = fs.p_max * 0.5
            step(fs, now=0.0, dt_s=300.0, home_load_kw=np.zeros(fs.n), cfg=CFG)
    assert np.array_equal(a.soc_kwh, b.soc_kwh)
