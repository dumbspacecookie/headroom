"""Headroom against standard practice, and against itself with parts removed. RATIONALE.md s8.

`runner/batch.py` answers "what does the honesty cost against the oracle?" and compares against
`reasonable`, which never nets out what it already admitted. That omission is public prior art
(Sunverge's patent, ERCOT NPRR 1186), so the batch headline mostly measures the standard part.
This runner asks the question that decides whether the project has a thesis:

    does the band or the N-1 reserve buy anything that a flat de-rate on a standard ledger
    does not?

Every subject runs the same seeds and the same chaos draws as the recorded batch, and is
scored against the SAME measured oracle ceiling, read from `prep/out/batch_results.json`.
The oracle's code is untouched by the comparison knobs, and `test_compare` checks the ceiling
still reproduces, so reusing it is safe.

    python run.py compare [N]              # default 1000; writes prep/out/compare_results.json
    python run.py compare N scattered      # same darkness, different SHAPE (sim/chaos.py WORLDS);
    python run.py compare N fragmented     # the ceiling is re-measured, since the faults moved
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

from config import DEFAULTS
from control.oracle import held_back_pct
from runner.run import VARIANTS, run_scenario
from sim.chaos import RATE_WORLDS, draw_faults, draw_faults_rate, draw_faults_world

ROOT = Path(__file__).resolve().parent.parent
BATCH = ROOT / "prep" / "out" / "batch_results.json"
OUT = ROOT / "prep" / "out" / "compare_results.json"

# The P90 variants have their own worlds (RATE_SUBJECTS); the regional list is what was published.
SUBJECTS = ("headroom", "reasonable", *(v for v in VARIANTS if not v.startswith("headroom_p90")))
# The shape worlds ask one question - does N-1 still beat a flat de-rate when outages do not
# follow region boundaries - so they run only the subjects that answer it.
WORLD_SUBJECTS = ("headroom", "reasonable_plus_d10", "reasonable_plus_d12",
                  "reasonable_plus_d15", "reasonable_plus_d20", "headroom_no_n1",
                  "headroom_no_stage2")


# The band's age-widening, tested at last: drop a quiet device after 10 s, or keep counting it.
FLAKY_SUBJECTS = ("headroom", "headroom_keep5m", "headroom_keep15m", "headroom_no_n1",
                  "reasonable_plus_d15", "reasonable_plus_d20")


# The P90 reserve against N-1, at four outage rates (RATIONALE.md s6d). Every calibration runs in
# every world: the diagonal is a P90 that knows the true rate, the rest is one that is wrong.
RATE_SUBJECTS = ("headroom", "headroom_no_n1", "headroom_p90_r050", "headroom_p90_r075",
                 "headroom_p90_r100", "headroom_p90_r200", "reasonable_plus_d15",
                 "reasonable_plus_d20")


def subjects_for(world: str) -> tuple[str, ...]:
    """The one place a world's subject list is chosen - run_seed and main must agree on it."""
    if world == "regional":
        return SUBJECTS
    if world in RATE_WORLDS:
        return RATE_SUBJECTS
    return FLAKY_SUBJECTS if world == "flaky" else WORLD_SUBJECTS


def out_path(world: str) -> Path:
    return OUT if world == "regional" else OUT.with_name(f"compare_results_{world}.json")


def _ceilings() -> dict[int, float]:
    rows = json.loads(BATCH.read_text(encoding="utf-8"))["rows"]
    return {r["seed"]: r["oracle"]["committed_kwh"] for r in rows}


