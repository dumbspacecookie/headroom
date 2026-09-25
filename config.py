"""Every [A] knob, in one place. This file IS `ASSUMPTIONS.md`, in a form the code can read.

If a value here disagrees with `ASSUMPTIONS.md`, that is a bug in one of them - `tests/unit/
test_config_matches_assumptions.py` asserts the headline ones agree.

NOTHING HERE MOVES DURING THE EVENT. If a knob has to move to make a result appear, that is the
result (SPEC §15).
"""
from __future__ import annotations

from dataclasses import dataclass, field


def ct(hh: int, mm: int = 0) -> float:
    """Seconds from midnight CT on the scenario day. Readable in logs and events."""
    return hh * 3600.0 + mm * 60.0


@dataclass(frozen=True)
class Config:
    # ---- device and fleet physics (ASSUMPTIONS.md §1)
    unit_small_kwh: float = 39.2
    unit_large_kwh: float = 78.4
    unit_small_kw: float = 11.5
    unit_large_kw: float = 23.0          # 0.7*11.5 + 0.3*23.0 = 14.95 kW avg
    small_share: float = 0.70
    floor_frac: float = 0.20
    aux_kw: float = 0.050
    home_load_p95_kw: float = 4.0
    eta: float = 1.0                     # Must scope is lossless, and says so

    # ---- estimator (§2)
    eps_frac: float = 0.01               # of e_max
    reach_k: float = 5.0                 # reachable if age <= k * t_tel
    assume_house_when_grid_unknown: bool = False
    haircut: str = "n_minus_1"           # independent | n_minus_1 | both
    outage_h: float = 0.5                # for the N-1 kWh reserve
    # Comparison knobs (RATIONALE.md s8). Defaults are headroom exactly; only runner/compare.py
    # moves them, to build `reasonable_plus` and the ablations out of the same ledger.
    band: bool = True                    # False = promise from the reported SoC as a point
    point_eps: bool = False              # with band=False: keep only the fixed eps margin
    # Ledger stage 2 (SPEC 6.3b). Stated per controller, never inferred from `haircut`: the one
    # time it was inferred, it ran inside the oracle and moved the ceiling (RATIONALE.md s6c).
    #   n1  - every CAPACITY hold survives losing any one region, per device (headroom)
    #   n0  - every hold is deliverable per device, no region removed (the oracle: no reserve,
    #         but it may not count empty batteries as power either)
    #   off - no stage 2 (the flat de-rate baselines and the -N-1 ablation)
    deliverability: str = "n1"
    flat_derate_frac: float = 0.0        # fixed share of KW and E0 held back, industry-style
    # How many regions the reserve covers (RATIONALE.md s6d). "n1": one, always (headroom).
    # "p90": per bucket, as many as keep P(more go dark) <= p90_alpha, from an outage-odds table
    # built by runner/p90.py at p90_calib_rate x the fault model's rate. Empty table = not built.
    reserve_rule: str = "n1"
    p90_alpha: float = 0.10
    p90_calib_rate: float = 1.0
    p90_table: tuple = ()

    # ---- ledger (§3)
    bucket_s: float = 300.0              # 5 min
    hysteresis_frac: float = 0.05
    hysteresis_hold_s: float = 60.0
    tolerance_frac: float = 0.05
    feeder_cap_frac: float = 0.80        # PROXY, loose in S1 on purpose

    # ---- allocator / control loop (§4)
    dt_ctrl_s: float = 10.0
    dt_phys_demo_s: float = 2.0
    dt_phys_batch_s: float = 10.0
    delta_kw: float = 0.5
    pi_kp: float = 0.5
    pi_ki: float = 0.05

    # ---- network and leases (§5)
    t_tel_s: float = 2.0
    lease_s: float = 60.0
    lease_piggyback: bool = True
    flush_msgs_per_s: float = 50.0
    msg_telemetry_bytes: int = 48
    msg_ack_bytes: int = 32

    # ---- scenario S1 (§6)
    n_devices_total: int = 1000
    n_devices_a1: int = 400
    n_regions_a1: int = 5
    start_soc_frac: float = 0.80
    coop_shave_h: float = 3.0
    t0: float = field(default_factory=lambda: ct(15, 0))
    horizon_ts: float = field(default_factory=lambda: ct(21, 30))
    crash_duration_s: float = 90.0

    # ---- priority ladder [A, §18 Q1] - lower number wins
    ladder: tuple[str, ...] = (
        "UTILITY_TOLL", "ADER_AS", "COOP_PEAK", "ADER_ENERGY", "STORM_PRECHARGE", "ARBITRAGE",
    )

    def priority(self, source: str) -> int:
        return self.ladder.index(source) + 1

    @property
    def bucket_h(self) -> float:
        return self.bucket_s / 3600.0


DEFAULTS = Config()
