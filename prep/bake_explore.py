"""Bake web/out/explore.js - everything web/explore.html shows, from the recorded sweeps.

    python run.py explore          # bake, then open the page

The page is static on purpose, like the demo: it reads one <script src> and nothing else, so
it opens from a file on any laptop. It cannot run a simulation. Every number on it comes from a
file in prep/out, and every evening it replays is re-run here from its seed - so the replay and
the sweep that counted its misses are the same code, not a drawing of it.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "prep" / "out"
OUT = ROOT / "web" / "out" / "explore.js"
REGIONS = ("R1", "R2", "R3", "R4", "R5")

# The rules a visitor can pick in the replay, and how the page names them.
REPLAY_RULES = ("headroom", "headroom_no_n1", "headroom_p90_r100", "reasonable_plus_d20")

RULES = {
    "headroom": ("Headroom", "N-1 reserve, checked per battery", "hero"),
    "headroom_no_stage2": ("No per-battery check", "the reserve as one fleet-wide number", "ablation"),
    "headroom_no_n1": ("No reserve", "same ledger, nothing held back for an outage", "ablation"),
    "headroom_no_band": ("No band", "trusts the reported charge exactly", "ablation"),
    "headroom_eps_only": ("Fixed margin only", "1% margin, no widening with age", "ablation"),
    "headroom_keep5m": ("Keep quiet batteries 5 min", "counts a silent battery for 5 minutes", "ablation"),
    "headroom_keep15m": ("Keep quiet batteries 15 min", "counts a silent battery for 15 minutes", "ablation"),
    "headroom_p90_r050": ("P90, thinks outages are rare (0.5x)", "holds regions only past 10% odds", "p90"),
    "headroom_p90_r075": ("P90, believes 0.75x", "holds regions only past 10% odds", "p90"),
    "headroom_p90_r100": ("P90, believes 1x", "holds regions only past 10% odds", "p90"),
    "headroom_p90_r200": ("P90, believes 2x", "holds regions only past 10% odds", "p90"),
    "reasonable_plus_d0": ("Industry ledger, 0% de-rate", "point charge, no reserve", "flat"),
    "reasonable_plus_d5": ("Flat 5% de-rate", "industry ledger minus 5%", "flat"),
    "reasonable_plus_d10": ("Flat 10% de-rate", "industry ledger minus 10%", "flat"),
    "reasonable_plus_d12": ("Flat 12% de-rate", "industry ledger minus 12%", "flat"),
    "reasonable_plus_d15": ("Flat 15% de-rate", "industry ledger minus 15%", "flat"),
    "reasonable_plus_d20": ("Flat 20% de-rate", "industry ledger minus 20%", "flat"),
    "reasonable": ("First draft", "books against energy it already sold", "naive"),
}

WORLDS = (
    ("regional", "compare_results.json", "Regional", "a whole region goes dark", "python run.py compare 1000"),
    ("scattered", "compare_results_scattered.json", "Scattered", "same darkness, random batteries", "python run.py compare 1000 scattered"),
    ("fragmented", "compare_results_fragmented.json", "Fragmented", "many small dropouts", "python run.py compare 1000 fragmented"),
    ("flaky", "compare_results_flaky.json", "Flaky links", "every link also drops on its own", "python run.py compare 1000 flaky"),
    ("rate050", "compare_results_rate050.json", "Outages x0.5", "half the assumed outage rate", "python run.py compare 1000 rate050"),
    ("rate075", "compare_results_rate075.json", "Outages x0.75", "three quarters of it", "python run.py compare 1000 rate075"),
    ("rate100", "compare_results_rate100.json", "Outages x1", "the assumed rate", "python run.py compare 1000 rate100"),
    ("rate200", "compare_results_rate200.json", "Outages x2", "twice the assumed rate", "python run.py compare 1000 rate200"),
    ("rate100_10k", "compare_results_rate100_10k.json", "10,000 evenings", "the long run, at x1", "python run.py compare 10000 rate100 --subjects headroom,headroom_no_n1,headroom_p90_r075,headroom_p90_r100 --tag 10k"),
)


def world_summary(key, fname, label, blurb, cmd) -> dict:
    p = json.loads((OUT_DIR / fname).read_text(encoding="utf-8"))
    rows = p["rows"]
    n = len(rows)
    subjects = {}
    for c in p["subjects"]:
        a = p["aggregate"][c]
        short = sum(max(0.0, r[c]["committed_kwh"] - r[c].get("delivered_kwh", r[c]["committed_kwh"]))
                    for r in rows)
        subjects[c] = {
            "held": a["held_back_pct_median"], "held90": a["held_back_pct_p90"],
            "silent": a["seeds_with_any_silent"], "late": a.get("seeds_with_any_late", 0),
            "floor": a["seeds_with_floor_breach"], "lost": round(short / n, 2),
        }
    return {"id": key, "label": label, "blurb": blurb, "cmd": cmd, "n": n, "rules": subjects}


def pick_evenings() -> list[dict]:
    rows = json.loads((OUT_DIR / "compare_results.json").read_text(encoding="utf-8"))["rows"]
    late = [r["seed"] for r in rows if r["headroom"].get("late", 0) > 0]
    silent = [r["seed"] for r in rows if r["headroom"]["silent"] > 0]
    carnage = sorted((r for r in rows if r["headroom"]["silent"] == 0 and r["headroom"].get("late", 0) == 0
                      and r["headroom_no_n1"]["silent"] > 0),
                     key=lambda r: -r["headroom_no_n1"]["silent"])[:6]
    quiet = [r["seed"] for r in rows if r["n_faults"] == 0][:2]
    held = {r["seed"]: {c: r[c]["held_back_pct"] for c in REPLAY_RULES if c in r} for r in rows}
    out = [{"seed": 42, "kind": "demo", "label": "S2 - the tower goes dark", "held": {}}]
    out += [{"seed": s, "kind": "silent", "held": held[s]} for s in silent]
    out += [{"seed": s, "kind": "late", "held": held[s]} for s in late]
    out += [{"seed": r["seed"], "kind": "carnage", "held": held[r["seed"]]} for r in carnage]
    out += [{"seed": s, "kind": "quiet", "held": held[s]} for s in quiet]
    return out


def replay(job: tuple) -> dict:
    seed, kind, rule = job
    sys.path.insert(0, str(ROOT))
    from config import ct
    from runner.run import RegionOutage, run_scenario
    from sim.chaos import draw_faults
    faults = ((RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),) if kind == "demo"
              else draw_faults(seed))
    r = run_scenario(rule, seed=seed, faults=faults)
    fr = r.frames
    silent_at, late_at, prev_s, prev_l = [], [], 0, 0
    for i, f in enumerate(fr):
        if f["silent_breaches"] > prev_s:
            silent_at.append(i)
        if f["late_breaches"] > prev_l:
            late_at.append(i)
        prev_s, prev_l = f["silent_breaches"], f["late_breaches"]
    dark = [sum(1 << REGIONS.index(k) for k in f["dark"] if k in REGIONS) for f in fr]
    notices = [{"t": e["ts"], "text": e["text"]} for e in r.events if e["kind"] == "notice"]
    return {
        "seed": seed, "rule": rule,
        "t0": fr[0]["ts"], "dt": fr[1]["ts"] - fr[0]["ts"],
        "booked": [round(f["booked_kw"]) for f in fr],
        "delivered": [round(f["delivered_truth_kw"]) for f in fr],
        "dark": dark, "silentAt": silent_at, "lateAt": late_at, "notices": notices,
        "m": {k: r.metrics[k] for k in ("committed_kwh", "delivered_kwh", "silent_breach_buckets",
                                        "late_breach_buckets", "notices", "capacity_availability_pct")},
        "faults": [{"r": f.region_id, "a": f.start_ts, "b": f.end_ts} for f in faults],
    }


def regions_dark_when_short(evenings: list[dict]) -> int:
    """The fewest regions dark at once during any of headroom's late minutes, over every late
    evening - read off the replays, so the quest's claim is measured, not typed."""
    least = len(REGIONS)
    for e in evenings:
        r = e["runs"]["headroom"]
        for i in r["lateAt"]:
            least = min(least, bin(r["dark"][i]).count("1"))
    return least


