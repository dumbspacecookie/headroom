"""(epoch, seq) command fencing. SPEC §5/6.5: a device applies a setpoint command only if its
(epoch, seq) is newer than the last one it applied; a stale-epoch command is rejected with
AckReason.STALE_EPOCH and leaves truth untouched. This is the fencing the crash-recovery path
(SPEC 6.5: "epoch += 1 ... devices reject stale-epoch commands") relies on; no crash yet.
"""
from __future__ import annotations

import numpy as np

from config import DEFAULTS
from contracts.types import AckReason, Command
from sim.fleet import apply_command, build_fleet
from sim.topology import build_topology

CFG = DEFAULTS


def _one():
    """A single-device fleet, isolated from the 1,000-device default so a test cannot be
    confused by another device's state."""
    topo = build_topology(CFG)
    fs = build_fleet(topo, CFG)
    for name in ("soc_kwh", "p_actual_kw", "setpoint_kw", "lease_expiry_ts", "last_epoch",
                 "last_seq", "e_max", "p_max", "floor_kwh", "aux_kw"):
        setattr(fs, name, getattr(fs, name)[:1])
    for name in ("mode", "grid_state", "guard_on"):
        setattr(fs, name, getattr(fs, name)[:1])
    device_id = topo.devices[0].device_id
    return fs, device_id


def _cmd(device_id: str, epoch: int, seq: int, setpoint_kw: float) -> Command:
    return Command(device_id=device_id, epoch=epoch, seq=seq, setpoint_kw=setpoint_kw,
                   issued_ts=0.0, lease_s=CFG.lease_s)


# REQ: HEADROOM-0930-S1A/AC-1
def test_stale_epoch_is_rejected_and_mutates_nothing_then_current_epoch_is_accepted():
    """A fleet whose epoch has already been bumped (crash recovery, SPEC 6.5) must reject a
    command still carrying the old epoch - AckReason.STALE_EPOCH - and must not move `setpoint`
    or `soc`. A command at the *current* epoch, with a newer seq, is accepted normally."""
    fs, device_id = _one()
    fs.last_epoch[0] = 3
    fs.last_seq[0] = 5
    fs.setpoint_kw[0] = 1.5
    fs.soc_kwh[0] = 12.0
    setpoint_before = fs.setpoint_kw.copy()
    soc_before = fs.soc_kwh.copy()

    stale = _cmd(device_id, epoch=2, seq=99, setpoint_kw=7.0)
    ack = apply_command(fs, 0, stale, now=100.0)

    assert ack.reason == AckReason.STALE_EPOCH
    assert np.array_equal(fs.setpoint_kw, setpoint_before)
    assert np.array_equal(fs.soc_kwh, soc_before)
    # stale rejection must not even touch the device's bookkeeping of what it last applied
    assert fs.last_epoch[0] == 3
    assert fs.last_seq[0] == 5

    current = _cmd(device_id, epoch=3, seq=6, setpoint_kw=7.0)
    ack2 = apply_command(fs, 0, current, now=101.0)

    assert ack2.reason == AckReason.OK
    assert fs.setpoint_kw[0] == 7.0
    assert fs.last_epoch[0] == 3
    assert fs.last_seq[0] == 6
    # applying a command never touches soc - only `step()` does
    assert np.array_equal(fs.soc_kwh, soc_before)


# REQ: HEADROOM-0930-S1A/AC-2
def test_within_one_epoch_only_a_newer_seq_is_applied():
    """Within the same epoch, a duplicate or reordered (not-newer) seq must be ignored; a
    strictly newer seq is applied."""
    fs, device_id = _one()
    fs.last_epoch[0] = 1
    fs.last_seq[0] = 10
    fs.setpoint_kw[0] = 2.0
    setpoint_before = fs.setpoint_kw.copy()
    soc_before = fs.soc_kwh.copy()

    duplicate = _cmd(device_id, epoch=1, seq=10, setpoint_kw=9.0)
    ack_dup = apply_command(fs, 0, duplicate, now=200.0)
    assert ack_dup.reason != AckReason.OK
    assert np.array_equal(fs.setpoint_kw, setpoint_before)
    assert fs.last_seq[0] == 10

    reordered = _cmd(device_id, epoch=1, seq=4, setpoint_kw=9.0)
    ack_reorder = apply_command(fs, 0, reordered, now=201.0)
    assert ack_reorder.reason != AckReason.OK
    assert np.array_equal(fs.setpoint_kw, setpoint_before)
    assert fs.last_seq[0] == 10
    assert np.array_equal(fs.soc_kwh, soc_before)

    newer = _cmd(device_id, epoch=1, seq=11, setpoint_kw=9.0)
    ack_newer = apply_command(fs, 0, newer, now=202.0)
    assert ack_newer.reason == AckReason.OK
    assert fs.setpoint_kw[0] == 9.0
    assert fs.last_seq[0] == 11


