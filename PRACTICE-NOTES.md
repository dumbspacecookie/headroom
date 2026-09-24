# PRACTICE-NOTES

Output of the SPEC §15 prep-week practice build, run 2026-09-16.

**What was built:** 50 devices (S1 scaled 1:8 so the arithmetic stays hand-checkable against §8.1),
one CAPACITY hold + two ENERGY claims, SPEC §6.3 admission implemented **literally as written**,
§6.4 allocator with per-device kWh cap and CAPACITY earmark, a truth sim with perfect comms,
`reasonable` alongside `headroom`, and the §12 energy-left chart.

**The code is deleted**, per §15 — a spike must not become the build's foundation. It stays in git
history at commit **`e37c6e6`** (`practice/spike.py`, `practice/chart.py`); restore a copy for a
one-off re-check with `git show e37c6e6:practice/spike.py > /tmp/spike.py`. (A moving ref like
`HEAD~1` would be wrong by the next commit.) The chart it produced is kept at
`prep/out/practice_energy_left.png`.

**It is also a control.** The spike computes S1 from the §6.3 formulas over a full 5-minute bucket
sweep; `prep/size_s1.py` computes the same thing in closed form. After FINDING-9 was fixed the two
agree exactly: spike 116.0 kW admitted / 134.0 kWh cut at 50 devices, sizing script 928 kW /
1,072 kWh at 400 — a factor of 8, to the decimal. Before the fix they differed by 10 kWh. Any
future change to `size_s1.py` should be re-checked against the restored spike.

**Headline: the §6.3 arithmetic works, and D1 reproduces.** But nine things in §6.3 / §6.4 / §10
cannot be typed in as written, and one of them lets Headroom become the thing it criticises.

**FINDING-11 was added later**, on the day the ledger was built, by the property lens - see the
end of this file. It is a second instance of FINDING-3's mechanism, which makes it a pattern.

---

## The result, first

| | headroom | reasonable |
|---|---|---|
| ADER_ENERGY admitted | **116.0 of 250 kW**, binding `kwh_slack`, 5 h ahead | 250 of 250 kW |
| capacity availability (AS hold) | **100.0%** | **75.0%** |
| silent breach buckets | 0 | **6** |
| promise kept (ENERGY) | 100.0% | 99.97% |

D1: cut **134.0 kWh** vs expected **134.0 kWh**, binding reason `kwh_slack`. **PASS.**
Scaled ×8 that is 928 kW admitted and a 1,072 kWh cut, against `scenarios/S1_sizing.md`'s
918 kW and 1,082 kWh. See FINDING-9 for the 10 kWh.

**S1's story survives contact with its own formulas.** The scarcity is real, the binding reason is
the one the pitch claims, and the invariant holds.

---

## FINDING-3 — the worst one. §6.3's kWh term has no upper bound on `t`, and the two readings give opposite answers.

> `admitted_kw = min( requested_kw, min_{b∈W} kw_room(b), min_{t ≥ start(W)} slack(t) / elapsed_h_W(t) )`

`t ≥ start(W)` is bounded below and not above. **Stopping at the claim's own window end is the
natural reading** — that is the window the claim occupies. It is also wrong, and it is wrong in the
exact way the whole project exists to complain about.

Run at S1's sizing the two readings agree, so this is invisible. Push COOP_PEAK from 375 kW to
460 kW and they separate:

| co-op requests 460 kW | slack evaluated to **W's end** | slack evaluated to **the horizon** |
|---|---|---|
| COOP_PEAK admitted | **460.0 kW (full)** | 413.7 kW, `kwh_slack` |
| ADER_ENERGY | REJECTED | REJECTED |
| capacity availability | **0.0%** | **100.0%** |
| silent breach buckets | **24** | **0** |

Under the natural reading the ledger admits a co-op claim that eats every kWh the AS hold was
keeping, **and reports nothing wrong**. All 24 of the AS window's buckets are silently
undeliverable. That is a strictly worse outcome than `reasonable`, produced by Headroom, under the
words in its own spec.

It compounds with **FINDING-1**: §6.3 subtracts a CAPACITY reservation only
`for a ∈ A CAPACITY, t ∈ window_a`. An ENERGY claim ending at 18:30 never sees a 19:00–21:00
reservation at any `t` inside its own window. The horizon reading is the only thing that makes the
ENERGY claim look far enough forward to meet the reservation at all.