def run_seed(args: tuple) -> dict:
    seed, ceiling_kwh, *rest = args
    world = rest[0] if rest else "regional"
    flaky = world == "flaky"
    subjects = subjects_for(world)
    if world == "regional":
        faults = draw_faults(seed, DEFAULTS)
    else:
        # "flaky" = the regional outages PLUS per-device links that drop for minutes (sim/link.py)
        from runner.batch import measure_ceiling
        if world in RATE_WORLDS:
            faults = draw_faults_rate(seed, RATE_WORLDS[world], DEFAULTS)
        else:
            faults = draw_faults(seed, DEFAULTS) if flaky else draw_faults_world(seed, world, DEFAULTS)
        _, oracle_m, _ = measure_ceiling(seed, faults, DEFAULTS, flaky=flaky)
        ceiling_kwh = oracle_m["committed_kwh"]
    row: dict = {"seed": seed, "n_faults": len(faults), "ceiling_kwh": ceiling_kwh}
    for c in subjects:
        m = run_scenario(c, seed=seed, faults=faults, flaky=flaky).metrics
        row[c] = {
            "committed_kwh": m["committed_kwh"],
            "held_back_pct": held_back_pct(m["committed_kwh"], ceiling_kwh),
            "silent": m["silent_breach_buckets"],
            "notices": m["notices"],
            "floor_s": m["floor_breach_dev_s"],
            "capacity_availability_pct": m["capacity_availability_pct"],
        }
    return row


def _q(xs: list[float], q: float) -> float:
    s = sorted(x for x in xs if x == x)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


def summarise(rows: list[dict], subjects: tuple[str, ...] = SUBJECTS) -> dict:
    out = {}
    for c in subjects:
        hb = [r[c]["held_back_pct"] for r in rows]
        out[c] = {
            "held_back_pct_median": round(_q(hb, 0.5), 2),
            "held_back_pct_p90": round(_q(hb, 0.9), 2),
            "silent_buckets_total": sum(r[c]["silent"] for r in rows),
            "seeds_with_any_silent": sum(1 for r in rows if r[c]["silent"] > 0),
            "seeds_with_floor_breach": sum(1 for r in rows if r[c]["floor_s"] > 0),
            "floor_breach_dev_s_total": sum(r[c]["floor_s"] for r in rows),
            "seeds_clean": sum(1 for r in rows if r[c]["silent"] == 0 and r[c]["floor_s"] == 0),
            "capacity_availability_pct_median": round(
                _q([r[c]["capacity_availability_pct"] for r in rows], 0.5), 2),
        }
    return out


def main(n_seeds: int = 1000, workers: int | None = None, world: str = "regional") -> dict:
    ceilings = _ceilings()
    seeds = [s for s in range(n_seeds) if s in ceilings]
    subjects = subjects_for(world)
    workers = workers or max(1, (os.cpu_count() or 4) - 1)
    t0 = time.perf_counter()
    with mp.Pool(workers) as pool:
        rows = pool.map(run_seed, [(s, ceilings[s], world) for s in seeds], chunksize=4)
    payload = {
        "n_seeds": len(rows), "workers": workers, "world": world,
        "runtime_s": round(time.perf_counter() - t0, 1),
        "subjects": list(subjects), "variants": VARIANTS,
        "aggregate": summarise(rows, subjects), "rows": rows,
    }
    out = out_path(world)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return payload


def report(p: dict) -> None:
    print(f"\n[{p.get('world', 'regional')}] {p['n_seeds']} seeded evenings in {p['runtime_s']:,.0f} s "
          f"(held back is vs the measured oracle ceiling; negative = booked past it)")
    print(f"  {'subject':<22}{'held med':>9}{'p90':>8}{'silent':>8}{'seeds':>7}"
          f"{'floor seeds':>12}{'clean':>7}{'cap avail':>10}")
    for c, a in p["aggregate"].items():
        print(f"  {c:<22}{a['held_back_pct_median']:>8.2f}%{a['held_back_pct_p90']:>7.2f}%"
              f"{a['silent_buckets_total']:>8}{a['seeds_with_any_silent']:>7}"
              f"{a['seeds_with_floor_breach']:>12}{a['seeds_clean']:>7}"
              f"{a['capacity_availability_pct_median']:>9.1f}%")
    print(f"  written to {out_path(p.get('world', 'regional')).relative_to(ROOT)}")


if __name__ == "__main__":
    import sys
    report(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1000,
                world=sys.argv[2] if len(sys.argv) > 2 else "regional"))
