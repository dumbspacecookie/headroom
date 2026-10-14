"""HEADROOM-0930-S1B: S3 part 2 - the controller crash window.

One scripted crash: the controller issues nothing while down, bumps its epoch by exactly 1 on
restart, rebuilds its ledger by replaying the retained append-only `events` log (not by reusing
the in-memory `admissions` list), and withholds commands until `reach_k * t_tel_s` of fresh
telemetry has elapsed. `crash=None` (every existing caller) must stay a complete no-op.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from config import DEFAULTS, ct
from control.admission import bucket_ends
from runner.run import CrashSpec, _admission_from_dict, _replay_admissions, run_scenario

T = ct(20, 0)   # tick-aligned: (T - s1.t0) is a whole number of 60 s ticks (and of 300 s buckets)


# ---------------------------------------------------------------- AC-1
# REQ: HEADROOM-0930-S1B/AC-1
def test_no_command_is_issued_on_down_ticks():
    """Every tick with crash_ts < t <= crash_ts + crash_duration_s is down: nothing is sent."""
    crash = CrashSpec(crash_ts=T, crash_duration_s=90.0)
    result = run_scenario("headroom", crash=crash)
    during_crash = [c for c in result.commands
                    if crash.crash_ts < c["ts"] <= crash.restart_boundary_ts]
    assert during_crash == [], (
        f"{len(during_crash)} command(s) issued while the controller was down: {during_crash[:3]}"
    )


# REQ: HEADROOM-0930-S1B/AC-1
def test_epoch_increases_by_exactly_one_across_the_crash():
    crash = CrashSpec(crash_ts=T, crash_duration_s=90.0)
    result = run_scenario("headroom", crash=crash)
    assert result.crash["epoch_after"] - result.crash["epoch_before"] == 1

    pre_epochs = {c["epoch"] for c in result.commands if c["ts"] <= crash.crash_ts}
    post_epochs = {c["epoch"] for c in result.commands if c["ts"] > crash.restart_boundary_ts}
    assert pre_epochs == {result.crash["epoch_before"]}
    assert post_epochs == {result.crash["epoch_after"]}


# REQ: HEADROOM-0930-S1B/AC-1
@pytest.mark.parametrize("crash_ts,crash_duration_s,expected_restart_ts", [
    (T, 90.0, T + 120.0),
    (T, 30.0, T + 60.0),
    (T + 45.0, 30.0, T + 120.0),
])
def test_restart_ts_matches_the_owners_tick_convention(crash_ts, crash_duration_s,
                                                        expected_restart_ts):
    """Owner answer 2026-10-01: restart is the first tick t > crash_ts + crash_duration_s, even
    when no tick fell inside (crash_ts, crash_ts + crash_duration_s] at all (the 30 s case)."""
    crash = CrashSpec(crash_ts=crash_ts, crash_duration_s=crash_duration_s)
    result = run_scenario("headroom", crash=crash)
    assert result.crash["restart_ts"] == pytest.approx(expected_restart_ts)


# REQ: HEADROOM-0930-S1B/AC-1
def test_crash_covering_the_first_tick_does_not_crash_the_run():
    """A crash window that swallows the scenario's very first tick (crash_ts == s1.t0, duration
    just over one tick) must not raise: `belief`/`estimates`/`dark_count` are read by the
    per-tick frame builder on every tick, including the first, and AC-1 only promises that a down
    tick issues no command - not that the run blows up. Adversary-found (run 2)."""
    crash = CrashSpec(crash_ts=DEFAULTS.t0, crash_duration_s=70.0)
    result = run_scenario("headroom", crash=crash)
    assert result is not None
    assert result.frames, "expected at least one recorded frame"
    first_tick_ts = DEFAULTS.t0 + 60.0
    during_crash = [c for c in result.commands if c["ts"] == first_tick_ts]
    assert during_crash == []


# REQ: HEADROOM-0930-S1B/AC-1
def test_crash_starting_before_scenario_start_does_not_crash_the_run():
    """A crash window beginning before `s1.t0` (the controller was already down when the
    scenario's evening starts) still makes the first simulated tick a down tick per AC-1's own
    definition (crash_ts < t <= crash_ts + crash_duration_s). The run must still complete."""
    crash = CrashSpec(crash_ts=DEFAULTS.t0 - 60.0, crash_duration_s=200.0)
    result = run_scenario("headroom", crash=crash)
    assert result is not None
    assert result.frames


# ---------------------------------------------------------------- AC-2
# REQ: HEADROOM-0930-S1B/AC-2
def test_replay_is_a_true_log_replay_not_in_memory_reuse():
    """`_replay_admissions` is fed a hand-built events list - no live Ledger, no Admission
    object it did not itself construct from the dicts below - and must still reconstruct the
    right numbers. This is what makes the restart path in `run_scenario` a REPLAY rather than a
    reuse of the in-process `admissions` variable."""
    from contracts.types import Product

    snapshot = {
        "ts": 1000.0, "kind": "ledger_snapshot", "controller": "headroom", "text": "1 claim",
        "admissions": [{
            "claim_id": "X", "product": "ENERGY", "requested_kw": 100.0, "admitted_kw": 80.0,
            "reason": "kwh_slack", "start_ts": 0.0, "end_ts": 3600.0, "max_deploy_h": 0.0,
            "kw_room_at_decision": 90.0, "kwh_slack_at_decision": 80.0, "bucket_s": 300.0,
            "locked_kw": {300.0: 80.0},
        }],
    }
    events = [
        {"ts": 0.0, "kind": "admit", "controller": "headroom", "text": "irrelevant"},
        snapshot,
        {"ts": 2000.0, "kind": "notice", "controller": "headroom", "text": "later, excluded"},
    ]
    replayed = _replay_admissions(events, upto_ts=1500.0)
    assert len(replayed) == 1
    a = replayed[0]
    assert a.claim_id == "X" and a.product is Product.ENERGY
    assert a.admitted_kw == 80.0 and a.locked_kw == {300.0: 80.0}

    # upto_ts before the snapshot ever happened: nothing to replay.
    assert _replay_admissions(events, upto_ts=500.0) == []

    # Round-trips through _admission_from_dict independently of run_scenario.
    rebuilt = _admission_from_dict(snapshot["admissions"][0])
    assert rebuilt.admitted_kw == a.admitted_kw and rebuilt.claim_id == a.claim_id


# REQ: HEADROOM-0930-S1B/AC-2
def test_rebuilt_ledger_matches_pre_crash_ledger_bucket_for_bucket():
    """The ledger the controller rebuilds at restart (`replayed_admissions`, read back off the
    append-only `events` log) must agree with the ledger as it stood just before the crash
    (`pre_crash_admissions`, read off the same log at the crash instant) for every bucket that
    had already started by then - across every claim on S1's ledger."""
    crash = CrashSpec(crash_ts=T, crash_duration_s=90.0)
    result = run_scenario("headroom", crash=crash)

    pre = {a.claim_id: a for a in result.crash["pre_crash_admissions"]}
    post = {a.claim_id: a for a in result.crash["replayed_admissions"]}
    assert pre, "no pre-crash ledger snapshot was ever recorded - nothing to compare"
    assert set(pre) == set(post)

    checked_any = False
    for claim_id, a_pre in pre.items():
        a_post = post[claim_id]
        for b in bucket_ends(a_pre.start_ts, a_pre.end_ts, a_pre.bucket_s):
            if b > crash.crash_ts:
                continue   # not started before the crash - not this test's claim
            checked_any = True
            assert a_post.kw_at(b) == pytest.approx(a_pre.kw_at(b), abs=1e-6), (
                f"{claim_id} bucket ending {b}: pre-crash {a_pre.kw_at(b)} kW != "
                f"replayed {a_post.kw_at(b)} kW")
    assert checked_any, "no already-started bucket existed to compare - test picked a bad T"


# ---------------------------------------------------------------- AC-3
# REQ: HEADROOM-0930-S1B/AC-3
def test_commands_withheld_until_restart_plus_k_times_t_tel():
    """Overriding cfg.t_tel_s to 120 makes k*T_tel = reach_k(5.0) * 120 = 600 s (10 ticks). The
    withhold must come from those config values, not be hard-coded to one tick."""
    crash = CrashSpec(crash_ts=T, crash_duration_s=90.0)
    cfg = replace(DEFAULTS, t_tel_s=120.0)
    result = run_scenario("headroom", cfg=cfg, crash=crash)

    expected_withhold_s = cfg.reach_k * cfg.t_tel_s
    assert expected_withhold_s == 600.0
    assert result.crash["withhold_until"] == pytest.approx(
        result.crash["restart_ts"] + expected_withhold_s)

    post_restart = sorted(c["ts"] for c in result.commands
                          if c["ts"] > crash.restart_boundary_ts)
    assert post_restart, "no command was ever issued again after the crash"
    assert post_restart[0] >= result.crash["restart_ts"] + expected_withhold_s - 1e-6

    # The default config (k*T_tel = 10 s, under one tick) must NOT withhold for 600 s - pins the
    # withhold to the config values, not to a fixed number of ticks.
    default_result = run_scenario("headroom", crash=crash)
    assert default_result.crash["withhold_until"] == pytest.approx(
        default_result.crash["restart_ts"] + DEFAULTS.reach_k * DEFAULTS.t_tel_s)
    assert default_result.crash["withhold_until"] < result.crash["withhold_until"]


# ---------------------------------------------------------------- AC-4
# REQ: HEADROOM-0930-S1B/AC-4
def test_crash_is_a_no_op_on_the_default_path():
    """`crash=None` (every existing caller) must leave `events_sha256` exactly where S1A pinned
    it - the crash mechanism may not perturb the ungated tick loop at all."""
    headroom = run_scenario("headroom")
    assert headroom.events_sha256 == (
        "374c99b558a5815025497f980d329089f83952a34b6b42531d8324ede675292b")

    from config import ct as _ct
    from runner.run import RegionOutage
    s2_fault = (RegionOutage("R3", _ct(20, 15), _ct(20, 45), "comms"),)
    s2 = run_scenario("headroom", faults=s2_fault)
    assert s2.events_sha256 == (
        "9e0a58f7ec4f6e8c3782f142eb952687bc30c46ab787a25a072d205e6f37c684")

    # And explicitly: the crash machinery produces no RunResult.crash / commands when unused.
    assert headroom.crash is None
    assert headroom.commands == []


# ---------------------------------------------------------------- AC-5
# REQ: HEADROOM-0930-S1B/AC-5
def test_every_devices_first_command_is_acked_ok_on_a_fresh_fleet():
    """A fresh device's last-applied (epoch, seq) is (0, 0); S1A's fencing rejects a (0, 0)
    command as DUP. The crash path must never hand a device a command it fences out."""
    crash = CrashSpec(crash_ts=T, crash_duration_s=90.0)
    result = run_scenario("headroom", crash=crash)
    assert result.commands, "no command was ever issued - nothing to check"

    first_by_device: dict[str, dict] = {}
    for cmd in sorted(result.commands, key=lambda c: c["ts"]):
        first_by_device.setdefault(cmd["device_id"], cmd)

    not_ok = {d: c["reason"] for d, c in first_by_device.items() if c["reason"] != "OK"}
    assert not not_ok, f"{len(not_ok)} device(s) had their first command rejected: {not_ok}"


# ---------------------------------------------------------------- HEADROOM-1003-S1B2
# REQ: HEADROOM-1003-S1B2/AC-1
def test_unset_duration_behaves_exactly_like_the_resolved_default():
    """`CrashSpec(crash_ts=T)` (no duration given) must behave EXACTLY like
    `CrashSpec(crash_ts=T, crash_duration_s=cfg.crash_duration_s)` for the `cfg` passed to
    `run_scenario`: identical crash bookkeeping and identical events."""
    implicit = CrashSpec(crash_ts=T)
    explicit = CrashSpec(crash_ts=T, crash_duration_s=DEFAULTS.crash_duration_s)

    r_implicit = run_scenario("headroom", crash=implicit)
    r_explicit = run_scenario("headroom", crash=explicit)

    assert r_implicit.crash["restart_ts"] == r_explicit.crash["restart_ts"]
    assert r_implicit.crash["epoch_before"] == r_explicit.crash["epoch_before"]
    assert r_implicit.crash["epoch_after"] == r_explicit.crash["epoch_after"]
    assert r_implicit.crash["withhold_until"] == r_explicit.crash["withhold_until"]
    assert r_implicit.crash["crash_duration_s"] == DEFAULTS.crash_duration_s
    assert r_implicit.events_sha256 == r_explicit.events_sha256


# REQ: HEADROOM-1003-S1B2/AC-2
def test_config_knob_changes_an_unset_duration_crash():
    """Changing `cfg.crash_duration_s` changes an unset-duration crash's restart point: 150 s ->
    restart at T+180 (first tick after T+150); the default 90 s -> restart at T+120."""
    crash = CrashSpec(crash_ts=T)

    cfg_150 = replace(DEFAULTS, crash_duration_s=150.0)
    result_150 = run_scenario("headroom", cfg=cfg_150, crash=crash)
    assert result_150.crash["restart_ts"] == pytest.approx(T + 180.0)
    assert result_150.crash["crash_duration_s"] == 150.0

    result_default = run_scenario("headroom", crash=crash)
    assert result_default.crash["restart_ts"] == pytest.approx(T + 120.0)
    assert result_default.crash["crash_duration_s"] == DEFAULTS.crash_duration_s


# REQ: HEADROOM-1003-S1B2/AC-3
def test_explicit_duration_still_overrides_the_config_default():
    """An explicit `CrashSpec(crash_ts=T, crash_duration_s=30.0)` wins over
    `cfg.crash_duration_s`: restart at T+60, epoch up by exactly 1 (S1B's sub-tick rule)."""
    crash = CrashSpec(crash_ts=T, crash_duration_s=30.0)
    cfg_150 = replace(DEFAULTS, crash_duration_s=150.0)  # would restart at T+180 if ignored
    result = run_scenario("headroom", cfg=cfg_150, crash=crash)

    assert result.crash["crash_duration_s"] == 30.0
    assert result.crash["restart_ts"] == pytest.approx(T + 60.0)
    assert result.crash["epoch_after"] - result.crash["epoch_before"] == 1