**Action before H0:** §6.3 must say the ENERGY kWh term is evaluated on bucket ends from
`start(W)` to **the end of the scenario horizon**, with `elapsed_h_W(t)` capped at W's duration.
One sentence. Without it, two lanes implementing the same paragraph produce 0% and 100%.

**Use it:** this is also the answer to red-team item "the single most embarrassing question."
It is worth saying out loud in the pitch — *we found this in our own ledger before the event* — and
a control test should plant it (`slack_horizon = "window"`) and assert the capacity silent-breach
count goes non-zero.

---

## The rest

**FINDING-1 — a CAPACITY reservation is invisible to any claim that finishes before the window.**
As above. Rescued by FINDING-3's horizon reading, not by the formula itself. Worth a unit test in
its own right: an ENERGY claim 15:30–18:30 and a CAPACITY hold 19:00–21:00 must not be jointly
admissible beyond E0.

**FINDING-2 — the kWh term divides by zero at `t = start(W)`.** `elapsed_h_W(start) = 0`. Evaluate
on bucket **ends** and skip any `elapsed_h ≤ 0`. Currently unstated, so lane B will either crash or
silently produce `inf` depending on whether numpy or plain floats are used.

**FINDING-4 — "the slack that existed without it" is ambiguous, and I got it wrong first try.**
D1 means the slack remaining **after ADER_ENERGY itself is subtracted** (§8.1: `9.92 − 2.0 = 7.92`).
Reading it as "before" double-subtracts and D1 fails with a plausible-looking number — 134 vs 250,
neither of which looks obviously wrong. **D1 is the single most load-bearing test in the build.**
Its wording goes in `contracts/` with the arithmetic spelled out, not in prose.

**FINDING-5 — S1's silent breach is the CAPACITY kind, and §10's metric list hides that.**
`reasonable` keeps 99.97% of its ENERGY promise on S1. Counting only ENERGY silent breaches scores
it **0 silent**, which would fail SPEC §11's control ("`reasonable` must show non-zero silent
breaches on S1") *on a correct implementation*. The SILENT tile in §13's UI mock must be
energy + capacity, and `metrics.json` should report the two separately.

**FINDING-6 — `reasonable` is not a strawman on S1, and the slide wording assumes it is.**
Its ENERGY delivery is essentially perfect. The whole difference is 75% vs 100% capacity
availability. The pitch line cannot be "it under-delivers"; it has to be **"the ancillary-service
hold you are paid to keep quietly stopped existing, and nothing told anyone."** That is a better
line anyway, and it survives the "strawman baseline" attack in §14.

**FINDING-7 — a chart can render successfully and be completely empty.** The first energy-left
chart plotted minutes-from-15:00 against a clock-hour axis. Matplotlib produced a clean,
correctly-styled, entirely blank figure and exited 0. The hero chart is on the **never-cut** list,
so its fixture test must assert the truth line has non-zero extent *inside* the axis limits — not
that a PNG was written.

**FINDING-8 — §6.4's `h_remaining(W)` is undefined at bucket granularity.** "the claim's remaining
duration" — measured from the bucket's start or its end? At 5-minute buckets that is an 8%
difference in the per-device cap in the final bucket, which is exactly where the floor clamp bites.
Picked bucket-start → window-end. Needs pinning in `contracts/`.

**FINDING-9 — `scenarios/S1_sizing.md` and the ledger disagree by 10 kWh, and the D1 bar is ±1 kWh.**
`prep/size_s1.py` charges a **fixed** aux drain over `WINDOW_H = 6.5 h`. §6.3's `drain_hi(now→t)` is
**t-dependent** and at the binding time (21:00) only 6.0 h have elapsed. At 400 devices that is
`400 × 0.050 kW × 0.5 h = 10.0 kWh`. Measured gap between the spike (×8) and the sizing file:
**10.16 kWh**. The known-answer test in §11 passes at "exact / ±1 kWh", so **D1 would fail at H0
for a reason that has nothing to do with the ledger.** Fix `size_s1.py` to use the t-dependent
drain before the event, then regenerate `scenarios/S1_sizing.md`.

