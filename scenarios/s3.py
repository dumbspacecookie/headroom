"""S3 - S1 plus a controller crash at 20:05 CT for `config.crash_duration_s`. SPEC §6.5/8.1,
HEADROOM-0930-S1C.

S3 adds exactly one thing to S1: the controller goes dark at 20:05 CT - five minutes into
ADER_ENERGY's 20:00-21:00 window - for `cfg.crash_duration_s` [A: 90 s]. The crash mechanism
itself (epoch bump, append-only-log replay, command withhold) is S1B's
(`runner.run.CrashSpec`/`run_scenario`, imported here unchanged). This module only fixes S3's
crash instant and turns the result into a `crash_recovery_s` metric, computed by
`metrics.score.crash_recovery_s` from the returned `RunResult.frames`/`crash` dict and merged
into `run_scenario`'s own `metrics` dict.

Per the independent spec review: `runner/run.py` is outside this change's allowed scope and its
`run_scenario` is not touched, so S1 and S2 (`crash=None`) stay byte-for-byte what they were
(events_sha256 unmoved). `crash_ts` lives here, on the scenario, never as a new `Config` knob
(owner answer, 2026-10-01/03) - `cfg.crash_duration_s` already exists and is reused as-is.
"""
from __future__ import annotations

from config import DEFAULTS, Config, ct
from metrics.score import crash_recovery_s
from runner.run import CrashSpec, RunResult, run_scenario

CRASH_TS = ct(20, 5)   # SPEC §6.5 / HEADROOM-0930-S1C: five minutes into ADER_ENERGY's window.


def build_s3_crash(cfg: Config = DEFAULTS, crash_ts: float = CRASH_TS,
                   crash_duration_s: float | None = None) -> CrashSpec:
    """S3's crash spec: the fixed 20:05 CT instant, for `cfg.crash_duration_s` unless an
    explicit `crash_duration_s` overrides it (S1B's own override rule, reused as-is)."""
    return CrashSpec(crash_ts=crash_ts, crash_duration_s=crash_duration_s)


def run_s3(controller: str = "headroom", seed: int = 42, cfg: Config = DEFAULTS,
          crash_ts: float = CRASH_TS, crash_duration_s: float | None = None,
          oracle_scale: float = 1.0, flaky: bool = False) -> RunResult:
    """S3's entry point (AC-1): S1 + the scripted 20:05 crash, run through the existing,
    unmodified `runner.run.run_scenario`, with `crash_recovery_s` filled into the returned
    `metrics` dict.

    Every argument besides the crash instant/duration is `run_scenario`'s own, passed straight
    through - S3 is a configuration of S1's tick loop, not a second one.
    """
    crash = build_s3_crash(cfg, crash_ts, crash_duration_s)
    result = run_scenario(controller, seed=seed, cfg=cfg, scenario="S3", oracle_scale=oracle_scale,
                          flaky=flaky, crash=crash)
    result.metrics = {**result.metrics,
                      "crash_recovery_s": crash_recovery_s(result.frames, result.crash, cfg=cfg)}
    return result
