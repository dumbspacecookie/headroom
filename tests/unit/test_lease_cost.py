"""metrics.lease.lease_cost: SPEC §6.6 / ASSUMPTIONS.md §5 lease-cost figures. HEADROOM-1014-S2."""
from __future__ import annotations

import dataclasses

from config import DEFAULTS
from metrics.lease import HEARTBEAT_S, lease_cost


# REQ: HEADROOM-1014-S2/AC-1
def test_lease_cost_returns_exactly_the_two_keys():
    result = lease_cost(DEFAULTS)
    assert set(result) == {"msgs_per_device_day", "bytes_per_device_day"}


# REQ: HEADROOM-1014-S2/AC-1
def test_lease_cost_on_defaults():
    result = lease_cost(DEFAULTS)
    assert result == {"msgs_per_device_day": 43200.0, "bytes_per_device_day": 2073600.0}


# REQ: HEADROOM-1014-S2/AC-1
def test_lease_cost_with_slower_telemetry():
    cfg = dataclasses.replace(DEFAULTS, t_tel_s=4.0)
    result = lease_cost(cfg)
    assert result == {"msgs_per_device_day": 21600.0, "bytes_per_device_day": 1036800.0}


# REQ: HEADROOM-1014-S2/AC-2
def test_lease_cost_standalone_heartbeat_adds_messages_and_bytes():
    assert HEARTBEAT_S == 20.0
    cfg = dataclasses.replace(DEFAULTS, lease_piggyback=False)
    result = lease_cost(cfg)
    assert result == {"msgs_per_device_day": 47520.0, "bytes_per_device_day": 2211840.0}


# REQ: HEADROOM-1014-S2/AC-2
def test_lease_cost_standalone_heartbeat_priced_at_msg_ack_bytes():
    cfg = dataclasses.replace(DEFAULTS, lease_piggyback=False, msg_ack_bytes=64)
    result = lease_cost(cfg)
    assert result["bytes_per_device_day"] == 2350080.0


# REQ: HEADROOM-1014-S2/AC-2
def test_lease_cost_piggyback_ignores_msg_ack_bytes():
    cfg = dataclasses.replace(DEFAULTS, lease_piggyback=True, msg_ack_bytes=64)
    result = lease_cost(cfg)
    assert result["bytes_per_device_day"] == 2073600.0