**FINDING-10 — the moment the chart exists to show is a few pixels tall.** The commitment stack
crossing above the truth line is the entire thesis, and at S1's sizing it is a ~40 kWh gap on a
1,500 kWh axis. The real build needs the crossing annotated — a marker, a label, a vertical rule —
not left to the projector and the judge's eyesight.

---

## What this does not tell us

Perfect comms throughout: no estimator band widening, no staleness, no N−1 recomputation on a dark
region, no leases, no epochs. **S2 is entirely unexercised.** The practice build says the *energy
accounting* is implementable; it says nothing about whether the belief side holds up, which is the
half the Orchestration track actually cares about.

## Do before H0

1. Add the horizon sentence to §6.3 (FINDING-3) and the `elapsed_h` rule (FINDING-2).
2. Rewrite D1's statement with explicit arithmetic (FINDING-4).
3. Fix `prep/size_s1.py`'s drain horizon and regenerate the sizing file (FINDING-9).
4. Split the silent-breach metric and the UI tile into energy + capacity (FINDING-5).
5. Pin `h_remaining` (FINDING-8).
6. Reword slide 2's `reasonable` comparison (FINDING-6).
7. Add two controls: plant `slack_horizon = "window"` and assert capacity silent breaches go
   non-zero; assert the hero chart's truth line is inside the axes (FINDING-3, FINDING-7).

---

## FINDING-11 — found by the property lens, 2026-09-16 (post-practice-build)

**§6.3's `kwh_reserve(t)` was conditional on `t` being inside an active window. A reserve that
evaporates between windows is not a reserve.**

Hypothesis found it within 200 examples of the ledger being written. The shape:

| | |
|---|---|
| E0 | 147.0 kWh, of which **140.0 is the N−1 reserve** → 7.0 kWh genuinely spendable |
| EARLY | ENERGY 15:30–16:00, 10 kW — its window sits **outside every other window** |
| LATE | ENERGY 17:00–17:30, 10 kW |

When EARLY is admitted, no other window exists yet, so at 17:05 `kwh_reserve_at` returns **0** —
the reserve is not charged at the times EARLY is being evaluated against. EARLY is admitted at its
full 10 kW. Continuing aux drain then eats 0.08 kWh **out of the reserve itself**, which nothing
detects because the reserve was never subtracted there.

It is a sibling of FINDING-3: **a term made conditional on window membership creates a hole for
anything outside those windows.** Same mechanism, different term. Two instances now, which makes
it a pattern rather than an accident — any term in §6.3 gated on "inside a window" deserves the
same question.

**Fix:** hold the reserve at every `t` from `now` to `horizon_ts`. SPEC → v0.6.

**Verified before changing anything** (freeze before you change): the fix moves **no S1 number** —
928.2 kW admitted and a 1,071.8 kWh cut under both readings. It closes the hole and costs nothing.

**Why the property lens caught it and nothing else did:** every known-answer and control test in
the build uses S1, where all three windows overlap the binding times, so the conditionality never
bites. It takes a generated scenario with a claim sitting *between* windows — which no human
would have chosen to write, because it is not a story anyone is telling.


---

## FINDINGS 12-16 - the first end-to-end run, 2026-09-17

The runner, allocator and metrics landed together. **The loop found five defects in one afternoon,
and four of them were invisible to every test that existed before it.** They share a shape: each is
a place where **two parts of the system disagreed about the same quantity**.

**FINDING-12 - re-admission double-counted energy already delivered.** The belief E0 is read from
telemetry, so energy already delivered is *already* in the observed SoC. Charging `consumed_cum`
from the window start subtracts it a second time. At 15:35 the co-op had delivered 250 kWh, the
meter had seen it, and the ledger subtracted it again. **The symptom was worse than the
arithmetic:** the ledger manufactured Notices nothing had caused - 928 to 728 to 478 kW at 15:35,
four hours before the fault that was supposed to explain them. *A Notice that fires without a cause
is exactly the notice-spam SPEC 14 says to expose, generated by us.* Fix: measure from
`max(start, now)`.

**FINDING-15 - and that fix broke its own divisor.** `consumed_cum` moved to a `now` origin; the
admission `elapsed_h_W(t)` still measured from `start(W)`. A half-delivered claim was then asked to
fit its *remaining* energy into its *whole* duration, so it re-admitted lower every bucket and
chipped itself down: **47 spurious Notices on an S1 with no faults at all**, COOP_PEAK walking
3,000 to 2,829 kW with nothing whatever happening in the world. **Two uses of "hours of W elapsed"
that do not agree is one bug wearing two hats.**