def quests(worlds: dict, evenings: list[dict]) -> list[dict]:
    reg = worlds["regional"]["rules"]
    k10 = worlds["rate100_10k"]["rules"]
    h = reg["headroom"]
    late = [e for e in evenings if e["kind"] == "late"]
    least = regions_dark_when_short(late) if late else 0
    active = ({"title": "Two regions dark at once",
               "why": f"Headroom's {h['late']} late-miss evenings in 1,000 all have at least {least} regions "
                      f"dark at the same moment. N-1 covers one; the Notice arrives, the bucket still runs short."}
              if late and least >= 2 else
              {"title": "Warned, and still short",
               "why": f"On {h['late']} evenings in 1,000 a Notice goes out and the bucket still runs short."})
    first = f"python run.py show {late[0]['seed']}" if late else "python run.py show <seed>"
    return [
        {"id": "late", "state": "active", **active,
         "stats": [[h["late"], "evenings in 1,000"], [h["lost"], "kWh lost a night"]],
         "go": {"view": "replay", "filter": "late"}, "cmd": first},
        {"id": "replan", "state": "next", "title": "React the moment a region goes quiet",
         "why": "The controller re-plans every five minutes, so an outage goes unseen for up to four. "
                "The reserve covers the gap. Re-planning on detection would need less of it.",
         "stats": [[reg["headroom_no_n1"]["silent"], "silent evenings without the reserve"]],
         "go": None, "cmd": "not built yet"},
        {"id": "page", "state": "next", "title": "One page a reviewer reads",
         "why": "The rationale is long and partly superseded. One chart, one table, every rule.",
         "stats": [], "go": {"view": "arena"}, "cmd": "python run.py explore"},
        {"id": "f27", "state": "cleared", "title": "Phantom misses",
         "why": "29 of the 36 evenings the sweep called 'warned, still short' were float noise: a claim "
                "cut to zero was booked at 0.0000000000001 kW, and every minute of it scored as a miss.",
         "stats": [[29, "phantom evenings"], [7, "real ones"]], "go": None,
         "cmd": "PRACTICE-NOTES.md FINDING-27"},
        {"id": "f26", "state": "cleared", "title": "The fault clock and the window clock",
         "why": "Every silent miss in 4,000 evenings was one artefact. Fixed, and everything re-run.",
         "stats": [[1, "silent evening left in 1,000"]], "go": None, "cmd": "PRACTICE-NOTES.md FINDING-26"},
        {"id": "p90", "state": "cleared", "title": "Would a P90 reserve do better?",
         "why": "No. On 10,000 evenings it saves a fraction of a point and misses silently twice as often.",
         "stats": [[k10["headroom"]["silent"], "headroom"], [k10["headroom_p90_r100"]["silent"], "P90"]],
         "go": {"view": "arena", "world": "rate100_10k"}, "cmd": "RATIONALE.md section 6e"},
        {"id": "flaky", "state": "cleared", "title": "Drop a quiet battery, or keep it?",
         "why": "Drop it after 10 seconds. Keeping it counted only adds misses.",
         "stats": [[worlds["flaky"]["rules"]["headroom_keep15m"]["silent"], "silent, kept 15 min"],
                   [worlds["flaky"]["rules"]["headroom"]["silent"], "silent, dropped"]],
         "go": {"view": "arena", "world": "flaky"}, "cmd": "python run.py compare 1000 flaky"},
        {"id": "latency", "state": "locked", "title": "Late but still reachable",
         "why": "Telemetry that arrives late while the battery still obeys. The one case left for the band.",
         "stats": [], "go": None, "cmd": "needs the SPEC 6.6 latency model"},
        {"id": "world", "state": "locked", "title": "A messier world",
         "why": "Home load, charging, losses, unequal regions, two regions dark at once.",
         "stats": [], "go": None, "cmd": "not started"},
    ]


