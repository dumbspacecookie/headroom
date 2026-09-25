"""DEMO.md §7's assertions, run against the real thing. SPEC §11 "demo" layer.

Every "Must be on screen" cell in DEMO.md §2 is a claim made to a judge. This file is where those
claims are checked, so a number in the script that stopped being true fails the build instead of
failing on stage.
"""
from __future__ import annotations

import pytest

from config import DEFAULTS, ct
from contracts.types import BindingReason
from control.ledger import Ledger
from runner.run import RegionOutage, run_scenario
from scenarios.s1 import build_s1

S2_FAULT = (RegionOutage("R3", ct(20, 15), ct(20, 45), "comms"),)


@pytest.fixture(scope="module")
def s1_head():
    return run_scenario("headroom")


@pytest.fixture(scope="module")
def s1_reas():
    return run_scenario("reasonable")


# ---------------------------------------------------------------- Beat A
def test_beat_A_ader_energy_is_partial_energy_says_928_the_reserve_says_763():
    """Two stages, both on screen since 2026-09-24 (RATIONALE.md s6c).

    The energy arithmetic still sells 928 kW, bound by kWh slack - that is D1, and it is still
    true. The decision is 763 kW, bound by the per-device N-1 reserve: once the co-op has
    emptied the small units, losing any one region at 20:xx would leave the AS hold without the
    kW it needs. If the final reason is 'feeder' or 'kw_room', neither story is what is on screen.
    """
    s1 = build_s1()
    energy_only = Ledger(s1.belief, now=s1.t0)
    energy_only.admit_priority(s1.claims)
    a1 = energy_only.admitted_by_id("ADER_ENERGY")
    assert a1.admitted_kw == pytest.approx(928.0, abs=10.0)
    assert a1.reason is BindingReason.KWH_SLACK

    led = Ledger(s1.belief, now=s1.t0)
    led.admit_all(s1.claims)
    a = led.admitted_by_id("ADER_ENERGY")
    assert a.admitted_kw == pytest.approx(763.0, abs=10.0)
    assert a.reason is BindingReason.RESERVE, f"final binding reason is {a.reason.value}"


def test_beat_A_the_squeeze_is_visible_five_hours_before_delivery():
    """'And it says so at 3pm. Five hours before delivery.' - the lead time IS the pitch."""
    s1 = build_s1()
    lead_s = next(c for c in s1.claims if c.claim_id == "ADER_ENERGY").start_ts - s1.t0
    assert lead_s >= 5 * 3600, f"only {lead_s/3600:.1f} h of lead, the script claims five"


def test_beat_A_d1_toggle_flips_partial_to_full():
    """Key `1`. 'Take the co-op away - full two megawatts. Put it back - 928.'"""
    with_coop = build_s1(include_coop=True)
    led_a = Ledger(with_coop.belief, now=with_coop.t0)
    led_a.admit_all(with_coop.claims)
    without = build_s1(include_coop=False)
    led_b = Ledger(without.belief, now=without.t0)
    led_b.admit_all(without.claims)

    # With the co-op: 763 kW, the reserve binding. Without it: the full award. The reserve only
    # binds BECAUSE the co-op drained the small units first - which is the collision itself.
    assert led_a.admitted_by_id("ADER_ENERGY").admitted_kw == pytest.approx(763.0, abs=10.0)
    assert led_b.admitted_by_id("ADER_ENERGY").is_full, "removing the co-op did not free it"
    # D1's 1.072 MWh is the ENERGY term's cut; tests/known_answer/test_d1.py pins it on stage 1.


# ---------------------------------------------------------------- Beat B
def test_beat_B_headroom_has_zero_silent_breaches_on_S1_and_S2(s1_head):
    assert s1_head.metrics["silent_breach_buckets"] == 0
    s2 = run_scenario("headroom", faults=S2_FAULT)
    assert s2.metrics["silent_breach_buckets"] == 0, (
        "the script says 'silent breaches 0' on screen during Beat B")