**FINDING-13 - greedy allocation manufactures stragglers.** Filling devices in order to their caps
drains the front of the list hardest, so late in a long window those devices sit at their floor
contributing 0 kW while others still hold energy. Measured: the last 25 minutes of the co-op
3 h window delivered **93-95% of an admitted 3,000 kW with 4,373 kWh still in the fleet** - seven
**silent** breaches, on `headroom`, caused entirely by how the power was spread rather than by any
shortage. Fix: water-fill, so every device approaches its floor together and no straggler is made.

**FINDING-14 - the reconcile locked started CLAIMS, but SPEC 6.3 locks started BUCKETS.** The
buckets from 20:20 onward have not started just because 20:00 has. Locking the whole claim meant
that when R3 went dark mid-delivery the ledger could not downgrade a booking it could no longer
keep: it under-delivered 94.7% of an admitted 928 kW for thirty ticks and called every one
**silent**. *The controller whose entire pitch is "we tell you early" sat on it.*

> **Residue — CLOSED 2026-09-17 by per-bucket admission (FINDINGS 18-20, at the end of this
> file).** It read: admission is still per-claim, so an in-flight claim can only be downgraded
> **late**, while Beat B promises a Notice *"ahead of the buckets it hits"*. Tracked as a
> **strict-xfail** so that the day per-bucket admission landed the test would fail *for passing*
> and force DEMO.md to be re-read. **It did exactly that** — `[XPASS(strict)]` — and the re-read
> found **three** wrong cells in that row, two of which had never been right. **An over-claim that
> quietly becomes true is still an over-claim until someone checks.**

**FINDING-16 - I gave the baseline the feature under test, and the comparison vanished.** The
reconcile loop was applied to `reasonable` too. It promptly re-admitted every bucket, cut the co-op
when energy got tight, and scored **0 silent / 100% capacity availability** - identical to
`headroom`. S1 entire argument disappeared for an afternoon, **and it would have read as a
result**. SPEC 7 is explicit: per-claim checks, **not across time** - and re-admission *is*
across-time reasoning. **A baseline handed the thing it is a baseline for is not a baseline.**

**Also:** dark devices were being commanded to zero. A device whose comms are down cannot be
commanded *at all* - not even to stop (SPEC 6.6: the last setpoint runs until the lease expires).
Zeroing made every comms outage look like an instant total loss of that region output.

### Where it left the numbers

| S1 | silent | capacity availability | ENERGY promise kept |
|---|---|---|---|
| **headroom** | **0** | **100.0%** | 100.00% |
| `reasonable` | **60 - every one of them capacity** | **50.0%** | 100.00% |

FINDING-6 now holds end-to-end on real runs rather than by argument: **`reasonable` keeps its energy
promise perfectly and loses the ancillary-service hold silently.** That is the sentence for Beat B.

---

## FINDINGS 18-20 — per-bucket admission, 2026-09-17 (FINDING-14 closed)

The lock unit moved from the **claim** to the **bucket**. `Ledger.locks` freezes every bucket
whose start has passed — including the one being dispatched right now, because the allocator is
already placing it on devices — and the pass revises only the tail. The Notice therefore names
buckets that have not happened yet and carries a real lead time: **300 s, one full bucket**, which
is what `DEMO.md` Beat B has been promising since it was written.

**The strict-xfail did its job.** `test_beat_B_notice_arrives_ahead_of_the_buckets_it_affects`
failed *for passing* (`[XPASS(strict)]`) the moment the refactor landed, and forced a re-read of
Beat B. **Three of that row's cells were wrong**, and two of them had never been right:

| cell | said | is |
|---|---|---|
| the cut | `0.61 MW` | **124 kW** (pre-refactor it was 213 kW — `0.61` matched no build, ever) |
| the cause | "names N−1 / reachability" | **`kwh_slack`** |
| the state | "right now is covered" | the **hold** is; the in-flight energy bucket is not |

**First admission is bit-identical.** S1 `headroom` and S1 `reasonable` both reproduce their
pre-refactor `events_sha256` exactly (`0ae548f3…`, `5bbe5e9a…`), and D1 is untouched at
**928.2 kW / 1,071.8 kWh**. With no locks, `split_hours` returns the old `elapsed_h` and 0.0 —
the arithmetic is the same arithmetic. That equality is the regression check for this whole change.

