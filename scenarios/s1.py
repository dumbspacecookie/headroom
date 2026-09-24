"""S1 - the co-op evening. SPEC §8.1, sized in scenarios/S1_sizing.md.

2026-07-22 CT. Window 15:00-21:30, discharge only, no recharge. Three paid claims want the same
evening's kWh:

  ADER_AS      CAPACITY  aggregation:A1  19:00-21:00  1500 kW, max_deploy_h 1.0   priority 2
  COOP_PEAK    ENERGY    territory:T1    15:30-18:30  3000 kW                     priority 3
  ADER_ENERGY  ENERGY    aggregation:A1  20:00-21:00  2000 kW                     priority 4

Claim windows follow the pulled data, not the other way round: COOP_PEAK covers the load peak
(HE 18:00, 91.1 GW) and ADER_ENERGY sits on the net-load and battery peak (HE 21:00, 75.2 GW and
+11.3 GW). Both facts come from data/raw/2026-07-21_24_eia930_hourly_wide.parquet.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import DEFAULTS, Config, ct
from contracts.types import Claim, ClaimSource, Hardness, Product
from control.estimator import DeviceStatic, ScopeBelief, estimate_device, scope_belief
from contracts.types import DeviceMode, Telemetry


@dataclass(frozen=True)
class Scenario:
    name: str
    t0: float
    horizon_ts: float
    claims: tuple[Claim, ...]
    belief: ScopeBelief
    statics: tuple[DeviceStatic, ...]
    cfg: Config


def _statics(cfg: Config) -> list[DeviceStatic]:
    """A1's devices as `control` is allowed to see them - no sim import anywhere near this."""
    from sim.topology import build_topology          # static topology only; not fleet truth
    topo = build_topology(cfg)
    return [
        DeviceStatic(
            device_id=d.device_id, region_id=d.region_id, feeder_id=d.feeder_id,
            aggregation_id=d.aggregation_id, territory=d.territory,
            e_max_kwh=d.e_max_kwh, p_max_kw=d.p_max_kw, floor_kwh=d.floor_kwh,
            aux_kw=d.aux_kw, safe_hold_behavior=d.safe_hold_behavior,
        )
        for d in topo.in_scope("aggregation:A1")
    ], topo


def build_claims(cfg: Config = DEFAULTS, include_coop: bool = True) -> list[Claim]:
    coop_start = ct(15, 30)
    coop_end = coop_start + cfg.coop_shave_h * 3600.0
    claims = [
        Claim(claim_id="ADER_AS", source=ClaimSource.ADER_AS, product=Product.CAPACITY,
              priority=cfg.priority("ADER_AS"), hardness=Hardness.FIRM,
              scope="aggregation:A1", start_ts=ct(19, 0), end_ts=ct(21, 0),
              power_kw=1500.0, max_deploy_h=1.0,
              tolerance_frac=cfg.tolerance_frac, created_ts=cfg.t0),
        Claim(claim_id="ADER_ENERGY", source=ClaimSource.ADER_ENERGY, product=Product.ENERGY,
              priority=cfg.priority("ADER_ENERGY"), hardness=Hardness.FIRM,
              scope="aggregation:A1", start_ts=ct(20, 0), end_ts=ct(21, 0),
              power_kw=2000.0, tolerance_frac=cfg.tolerance_frac, created_ts=cfg.t0),
    ]
    if include_coop:
        claims.append(
            Claim(claim_id="COOP_PEAK", source=ClaimSource.COOP_PEAK, product=Product.ENERGY,
                  priority=cfg.priority("COOP_PEAK"), hardness=Hardness.FIRM,
                  scope="territory:T1", start_ts=coop_start, end_ts=coop_end,
                  power_kw=3000.0, tolerance_frac=cfg.tolerance_frac, created_ts=cfg.t0)
        )
    return claims


def build_s1(include_coop: bool = True, cfg: Config = DEFAULTS,
             soc_frac: float | None = None) -> Scenario:
    """The scenario as the controller sees it at 15:00 with fresh telemetry from every device."""
    statics, topo = _statics(cfg)
    frac = cfg.start_soc_frac if soc_frac is None else soc_frac
    now = cfg.t0

    estimates = []
    for s in statics:
        tel = Telemetry(
            device_id=s.device_id, seq=1, device_ts=now, soc_kwh=frac * s.e_max_kwh,
            p_kw=0.0, home_load_kw=0.0, mode=DeviceMode.GRID,
            last_applied_epoch=0, last_applied_seq=0, recv_ts=now,
        )
        estimates.append(estimate_device(s, tel, now, last_setpoint_kw=0.0,
                                         lease_expiry_ts=now + cfg.lease_s, cfg=cfg))

    caps = {f.feeder_id: f.export_cap_kw for f in topo.feeders}
    belief = scope_belief(estimates, caps, scope="aggregation:A1", cfg=cfg)
    return Scenario(
        name="S1", t0=now, horizon_ts=cfg.horizon_ts,
        claims=tuple(build_claims(cfg, include_coop)), belief=belief,
        statics=tuple(statics), cfg=cfg,
    )