# REQ: HEADROOM-1009-S2/AC-1
# REQ: HEADROOM-1009-S2/AC-2
def test_first_zero_zero_command_is_accepted_then_repeat_is_dup():
    """A fresh fleet has never applied a command, so the device's very first (epoch=0, seq=0)
    command must be accepted (AC-1) - the -1 sentinel, not a real (0, 0), is what build_fleet
    starts from. Once that first command has landed, a repeat of the same (0, 0) is a true
    duplicate and must be rejected as DUP, mutating nothing (AC-2)."""
    fs, device_id = _one()

    first = _cmd(device_id, epoch=0, seq=0, setpoint_kw=4.0)
    ack_first = apply_command(fs, 0, first, now=0.0)

    assert ack_first.reason == AckReason.OK
    assert fs.setpoint_kw[0] == 4.0
    assert fs.last_epoch[0] == 0
    assert fs.last_seq[0] == 0

    setpoint_after_first = fs.setpoint_kw.copy()
    epoch_after_first = fs.last_epoch.copy()
    seq_after_first = fs.last_seq.copy()
    soc_after_first = fs.soc_kwh.copy()

    repeat = _cmd(device_id, epoch=0, seq=0, setpoint_kw=9.0)
    ack_repeat = apply_command(fs, 0, repeat, now=1.0)

    assert ack_repeat.reason == AckReason.DUP
    assert np.array_equal(fs.setpoint_kw, setpoint_after_first)
    assert np.array_equal(fs.last_epoch, epoch_after_first)
    assert np.array_equal(fs.last_seq, seq_after_first)
    assert np.array_equal(fs.soc_kwh, soc_after_first)


# REQ: HEADROOM-1009-S2/AC-3
def test_first_command_at_nonzero_epoch_and_seq_is_also_accepted():
    """The sentinel initial state must accept any epoch>=0, seq>=0 first command, not only
    (0, 0): a device whose first command happens to arrive at (epoch=5, seq=7) is accepted."""
    fs, device_id = _one()

    first = _cmd(device_id, epoch=5, seq=7, setpoint_kw=3.0)
    ack = apply_command(fs, 0, first, now=0.0)

    assert ack.reason == AckReason.OK
    assert fs.last_epoch[0] == 5
    assert fs.last_seq[0] == 7


# REQ: HEADROOM-1009-S2/AC-4
def test_already_applied_device_rejects_stale_dup_stale_seq_and_accepts_newer():
    """Once a device has applied a concrete (epoch, seq), the full fencing matrix holds: a
    strictly lower epoch is STALE_EPOCH, the same (epoch, seq) is DUP, the same epoch with a
    strictly lower seq is STALE_SEQ, and a strictly newer (epoch, seq) is OK. Each rejection
    leaves setpoint_kw, last_epoch and last_seq untouched."""
    fs, device_id = _one()

    established = _cmd(device_id, epoch=5, seq=7, setpoint_kw=2.0)
    ack0 = apply_command(fs, 0, established, now=0.0)
    assert ack0.reason == AckReason.OK

    setpoint_before = fs.setpoint_kw.copy()
    epoch_before = fs.last_epoch.copy()
    seq_before = fs.last_seq.copy()

    stale_epoch = _cmd(device_id, epoch=4, seq=99, setpoint_kw=8.0)
    ack_stale_epoch = apply_command(fs, 0, stale_epoch, now=1.0)
    assert ack_stale_epoch.reason == AckReason.STALE_EPOCH
    assert np.array_equal(fs.setpoint_kw, setpoint_before)
    assert np.array_equal(fs.last_epoch, epoch_before)
    assert np.array_equal(fs.last_seq, seq_before)

    dup = _cmd(device_id, epoch=5, seq=7, setpoint_kw=8.0)
    ack_dup = apply_command(fs, 0, dup, now=2.0)
    assert ack_dup.reason == AckReason.DUP
    assert np.array_equal(fs.setpoint_kw, setpoint_before)
    assert np.array_equal(fs.last_epoch, epoch_before)
    assert np.array_equal(fs.last_seq, seq_before)

    stale_seq = _cmd(device_id, epoch=5, seq=6, setpoint_kw=8.0)
    ack_stale_seq = apply_command(fs, 0, stale_seq, now=3.0)
    assert ack_stale_seq.reason == AckReason.STALE_SEQ
    assert np.array_equal(fs.setpoint_kw, setpoint_before)
    assert np.array_equal(fs.last_epoch, epoch_before)
    assert np.array_equal(fs.last_seq, seq_before)

    newer = _cmd(device_id, epoch=5, seq=8, setpoint_kw=8.0)
    ack_newer = apply_command(fs, 0, newer, now=4.0)
    assert ack_newer.reason == AckReason.OK
    assert fs.setpoint_kw[0] == 8.0
    assert fs.last_epoch[0] == 5
    assert fs.last_seq[0] == 8