**FINDING-18 — the build scored zero breaches here by un-promising the bucket it was already
delivering.** Per-claim reconcile cut ADER_ENERGY 928 → 213 kW *at 20:15*, including the
20:15–20:20 bucket then in flight, so `booked_kw` fell to meet delivery and nothing was recorded.
Per-bucket, that bucket stays sold at 928 and the fleet — minus a fifth of itself, with the AS
hold earmarked first (I8) — delivers **94.7%** of it for five minutes: **6 late-breach buckets**,
silent still **0**. The breaches are not new damage, they are the truth surfacing.
🔑 **Lowering a promise you are already delivering and then scoring yourself against the lowered
number is marking your own homework.** It looks *better* than the honest version on every metric,
which is exactly why it survived a week.

**FINDING-19 — a decision is four fields, and the clamp was carrying one.** Reconcile is
downgrade-only. My first version wrote `new.admitted_kw = min(new.admitted_kw, old.admitted_kw)`
and left `reason` alone. When R3 came back at 20:45 the ledger re-admitted ADER_ENERGY in full,
the clamp held the kW at 124 — and let the reason become `NONE`. The evening ended with a booking
reading **124 of 2,000 kW, binding reason: none**, on screen.
The property lens already forbids exactly this (*"a PARTIAL with reason NONE is a UI that lies"*)
and could not see it: **the property lens never runs reconcile.** It tests the ledger; this bug
lives in the loop around it. Fixed with `dataclasses.replace(old, locked_kw=…)`, which cannot drop
a field, plus a runner-level test. Same shape as the rule that every record *rebuild* drops
one — here it was a record *partial-update*.
🔑 **A guard that covers the object does not cover the loop that mutates it.**

**FINDING-20 — we were generating notice-spam about settled claims.** Every reconcile re-admitted
`COOP_PEAK`, whose window closed at **18:30**. At 20:15 it had no hours left to divide by (kWh
term `+inf`) and its kW room was read off buckets an hour and three quarters in the past, so the
pass "cut" it 3,000 → 2,870 kW and told a judge about it. A claim with no free buckets now decides
nothing. **S2 went from 2 notices to 1, and the one that remains is the one the demo is about.**

### Where it leaves the numbers

| S2 (`headroom`, R3 dark 20:15–20:45) | before | after |
|---|---|---|
| notices | 2 (one about a claim settled at 18:30) | **1** |
| median lead time | **0 s** — "IN DELIVERY - late" | **300 s** — "5 min ahead of delivery" |
| silent breaches | 0 | **0** |
| late breaches | 0 *(by un-promising the in-flight bucket)* | **6** *(honest)* |
| `ADER_AS` | 1.5 MW, availability 100% | **1.5 MW, availability 100%** |
| `ADER_ENERGY` | 928 → 213 kW | 928 → **124 kW** |

**The 124 kW decomposes, and Beat B should have the decomposition ready:** believed energy
**2,150 kWh** − the senior AS reservation **1,500** − the N−1 kWh reserve **478** − aux drain =
**82.5 kWh** over the 40 free minutes = **124 kW**. Every kWh is accounted for, and the ladder is
doing exactly what it is sold as doing: the senior hold is untouched and the junior award absorbs
all of it.

> ⚖️ **An owner call, not a defect.** The N−1 reserve is still held **while a region is already
> dark** — i.e. reserving against losing a *second* region. Releasing it once one is lost would
> admit **~841 kW** instead of 124. Holding it is the conservative reading of `haircut =
> n_minus_1` and is what ships; it is also the most likely thing a Base engineer pushes on, so it
> is written down here rather than discovered on stage.

**The recurring shape, fourth sighting.** FINDING-3, -11 and -17 were all two window-membership
tests that disagreed. Per-bucket admission adds a *fifth* window test (`Admission.bucket_end_at`),
so the grid now has **one definition** — `control/admission.py:bucket_ends` — which `Ledger.buckets`
and the integrator both call, and `kw_at` answers the membership question half-open `(start, end]`
like every one of its neighbours. `tests/unit/test_admission_profile.py` pins the boundary case.


---

## FINDINGS 21-23 — the batch runner, 2026-09-17

