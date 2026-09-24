"""The batch runner. SPEC 11 (`perf` lens) and DEMO.md Slide 3 ("where we lose").

Answers one question, over many seeded evenings: **what does the honesty cost?**

    capacity_held_back_vs_oracle_pct  =  100 * (oracle_kWh - controller_kWh) / oracle_kWh

on ADMITTED energy, because the slide asks what our caution refused to *sell*, and a booking
never made never shows up in delivery.

## The ceiling is measured, not assumed

`control/oracle.py` gets true SoC and holds no N-1 reserve, and that alone does **not** make it
a ceiling. Run it flat out on a completely quiet seed and it books 1,610 kW of the 20:00 award
and delivers exactly 90% of it - **60 silent breaches, with 3,105 kWh sitting unused in the
fleet.** Not an energy shortage: the ledger admits aggregate kW that the allocator cannot place
on individual devices, because the ledger's per-claim divisor and the allocator's
`h_remaining` (measured from the bucket's start, FINDING-8) do not agree about the same hour.

**`headroom` has that defect too. Its N-1 reserve is simply so large that the gap never shows.**
That is worth saying out loud: part of the margin is load-bearing for a bug rather than for the
outage it is sold as covering. Logged as FINDING-21; not fixed here, because closing it moves
S1's numbers and the demo bake eight days before the event, and that is ash's call not mine.

So the ceiling is **searched for**: the largest fraction of its perfect-information admission
that the oracle can promise *and keep*, bisected per seed until 0 silent and 0 floor breaches.
That measured point is a ceiling you can defend on stage. A bigger number would just be the
oracle cheating, and every held-back percentage would be inflated by exactly the amount it
cheated.

    python run.py batch              # 200 seeds, the default sweep
    python run.py batch 1000         # the SPEC 11 perf bar
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

from config import DEFAULTS, Config
from control.oracle import held_back_pct, is_a_valid_ceiling
from runner.run import run_scenario
from sim.chaos import describe, draw_faults

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "prep" / "out" / "batch_results.json"

SUBJECTS = ("reasonable", "headroom")
CEILING_TOL = 0.01           # bisection stops when the bracket is this wide: ~7 probes
CEILING_PROBES = 7


def _keeps_its_promises(m: dict) -> bool:
    return m["silent_breach_buckets"] == 0 and m["floor_breach_dev_s"] == 0.0


def measure_ceiling(seed: int, faults, cfg: Config) -> tuple[float, dict, int]:
    """The largest oracle admission that survives its own delivery. Returns (scale, metrics, probes).

    Bisects on the belief scale, which the ledger is monotone in. `lo` starts at 0, which admits
    nothing and is therefore trivially feasible - so the bracket always contains an answer and
    the search cannot fail to terminate.
    """
    probes = 0
    full = run_scenario("oracle", seed=seed, faults=faults, cfg=cfg, oracle_scale=1.0)
    probes += 1
    if _keeps_its_promises(full.metrics):
        return 1.0, full.metrics, probes

    lo, hi = 0.0, 1.0
    best = None
    for _ in range(CEILING_PROBES):
        if hi - lo <= CEILING_TOL:
            break
        mid = (lo + hi) / 2.0
        r = run_scenario("oracle", seed=seed, faults=faults, cfg=cfg, oracle_scale=mid)
        probes += 1
        if _keeps_its_promises(r.metrics):
            lo, best = mid, r
        else:
            hi = mid
    if best is None:                       # even a hair over nothing breaches: report the floor
        best = run_scenario("oracle", seed=seed, faults=faults, cfg=cfg, oracle_scale=0.0)
        probes += 1
        lo = 0.0
    return lo, best.metrics, probes


def run_seed(seed: int, cfg: Config = DEFAULTS) -> dict:
    """One seeded evening, all three controllers. Picklable and side-effect free, for the pool."""
    faults = draw_faults(seed, cfg)
    scale, oracle_m, probes = measure_ceiling(seed, faults, cfg)
    ceiling_kwh = oracle_m["committed_kwh"]

    row = {"seed": seed, "faults": describe(faults), "n_faults": len(faults),
           "oracle": {"committed_kwh": ceiling_kwh, "scale": round(scale, 4),
                      "silent": oracle_m["silent_breach_buckets"],
                      "floor_s": oracle_m["floor_breach_dev_s"]},
           "probes": probes}
    for c in SUBJECTS:
        m = run_scenario(c, seed=seed, faults=faults, cfg=cfg).metrics
        row[c] = {
            "committed_kwh": m["committed_kwh"],
            "held_back_pct": held_back_pct(m["committed_kwh"], ceiling_kwh),
            "silent": m["silent_breach_buckets"],
            "silent_energy": m["silent_energy_buckets"],
            "silent_capacity": m["silent_capacity_buckets"],
            "capacity_availability_pct": m["capacity_availability_pct"],
            "promise_kept_pct": m["promise_kept_pct"],
            "notices": m["notices"],
            "median_lead_time_s": m["median_lead_time_s"],
            "floor_s": m["floor_breach_dev_s"],
        }
    return row


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def summarise(rows: list[dict]) -> dict:
    agg = {}
    for c in SUBJECTS:
        hb = [r[c]["held_back_pct"] for r in rows if r[c]["held_back_pct"] == r[c]["held_back_pct"]]
        agg[c] = {
            "held_back_pct_median": round(_pct(hb, 0.50), 2),
            "held_back_pct_p90": round(_pct(hb, 0.90), 2),
            "held_back_pct_mean": round(sum(hb) / len(hb), 2) if hb else float("nan"),
            "seeds_with_any_silent": sum(1 for r in rows if r[c]["silent"] > 0),
            "silent_buckets_total": sum(r[c]["silent"] for r in rows),
            "seeds_with_floor_breach": sum(1 for r in rows if r[c]["floor_s"] > 0),
            "capacity_availability_pct_median": round(
                _pct([r[c]["capacity_availability_pct"] for r in rows], 0.50), 2),
        }

    # WHERE WE LOSE. Not "the worst seeds" - the seeds where the caution cost the most while
    # buying nothing, i.e. headroom held back the most on evenings that stayed quiet. An
    # honest "where we lose" panel has to be able to come back EMPTY, so nothing here invents
    # a row; if the reserve always paid for itself, the list is short and says so.
    quiet_cost = sorted((r for r in rows if r["n_faults"] == 0),
                        key=lambda r: -r["headroom"]["held_back_pct"])[:5]
    beaten = [r for r in rows
              if r["reasonable"]["held_back_pct"] < r["headroom"]["held_back_pct"]
              and r["reasonable"]["silent"] == 0]
    return {
        "aggregate": agg,
        "where_we_lose": {
            "quiet_evenings_we_still_held_back": [
                {"seed": r["seed"], "held_back_pct": round(r["headroom"]["held_back_pct"], 2)}
                for r in quiet_cost],
            "seeds_where_reasonable_beat_us_with_no_silent_breach": [r["seed"] for r in beaten],
            "note": ("headroom holds an N-1 reserve for an outage that may never come. On a "
                     "quiet evening that reserve is pure cost, and this is what it cost."),
        },
    }


def main(n_seeds: int = 200, workers: int | None = None, out: Path = OUT) -> dict:
    cfg = DEFAULTS
    workers = workers or max(1, (os.cpu_count() or 4) - 1)
    t0 = time.perf_counter()
    with mp.Pool(workers) as pool:
        rows = pool.map(run_seed, range(n_seeds), chunksize=4)
    runtime = time.perf_counter() - t0

    bad = [r for r in rows if r["oracle"]["silent"] or r["oracle"]["floor_s"]]
    payload = {
        "n_seeds": n_seeds, "workers": workers,
        "runtime_s": round(runtime, 1),
        "runs": sum(2 + r["probes"] for r in rows),
        "devices": cfg.n_devices_total,
        "ceiling_is_valid": not bad,
        "ceiling_invalid_seeds": [r["seed"] for r in bad][:20],
        **summarise(rows),
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return payload


def _report(p: dict) -> None:
    print(f"\n{p['n_seeds']} seeded evenings, {p['devices']} devices, {p['runs']} runs on "
          f"{p['workers']} workers in {p['runtime_s']:,.0f} s")
    print(f"  ceiling valid: {'YES' if p['ceiling_is_valid'] else 'NO -> ' + str(p['ceiling_invalid_seeds'])}")
    for c in SUBJECTS:
        a = p["aggregate"][c]
        print(f"  {c:<11} held back median {a['held_back_pct_median']:>6.2f}%  "
              f"p90 {a['held_back_pct_p90']:>6.2f}%  "
              f"silent buckets {a['silent_buckets_total']:>6}  "
              f"seeds w/ silent {a['seeds_with_any_silent']:>4}/{p['n_seeds']}  "
              f"cap avail median {a['capacity_availability_pct_median']:>6.1f}%")
    w = p["where_we_lose"]
    print(f"  where we lose: {len(w['quiet_evenings_we_still_held_back'])} quiet evenings listed; "
          f"{len(w['seeds_where_reasonable_beat_us_with_no_silent_breach'])} seeds where "
          f"`reasonable` did better with no silent breach")
    print(f"  written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    _report(main(n))