def test_beat_B_reasonable_keeps_its_ENERGY_promise_and_loses_the_CAPACITY_hold(s1_reas):
    """The line the demo must say, and the one it must NOT.

    DEMO.md carries a warning against saying 'reasonable under-delivers' on S1. This is the
    assertion behind that warning: its energy promise is ~100% and everything it loses is the
    ancillary-service hold (PRACTICE-NOTES.md FINDING-6).
    """
    m = s1_reas.metrics
    assert m["promise_kept_pct"] >= 99.0, (
        f"reasonable kept only {m['promise_kept_pct']:.1f}% of its ENERGY promise. If this ever "
        f"drops, 'it under-delivers' becomes true and DEMO.md's warning is wrong."
    )
    assert m["silent_capacity_buckets"] > 0, "the S1 miss vanished"
    assert m["silent_energy_buckets"] == 0, (
        "reasonable is now breaching ENERGY too, which makes it look like a strawman")
    assert m["capacity_availability_pct"] < 90.0, (
        f"capacity availability {m['capacity_availability_pct']:.0f}% - the hold is fine, so "
        f"there is nothing to show")


def test_beat_B_headroom_beats_reasonable_on_the_metric_the_pitch_names(s1_head, s1_reas):
    assert (s1_head.metrics["capacity_availability_pct"]
            > s1_reas.metrics["capacity_availability_pct"] + 10.0)


# ---------------------------------------------------------------- honesty
def test_no_controller_breaches_the_backup_floor(s1_head, s1_reas):
    """The homeowner's reserve. If either controller crosses it, nothing else in the demo matters."""
    for r in (s1_head, s1_reas):
        assert r.metrics["floor_breach_dev_s"] == 0.0, f"{r.controller} crossed the floor"


def test_runs_are_deterministic():
    a, b = run_scenario("headroom"), run_scenario("headroom")
    assert a.events_sha256 == b.events_sha256


# ---------------------------------------------------------------- Beat B, per-bucket
# These replace a strict-xfail that FAILED FOR PASSING the moment per-bucket admission landed
# (2026-09-17), which is exactly what it was for: an over-claim that quietly becomes true is
# still an over-claim until someone re-reads the script. DEMO.md Beat B was re-read and three
# of its cells were wrong - the cut (0.61 MW, never true), the cause ("N-1 / reachability";
# it is kwh_slack) and "right now is covered" (the HOLD is; the energy award runs ~5% light
# for the five minutes already in flight). All three are fixed in DEMO.md and asserted here.


def _notices(r):
    return [e for e in r.events if e["kind"] == "notice"]


def _ahead(r):
    return [e for e in _notices(r) if "ahead of delivery" in e["text"]]


def _late(r):
    return [e for e in _notices(r) if "IN DELIVERY - late" in e["text"]]


def test_beat_B_notice_arrives_ahead_of_the_buckets_it_affects():
    """'ahead of the buckets it hits, naming the cause.' Now true, and this is why.

    The lock unit is the BUCKET. R3 goes dark at 20:15 and nothing re-plans until 20:20
    (FINDING-26: an outage is noticed at the next bucket boundary, up to four minutes later). At
    20:20 the bucket being dispatched (20:20-20:25) is already sold and stays sold; the pass
    revises from 20:25 on. So the lead is one full bucket - five minutes - and never zero. A lead of 0 means something re-planned a bucket it was already
    delivering, which is not an early warning, it is rewriting history.
    """
    r = run_scenario("headroom", faults=S2_FAULT)
    assert _ahead(r), "no Notice arrived ahead of the buckets it affects"
    assert r.metrics["median_lead_time_s"] == 300.0, (
        f"median lead {r.metrics['median_lead_time_s']}s; DEMO.md says 5m ahead")


def test_beat_B_the_bucket_already_in_delivery_is_announced_late_not_hidden(s1_head):
    """The other half of SPEC 6.3's reconcile rule, and the one that was missing.

    A bucket being dispatched cannot be un-sold, so per-bucket admission leaves it alone - and
    for a while that meant nothing was said about it at all. The 1,000-seed sweep found the
    cost: on seeds where a region went dark inside the LAST bucket of an award there was no free
    bucket to revise, so no Notice fired and the miss scored SILENT. (FINDING-22.)
    """
    del s1_head
    # Since 2026-09-24 S2's bucket in delivery is covered (the per-device N-1 stage sold 763 kW,
    # sized to survive losing R3), so S2 no longer exercises this path. Seed 76 does: a SECOND
    # region goes dark at 20:25, beyond what an N-1 reserve protects, and the AS hold's bucket in
    # delivery really is short. It must be announced late - and quote the bucket's own figure.
    from sim.chaos import draw_faults

    r = run_scenario("headroom", seed=76, faults=draw_faults(76))
    late = _late(r)
    assert late, "a bucket genuinely short in delivery produced no late Notice"
    assert "ADER_AS" in late[0]["text"] and "1,500" in late[0]["text"], late[0]["text"]
    assert r.metrics["silent_breach_buckets"] == 0, (
        "the in-flight shortfall must be LATE, never silent - it is announced the moment it is "
        "known, which is the whole claim")
    assert r.metrics["late_breach_buckets"] > 0, "the shortfall the Notice announced did not happen"