The `perf` lens and Slide 3 turned out to be the same machinery, and building it found three
things, each of which had been quietly true for days.

### FINDING-21 — the seed did nothing, and two tests were built on top of that

`run_scenario(seed=...)` carried a seed into `RunResult` and into `metrics.json`. **Nothing in
`sim/` ever read it.** There is no RNG in the fleet, the topology or the tick loop; every seed
produced a byte-identical run. Two things rested on it:

- **`tests/determinism` asserted I6 against a constant.** "Same seed → same `events_sha256`"
  cannot fail when *different* seeds also give the same hash. It would have passed just as green
  with the seed deleted from the signature.
- **The same file passed `"S1"` as the CONTROLLER.** `run_scenario`'s first positional argument
  is `controller`, not `scenario`. `"S1"` is not `"reasonable"`, so it fell through to
  headroom's branch and ran it under a name nothing else in the build uses. Green, twice over,
  for a fortnight.

🔑 **A determinism test needs a control that the thing under test can actually vary.** Fixed by
`sim/chaos.py` (seeded fault composition, ~25% of seeds deliberately quiet), a
`test_different_seeds_give_different_runs` control, and a `CONTROLLERS` allow-list in the runner
so an unknown controller name raises instead of silently meaning "the good one".

Had this not been found, **SPEC 11's "1k devices × 1000 seeds" would have been one run reported
a thousand times**, and Slide 3's "across N seeded chaos runs" would have been false with every
number in it real.

### FINDING-22 — we implemented half of the reconcile rule and the missing half was the pitch

SPEC 6.3: *"a later admission pass that lowers a booking for a not-yet-started bucket → Notice.
**A lowered started bucket → late Notice.**"* Per-bucket admission (FINDING-14) built the first
sentence and not the second.

The 1,000-seed sweep found the price. On **seeds 60 and 911** a region went dark at **20:55 and
21:00** — inside the *last* bucket of the 20:00–21:00 award. With no free bucket left to revise,
**no Notice fired at all** and the shortfall scored **SILENT**: 7 silent buckets across 2 of
1,000 evenings, in the controller whose entire pitch is that it does not miss quietly.

You cannot un-sell the bucket you are delivering. **You can still say so.** Reconcile now
re-admits with no locks purely to ask *"knowing what I know now, would I have sold this bucket?"*
— and if not, it says so late rather than not at all. Result: **7 buckets on 2 seeds → 1 bucket
on 1 seed**, and S2 now shows **two** Notices, which is a better Beat B than one: *the five
minutes I am in, I cannot fix — here is what it will really be; the next forty, here is the new
number, five minutes early.*

🔑 **When a spec sentence has two clauses, the second one is the one nobody implements.**
S1 and S2 could not have found this: it needs a fault landing in a claim's final bucket, which
no hand-written scenario contains, because it is not a story anyone thinks to tell.

**The residual, stated:** seed 60 draws `R2 21:00+10m` — dark at 21:00:00 **exactly**, the
instant the award ends. The controller learns of it on the same tick it is scored on. Delivery
collapses for that one tick because the senior AS earmark, now spread over 320 devices instead
of 400, absorbs the little margin left at the end of the evening. **One scored bucket out of
~60,000.** It is pinned in `tests/perf` **by seed**, not by a threshold: a count can quietly
grow, a named set cannot.

### FINDING-23 — part of the N-1 reserve is load-bearing for a bug, not for the outage

Building the oracle meant removing the haircut, and the moment it came off the oracle started
breaching **on completely quiet seeds**: it booked 1,610 kW of the 20:00 award and delivered
exactly 90% of it, with **3,105 kWh sitting unused in the fleet**. Not an energy shortage — the
ledger admits aggregate kW that the allocator cannot place on individual devices, because the
ledger's per-claim divisor and the allocator's `h_remaining` (measured from the bucket's start,
FINDING-8) do not agree about the same hour.

**`headroom` has that defect too. Its N-1 reserve is simply large enough that the gap never
shows.** That is worth saying out loud, because it is the most quietly embarrassing thing in the
build: some of the margin sold as insurance against losing a region is actually paying for an
internal disagreement. **[OWNER CALL — not fixed.]** Closing it moves S1's numbers and the demo
bake eight days out.

🔑 **Removing a safety margin is a good way to find out what it was really covering.**

