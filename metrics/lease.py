"""Lease-cost figures: msgs and bytes per device-day. SPEC §6.6 / ASSUMPTIONS.md §5.

Every telemetry tick costs one message (`cfg.t_tel_s` apart). When leases piggyback on
telemetry (`cfg.lease_piggyback`), that is the whole bill. When they do not, each device also
sends a standalone heartbeat every `HEARTBEAT_S` seconds, priced at `cfg.msg_ack_bytes` each,
on top of the telemetry traffic.
"""
from __future__ import annotations

from config import DEFAULTS, Config

HEARTBEAT_S = 20.0
SECONDS_PER_DAY = 86400.0


def lease_cost(cfg: Config = DEFAULTS) -> dict[str, float]:
    msgs = SECONDS_PER_DAY / cfg.t_tel_s
    byte_count = msgs * cfg.msg_telemetry_bytes
    if not cfg.lease_piggyback:
        hb = SECONDS_PER_DAY / HEARTBEAT_S
        msgs += hb
        byte_count += hb * cfg.msg_ack_bytes
    return {
        "msgs_per_device_day": float(msgs),
        "bytes_per_device_day": float(byte_count),
    }