def test_beat_B_a_covered_bucket_gets_no_late_notice():
    """The late Notice used to fire on S2 quoting 209 kW while truth delivered the bucket in full
    - a correct answer to "is the rest of the window sellable?" quoted as the bucket's. It now asks
    the bucket's own question (`control/deliverability.in_flight_kw`). A Notice that cries wolf is
    the notice-spam SPEC 14 says to expose; this pins that it does not."""
    r = run_scenario("headroom", faults=S2_FAULT)
    assert not _late(r), f"false alarm: {_late(r)[0]['text']!r}"


def test_beat_B_the_notice_says_the_cut_the_time_and_the_cause():
    """Every token DEMO.md puts on screen, read off the event a judge sees."""
    r = run_scenario("headroom", faults=S2_FAULT)
    text = _ahead(r)[0]["text"]
    for token in ("ADER_ENERGY", "763", "120", "reserve", "20:25", "5 min ahead"):
        assert token in text, f"{token!r} missing from the Notice on screen: {text!r}"


def test_beat_B_the_senior_hold_survives_and_the_junior_claim_absorbs_the_loss():
    """The point of the ladder, and the strongest thing on the slide.

    R3 dark costs the scope ~20% of its believed energy. ADER_AS (priority 2) keeps all
    1,500 kW and capacity availability stays 100%; ADER_ENERGY (priority 4) takes the entire
    cut. If this ever inverts, the pitch is making a claim about priority that the code is not.
    """
    r = run_scenario("headroom", faults=S2_FAULT)
    by_id = {a.claim_id: a for a in r.admissions}
    assert by_id["ADER_AS"].is_full, "the ancillary-service hold was cut - the ladder inverted"
    assert r.metrics["capacity_availability_pct"] == 100.0
    # 84, not the 183 it was until FINDING-26: the four minutes before anyone notices R3 are
    # carried by the lit regions, and that energy is gone when the tail is re-planned.
    assert by_id["ADER_ENERGY"].admitted_kw == pytest.approx(84.0, abs=15.0)
    assert by_id["ADER_ENERGY"].reason is BindingReason.RESERVE


def test_beat_B_the_in_flight_bucket_is_now_covered_and_that_is_measured_not_assumed():
    """The bucket already in delivery when R3 goes dark. Its history is the point of this test.

    Until 2026-09-24 it ran light: locked at 928 kW, the fleet minus a fifth of itself delivered
    ~94.7% for five minutes, a LATE breach - so DEMO.md said never to claim "right now is
    covered". The per-device N-1 stage sells 763 kW instead, sized so that losing any one region
    still leaves the AS hold AND this bucket deliverable. Truth now delivers it in full.

    "Covered" is therefore a measured claim now, and it stays one: if the bucket ever runs light
    again, this fails and the script goes back to not saying it. Since FINDING-26 there are TWO
    such buckets: 20:15-20:20, when R3 is dark and nobody knows yet, and 20:20-20:25, in flight
    when the re-plan notices. The reserve carries both. It still must not be achieved by
    lowering the bucket in flight - that is FINDING-18's marking-your-own-homework.
    """
    r = run_scenario("headroom", faults=S2_FAULT)
    assert r.metrics["silent_breach_buckets"] == 0, "the script says 'silent breaches 0'"
    ader = next(a for a in r.admissions if a.claim_id == "ADER_ENERGY")
    assert ader.locked_kw[ct(20, 20)] == pytest.approx(763.0, abs=10.0)
    assert ader.locked_kw[ct(20, 25)] == pytest.approx(763.0, abs=10.0), (
        "the in-flight bucket was lowered - that is marking your own homework, re-read "
        "PRACTICE-NOTES.md FINDING-18.")
    ratios = [f["delivered_truth_kw"] / f["booked_kw"] for f in r.frames
              if ct(20, 15) < f["ts"] <= ct(20, 25) and f["booked_kw"] > 0]
    assert ratios and min(ratios) >= 1.0 - DEFAULTS.tolerance_frac, (
        f"the in-flight bucket runs light again ({min(ratios):.3f}); DEMO.md may not say "
        f"'right now is covered'.")
    assert r.metrics["late_breach_buckets"] == 0


