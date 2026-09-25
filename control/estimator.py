"""The SoC band and scope capability. SPEC §6.2.

This module may not import `sim/`. It sees telemetry and nothing else - that restriction is the
project's whole thesis expressed as an import rule, and `tests/boundary` enforces it.

The asymmetry to hold on to: `soc_low` carries every plausible drain (commanded power, aux, and
the house where the mode says so) while `soc_high` ignores aux entirely. Promises are made from
`soc_low` only. The band is not a confidence interval - it is a floor under what you may sell.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import DEFAULTS, Config
from contracts.types import DeviceMode, Estimate, SafeHoldBehavior, Telemetry


@dataclass(frozen=True)
class DeviceStatic:
    """What control is allowed to know about a device without looking at sim/."""
    device_id: str
    region_id: str
    feeder_id: str
    aggregation_id: str | None
    territory: str
    e_max_kwh: float
    p_max_kw: float
    floor_kwh: float
    aux_kw: float
    safe_hold_behavior: SafeHoldBehavior


@dataclass(frozen=True)
class ScopeBelief:
    """What a scope can promise, per §6.2. Dark devices contribute 0 kW AND 0 kWh."""
    scope: str
    E0_kwh: float
    KW_kw: float
    kw_reserve_kw: float
    kwh_reserve_kwh: float
    drain_kw: float             # aux (+ load where on) of REACHABLE devices; feeds §6.3 drain_hi
    estimates: tuple[Estimate, ...]
    # True when each estimate's kwh_above_floor_low already has the device's standby draw to the
    # horizon taken off (the oracle, which knows it). A forward projection must then not drain
    # aux again, or it counts the same energy out twice. See control/deliverability.py.
    aux_reserved: bool = False
    # Reachable region kW after the feeder clip, largest first. The P90 reserve sums the top k.
    region_kw_desc: tuple[float, ...] = ()

    @property
    def reachable_count(self) -> int:
        return sum(1 for e in self.estimates if e.reachable)


def estimate_device(stat: DeviceStatic, tel: Telemetry | None, now: float,
                    last_setpoint_kw: float, lease_expiry_ts: float,
                    cfg: Config = DEFAULTS) -> Estimate:
    if tel is None:
        # never heard from it. Unreachable, and it contributes nothing in either unit.
        return Estimate(
            device_id=stat.device_id, recv_age_s=float("inf"),
            soc_low_kwh=0.0, soc_high_kwh=0.0, reachable=False, lease_live=False,
            load_term_on=False, kw_cap=0.0, kwh_above_floor_low=0.0,
            region_id=stat.region_id, feeder_id=stat.feeder_id,
            aggregation_id=stat.aggregation_id,
        )

    recv_ts = tel.recv_ts if tel.recv_ts is not None else tel.device_ts
    age = max(0.0, now - recv_ts)
    reachable = age <= cfg.reach_k * cfg.t_tel_s
    lease_live = now < lease_expiry_ts

    # setpoint uncertainty stops at lease expiry; standby drain never stops.
    tau_cmd = max(0.0, min(now, lease_expiry_ts) - recv_ts)
    tau_aux = age

    # mode-aware load term (§6.2, v0.3). Default OFF for a comms-dark device - which is exactly
    # what stops S1's and S2's Notices from being blamed on invented house load.
    load_term_on = (
        tel.mode == DeviceMode.ISLANDED
        or (stat.safe_hold_behavior == SafeHoldBehavior.SELF_CONSUME and not lease_live)
        or (cfg.assume_house_when_grid_unknown and not reachable)
    )
    if tel.mode == DeviceMode.ISLANDED:
        tau_load = tau_aux
    else:
        tau_load = max(0.0, now - max(recv_ts, lease_expiry_ts))

    eps = cfg.eps_frac * stat.e_max_kwh
    drain_cmd = max(tel.p_kw, last_setpoint_kw, 0.0) * tau_cmd / 3600.0
    drain_aux = stat.aux_kw * tau_aux / 3600.0
    drain_load = (cfg.home_load_p95_kw * tau_load / 3600.0) if load_term_on else 0.0

    soc_low = tel.soc_kwh - drain_cmd - drain_aux - drain_load - eps
    soc_high = tel.soc_kwh - min(tel.p_kw, last_setpoint_kw, 0.0) * tau_cmd / 3600.0 + eps
    if not cfg.band:
        # `reasonable_plus` / the -band ablation: the last reading IS the inventory. Reachability
        # still applies (a timeout drops the device), which is what Tesla describes publicly.
        soc_low = soc_high = tel.soc_kwh
        if cfg.point_eps:
            # the -staleness ablation: a fixed margin, none of the widening with age
            soc_low = tel.soc_kwh - eps

    # ISLANDED is never dispatchable, whatever its SoC says.
    dispatchable = reachable and tel.mode != DeviceMode.ISLANDED
    return Estimate(
        device_id=stat.device_id, recv_age_s=age,
        soc_low_kwh=soc_low, soc_high_kwh=soc_high,
        reachable=dispatchable, lease_live=lease_live, load_term_on=load_term_on,
        kw_cap=stat.p_max_kw if dispatchable else 0.0,
        kwh_above_floor_low=max(0.0, soc_low - stat.floor_kwh) if dispatchable else 0.0,
        region_id=stat.region_id, feeder_id=stat.feeder_id,
        aggregation_id=stat.aggregation_id,
    )


def scope_belief(estimates: list[Estimate], feeder_caps: dict[str, float], scope: str,
                 cfg: Config = DEFAULTS) -> ScopeBelief:
    """KW(scope) = sum_f min(export_cap_f, sum_{i in f} kw_cap_i);  E0 = sum kwh_above_floor_low.

    The N-1 reserve is the largest *currently reachable* region inside the scope. Dark regions
    contribute 0 kW and 0 kWh already, so they are not subtracted twice - a dark region is not
    also the thing you are holding reserve against.
    """
    by_feeder: dict[str, float] = {}
    by_region: dict[str, float] = {}
    e0 = 0.0
    for est in estimates:
        by_feeder[est.feeder_id] = by_feeder.get(est.feeder_id, 0.0) + est.kw_cap
        e0 += est.kwh_above_floor_low

    kw_total = 0.0
    feeder_kw: dict[str, float] = {}
    for fid, raw in by_feeder.items():
        capped = min(feeder_caps.get(fid, float("inf")), raw)
        feeder_kw[fid] = capped
        kw_total += capped

    # region kW after the feeder clip, so the reserve is what you could actually lose
    for est in estimates:
        if est.kw_cap <= 0:
            continue
        raw = by_feeder[est.feeder_id] or 1.0
        share = feeder_kw[est.feeder_id] * (est.kw_cap / raw)
        by_region[est.region_id] = by_region.get(est.region_id, 0.0) + share

    if cfg.haircut == "independent" or not by_region:
        kw_reserve = 0.0
    else:
        kw_reserve = max(by_region.values())
    kwh_reserve = kw_reserve * cfg.outage_h
    if cfg.flat_derate_frac > 0.0:
        # A flat de-rate of both units, the way capacity markets de-rate storage. Not N-1: it
        # does not know which region could go dark, only that some share might not show up.
        kw_reserve = max(kw_reserve, cfg.flat_derate_frac * kw_total)
        kwh_reserve = max(kwh_reserve, cfg.flat_derate_frac * e0)

    # drain_hi's rate. Uniform aux is an ASSUMPTIONS.md §1 default, not a fact about the world,
    # so tests/unit asserts the topology actually is uniform - the day it is not, this line is
    # wrong and nothing else would notice.
    drain_kw = sum(cfg.aux_kw for est in estimates if est.reachable)

    return ScopeBelief(
        scope=scope, E0_kwh=e0, KW_kw=kw_total,
        kw_reserve_kw=kw_reserve, kwh_reserve_kwh=kwh_reserve,
        drain_kw=drain_kw, estimates=tuple(estimates),
        region_kw_desc=tuple(sorted(by_region.values(), reverse=True)),
    )
