"""Fleet truth, vectorized. SPEC §6.1 step 6.

This is the world. `control/` never sees any of it - only what the network chooses to deliver
(tests/boundary enforces the import ban).

Physics per step, per device:
    soc -= (p_actual + aux + served_home_load) * dt/3600      with eta = 1 [A, Must]
  * GRID with the floor guard ON clamps the grid setpoint so soc cannot cross the floor.
  * aux drains regardless of the guard - standby draw does not stop because you asked it to.
    That asymmetry is deliberate: it is why I2 (floor never breached) is asserted with the guard
    OFF, and why a quiet device's band widens even when nothing is dispatching it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from config import DEFAULTS, Config
from contracts.types import DeviceMode, GridState
from sim.topology import Topology


@dataclass
class FleetState:
    """Truth. Arrays are device-indexed and share the order of `topology.devices`."""
    soc_kwh: np.ndarray
    p_actual_kw: np.ndarray
    setpoint_kw: np.ndarray
    mode: np.ndarray            # DeviceMode values as a string array
    lease_expiry_ts: np.ndarray
    last_epoch: np.ndarray
    last_seq: np.ndarray
    grid_state: np.ndarray      # GridState values
    guard_on: np.ndarray        # bool; ablated OFF to exercise I2

    # static, cached from topology
    e_max: np.ndarray
    p_max: np.ndarray
    floor_kwh: np.ndarray
    aux_kw: np.ndarray

    @property
    def n(self) -> int:
        return int(self.soc_kwh.size)

    @property
    def kwh_above_floor(self) -> np.ndarray:
        return np.maximum(0.0, self.soc_kwh - self.floor_kwh)


def build_fleet(topo: Topology, cfg: Config = DEFAULTS, soc_frac: float | None = None) -> FleetState:
    d = topo.devices
    n = len(d)
    e_max = np.array([x.e_max_kwh for x in d], dtype=float)
    frac = cfg.start_soc_frac if soc_frac is None else soc_frac
    return FleetState(
        soc_kwh=frac * e_max,
        p_actual_kw=np.zeros(n),
        setpoint_kw=np.zeros(n),
        mode=np.array([DeviceMode.GRID.value] * n, dtype=object),
        lease_expiry_ts=np.full(n, np.inf),
        last_epoch=np.zeros(n, dtype=int),
        last_seq=np.zeros(n, dtype=int),
        grid_state=np.array([GridState.UP.value] * n, dtype=object),
        guard_on=np.ones(n, dtype=bool),
        e_max=e_max,
        p_max=np.array([x.p_max_kw for x in d], dtype=float),
        floor_kwh=np.array([x.floor_kwh for x in d], dtype=float),
        aux_kw=np.array([x.aux_kw for x in d], dtype=float),
    )


def step(state: FleetState, now: float, dt_s: float, home_load_kw: np.ndarray,
         cfg: Config = DEFAULTS) -> None:
    """Advance truth by dt_s. Mutates `state` in place.

    Mode is derived from the grid state and the lease, in that order - a grid outage wins over a
    live lease, because a battery cannot export to a dead feeder no matter what it was told.
    """
    dt_h = dt_s / 3600.0
    lease_live = now < state.lease_expiry_ts
    islanded = state.grid_state == GridState.OUTAGE.value

    mode = np.where(islanded, DeviceMode.ISLANDED.value,
                    np.where(lease_live, DeviceMode.GRID.value, DeviceMode.SAFE_HOLD.value))
    state.mode = mode

    # what the device is being asked to put on the grid
    want = np.where(mode == DeviceMode.GRID.value, state.setpoint_kw, 0.0)

    # the house is served from the battery only when the grid cannot serve it, or when a
    # lease-expired device is configured SELF_CONSUME (not in the Must default).
    served_home = np.where(islanded, home_load_kw, 0.0)

    headroom_kwh = state.soc_kwh - state.floor_kwh
    # the guard clamps the GRID setpoint only; it never limits aux or the house.
    max_grid_kw = np.where(state.guard_on, np.maximum(0.0, headroom_kwh) / dt_h, np.inf)
    actual = np.minimum(want, max_grid_kw)
    actual = np.minimum(actual, np.maximum(0.0, state.soc_kwh) / dt_h)   # cannot discharge past empty

    state.p_actual_kw = actual
    state.soc_kwh = state.soc_kwh - (actual + state.aux_kw + served_home) * dt_h / cfg.eta
    np.maximum(state.soc_kwh, 0.0, out=state.soc_kwh)
