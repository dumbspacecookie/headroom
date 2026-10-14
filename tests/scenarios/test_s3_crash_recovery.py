"""HEADROOM-0930-S1C: S3 part 3 - crash_recovery_s and the S3 scenario.

S3 = S1 + a controller crash at 20:05 CT for `config.crash_duration_s`. The crash mechanism
(epoch bump, log replay, command withhold, fencing) is S1B's and S1A's, unchanged; this spec adds
the `crash_recovery_s` metric (`metrics.score.crash_recovery_s`) and `scenarios/s3.py`'s entry
point that wires the fixed 20:05 crash instant through the existing `runner.run.run_scenario`.
"""
from __future__ import annotations

import math

import pytest

from config import DEFAULTS, ct
from contracts.types import AckReason, Command
from metrics.score import crash_recovery_s
from runner.run import CrashSpec, run_scenario
from scenarios.s3 import CRASH_TS, build_s3_crash, run_s3
from sim.fleet import apply_command, build_fleet
from sim.topology import build_topology


# ---------------------------------------------------------------- AC-1
# REQ: HEADROOM-0930-S1C/AC-1
def test_s3_crash_recovery_s_is_a_finite_float_within_the_loose_bound():
    """run_scenario invoked with the S3 crash spec (S1 + a controller crash at 20:05 CT for
    `cfg.crash_duration_s`) completes and returns a metrics dict whose `crash_recovery_s` is a
    finite float with 0 < crash_recovery_s <= 600 - a loose bound, no tighter figure is specified
    anywhere (owner answer)."""
    result = run_s3()
    recovery = result.metrics["crash_recovery_s"]
    assert isinstance(recovery, float)
    assert math.isfinite(recovery)
    assert 0.0 < recovery <= 600.0
    # Sanity: S3's crash instant really is 20:05 CT, and the run really is tagged S3.
    assert CRASH_TS == ct(20, 5)
    assert result.metrics["scenario"] == "S3"


# REQ: HEADROOM-0930-S1C/AC-1
def test_s3_crash_recovery_s_pinned_value():
    """Pins the measured value as a literal: any later change to recovery behaviour (the crash
    mechanism, the tolerance definition, the recovery search) is visible here, not silently
    absorbed by a loose bound."""
    result = run_s3()
    assert result.metrics["crash_recovery_s"] == pytest.approx(30.0)


# REQ: HEADROOM-0930-S1C/AC-1
def test_crash_recovery_s_measured_from_the_raw_restart_boundary_not_the_tick():
    """'Crash end' is `crash_ts + crash_duration_s`, never snapped to the tick grid. A crash
    that ends 10 s before a tick where delivery is already back in tolerance must report that
    10 s, not 0 and not a whole tick width."""
    crash = CrashSpec(crash_ts=ct(20, 5), crash_duration_s=50.0)   # boundary at 72350, tick 72360
    result = run_scenario("headroom", crash=crash)
    recovery = crash_recovery_s(result.frames, result.crash)
    assert recovery is not None
    boundary = result.crash["crash_ts"] + result.crash["crash_duration_s"]
    first_recovered_tick = boundary + recovery
    assert first_recovered_tick % 60.0 == pytest.approx(0.0, abs=1e-6), (
        "the recovered tick should fall on the 60 s tick grid even though the boundary does not")
    assert recovery not in (0.0, 60.0), "recovery should be the raw, un-snapped gap"


# REQ: HEADROOM-0930-S1C/AC-1
def test_crash_recovery_s_none_when_crash_is_none():
    """No crash, nothing to measure."""
    result = run_scenario("headroom")
    assert crash_recovery_s(result.frames, result.crash) is None
    assert result.crash is None


# ---------------------------------------------------------------- AC-2
# REQ: HEADROOM-0930-S1C/AC-2
def test_booked_zero_tick_with_stale_power_never_counts_as_recovered():
    """A crash whose down window ends as ADER_ENERGY's window closes (20:00-21:00): the first
    tick after restart has booked_kw == 0 (the claim's window has already closed) while the
    device is still physically delivering the stale pre-crash setpoint (~763 kW, run 1's probe)
    because commands are withheld right at restart. That tick must never be read as "recovered",
    and - since nothing is ever booked again before the scenario ends - crash_recovery_s must be
    None altogether rather than opportunistically reporting the stale-power tick."""
    crash = CrashSpec(crash_ts=ct(20, 59), crash_duration_s=90.0)
    result = run_scenario("headroom", crash=crash)
    boundary = result.crash["crash_ts"] + result.crash["crash_duration_s"]
    restart_ts = result.crash["restart_ts"]
    assert restart_ts > boundary

    by_ts = {f["ts"]: f for f in result.frames}
    stale_frame = by_ts[restart_ts]
    # The premise this test depends on: booked has dropped to zero (window closed) while truth
    # is still delivering substantial stale power.
    assert stale_frame["booked_kw"] == 0.0
    assert stale_frame["delivered_truth_kw"] > 500.0, (
        "expected stale pre-crash power still flowing at the restart tick - test picked a bad "
        "crash window if this fails")

    # No later tick ever has booked_kw > 0 either (ADER_ENERGY was the only live ENERGY claim
    # and its window is over) - so there is nothing for crash_recovery_s to honestly report.
    assert all(f["booked_kw"] <= 0.0 for f in result.frames if f["ts"] > boundary)
    assert crash_recovery_s(result.frames, result.crash) is None


