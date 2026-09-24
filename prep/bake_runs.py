#!/usr/bin/env python
"""Pre-compute every run the demo can show, into one local JSON file.

The demo page is a **static file**. There is no server, no port, no async loop and nothing to
start before the judges: `web/demo.html` opens over `file://` and reads `web/out/runs.json`.
`DEMO.md` pre-flight check 3's own pass criterion is "devtools Network shows 0 external
requests" - a baked page satisfies that by construction rather than by inspection.

Everything here comes from the same `runner.run.run_scenario` and `control.ledger.Ledger` the
gate tests drive. Nothing is re-derived for the browser: if the projector and the ledger could
disagree, one of them would be lying, and it would be the projector.

    python prep/bake_runs.py            # rebuild web/out/runs.json
    python prep/bake_runs.py --check    # fail if the committed bake is stale
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import DEFAULTS, ct                                          # noqa: E402
from control.ledger import Ledger                                        # noqa: E402
from runner.run import TICK_S, RegionOutage, run_scenario                # noqa: E402
from scenarios.s1 import build_s1                                        # noqa: E402
from web.batch_chart import build_batch_chart                            # noqa: E402
from web.batch_chart import render_png as render_batch_png              # noqa: E402
from web.hero_chart import build_hero_chart                              # noqa: E402

BATCH = ROOT / "prep" / "out" / "batch_results.json"
BATCH_PNG = ROOT / "prep" / "out" / "batch_where_we_lose.png"
OUT = ROOT / "web" / "out" / "runs.json"
# The page loads its data with <script src="out/runs.js">, NOT fetch(): Chrome blocks fetch on
# a file:// origin as a cross-origin read, so a fetch-based page works when served and dies on
# the demo laptop. A <script src> is exempt. Both files are written from ONE payload string in
# `main()` and a test asserts the wrapper is exactly that string, so they cannot drift apart.
OUT_JS = ROOT / "web" / "out" / "runs.js"
JS_PREFIX = "window.RUNS="


def wrap(payload: str) -> str:
    """The one place runs.js is derived from runs.json. Both `main()` and `--check` call it."""
    return JS_PREFIX + payload + ";" + "\n"
SEED = 42
TOWER_FAULT = (RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),)

# Only the fields the page draws. A frame carries more than a projector needs and the file is
# read over file:// on a demo laptop.
FRAME_KEYS = ("ts", "booked_kw", "delivered_truth_kw", "energy_left_truth_kwh",
              "energy_left_low_kwh", "committed_future_kwh", "silent_breaches", "notices",
              "cap_ok", "bookings")


def _trim(frames: list[dict]) -> list[dict]:
    return [{k: f[k] for k in FRAME_KEYS} for f in frames]


def _run(key: str, label: str, say: str, **kw) -> dict:
    r = run_scenario(seed=SEED, **kw)
    # `runtime_s` is a stopwatch reading of the machine that baked this. It changes on every
    # run, so leaving it in makes `--check` permanently red and the staleness signal worthless;
    # it is also meaningless on a projector. A baked artifact carries no wall clock.
    metrics = {k: v for k, v in r.metrics.items() if k != "runtime_s"}
    return {
        "key": key, "label": label, "say": say,
        "controller": r.controller,
        "frames": _trim(r.frames),
        "events": r.events,
        "metrics": metrics,
        "chart": json.loads(build_hero_chart(r.frames, r.controller, subtitle=label).to_json()),
    }


def _register(led: Ledger) -> list[dict]:
    return [{"claim_id": a.claim_id, "product": a.product.value,
             "requested_kw": a.requested_kw, "admitted_kw": a.admitted_kw,
             "reason": a.reason.value, "is_full": a.is_full,
             "start_ts": a.start_ts, "end_ts": a.end_ts} for a in led.admitted]


def build() -> dict:
    # ---- D1, the `1` toggle. The co-op is not re-simulated, it is re-ADMITTED, and the
    # difference is the whole claim.
    #
    # Two ledgers per side since 2026-09-24. The D1 tile is the ENERGY arithmetic - stage 1,
    # `admit_priority`, exactly what tests/known_answer/test_d1.py drives - because D1 is a claim
    # about kWh slack. The bookings the page lists are the DECISION - `admit_all`, where the
    # per-device N-1 stage trims ADER_ENERGY to 763 kW. This file used to say "exactly as
    # test_d1 drives it" while calling admit_all; the day the second stage started binding, the
    # tile would have gone red on a D1 that the test still passed.
    with_coop = build_s1(include_coop=True)
    energy_a = Ledger(with_coop.belief, now=with_coop.t0)
    energy_a.admit_priority(with_coop.claims)
    led_a = Ledger(with_coop.belief, now=with_coop.t0)
    led_a.admit_all(with_coop.claims)
    without = build_s1(include_coop=False)
    led_b = Ledger(without.belief, now=without.t0)
    led_b.admit_all(without.claims)

    ader_a = energy_a.admitted_by_id("ADER_ENERGY")
    cut_mwh = (ader_a.requested_kw - ader_a.admitted_kw) * ader_a.duration_h / 1000.0

    return {
        "seed": SEED,
        "git_head": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip(),
        "cfg": {"t0": DEFAULTS.t0, "horizon_ts": DEFAULTS.horizon_ts,
                "bucket_s": DEFAULTS.bucket_s, "tick_s": TICK_S},
        "d1": {
            "with_coop": _register(led_a),
            "without_coop": _register(led_b),
            "cut_mwh": round(cut_mwh, 3),
            "expect_cut_mwh": 1.072,
            "energy_term_kw": round(ader_a.admitted_kw, 1),
            "decided_kw": round(led_a.admitted_by_id("ADER_ENERGY").admitted_kw, 1),
            "green": abs(cut_mwh - 1.072) <= 0.05 and not ader_a.is_full
                     and led_b.admitted_by_id("ADER_ENERGY").is_full,
        },
        "runs": {
            "base": _run("base", "S1 · headroom", "the evening as booked at 15:00",
                         controller="headroom"),
            "tower": _run("tower", "S2 · headroom · R3 dark 20:15",
                          "a cell region goes quiet mid-delivery",
                          controller="headroom", faults=TOWER_FAULT),
            "reasonable": _run("reasonable", "S1 · reasonable",
                               "keeps its energy promise, loses the hold it is paid for",
                               controller="reasonable"),
        },
        # Slide 3's picture, from the sweep. Embedded rather than re-derived in JavaScript, for
        # the same reason as everything else here: if the projector and the batch could
        # disagree, one of them would be lying and it would be the projector.
        "batch": _batch(),
        # Key 4 was Beat C, CUT on 2026-09-17 - crash recovery moved to DEMO.md section 4 Q&A.
        # The key still says NOT BUILT out loud rather than doing nothing: a key that silently
        # no-ops in rehearsal reads as working right up until it matters.
        "not_built": {"4": "S3 controller crash/recovery - cut from the 3:00, not built"},
    }


def _batch() -> dict:
    if not BATCH.exists():
        raise SystemExit(f"FAIL: {BATCH.relative_to(ROOT)} missing - run `python run.py batch 1000`")
    results = json.loads(BATCH.read_text(encoding="utf-8"))
    return {
        "n_seeds": results["n_seeds"],
        "devices": results["devices"],
        "runtime_s": results["runtime_s"],
        "ceiling_is_valid": results["ceiling_is_valid"],
        "aggregate": results["aggregate"],
        "where_we_lose": results["where_we_lose"],
        "chart": json.loads(build_batch_chart(results).to_json()),
    }


def main() -> int:
    check = "--check" in sys.argv
    fresh = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(fresh, separators=(",", ":"), sort_keys=True)

    if check:
        if not OUT.exists():
            print(f"FAIL: {OUT.relative_to(ROOT)} has never been baked")
            return 1
        old = json.loads(OUT.read_text(encoding="utf-8"))
        # git_head moves on every commit and is not a staleness signal about the RUNS.
        a, b = dict(old), dict(fresh)
        a.pop("git_head", None), b.pop("git_head", None)
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            print("FAIL: web/out/runs.json is STALE - the code has moved since it was baked.")
            print("      Run `python prep/bake_runs.py` and commit the result.")
            return 1
        if not OUT_JS.exists() or OUT_JS.read_text(encoding="utf-8") != wrap(
                OUT.read_text(encoding="utf-8")):
            print("FAIL: web/out/runs.js is not the wrapper of web/out/runs.json")
            return 1
        print(f"ok: {OUT.relative_to(ROOT)} and runs.js match the current code")
        return 0

    OUT.write_text(payload, encoding="utf-8")
    OUT_JS.write_text(wrap(payload), encoding="utf-8")
    # The deck's copy of the same geometry. One command refreshes every demo artifact, so the
    # PNG on the slide and the tab on the laptop cannot come from different sweeps.
    render_batch_png(build_batch_chart(json.loads(BATCH.read_text(encoding="utf-8"))), BATCH_PNG)
    kb = len(payload.encode()) / 1024
    d1 = fresh["d1"]
    print(f"baked {OUT.relative_to(ROOT)} + runs.js  ({kb:,.0f} kB each)")
    print(f"       {BATCH_PNG.relative_to(ROOT)}  (slide 3)")
    print(f"  runs      : {', '.join(fresh['runs'])}")
    print(f"  D1        : cut {d1['cut_mwh']:.3f} MWh vs expected {d1['expect_cut_mwh']:.3f} "
          f"-> {'GREEN' if d1['green'] else 'RED'}")
    for k, r in fresh["runs"].items():
        m = r["metrics"]
        print(f"  {k:<10}: silent {m['silent_breach_buckets']:>2}  "
              f"capacity {m['capacity_availability_pct']:>5.1f}%  "
              f"notices {m['notices']}  lead {m['median_lead_time_s']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
