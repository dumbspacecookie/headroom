"""Headroom data contracts. SPEC §5, written once, FROZEN at H2.

After `git tag contracts-v1` nothing in this package changes. `tests/boundary/test_contracts_hash.py`
enforces it. If a lane needs a field that is not here, it says so in its handback and ash decides;
a lane never edits this file.

Units are in the field names or stated in a comment. There are no bare numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------- enums (§5)
class LinkState(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class GridState(str, Enum):
    """Per feeder. An independent fault from LinkState - never couple them."""
    UP = "UP"
    OUTAGE = "OUTAGE"


class DeviceMode(str, Enum):
    GRID = "GRID"
    SAFE_HOLD = "SAFE_HOLD"
    ISLANDED = "ISLANDED"


class SafeHoldBehavior(str, Enum):
    """[G] guess about Base internals; default IDLE, swept. ASSUMPTIONS.md §1."""
    IDLE = "IDLE"
    SELF_CONSUME = "SELF_CONSUME"


class Product(str, Enum):
    ENERGY = "ENERGY"
    CAPACITY = "CAPACITY"


class ClaimSource(str, Enum):
    COOP_PEAK = "COOP_PEAK"
    UTILITY_TOLL = "UTILITY_TOLL"
    ADER_AS = "ADER_AS"
    ADER_ENERGY = "ADER_ENERGY"
    STORM_PRECHARGE = "STORM_PRECHARGE"
    ARBITRAGE = "ARBITRAGE"


class Hardness(str, Enum):
    FIRM = "FIRM"
    SOFT = "SOFT"


class BookingStatus(str, Enum):
    PENDING = "PENDING"
    ADMITTED = "ADMITTED"
    PARTIAL = "PARTIAL"
    DOWNGRADED = "DOWNGRADED"
    PREEMPTED = "PREEMPTED"
    REJECTED = "REJECTED"
    DEPLOYED = "DEPLOYED"
    COMPLETE = "COMPLETE"
    BROKEN = "BROKEN"


class AckReason(str, Enum):
    OK = "OK"
    DUP = "DUP"
    STALE_SEQ = "STALE_SEQ"
    STALE_EPOCH = "STALE_EPOCH"
    FLOOR_GUARD = "FLOOR_GUARD"
    LEASE_EXPIRED = "LEASE_EXPIRED"


class BindingReason(str, Enum):
    """Which term of §6.3 decided the admission. Shown in the UI; asserted by D1."""
    NONE = "none"
    KW_ROOM = "kw_room"
    KWH_SLACK = "kwh_slack"
    FEEDER = "feeder"
    RESERVE = "reserve"


class FreshnessBand(str, Enum):
    FRESH = "FRESH"
    AGING = "AGING"
    STALE = "STALE"
    LEASE_EXPIRED = "LEASE_EXPIRED"


# ---------------------------------------------------------------- static topology (§5)
@dataclass(frozen=True)
class Firmware:
    lease: bool = True
    seq_check: bool = True
    guard: bool = True          # floor guard; ablated OFF to exercise I2


@dataclass(frozen=True)
class Device:
    device_id: str
    region_id: str              # cell-tower region; the N-1 reserve object
    feeder_id: str
    load_zone: str
    territory: str
    e_max_kwh: float
    p_max_kw: float
    floor_frac: float
    aux_kw: float
    safe_hold_behavior: SafeHoldBehavior = SafeHoldBehavior.IDLE
    aggregation_id: str | None = None
    partition_id: str | None = None
    firmware: Firmware = field(default_factory=Firmware)

    @property
    def floor_kwh(self) -> float:
        return self.floor_frac * self.e_max_kwh


@dataclass(frozen=True)
class Feeder:
    feeder_id: str
    region_id: str
    export_cap_kw: float        # PROXY for distribution limits, not power flow


@dataclass(frozen=True)
class Aggregation:
    """ADER scope. ALR model: one load zone, same LSE and DSP. [S]"""
    aggregation_id: str
    load_zone: str
    lse: str
    dsp: str
    device_ids: tuple[str, ...]


@dataclass(frozen=True)
class Partition:
    partition_id: str
    owner: str
    reserved_kw: float
    max_deploy_h: float
    device_ids: tuple[str, ...] = ()
    territory: str | None = None


# ---------------------------------------------------------------- sim truth (§5)
# NEVER sent to control. control/ may not import sim/ (tests/boundary).
@dataclass
class DeviceTruth:
    device_id: str
    soc_kwh: float
    p_actual_kw: float          # >0 discharge
    mode: DeviceMode
    setpoint_kw: float
    lease_expiry_ts: float
    last_epoch: int
    last_seq: int
    home_load_kw: float
    grid_state: GridState
    link_state: LinkState
    clock_skew_s: float = 0.0
    sf_buffer: int = 0          # store-and-forward depth, messages


# ---------------------------------------------------------------- uplink / downlink (§5)
@dataclass(frozen=True)
class Telemetry:
    device_id: str
    seq: int
    device_ts: float
    soc_kwh: float
    p_kw: float
    home_load_kw: float
    mode: DeviceMode
    last_applied_epoch: int
    last_applied_seq: int
    buffered: bool = False
    recv_ts: float | None = None        # stamped by the network, not the device


@dataclass(frozen=True)
class Ack:
    device_id: str
    cmd_epoch: int
    cmd_seq: int
    reason: AckReason
    device_ts: float
    recv_ts: float | None = None


@dataclass(frozen=True)
class Command:
    device_id: str
    epoch: int
    seq: int
    setpoint_kw: float
    issued_ts: float
    lease_s: float
    booking_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaseRenew:
    """Piggybacks on the telemetry ack - 0 extra messages. ASSUMPTIONS.md §5."""
    device_id: str
    epoch: int
    lease_expiry_ts: float


# ---------------------------------------------------------------- ledger (§5)
@dataclass(frozen=True)
class Claim:
    claim_id: str
    source: ClaimSource
    product: Product
    priority: int               # lower wins; the ladder is config (§18 Q1)
    hardness: Hardness
    scope: str                  # "fleet" | "aggregation:A" | "territory:Y" | "partition:P" | "feeder:Z"
    start_ts: float
    end_ts: float
    power_kw: float             # > 0, discharge. Must scope is discharge-only.
    tolerance_frac: float
    created_ts: float
    max_deploy_h: float = 0.0   # CAPACITY only
    price_ref: float | None = None

    @property
    def duration_h(self) -> float:
        return (self.end_ts - self.start_ts) / 3600.0

    @property
    def requested_kwh(self) -> float:
        return self.power_kw * self.duration_h


@dataclass
class Booking:
    booking_id: str
    claim_id: str
    bucket_ts: float            # bucket END
    requested_kw: float
    admitted_kw: float
    hold_kw: float
    reserved_kwh: float
    consumed_kwh: float
    status: BookingStatus
    reason: BindingReason
    kw_room_at_decision: float
    kwh_slack_at_decision: float
    decided_ts: float


@dataclass(frozen=True)
class Deployment:
    """Should tier. CAPACITY holds are not called in the Must scope."""
    deployment_id: str
    booking_id: str
    start_ts: float
    end_ts: float
    kw: float


@dataclass(frozen=True)
class Notice:
    notice_id: str
    booking_id: str
    ts: float
    old_kw: float
    new_kw: float
    lead_time_s: float
    cause: str                  # plain words; names the binding term AND what consumed it


# ---------------------------------------------------------------- controller belief (§5)
@dataclass
class Estimate:
    device_id: str
    recv_age_s: float
    soc_low_kwh: float
    soc_high_kwh: float
    reachable: bool
    lease_live: bool
    load_term_on: bool
    kw_cap: float
    kwh_above_floor_low: float
    region_id: str
    feeder_id: str
    aggregation_id: str | None
    earmark_kw: float = 0.0
    earmark_kwh: float = 0.0


# ---------------------------------------------------------------- log / frames / metrics (§5)
@dataclass(frozen=True)
class Event:
    ts: float
    seq: int
    kind: str
    controller: str
    payload: dict
    text: str                   # one sentence, names the binding term and what consumed it


@dataclass
class Frame:
    ts: float
    controller: str
    booked_kw: float
    delivered_truth_kw: float
    belief_low_kw: float
    belief_high_kw: float
    kw_room: float
    energy_left_low_kwh: float
    energy_left_truth_kwh: float
    committed_future_kwh: dict[str, float]      # stacked by claim_id
    oracle_kw_room: float
    floor_breach_dev_s: float
    feeder_breach_kw_s: float
    silent_breaches: int
    notices: int
    region_freshness: dict[str, FreshnessBand]
    log_seq_range: tuple[int, int]


@dataclass
class Metrics:
    scenario: str
    seed: int
    controller: str
    ablation: str
    promise_kept_pct: float
    capacity_availability_pct: float
    committed_kwh: float
    delivered_kwh: float
    # §10 v0.4: silent_breach_buckets = energy + capacity, and all three are reported.
    # On S1 reasonable's ENERGY promise is ~100%; the breach is the CAPACITY kind.
    silent_breach_buckets: int
    silent_energy_buckets: int
    silent_capacity_buckets: int
    late_breach_buckets: int
    downgrades: int
    median_lead_time_s: float
    flap_count: int
    floor_breach_dev_s: float
    feeder_breach_kw_s: float
    crash_recovery_s: float
    capacity_held_back_vs_oracle_pct: float
    msgs_per_device_day: float
    bytes_per_device_day: float
    revenue_indicative_usd: float
    runtime_s: float
    events_sha256: str
