"""The `perf` lens. SPEC 11: batch 1k devices x 1000 seeds x (reasonable, headroom, oracle) <= 20 min.

"Batch is not a demo-day surprise" is what this lens exists for, so it checks two different
things and they are not interchangeable:

  * **The recorded run** (`prep/out/batch_results.json`) - proof the full sweep has actually
    been executed at full scale, and what it found. A wall-clock number is a property of a
    machine as much as of the code, so it is recorded with the run rather than re-measured here.
  * **A live re-run of a few seeds** - proof the recording still describes THIS code. A
    committed artifact can go stale silently, which is the one failure mode a recorded
    measurement adds. Three seeds re-run and compared exactly is cheap and catches drift.

It also carries the controls on the headline number. `capacity_held_back_vs_oracle_pct` is the
number Slide 3 puts on the projector, and it is a ratio against a ceiling - so the ceiling being
real is part of the measurement, not a footnote.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "prep" / "out" / "batch_results.json"
BAR_S = 20 * 60


@pytest.fixture(scope="module")
def batch() -> dict:
    if not RESULTS.exists():
        pytest.fail(
            f"{RESULTS.relative_to(ROOT)} does not exist. The perf lens cannot pass on a sweep "
            f"that was never run: `python run.py batch 1000`.")
    return json.loads(RESULTS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the bar
def test_the_sweep_ran_at_full_scale_and_inside_the_bar(batch):
    assert batch["n_seeds"] >= 1000, (
        f"the recorded sweep is {batch['n_seeds']} seeds; SPEC 11 asks for 1000. A smaller "
        f"sweep is a different measurement, not a cheaper one.")
    assert batch["devices"] == 1000, f"{batch['devices']} devices; the bar is 1k"
    assert batch["runtime_s"] <= BAR_S, (
        f"the sweep took {batch['runtime_s']:,.0f}s against a {BAR_S}s bar. Batch running long "
        f"is exactly the demo-day surprise this lens exists to prevent.")


def test_the_recorded_sweep_still_describes_this_code(batch):
    """Re-run three seeds live and demand the same numbers.

    Without this the lens would be reading a file, not testing a system - and the file would go
    on passing for as long as nobody re-baked it.
    """
    from runner.batch import run_seed

    by_seed = {r["seed"]: r for r in batch["rows"]}
    for seed in (0, 1, 7):
        if seed not in by_seed:
            continue
        fresh, old = run_seed(seed), by_seed[seed]
        assert fresh["faults"] == old["faults"], (
            f"seed {seed} now draws different faults ({fresh['faults']!r} vs {old['faults']!r}). "
            f"sim/chaos.py moved; re-run `python run.py batch 1000`.")
        for c in ("headroom", "reasonable"):
            assert fresh[c]["committed_kwh"] == pytest.approx(old[c]["committed_kwh"], rel=1e-6), (
                f"seed {seed}/{c} committed {fresh[c]['committed_kwh']:,.1f} kWh now, "
                f"{old[c]['committed_kwh']:,.1f} in the recorded sweep. The batch results are "
                f"STALE - re-run `python run.py batch 1000` and commit it.")


def test_a_single_run_is_still_fast_enough_to_make_the_bar(batch):
    """A live throughput floor, so a 10x slowdown fails here and not at 3am before the event."""
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    t = time.perf_counter()
    run_scenario("headroom", seed=5, faults=draw_faults(5))
    single = time.perf_counter() - t
    budget = BAR_S * batch["workers"] / max(1, batch["runs"])
    assert single <= budget, (
        f"one run takes {single:.2f}s; to finish {batch['runs']:,} runs on "
        f"{batch['workers']} workers inside {BAR_S}s it must stay under {budget:.2f}s.")


# ---------------------------------------------------------------- the ceiling is real
def test_the_oracle_is_a_ceiling_it_actually_reached(batch):
    """Every held-back % is a ratio against the oracle. If the oracle breached its own promises
    it is not a ceiling, it is just a bigger number, and the whole of Slide 3 is inflated."""
    assert batch["ceiling_is_valid"], (
        f"the oracle breached on seeds {batch['ceiling_invalid_seeds']}. It admitted more than "
        f"it could keep, so `capacity_held_back_vs_oracle_pct` is inflated by however much it "
        f"over-promised. Do not put a held-back number on a slide until this is green.")


# The sweep's residual, named. S1 and S2 are clean; 1,000 seeded evenings are not QUITE, and
# pretending otherwise would be the exact failure this project keeps finding in itself.
#
# Seeds 60 and 886 draw `R2 21:00+10m` and `R4 21:00+10m`: a region goes dark at **21:00:00**,
# to the second - the instant the 20:00-21:00
# award ends. The controller learns of it on the same tick it is scored on - there is no earlier
# moment at which anything could have been said. Delivery collapses for that one tick because
# the senior AS earmark, now spread over 320 devices instead of 400, absorbs what little margin
# is left at the end of the evening. One scored bucket, out of roughly 60,000 in the sweep.
#
# It is listed by seed rather than absorbed into a threshold: a count can quietly grow, a named
# set cannot. Any OTHER seed going silent fails this test.
KNOWN_SILENT_SEEDS = {60, 886}


def test_headroom_kept_every_promise_it_had_time_to_keep(batch):
    a = batch["aggregate"]["headroom"]
    silent = {r["seed"] for r in batch["rows"] if r["headroom"]["silent"] > 0}
    assert silent <= KNOWN_SILENT_SEEDS, (
        f"headroom went silent on seeds {sorted(silent - KNOWN_SILENT_SEEDS)}, which are not the "
        f"known boundary case. The claim is that it does not miss silently; a NEW silent seed is "
        f"a defect, not a threshold to widen. Investigate before touching this set.")
    assert a["silent_buckets_total"] <= 2, (
        f"{a['silent_buckets_total']} silent buckets on the known seeds - it was 2. The "
        f"boundary case got worse, which means something other than the boundary changed.")


def test_any_residual_silence_was_genuinely_unforeseeable(batch):
    """The mechanism, not just the count.

    A silent breach is only forgivable if there was no earlier moment to speak. That means the
    fault must land inside the LAST bucket of a claim - after that, per-bucket admission has no
    free bucket left to revise and `SPEC 6.3`'s late Notice has nothing left to be late about.
    If a silent seed ever appears whose fault landed with time to spare, the Notice path is
    broken and the count is hiding it.
    """
    for row in batch["rows"]:
        if row["headroom"]["silent"] == 0:
            continue
        assert row["n_faults"] > 0, (
            f"seed {row['seed']} went silent on an evening with NO faults at all. Nothing "
            f"external caused it, so the controller broke its own promise unprompted.")
        assert "21:0" in row["faults"] or "20:5" in row["faults"], (
            f"seed {row['seed']} went silent on faults {row['faults']!r}, which did not land in "
            f"the final bucket of the award. There was time to issue a Notice and none was "
            f"issued - that is the Notice path failing, not a boundary.")


# ---------------------------------------------------------------- FINDING-24/25, closed
# The sweep found a floor exposure S1 and S2 cannot show, and it was NOT a controller bug -
# `reasonable` had it worse (115 seeds vs 96). Two causes, both now fixed:
#
#   FINDING-24  the allocator re-spread the FULL booked kW over only the devices it could still
#               reach, while the dark ones kept running their last setpoint. The dark region's
#               share went out TWICE. Seed 52: 3,000 -> 3,600 kW at 18:15, exactly R2's 20%.
#   FINDING-25  `build_fleet` set every lease to +inf, so leases never expired and SAFE_HOLD
#               never happened. A dark device held a discharge setpoint for HOURS, drove itself
#               onto its floor, and `aux` - which no guard clamps, by design - ate through the
#               homeowner's reserve. 96 of 1,000 evenings.
#
# The tests below pin the CAUSE, not the count. A floor-breach count can be driven to zero by
# luck, by a quieter fault draw, or by a metric that stopped counting; "delivery never exceeds
# what was sold" can only be true if the mechanism is actually working.


def test_the_backup_floor_is_never_crossed_across_the_whole_sweep(batch):
    for c in ("headroom", "reasonable"):
        seeds = [r["seed"] for r in batch["rows"] if r[c][
            "floor_s"] > 0]
        assert not seeds, (
            f"{c} crossed the homeowner's backup floor on seeds {seeds[:10]} "
            f"({len(seeds)} of {batch['n_seeds']}). That reserve is the customer promise; it "
            f"was 96/1000 before FINDING-24/25 and must stay at zero.")


def test_a_dark_region_does_not_get_its_energy_delivered_twice():
    """FINDING-24's mechanism, asserted directly on the run that exposed it.

    A device we cannot reach cannot be told to stop, so it keeps running its last setpoint. If
    the allocator then asks the devices it CAN reach for the full booking, the scope delivers
    the dark region's share twice. Measured before the fix on seed 52: 3,000 -> 3,600 kW the
    instant R2 went dark, and the surplus came out of the backup floor.
    """
    from runner.run import run_scenario
    from sim.chaos import draw_faults

    r = run_scenario("headroom", seed=52, faults=draw_faults(52))
    worst = max(((f["delivered_truth_kw"] / f["booked_kw"], f["ts"]) for f in r.frames
                 if f["booked_kw"] > 0), default=(0.0, 0.0))
    ratio, ts = worst
    assert ratio <= 1.05, (
        f"at {int(ts // 3600) % 24:02d}:{int(ts % 3600) // 60:02d} the scope delivered "
        f"{ratio:.0%} of what it sold. Over-delivery is not a happy accident - it is unpaid "
        f"energy taken out of the customer's reserve, and it is how FINDING-24 presented.")


def test_lease_expiry_actually_stops_a_dark_device():
    """FINDING-25's mechanism. SPEC 6.6: the last setpoint runs until the lease expires, then
    SAFE_HOLD. `build_fleet` had every lease at +inf, so "until" never arrived.

    Asserted on physics, not on a metric: a region that goes dark and never comes back must
    stop contributing within a lease period, or it will discharge itself through its floor.
    """
    from config import DEFAULTS, ct
    from runner.run import RegionOutage, run_scenario

    dark_to_the_end = (RegionOutage("R2", ct(17, 0), ct(23, 0), "comms"),)
    r = run_scenario("headroom", seed=0, faults=dark_to_the_end)
    assert r.metrics["floor_breach_dev_s"] == 0.0, (
        f"a region dark from 17:00 to the horizon drove {r.metrics['floor_breach_dev_s']:,.0f} "
        f"device-seconds below the backup floor. Its lease is not expiring, so it is still "
        f"discharging on a command nobody can withdraw.")
    del DEFAULTS


def test_the_baseline_still_fails_the_way_the_pitch_says_it_does(batch):
    """The control on the comparison. A clean `reasonable` means the sweep is measuring nothing
    - and it would read as a result rather than as a broken harness."""
    r = batch["aggregate"]["reasonable"]
    assert r["silent_buckets_total"] > 0, (
        "`reasonable` had zero silent breaches across the whole sweep. Either the chaos draw "
        "stopped producing faults or the metric stopped counting - both make the headline "
        "comparison meaningless.")
    assert r["capacity_availability_pct_median"] < 90.0, (
        f"`reasonable` holds {r['capacity_availability_pct_median']:.0f}% capacity availability "
        f"across the sweep; the pitch says it loses the hold it is paid to keep.")


def test_the_cost_of_honesty_is_a_number_we_can_say_out_loud(batch):
    """Guards the headline from both directions.

    0% would mean the oracle is not actually a ceiling and the comparison is broken. A very
    large number would mean the reserve is so expensive that a judge is right to ask why anyone
    would buy it. Either way the slide needs re-reading before it is said on stage.
    """
    hb = batch["aggregate"]["headroom"]["held_back_pct_median"]
    assert 0.0 < hb < 25.0, (
        f"headroom holds back {hb:.2f}% of the oracle across the sweep. Outside 0-25% this "
        f"stops being 'the price of honesty' and becomes something to explain - re-read "
        f"DEMO.md Slide 3 before quoting it.")


def test_where_we_lose_is_populated_and_could_have_been_empty(batch):
    """SPEC 14 asks for a 'where we lose' panel. A panel that cannot come back empty is
    decoration; this asserts it was filled by the data, not by the renderer."""
    w = batch["where_we_lose"]
    quiet = w["quiet_evenings_we_still_held_back"]
    assert quiet, (
        "no quiet evening cost us anything. Either the chaos draw stopped producing quiet "
        "seeds (sim/chaos.py reserves ~25% of them) or held-back stopped being computed.")
    assert all(row["held_back_pct"] > 0 for row in quiet), (
        "a 'where we lose' row that cost nothing is not a loss - the panel is being padded.")