def test_started_buckets_are_never_rewritten():
    """The lock invariant, read off the final profile.

    Buckets that had started when the re-plan noticed R3 (20:20) must still hold the 763 kW they
    were sold for; only the tail moves. A locked bucket that changed value means something re-planned the past.
    """
    r = run_scenario("headroom", faults=S2_FAULT)
    ader = next(a for a in r.admissions if a.claim_id == "ADER_ENERGY")
    before = [kw for b, kw in ader.locked_kw.items() if b <= ct(20, 25)]
    after = [kw for b, kw in ader.locked_kw.items() if b > ct(20, 25)]
    assert before and after, "the fault did not split the profile"
    assert all(kw == pytest.approx(763.0, abs=10.0) for kw in before), (
        f"a bucket already in delivery was rewritten: {sorted(set(before))}")
    tail = [kw for _, kw in sorted((b, kw) for b, kw in ader.locked_kw.items() if b > ct(20, 25))]
    assert tail[0] == pytest.approx(120.0, abs=10.0), tail
    assert all(b <= a + 1e-9 for a, b in zip(tail, tail[1:])), f"the tail went UP: {tail}"


def test_reconcile_never_leaves_a_partial_blaming_nothing():
    """A PARTIAL with reason NONE is 'a UI that lies' - the property lens says so, on the
    LEDGER. It cannot see this: the property lens never runs reconcile.

    Found 2026-09-17 in my own per-bucket work. R3 comes back at 20:45, the ledger re-admits
    ADER_ENERGY in full, the downgrade-only clamp held the kW at 124 and let the reason go to
    NONE - 124 of 2,000 kW, blaming nothing, on screen. A decision is four fields and the
    clamp was carrying one. (PRACTICE-NOTES.md FINDING-19.)
    """
    for faults in ((), S2_FAULT):
        r = run_scenario("headroom", faults=faults)
        for a in r.admissions:
            if a.is_full:
                assert a.reason is BindingReason.NONE, f"{a.claim_id} got it all, blames {a.reason}"
            else:
                assert a.reason is not BindingReason.NONE, (
                    f"{a.claim_id} admitted {a.admitted_kw:,.0f} of {a.requested_kw:,.0f} kW "
                    f"and blames nothing")


def test_no_notice_is_issued_about_a_claim_whose_window_has_closed():
    """COOP_PEAK ran 15:30-18:30. At 20:15 it is settled and must not be mentioned.

    The pre-per-bucket reconcile recomputed it anyway: no hours left to divide by, so its kWh
    term was +inf, and its kW room was read off buckets an hour and three quarters in the past.
    It duly 'cut' 3,000 -> 2,870 kW and told a judge about it. Notice-spam of exactly the kind
    SPEC 14 says to expose, generated by us. (PRACTICE-NOTES.md FINDING-20.)
    """
    r = run_scenario("headroom", faults=S2_FAULT)
    for e in _notices(r):
        assert e["ts"] <= ct(18, 30) or "COOP_PEAK" not in e["text"], (
            f"a settled claim was re-litigated at {e['ts']/3600:.2f}h: {e['text']!r}")


# ---------------------------------------------------------------- the clock
def test_the_spoken_script_still_fits_the_three_minute_bar():
    """SPEC 15 gives the pitch 3:00. The Say column is the only part that costs time.

    Added 2026-09-17: rewriting Beat B for per-bucket admission pushed the script to 436 words,
    3:00.4 at 145 wpm - over the bar, by an edit whose whole purpose was honesty. The count was
    a sentence in DEMO.md that nothing checked, so it had already drifted from its stated 429
    before this session touched it. A stated measurement with no test is a claim, not a number.
    """
    import pathlib
    import re

    lines = (pathlib.Path(__file__).resolve().parents[2] / "DEMO.md").read_text(
        encoding="utf-8").splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith("## ")]
    lo = next(i for i in starts if lines[i].startswith("## 2."))
    hi = next(i for i in starts if i > lo)
    words = sum(len(q.split()) for l in lines[lo:hi] if l.startswith("|")
                for q in re.findall(r'"([^"]+)"', l))
    assert words == pytest.approx(433, abs=2), (
        f"the Say column is {words} words; DEMO.md's timing line says 433. Update BOTH or "
        f"neither - the stated number is read by whoever rehearses it.")
    assert words / 145.0 * 60.0 <= 180.0, (
        f"{words} words is {words/145.0*60.0:.0f}s at 145 wpm, over the 3:00 bar in SPEC 15")