# REQ: HEADROOM-0930-S1C/AC-2
def test_crash_recovery_s_skips_a_synthetic_booked_zero_frame_directly():
    """Unit-level pin on `crash_recovery_s` itself, independent of a full run: a naive one-sided
    `delivered_kw >= (1 - tolerance_frac) * booked_kw` check is trivially true whenever
    `booked_kw <= 0`. Feeding the function a booked-zero frame with large delivered power, ahead
    of a later genuinely-recovered frame, must skip the former and report the latter."""
    crash = {"crash_ts": 1000.0, "crash_duration_s": 90.0}   # boundary at 1090
    frames = [
        {"ts": 1030.0, "booked_kw": 0.0, "delivered_truth_kw": 763.0},     # before boundary: N/A
        {"ts": 1120.0, "booked_kw": 0.0, "delivered_truth_kw": 763.0},     # stale, booked == 0
        {"ts": 1180.0, "booked_kw": 100.0, "delivered_truth_kw": 96.0},    # genuinely recovered
    ]
    assert crash_recovery_s(frames, crash) == pytest.approx(1180.0 - 1090.0)

    # And if no real recovery ever follows, None - not the stale tick.
    assert crash_recovery_s(frames[:2], crash) is None


# ---------------------------------------------------------------- AC-3
# REQ: HEADROOM-0930-S1C/AC-3
def test_no_recorded_command_with_a_stale_epoch_is_ever_applied_after_restart():
    """Scans every command the physics layer actually applied during an S3 run: none carries an
    epoch older than the epoch the restart bumped to while being applied after the restart."""
    crash = build_s3_crash()
    result = run_scenario("headroom", crash=crash)
    epoch_after = result.crash["epoch_after"]
    restart_ts = result.crash["restart_ts"]
    assert result.commands, "no command was ever recorded - nothing to check"

    stale_applied_post_restart = [
        c for c in result.commands
        if c["ts"] > restart_ts and c["epoch"] < epoch_after and c["reason"] == "OK"
    ]
    assert stale_applied_post_restart == [], (
        f"{len(stale_applied_post_restart)} stale-epoch command(s) were applied after restart: "
        f"{stale_applied_post_restart[:3]}")


# REQ: HEADROOM-0930-S1C/AC-3
def test_an_explicit_stale_epoch_command_is_rejected_and_leaves_state_untouched():
    """`run_scenario`'s own tick loop never issues a post-restart command carrying a stale epoch
    (every command after the bump uniformly carries `cmd_epoch`), so scanning `result.commands`
    alone (the test above) would pass vacuously without ever exercising the rejection path. This
    test builds an explicit stale-epoch `Command` - reusing the current epoch recorded in
    `result.crash` - and feeds it through `sim.fleet.apply_command` directly against a
    post-run-shaped `FleetState`, to actually exercise `AckReason.STALE_EPOCH` and the
    unchanged-setpoint/soc guarantee."""
    crash = build_s3_crash()
    result = run_scenario("headroom", crash=crash)
    epoch_after = result.crash["epoch_after"]
    epoch_before = result.crash["epoch_before"]
    restart_ts = result.crash["restart_ts"]

    post_restart_ok = [c for c in result.commands
                      if c["ts"] > restart_ts and c["reason"] == "OK"]
    assert post_restart_ok, "no post-restart command was ever accepted - nothing to model"
    device_id = post_restart_ok[0]["device_id"]
    last_seq_applied = max(c["seq"] for c in post_restart_ok if c["device_id"] == device_id)

    topo = build_topology(DEFAULTS)
    idx = {d.device_id: i for i, d in enumerate(topo.devices)}
    i = idx[device_id]

    # A FleetState shaped like this device's state at the end of the run: fenced at the fleet's
    # current (post-restart) epoch/seq, with recognizable setpoint/soc sentinels to prove they
    # do not move.
    fleet = build_fleet(topo, DEFAULTS)
    fleet.last_epoch[i] = epoch_after
    fleet.last_seq[i] = last_seq_applied
    fleet.setpoint_kw[i] = 42.0
    fleet.soc_kwh[i] = 123.0

    stale_cmd = Command(device_id=device_id, epoch=epoch_before, seq=last_seq_applied + 1,
                        setpoint_kw=999.0, issued_ts=restart_ts, lease_s=DEFAULTS.lease_s)
    assert stale_cmd.epoch < epoch_after, "the command must actually be stale to test anything"
    ack = apply_command(fleet, i, stale_cmd, now=restart_ts)

    assert ack.reason == AckReason.STALE_EPOCH
    assert fleet.setpoint_kw[i] == 42.0
    assert fleet.soc_kwh[i] == 123.0
    assert int(fleet.last_epoch[i]) == epoch_after
    assert int(fleet.last_seq[i]) == last_seq_applied