def main() -> int:
    worlds = {w[0]: world_summary(*w) for w in WORLDS}
    evenings = pick_evenings()
    jobs = [(e["seed"], e["kind"], rule) for e in evenings for rule in REPLAY_RULES]
    with mp.Pool() as pool:
        runs = pool.map(replay, jobs, chunksize=2)
    by = {}
    for (seed, kind, rule), run in zip(jobs, runs):
        by.setdefault((seed, kind), {})[rule] = run
    for e in evenings:
        rs = by[(e["seed"], e["kind"])]
        e["faults"] = rs["headroom"]["faults"]
        e["runs"] = {rule: {k: v for k, v in rs[rule].items() if k not in ("seed", "rule", "faults")}
                     for rule in REPLAY_RULES}
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = ""
    data = {
        "baked": date.today().isoformat(), "commit": commit, "regions": REGIONS,
        "rules": {k: {"name": v[0], "line": v[1], "kind": v[2]} for k, v in RULES.items()},
        "replayRules": REPLAY_RULES, "worlds": [worlds[w[0]] for w in WORLDS],
        "evenings": evenings, "quests": quests(worlds, evenings),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.EXPLORE = " + json.dumps(data, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    print(f"{len(evenings)} evenings x {len(REPLAY_RULES)} rules, {len(WORLDS)} worlds -> "
          f"{OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
