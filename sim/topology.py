"""The static world: devices, feeders, aggregations. Deterministic, no RNG.

SPEC §8.1 fleet: 1,000 devices. T1 = ADER aggregation A1 = 400 devices in 5 regions x 80, feeder
cap 80%. T2/T3 = the other 600, outside A1 - they exist to show that *the fleet has kWh the
aggregation cannot use*, which is the ADER ALR constraint [S] and half the reason S1 is scarce.

The 70/30 unit mix is laid down identically inside every region, so the N-1 reserve does not
depend on which region happens to be largest. That is a modelling choice, not a fact: it makes
the reserve a clean function of region size. Recorded here so it is visible rather than emergent.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import DEFAULTS, Config
from contracts.types import Aggregation, Device, Feeder, SafeHoldBehavior


@dataclass(frozen=True)
class Topology:
    devices: tuple[Device, ...]
    feeders: tuple[Feeder, ...]
    aggregations: tuple[Aggregation, ...]

    def by_id(self, device_id: str) -> Device:
        return self._index[device_id]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_index", {d.device_id: d for d in self.devices})

    # ---- scope resolution (SPEC §5 Claim.scope)
    def in_scope(self, scope: str) -> tuple[Device, ...]:
        """"fleet" | "aggregation:A1" | "territory:T1" | "partition:P" | "feeder:F03"."""
        if scope == "fleet":
            return self.devices
        kind, _, name = scope.partition(":")
        if kind == "aggregation":
            return tuple(d for d in self.devices if d.aggregation_id == name)
        if kind == "territory":
            return tuple(d for d in self.devices if d.territory == name)
        if kind == "partition":
            return tuple(d for d in self.devices if d.partition_id == name)
        if kind == "feeder":
            return tuple(d for d in self.devices if d.feeder_id == name)
        raise ValueError(f"unknown scope {scope!r}")


def build_topology(cfg: Config = DEFAULTS) -> Topology:
    devices: list[Device] = []
    feeders: list[Feeder] = []

    per_region = cfg.n_devices_a1 // cfg.n_regions_a1          # 80
    n_small = round(per_region * cfg.small_share)              # 56 of 80

    # --- A1 / T1: the ADER aggregation that holds the award
    for r in range(cfg.n_regions_a1):
        region_id, feeder_id = f"R{r + 1}", f"F{r + 1}"
        region_p_max = 0.0
        for j in range(per_region):
            small = j < n_small
            dev = Device(
                device_id=f"A1-{r + 1}-{j:03d}",
                region_id=region_id,
                feeder_id=feeder_id,
                load_zone="LZ_SOUTH",
                territory="T1",
                e_max_kwh=cfg.unit_small_kwh if small else cfg.unit_large_kwh,
                p_max_kw=cfg.unit_small_kw if small else cfg.unit_large_kw,
                floor_frac=cfg.floor_frac,
                aux_kw=cfg.aux_kw,
                safe_hold_behavior=SafeHoldBehavior.IDLE,
                aggregation_id="A1",
            )
            devices.append(dev)
            region_p_max += dev.p_max_kw
        feeders.append(Feeder(feeder_id=feeder_id, region_id=region_id,
                              export_cap_kw=cfg.feeder_cap_frac * region_p_max))

    # --- T2 / T3: kWh the aggregation cannot use. Idle in S1, and that is the point.
    rest = cfg.n_devices_total - cfg.n_devices_a1
    for k in range(rest):
        terr = "T2" if k < rest // 2 else "T3"
        region_id = f"{terr}-R{k % 3 + 1}"
        feeder_id = f"{terr}-F{k % 3 + 1}"
        small = (k % 10) < round(10 * cfg.small_share)
        devices.append(Device(
            device_id=f"{terr}-{k:04d}",
            region_id=region_id,
            feeder_id=feeder_id,
            load_zone="LZ_SOUTH",
            territory=terr,
            e_max_kwh=cfg.unit_small_kwh if small else cfg.unit_large_kwh,
            p_max_kw=cfg.unit_small_kw if small else cfg.unit_large_kw,
            floor_frac=cfg.floor_frac,
            aux_kw=cfg.aux_kw,
            aggregation_id=None,
        ))
    for terr in ("T2", "T3"):
        for i in (1, 2, 3):
            fid = f"{terr}-F{i}"
            p = sum(d.p_max_kw for d in devices if d.feeder_id == fid)
            feeders.append(Feeder(feeder_id=fid, region_id=f"{terr}-R{i}",
                                  export_cap_kw=cfg.feeder_cap_frac * p))

    a1_ids = tuple(d.device_id for d in devices if d.aggregation_id == "A1")
    aggregations = (Aggregation(aggregation_id="A1", load_zone="LZ_SOUTH", lse="BASE",
                                dsp="DSP_SOUTH", device_ids=a1_ids),)
    return Topology(tuple(devices), tuple(feeders), aggregations)