Consequence for the headline: the ceiling cannot be assumed, it has to be **searched for** — the
largest fraction of its perfect-information admission the oracle can promise *and keep*, bisected
per seed to 0 silent and 0 floor breaches. `ceiling_is_valid` is asserted before any held-back
percentage is allowed to mean anything.

### What the sweep says

| 1,000 seeded evenings, 1k devices, 6,388 runs | `headroom` | `reasonable` |
|---|---|---|
| held back vs the oracle ceiling (median) | **6.4%** | **−18.6%** — it books *past* the ceiling |
| held back p90 | 7.5% | −5.6% |
| silent breach buckets, whole sweep | **1** | **62,603** |
| seeds with any silent breach | **1 / 1000** | **1000 / 1000** |
| capacity availability (median) | **100%** | **50%** |
| floor breaches | 0 | 0 |

**The negative held-back for `reasonable` is the whole argument in one number.** It does not hold
anything back; it commits ~19% *more* than a perfect-information controller could actually
deliver, and then misses quietly 62,603 times. **6.4% is what the honesty costs.**


### FINDING-24 — a comms outage makes the scope deliver 120% of what it sold [CLOSED]

**The most serious thing the sweep found, and the one S1 and S2 could never have shown.**
Across 1,000 seeded evenings, `headroom` crossed the homeowner's backup floor on **96 seeds**
and `reasonable` on **115** — never on a quiet evening, always after a region went dark.

**Mechanism, measured on seed 52 (`R2 18:15+30m`):** at 18:15 delivered power jumps
**3,000 → 3,600 kW**. R2 is 80 of 400 devices, and 20% of 3,000 is exactly 600. Two things
combine:

- a dark device **cannot be commanded at all**, so it holds its last setpoint — there is no
  lease expiry / SAFE_HOLD in the Must scope (SPEC §6.6, listed as not built);
- the allocator re-spreads the **full** booked kW across only the devices it can still reach.

So the dark region's share is delivered **twice**. The scope over-delivers by exactly the dark
fraction, drains faster than the ledger believes it is draining, parks devices on their floor —
and `aux`, which no guard clamps *by design*, then pushes them under it. 584,640 device-seconds
on a single-fault seed is one region below floor for two hours.

🔑 **`runner/run.py` says this simplification "is the CONSERVATIVE direction for `headroom`
(it keeps delivering) and the generous one for the metric, so it cannot flatter us." That comment
is wrong in both halves.** It over-delivers, and it spends the homeowner's reserve. A comment
asserting a safety property is not a safety property, and this one had been sitting above the
code claiming the opposite of what the code does since the runner was written.

**Why nothing caught it:** S1 has no faults. S2 has one, at 20:15, by which time the evening is
nearly over and the fleet never reaches its floor before the horizon. It needs a fault in the
**middle** of a long discharge — which is what a seeded sweep produces and a hand-written
scenario does not.

**FIXED 2026-09-17 — and it took BOTH candidate fixes, which is the lesson.**

Pinned in `tests/perf` **by count and by mechanism** — capped so it cannot grow, asserted to
happen only on fault seeds, and asserted to stay *worse on `reasonable` than on `headroom`*, so
that if it ever migrates into admission the test says so.


### FINDING-25 — leases never expired, and fixing the visible bug did not move the symptom

I diagnosed FINDING-24 as "the dark region's share goes out twice", implemented the allocator
fix, and watched the over-delivery vanish: `promise_kept_pct` on the worst seeds went from
**101.74% and 104.16% to exactly 100.00%**. Correct, and worth doing.

**The floor breaches did not move at all.** 584,640 device-seconds on seed 52, before and after.

🔑 **I had fixed the defect I could see and assumed it was the one I was measuring.** Two
mechanisms were stacked, and the loud one was not the harmful one.

The second: `sim/fleet.build_fleet` sets `lease_expiry_ts = np.full(n, np.inf)`. **Leases never
expired.** `lease_live` was permanently true, SAFE_HOLD never happened, and the runner's own
docstring said so — *"No lease expiry / SAFE_HOLD yet"* — while asserting in the same breath that
holding forever was *"the CONSERVATIVE direction ... so it cannot flatter us"*. A dark device
therefore held a **discharge** setpoint for the rest of the evening, drove itself down onto its
floor, and `aux` — which no guard clamps, deliberately — ate through the homeowner's backup
reserve for hours.