def test_the_preflight_states_the_real_lens_count():
    """DEMO.md pre-flight check 6 names a number that `tools/gate.py` owns.

    It said **8** while the gate expected **12** - stale since the self-audit added four of
    SPEC 11's layers. The one checklist whose job is to catch staleness, stale. Found
    2026-09-17 while answering "what's next", not by the checklist.
    """
    import pathlib
    import re

    from tools.gate import EXPECTED_LENSES

    demo = (pathlib.Path(__file__).resolve().parents[2] / "DEMO.md").read_text(encoding="utf-8")
    row = next(l for l in demo.splitlines() if "run.py gate`" in l and "lenses expected" in l)
    stated = [int(n) for n in re.findall(r"\*\*(\d+) lenses expected, (\d+) ran, (\d+) passed\*\*",
                                         row)[0]]
    assert stated == [EXPECTED_LENSES] * 3, (
        f"pre-flight check 6 says {stated}; tools/gate.py expects {EXPECTED_LENSES} lenses and "
        f"demo day needs every one of them to RUN. Update DEMO.md, not this test.")


# ---------------------------------------------------------------- the demo surface
# `web/demo.html` is a static file: no server, no port, nothing to start in front of judges.
# That buys away a whole class of stage failure and buys in exactly one: the page can show a
# run that is older than the code. These are the lenses on that.
def _bake_module():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[2] / "prep" / "bake_runs.py"
    spec = importlib.util.spec_from_file_location("bake_runs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_baked_demo_is_not_stale():
    """What is on the projector must be what this commit computes.

    The page reads `web/out/runs.js`, which is baked, committed, and therefore able to drift
    from the code silently - the one failure a static page adds. Re-bake with
    `python prep/bake_runs.py` and commit the result.
    """
    import json

    bake = _bake_module()
    assert bake.OUT.exists(), "web/out/runs.json has never been baked"
    committed = json.loads(bake.OUT.read_text(encoding="utf-8"))
    fresh = bake.build()
    for d in (committed, fresh):
        d.pop("git_head", None)          # moves every commit; not a fact about the runs
    # Compare DIGESTS, never the strings. The bake is megabytes, and a failing `==` on two
    # strings that size sends pytest's assertion diff into difflib for over an hour - the gate
    # hung rather than failed the first time the bake genuinely went stale (2026-09-24).
    import hashlib

    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    was = digest(json.dumps(committed, sort_keys=True))
    now = digest(json.dumps(fresh, sort_keys=True))
    assert was == now, (
        f"web/out/runs.json is STALE ({was} committed vs {now} fresh). Run "
        f"`python prep/bake_runs.py` and commit it, or the demo shows numbers from an older "
        f"build than the one the gate just passed.")
    js, wrapped = bake.OUT_JS.read_text(encoding="utf-8"), bake.wrap(
        bake.OUT.read_text(encoding="utf-8"))
    assert digest(js) == digest(wrapped), "runs.js is not the wrapper of runs.json"


def test_the_demo_page_makes_no_network_request():
    """Pre-flight check 3's pass criterion, asserted instead of eyeballed in devtools.

    A webfont or a CDN script is one bad conference wifi away from a blank projector. The page
    must reference nothing but its own baked data.
    """
    import pathlib
    import re

    html = (pathlib.Path(__file__).resolve().parents[2] / "web" / "demo.html").read_text(
        encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", html, flags=re.S)       # the header comment explains why
    for pattern in ("http://", "https://", "//fonts.", "cdn.", "@import"):
        assert pattern not in body, f"{pattern!r} in demo.html - that is a network request"
    assert '<script src="out/runs.js">' in body, (
        "the page must load its data with a <script src>. fetch() on a file:// origin is "
        "blocked by Chrome as cross-origin, so a fetch-based page dies on the demo laptop.")


def test_the_bake_carries_every_number_beat_A_and_beat_B_put_on_screen():
    """DEMO.md's 'Must be on screen' cells, read out of the artifact the projector reads."""
    import json

    bake = _bake_module()
    d = json.loads(bake.OUT.read_text(encoding="utf-8"))

    ader = next(b for b in d["d1"]["with_coop"] if b["claim_id"] == "ADER_ENERGY")
    assert ader["admitted_kw"] == pytest.approx(763.0, abs=10.0) and not ader["is_full"]
    assert ader["reason"] == "reserve"
    assert d["d1"]["energy_term_kw"] == pytest.approx(928.0, abs=10.0), "D1's energy term moved"
    assert d["d1"]["cut_mwh"] == pytest.approx(1.072, abs=0.05) and d["d1"]["green"]
    assert next(b for b in d["d1"]["without_coop"]
                if b["claim_id"] == "ADER_ENERGY")["is_full"], "key 1 does not free the award"

    tower = d["runs"]["tower"]
    notices = [e for e in tower["events"] if e["kind"] == "notice"]
    ahead = [e for e in notices if "ahead of delivery" in e["text"]]
    late = [e for e in notices if "IN DELIVERY - late" in e["text"]]
    # No late Notice: the bucket in delivery is covered since 2026-09-24. Then the tail is cut
    # five minutes ahead - noticed at 20:20 (763 -> 120), then 120 -> 87 -> 84 as R3 stays dark.
    assert len(late) == 0 and 1 <= len(ahead) <= 4, [e["text"] for e in notices]
    assert len(late) + len(ahead) == len(notices), [e["text"] for e in notices]
    for token in ("763", "120", "reserve", "20:25", "5 min ahead"):
        assert token in ahead[0]["text"], f"{token!r} missing from the Notice on the projector"
    assert tower["metrics"]["silent_breach_buckets"] == 0
    assert tower["metrics"]["capacity_availability_pct"] == 100.0

    assert d["runs"]["reasonable"]["metrics"]["silent_capacity_buckets"] > 0, (
        "the baseline's silent loss is what Beat B compares against")
    assert "4" in d["not_built"], (
        "key 4 must be declared unbuilt in the bake so the page can say so out loud")


# ---------------------------------------------------------------- Slide 3
def test_slide_3_quotes_the_batch_artifact_and_not_a_memory_of_it():
    """Every number in Slide 3's "must be on screen" cell, read out of the sweep that produced it.

    Slide 3 sat on three `[TBD@H26]` placeholders until the batch runner existed. Filling them by
    hand from a terminal I had just read is exactly how `0.61 MW` got into Beat B and stayed
    there for a week without ever having been produced by any build. So they are asserted.
    """
    import json
    import pathlib as _p

    root = _p.Path(__file__).resolve().parents[2]
    batch = json.loads((root / "prep" / "out" / "batch_results.json").read_text(encoding="utf-8"))
    row = next(l for l in (root / "DEMO.md").read_text(encoding="utf-8").splitlines()
               if l.startswith("| Batch tab"))
    screen = row.split("|")[3]                       # the "Must be on screen" cell

    hb = batch["aggregate"]["headroom"]
    rs = batch["aggregate"]["reasonable"]
    want = {
        "the seed count": f"{batch['n_seeds']:,}",
        "held back": f"{hb['held_back_pct_median']:.1f}%",
        "held back p90": f"{hb['held_back_pct_p90']:.1f}%",
        "reasonable held back": f"{abs(rs['held_back_pct_median']):.1f}%",
        "headroom silent buckets": f"{hb['silent_buckets_total']:,}",
        "reasonable silent buckets": f"{rs['silent_buckets_total']:,}",
    }
    missing = {k: v for k, v in want.items() if v not in screen}
    assert not missing, (
        f"Slide 3 no longer quotes the sweep: {missing} absent from {screen.strip()!r}. "
        f"Re-read DEMO.md against prep/out/batch_results.json - do not edit this test.")

    # And the direction of the headline, which is the entire argument: `reasonable` does not
    # hold anything back, it books PAST what a perfect-information controller could deliver.
    # The SPOKEN claim is a floor - "more than ten per cent" - precisely so it cannot go stale
    # the way `0.61 MW` did. It still has to be true. It said fifteen until 2026-09-24, when the
    # oracle ceiling was corrected (RATIONALE.md s6c) and the sweep moved to -13.2%: the words
    # and this bound moved together, to the truth, in the same change.
    assert abs(rs["held_back_pct_median"]) > 10.0, (
        f"the script says `reasonable` over-promises by MORE THAN ten per cent; the sweep "
        f"says {rs['held_back_pct_median']:.1f}%. Re-read Beat B before saying it out loud.")
    assert rs["held_back_pct_median"] < 0 < hb["held_back_pct_median"], (
        f"headroom held back {hb['held_back_pct_median']}%, reasonable "
        f"{rs['held_back_pct_median']}%. The slide says one is cautious and the other "
        f"over-commits; if that flips, the slide is wrong, not the data.")