**The fix is SPEC §6.6, which was already written and simply not built:** the lease is renewed on
the **telemetry ack**, so it renews exactly on the devices we can hear from. A dark device's
lease lapses 60 s after it goes quiet and it drops to SAFE_HOLD. Three lines in the runner. The
estimator also stopped being fed a hardcoded always-live lease — SPEC §6.2 defines `tau_cmd` as
stopping *at* lease expiry, and it had never been given a real one.

**Result: floor breaches 96 / 1,000 seeds → 0.** S1 is byte-identical (`events_sha256`
`0ae548f3…`), because on a fault-free evening every device is heard every tick and the real
lease equals the hardcoded one.

**Both fixes were needed and they are independent.** Lease expiry alone stops the device but
leaves the allocator double-spreading during the 60 s the lease is still live. The allocator fix
alone stops the double-spread but leaves the device discharging onto its floor for hours. The
tests pin the **causes**, not the count: *delivery never exceeds what was sold*, and *a region
dark to the horizon crosses no floor*. A breach count can go to zero by luck or by a metric that
stopped counting; those two can only hold if the mechanisms work.

### The Beat C decision, 2026-09-17

**Beat C (kill the controller) is CUT from the 3:00.** It was 20 s proving something judges
already assume, it needed S3 built, and it owned both remaining `[TBD@H26]`s. `DEMO.md`'s own
*"Never cut"* list already ranked it below *"the 'where we lose' line in slide 3"* — the document
had made this decision months before anyone acted on it.

The 20 s went to Slide 3, which now carries **the finding our own harness caught**: a tower goes
dark and the fleet delivers *more* than it sold, out of the customer's reserve — 96 evenings in
1,000, found by an instrument we built, invisible to any demo. Crash recovery moved to §4 Q&A
spares with an honest "S3 is scheduled, not built — say that, don't bluff it."

**The 3:00 now contains zero placeholders**, 431 spoken words, 2:58 at 145 wpm. And
`reasonable`'s **−18.6%** is finally spoken aloud rather than buried in an on-screen cell:
*"over a thousand evenings it holds back nothing — it promises nineteen per cent more than
physics allows, and never says so."*


### Slide 3 got a picture, 2026-09-17 — and the first caption I wrote for it was false

Widening Slide 3 to two spoken rows and 40 seconds left it with **nothing to point at**:
`DEMO.md` said *"Batch tab (static PNG in the deck; live tab as backup)"* and neither existed.
Now both do, from **one** `ChartSpec` — `web/batch_chart.py` renders the deck PNG and
`prep/bake_runs.py` embeds the same geometry for the page's **`B`** tab, so the slide and the
laptop cannot come from different sweeps.

**The form is chosen, not defaulted.** The data's job is *polarity*: not how big either number
is, but **which side of zero each controller lands on** — zero being *exactly what a
perfect-information controller could have delivered that evening*. Two distributions on one
shared axis, heavy zero line, direct labels flanking it. Colour is categorical (two identities),
not diverging, because the polarity lives on the **axis**; validated rather than eyeballed —
`#2a78d6` / `#eb6834`, CVD ΔE 24.7 protan, 33.6 normal, contrast ≥ 3:1, all checks pass.

🔑 **Rendering it and looking at it caught three things the tests could not.** The first draft
let outliers size the axis — a handful of seeds run to **−135%**, which squashed the −30…+10
band, where 99% of the evenings and the entire argument live, into the right-hand third. The
two leader-line labels collided with the bars and each other. The zero caption overflowed the
frame. None of that is visible from geometry assertions; all of it is fatal on a projector.

🔑 **And the caption I wrote was an over-claim.** I typed *"`headroom` sits entirely to the
right of it"* into the module docstring before checking. It does not: **957 of 1,000**, with the
other 43 running to −3.9%. `reasonable` is the one that is absolute — **1,000 of 1,000** on the
wrong side, every single evening. The picture was honest the whole time (the small blue bars
left of zero are drawn, not trimmed); only my prose was not. Same shape as the phantom
`0.61 MW`: **a number written from memory beside a number that was measured.** Both are now
asserted — `tests/chart_fixture/test_batch_chart.py` fails if `reasonable` is ever ≥ 0 on any
evening, or if `headroom` drops below 95% on the safe side.
