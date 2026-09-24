# RATIONALE

**Why this project exists, what it assumes, what is already known, and why we chose this path.**
Written 2026-09-23, when the hackathon framing was dropped and Headroom became a standalone
preliminary repo. The rule for this file: **read it before building anything else.**

SPEC.md says *how* Headroom works; DECISIONS.md is a build log. Neither answers the question
this file answers: *is this worth building, compared to what already exists?*

Evidence markers, same as SPEC: **[S]** sourced · **[A]** our assumption · **[G]** guess about
Base internals · **[I]** our inference from sources. Quotes marked † went through a web-fetch
summariser rather than raw text. Check them at the URL before quoting them anywhere public.

---

## 1. The problem, in one paragraph

A fleet of home batteries is sold several times over. It is sold to a wholesale market (ERCOT
ADER energy and ancillary services), to a co-op for peak and transmission shaving, and as a fixed
slice to a utility. These claims can want **the same evening's kWh**. The controller decides
what to promise from telemetry that arrives over cellular backhaul and is sometimes stale or
missing. If it treats the last reported state of charge as inventory, and checks each claim on
its own, it can promise energy that will not exist when the claim comes due. It then
under-delivers **silently**: nobody is warned in advance.

## 2. The premises, and their status after research

The whole idea stands on five premises. Each is marked with what the public record says.

| # | Premise | Status | Best evidence | If it is false |
|---|---|---|---|---|
| P1 | One fleet serves several programs that can want the same evening's kWh | **Confirmed [S]** | GVEC runs the same batteries for 4CP transmission shaving, price arbitrage **and** ADER. It is the single operator. Austin Energy holds *"operational control … for the portion reserved for Austin Energy"* (40 MW at a fixed $/kW-month). Base's Algorithms Engineer role must *"integrate wholesale energy market operations algorithms with grid-service control loops"*. | No collision, no project. |
| P2 | Telemetry goes stale or dark, and **in correlated chunks** (a tower, a backhaul region) | **Half confirmed** | Base: *"often unreliable and expensive 4G backhaul"*, with store-and-forward built because the network goes down [S]. AEMO's VPP trials: dropouts hit *"up to 30% of the VPPs' fleets at any one time"* and *"need to be catered for in bidding strategies"* [S]. **Against:** Base's CEO publicly claims *"triple nines uptime"*† and Base advertises 96% fleet availability [S]. **Correlation by region is our inference [I]**; no source states it. | N-1 on a region is the wrong reserve object; a small independent haircut would do. The band still matters for stale-but-reachable devices. |
| P3 | Base's scheduler treats reported SoC as a point, not a range | **Unknown [G]** | Nothing public either way. Base uses Monte Carlo VaR/CVaR for *trading* risk [S]; the job lists MPC/RL/MDP [S]. Tesla publicly drops a site after a timeout and *"bid[s] conservatively"* [S]. That is a yes/no rule, not a widening range. | Headroom's band duplicates something they already have. |
| P4 | Leases, fencing and safe-hold exist at Base | **Partial [S]** | Base: commands *"persist until they expired"* and survive restarts. Nothing public on epochs, fencing, or what a device does at expiry. | Nothing. This was always "plumbing, not pitch" (SPEC §2). |
| P5 | Missing a commitment costs real money | **Partial [S]** | ADER: ERCOT *"may revoke an ADER's qualification … if the ADER demonstrates a continuing failure to perform"*. Telemetry must be within 10% of meter data. ERCOT NPRR 1186 requires SoC ≥ AS responsibility at each hour's start. Base's tolling offer is *"pay-for-performance"*. No penalty amounts are published for Base's contracts. | The cost of a silent miss is reputational and regulatory, not a number. The argument gets softer, not wrong. |

**The facts we now have that we previously assumed:**
- The 20% backup floor is Base's own public number (*"Base aims to reserve at least 20% for
  members"* [S]). It was [A] with an Austin Energy analogue.
- ADER ancillary services are **Non-Spin and ECRS**, capped at **100 MW** across the pilot [S].

**Premise questions that only Base can answer.** These are SPEC §18, still open. The hackathon was
the channel to ask them, and that channel is gone.
1. When 4CP and ADER want the same evening, who wins, and is that decided in code or by contract?
2. Is Austin's slice a fixed set of devices or a floating kW amount?
3. How stale does telemetry really get, and **do outages cluster by tower or region?** (P2)
4. When the command link is lost but the grid is up, does a device idle or self-consume?
5. Which broken rule pages someone?

## 3. What already exists

### 3.1 At Base (public record only)
Five engineering posts, none about dispatch. There are job posts, program announcements and a
utilities page. What is public: 2-second telemetry, store-and-forward, commands that expire, a
20% floor, Monte Carlo risk on the trading side, and MPC/RL for fleet scheduling. **How they
decide what to promise is not public.** Section 2's P3 is the gap.

### 3.2 In industry

| Who | What they do publicly | Covers which Headroom part |
|---|---|---|
| **Tesla Autobidder / VPP** (QCon talk) | Excludes sites *"if [they] haven't reported signals in a certain amount of time"*; *"bid[s] conservatively"*; setpoints carry a timeframe; offline sites fall back to local optimisation | Offline handling: **substantial**. Nothing on a widening range, multi-program admission or a regional reserve. |
| **AEMO VPP demonstrations** (2021) | States the problem: 30% dropouts, bids must cater, *"a further buffer may be required"* when prioritising network services | Names the problem and the need for a buffer; **no method**. |
| **Sunverge patent US9960637B2** | Program priority, pre-emption, *"energy reserved by other applications"* | **Across-time commitment accounting and priority ladder: substantial.** |
| **ERCOT NPRR 1186 / UK Dynamic Containment** | Market rules: hold enough SoC to cover a committed service for its duration, or be deemed unavailable | The energy-behind-capacity rule, as regulation. |
| Fluence Mosaic, Stem Athena | SoC-aware stochastic bidding and value stacking, for utility-scale or single-site assets | Partial; not residential fleets with dropouts. |

### 3.3 In the research literature

| Headroom part | Closest published work | Verdict |
|---|---|---|
| SoC band widening with data age | Set-membership estimation; Kalman filtering with intermittent observations (Sinopoli 2004); Mathieu, Koch and Callaway 2013 (fleet estimation with delayed or missing data) | **Standard idea.** Our band is a hand-built set-membership estimator, and we should call it that. |
| Admitting from the pessimistic edge | Chance-constrained bidding: Lunde et al. 2024 (P90 rule, Nordic FCR-D); Paredes et al. 2026 (distributionally robust); **Brändle and Hug 2026**: an adaptive margin loses to decision-dependent uncertainty | **Standard, and the literature has a stronger version.** |
| Energy held behind a capacity promise | **Evans, Tindemans and Angeli 2022**: reserves the whole discharge plus recovery cycle | **Prior art, and stronger than ours** (it books recovery; we are discharge-only). |
| Fleet-level feasibility | Virtual battery (Hao 2015); zonotope aggregation (Müller 2019); Elsaadany and Almassalkhi 2026: when an aggregate plan can actually be split across devices | Tighter than summing per-device bands. |
| Gate layered under an optimizer | Vrettos, Oldewurtel and Andersson 2016: robust reserve, then robust MPC, then feedback | Same architecture, built from robust optimisation. |
| Services stacked with separate budgets | Namor et al. 2018; Shi et al. 2018 | Prior art for the split between energy bookings and capacity holds. |
| **N-1 on a telemetry region** | Nothing found. The closest are cyber-physical reliability studies with random ICT failures, and joint chance constraints with correlated delivery failure | **Not found, by web search only.** |
| **Early Notices as a contract for the layer above** | Nothing found as an explicit mechanism; the market-side analogue is re-declaring availability | **Not found, by web search only.** |

"Not found" means a web search found nothing. It does not mean nothing exists. Before calling
anything "first" in public, search Scholar or IEEE Xplore properly.

## 4. What Headroom is, stated honestly

Five parts:

1. **SoC band widening with data age**, mode-aware (grid-connected vs islanded house load).
   Promises come from the low edge. *Standard idea; our implementation is simple and auditable.*
2. **A ledger of kWh committed across time**, in 5-minute buckets, separating ENERGY bookings
   from CAPACITY holds. *Prior art (Sunverge; Evans 2022; ERCOT's own NPRR 1186 rule).*
3. **N-1 on the telemetry region, checked per device and projected forward** (since 2026-09-24,
   SPEC §6.3b). Every capacity hold must survive losing any one reachable region, counting only
   kW that still has energy behind it, with ENERGY placed where the allocator will place it.
   *Grid N-1 practice applied to a new object; the per-device projection is the disaggregation
   problem in A3 and in Elsaadany and Almassalkhi 2026. Not found combined in the literature.*
4. **Early Notices**: a downgrade with at least 300 s lead, never a silent miss. *Not found as
   an explicit mechanism.*
5. **All of it as a separate admission layer under any optimizer.** *Vrettos 2016 is similar in
   shape; our version is optimizer-agnostic.*

**The honest claim is therefore the one SPEC §0 already makes, now with evidence:** no part is
new, except possibly 3 and 4. The contribution is **the composition, the ops contract (Notices),
and the measurement**, all against an oracle ceiling.

## 5. Alternatives, and why we did or did not take them

**A1. Chance-constrained admission (P90) instead of the pessimistic edge.** *The strongest
alternative.* It holds back less, markets are moving toward probabilistic reliability rules
(Denmark's P90, distributionally robust joint chance constraints), and Brändle and Hug show an
adaptive margin like ours leaves money on the table.
*Why we did not start there:* a chance constraint needs a **calibrated** SoC distribution for a
device you have not heard from. We have no real data to calibrate it, and a miscalibrated P90 is
worse than an honest bound. A bound needs only a worst-case drain, which is physics (aux draw,
last command, lease expiry).
*What this obliges us to do:* **measure the cost.** Run a P90 variant beside the band and publish
how much each holds back. If P90 wins by a lot, say so.

**A2. A Kalman filter instead of a hand-built widening rule.** It gives a variance, and a variance
plugs straight into A1. *Not taken:* same calibration problem. The band is the set-membership
special case. Revisit if A1 is adopted.

**A3. Fleet-level aggregation (virtual battery, zonotopes) instead of summing per-device bands.**
It is tighter, and it comes with guaranteed disaggregation. *Not taken:* telemetry age differs
per device, and an aggregate model hides which devices are stale. Our allocator's per-device kWh
cap (SPEC §6.4, I9) handles the disaggregation. Cost: we are looser than the best available set.
*Update 2026-09-24:* the aggregate view is exactly what failed (section 6c): the scope reported
4,784 kW while the kW with energy behind it was 2,208. The N-1 check is now per device. The ledger
itself still decides with sums; only the reserve check disaggregates.

**A4. A probabilistic correlated-outage reserve instead of deterministic N-1.** For example a
joint chance constraint, or a Bertsimas–Sim budget Γ on how many regions may fail at once.
*Not taken:* N-1 is simple, auditable, and needs no outage statistics we do not have. Cost: it
always holds back the largest region. The known sharp edge is that N-1 is **still held while a
region is already dark**: on S2, ADER_ENERGY ends the evening at 183 kW because the fleet keeps
protecting against a second loss. Relaxing that is an owner decision (section 2, SPEC §18 Q3).

**A5. Fold all of it into the optimizer's constraints (robust or stochastic MPC).** *Not taken:* a
separate gate can be tested, audited and kept when the optimizer changes, and Base's own job post
says the optimizer is theirs to build. The price is that a gate cannot trade reliability for
revenue the way a joint optimisation can.

## 6. What our evidence shows, and a problem with it

> **Sections 6, 6a and 6b are the evidence as it stood on 2026-09-23. Their numbers are
> superseded by section 6c:** four defects were found and fixed after they were written, and the
> reserve they describe was replaced. They are kept because the chain of reasoning - what each
> result seemed to show, and why it did not - is the most useful thing in this file.

**Headline today** (1,000 seeded evenings): `headroom` holds back 6.4% of a measured oracle
ceiling, with 2 silent breach buckets. `reasonable` commits **15.8% past** the ceiling and misses
silently 54,183 times.

**Research turned up a problem with that comparison.** `reasonable`
(`control/baselines.py`) **never subtracts kWh it has already admitted.** It checks each claim
against the full inventory. Its own docstring says: *"That single omission is the whole difference
on S1."* But subtracting committed energy is **public prior art**: Sunverge's patent (*"energy
reserved by other applications"*), ERCOT's own NPRR 1186, and Evans et al. So the
headline mostly measures part 2, the standard one. It does not isolate the parts that might be
new (1, 3, 4). A Base engineer would see this in seconds.

`reasonable` also differs from SPEC §7 in the code:

| SPEC §7 says `reasonable` has | The code has |
|---|---|
| belief = max-seq point estimate | the **same pessimistic band** as headroom (`scope_belief` → `kwh_above_floor_low`). At the 15:00 admission data is fresh, so the difference is about ε. |
| fixed 10% kW reserve | **no reserve at all** |
| 15-minute leases | the shared 60 s lease |

It also admits once and never revisits (`runner/run.py:163`), which is by design, since
re-admission is part of what is under test.

**Conclusion:** the comparison is fair to what SPEC calls "a careful engineer's first draft". It is
**not** fair to "what a competent fleet operator already runs". SPEC §14 claims the baseline is
not a strawman. **That claim holds only against a first draft.**

## 6a. Result: headroom against standard practice (run 2026-09-23)

`python run.py compare 1000` (`runner/compare.py`, results in `prep/out/compare_results.json`).
Same 1,000 seeded evenings and fault draws as the batch, scored against the same measured oracle
ceiling. `reasonable_plus_dN` = headroom's own ledger with re-admission and Notices, point-estimate
SoC, timeout exclusion, **no band, no N-1**, plus a flat N% de-rate of both kW and kWh.
Integrity: headroom and `reasonable` match the recorded batch on 2,000 of 2,000 rows, and 8 seeds
re-run live matched exactly.

| subject | held back (median / p90) | silent buckets (seeds) | seeds crossing the 20% floor | clean seeds |
|---|---|---|---|---|
| **headroom** | **6.42% / 7.45%** | 2 (2) | **0** | 998 |
| reasonable (old baseline) | −15.78% / −5.57% | 54,183 (1000) | 0 | 0 |
| reasonable_plus_d0 | 0.00% / 0.00% | 15,194 (291) | 123 | 586 |
| reasonable_plus_d5 | 3.01% / 4.45% | 601 (45) | 87 | 868 |
| reasonable_plus_d10 | 6.53% / 9.90% | 0 | 65 | 935 |
| reasonable_plus_d12 | 8.46% / 12.25% | 0 | 43 | 957 |
| **reasonable_plus_d15** | **12.14% / 15.77%** | **0** | **0** | **1000** |
| reasonable_plus_d20 | 18.26% / 21.64% | 0 | 0 | 1000 |
| headroom_no_band | 4.79% / 5.58% | 3 (2) | 59 | 939 |
| headroom_eps_only | 6.42% / 7.45% | 2 (2) | 0 | 998 |
| headroom_no_n1 | 1.63% / 1.88% | 15,006 (283) | 0 | 717 |

**What this shows:**
1. **Headroom survives the fair baseline, by about half.** The cheapest flat de-rate that is
   clean on all 1,000 evenings is between 12% and 15%. It holds back **12.1%** at the median
   (15.8% on the 225 quiet evenings). Headroom gets the same protection at **6.4%** (4.7% on
   quiet evenings). The honest caveat: d15 has **0** silent seeds and headroom has **2** (the
   known 21:00:00 boundary case). d15 is strictly cleaner at roughly twice the cost.
2. **The N-1 reserve does the work.** Remove it and 283 evenings miss silently. It beats a flat
   de-rate because it holds kW for a whole region but kWh for only the outage duration
   (`outage_h` 0.5 h). A flat de-rate has to hold the same share of *every* kWh.
3. **The band's age-widening contributes nothing measurable here.** `headroom_eps_only` (a fixed
   1% margin, no widening) matches headroom on **1,000 of 1,000** rows. What protects the floor
   is the fixed ε margin (without it, 59 evenings cross the floor). This is **structural to the
   simulator, not a finding about the world**: `runner/run.py` ticks every 60 s and a device is
   unreachable after 10 s, so a device the controller can see is always 0 s old. Staleness is
   binary here. Part 1 of section 4 is **untested, not refuted**.
4. **The test world is built on premise P2.** `sim/chaos.py` draws only whole-region comms
   outages across 5 equal regions, with comms otherwise perfect. N-1 is being tested in the
   world it was designed for. If Base's outages are scattered rather than regional, point 2 has
   not been shown.

**The claim this supports, stated at its true size:** *on a standard ledger, a region-shaped
reserve gives flat-derate-15% safety at about 6% cost, provided outages come by region.*

## 6b. Result: does the SHAPE of an outage matter? (run 2026-09-23)

`python run.py compare 1000 scattered|fragmented` (`sim/chaos.py` WORLDS). Every evening goes
**exactly as dark** as in the regional draw, in device-seconds (asserted by
`tests/unit/test_chaos_worlds.py`). Only the shape changes:
- **scattered**: the same devices-worth of outage, the same window, spread across all regions.
- **fragmented**: 8 groups of 10 devices, each with its own random start.

The oracle ceiling is re-measured per world. Outage evenings only (n = 775; the 225 quiet
evenings are identical in every world):

| world | headroom held / silent seeds / floor seeds | cheapest clean flat de-rate (d15) | d10 floor seeds |
|---|---|---|---|
| regional | 6.46% / 2 / 0 | 11.69% / 0 / 0 | 65 |
| scattered | 6.47% / 2 / 0 | 11.19% / 0 / 0 | 58 |
| fragmented | −2.21%\* / 0 / 0 | 3.84%\* / 0 / 0 | 437 |

\* **In the fragmented world the ceiling is not a ceiling.** Headroom commits *above* the
measured oracle with 0 misses, so the oracle bisection (7 probes) under-shoots there. Absolute
held-back numbers in that row are unreliable. The **gap between subjects** on the same seed is
still valid, and it is about 6 points again.

**What this shows:**
1. **Headroom's edge over the cheapest safe flat de-rate survives in all three worlds, at about
   5–6 points of held-back energy.** Premise P2 is *not* load-bearing for that edge.
2. **But the reason is not "regions".** Scattered equals regional because, in this test world,
   the reserve is the size of one region (80 devices) and so is each outage. Region-awareness adds
   nothing measurable with 5 equal regions. With equal regions, N-1 behaves like "hold 20% of kW,
   backed by 0.5 h of energy". **The demonstrated contribution is sizing a reserve as power for a
   duration rather than as a flat share of energy.** That is how reserve products are already
   specified (MW for a duration), so it is **not novel**. It is still a real improvement over a
   flat de-rate.
3. **🔴 Part of what any reserve buys is cover for a known bug.** With no reserve
   (`headroom_no_n1`, `reasonable_plus_d0`), **every one of the 225 quiet evenings misses
   silently**. There is no outage at all; the misses come from the ledger/allocator divisor
   mismatch recorded in `runner/batch.py`'s docstring (numbered FINDING-21 there, a different
   defect from `PRACTICE-NOTES.md`'s FINDING-21). The ledger admits aggregate kW the allocator
   cannot place on devices. On outage evenings `headroom_no_n1` misses on only 58 of 775,
   *fewer* than on quiet ones, because outages shrink admission and hide the bug. **So the
   `no_n1` ablation (section 6a) measures the bug as much as the reserve.** Every no-reserve
   number is contaminated until the bug is fixed.

## 6c. What the fair baseline uncovered, and the reserve that came out of it (2026-09-24)

**The short version:** fixing four defects made the old N-1 reserve look useless. The reason it
looked useless turned out to be the most important finding in the project, and a reserve built to
answer it is the first version of the idea that does what it claims.

**1. Every no-reserve controller missed silently on every quiet evening.** Section 6b's third
point. Two allocator defects, and both were needed to explain it:
- `h_remaining` was padded by five minutes on every tick. SPEC §6.4 measured it from the bucket's
  start, which is right for an allocator called once per bucket; the runner calls it every tick
  and passes the tick time.
- The CAPACITY earmark was greedy. It emptied the richest devices first, so ENERGY drained the
  rest together and its final buckets ended up on too few devices.

Fixing only the first made the zero-margin baseline *worse* (floor-breach evenings 6 -> 42): the
pad had been acting as a floor buffer. They shipped together.

**2. With the allocator fixed, the old reserve looked like a cost with no benefit.** Headroom
without its N-1 reserve held back 0.66% with 2 silent evenings; with it, 5.45% and 17. Tracing
the 17 found three more defects:
- The priority pass admitted senior claims before a junior claim's *locked* in-flight bucket was
  in the ledger. Fixed: locked buckets are charged first.
- A Notice about one claim excused every other claim's shortfall in scoring. Fixed.
- **The ledger counted empty batteries as power.** On seed 259 at 20:20, 224 of the 320 reachable
  devices held 0.69 kWh each. The 96 large units held 2,094 kWh behind 2,208 kW, and that had to
  carry 928 kW of ENERGY plus the 1,500 kW AS hold. The scope reported 4,784 kW.

**3. Two plausible fixes failed first.** A per-device ceiling with the reserve left in nameplate
units cleared all 17 evenings, but only by cutting the AS hold on a *fault-free* evening (1,500 ->
1,195 kW, with 5 Notices): zero by timidity. The units-matched version cleared none. The missing
piece was the future. The fault is at 20:20; the mistake is made at 15:00, when the co-op is
booked without asking what state it will leave the small units in.

**4. What was built (SPEC §6.3b, `control/deliverability.py`).** The ledger projects each device
forward the way the allocator will drain it, and requires every hold to survive losing any one
region, counting only kW with energy behind it. The most junior claim yields. On S1 it sees at
15:00 what would happen at 20:20 and sells ADER_ENERGY **763 kW instead of 928**, binding reason
`reserve`, with no Notice needed. Without the co-op it sells the full 2,000 kW: the reserve binds
*because* of the collision the project is about.

**5. The late Notice stopped crying wolf.** It quoted the counterfactual's figure for the whole
remaining window as if it were the bucket's. It now also asks whether the bucket in delivery is
actually short, and quotes that. On S2 it no longer fires (the bucket is covered); on seed 76,
where a second region goes dark, it still does.

**6. A leak I introduced, caught by its result.** The first sweep of the new ledger reported
headroom holding back 0.96%. Too good. The new stage keyed on a config field the oracle never set,
so it ran inside the oracle and lowered the ceiling everything is measured against. The unit test
written to prevent exactly that passed, because it tested a path the oracle does not take. Fixed,
re-tested through the oracle's real path (and shown to fail with the leak restored), and the
sweep discarded.

### Results, final (1,000 seeded evenings per world)

Held back against a measured oracle ceiling (valid on all 1,000 evenings; the oracle keeps every promise it makes). Batch runtime 483 s for 1,000 evenings.

| controller | world | held back, quiet evenings | held back, outage evenings | silent evenings | floor evenings |
|---|---|---|---|---|---|
| **headroom** | regional | 6.8% | 6.9% | 0 (0 buckets) | 0 |
| **headroom** | scattered | 6.8% | 6.7% | 0 (0 buckets) | 0 |
| **headroom** | fragmented | 6.8% | 6.5% | 0 (0 buckets) | 0 |
| headroom, stage 2 off (aggregate N-1 only) | regional | 5.2% | 6.7% | 4 (14 buckets) | 0 |
| headroom, stage 2 off (aggregate N-1 only) | scattered | 5.2% | 6.7% | 2 (7 buckets) | 0 |
| headroom, stage 2 off (aggregate N-1 only) | fragmented | 5.2% | 6.3% | 0 (0 buckets) | 0 |
| headroom, no N-1 reserve at all | regional | 0.6% | 1.6% | 2 (4 buckets) | 0 |
| headroom, no N-1 reserve at all | scattered | 0.6% | 1.6% | 7 (92 buckets) | 0 |
| headroom, no N-1 reserve at all | fragmented | 0.6% | 1.5% | 0 (0 buckets) | 0 |
| standard practice, flat 12% de-rate | regional | 12.7% | 7.4% | 0 (0 buckets) | 76 |
| standard practice, flat 12% de-rate | scattered | 12.7% | 7.5% | 0 (0 buckets) | 78 |
| standard practice, flat 12% de-rate | fragmented | 12.7% | 9.9% | 0 (0 buckets) | 255 |
| standard practice, flat 15% de-rate | regional | 16.2% | 11.1% | 0 (0 buckets) | 15 |
| standard practice, flat 15% de-rate | scattered | 16.2% | 10.9% | 0 (0 buckets) | 15 |
| standard practice, flat 15% de-rate | fragmented | 16.2% | 13.2% | 0 (0 buckets) | 20 |
| standard practice, flat 20% de-rate | regional | 22.0% | 17.0% | 0 (0 buckets) | 0 |
| standard practice, flat 20% de-rate | scattered | 22.0% | 16.6% | 0 (0 buckets) | 0 |
| standard practice, flat 20% de-rate | fragmented | 22.0% | 19.0% | 0 (0 buckets) | 0 |

### What this shows, and what it does not

1. **Zero silent misses and zero floor breaches on 3,000 evenings, at about
   6.9% held back.** No other configuration tested has that record for less
   than a flat 20% de-rate (17.3%).
2. **What buys the zero is mostly the correct ledger, not the reserve.** With the four defects
   fixed, the same ledger with no N-1 reserve at all holds back 0.9% and
   misses silently on 9 of 3,000 evenings. The reserve closes that last gap and costs
   roughly 5.9 points of held-back energy, most of it on
   evenings where nothing goes wrong (6.8% vs 0.6% on quiet evenings). That is
   an insurance premium, and whether it is worth paying is a business decision: it depends on what
   a silent miss costs Base under ADER and its utility contracts (premise P5), which we do not know.
3. **The per-device stage is a small part of the reserve's value.** On top of the aggregate
   reserve it removes 6 silent evenings in 3,000 for about
   1.3 points. Most of what it seemed to prevent earlier
   was the one-tick defect, fixed at its source. It stays on by default because it is the only
   check that looks at kW with energy behind it, and it is what makes the oracle a ceiling.
4. **Outage shape does not break it in these three worlds.** Scattered and fragmented outages of the
   same total darkness gave the same record. That is three shapes of one fault type (comms only),
   in a simulator.
5. **Still untested:** the band's age-widening (the link model in `sim/link.py` exists and is not
   wired in), a P90 comparator, two regions dark at once, and anything about the real fleet.

## 7. What would prove this approach wrong

- **A stronger baseline matches us.** Give `reasonable` cumulative commitment accounting,
  capacity-energy reservation (both standard) and re-admission with Notices. If it then gets
  near-zero silent breaches at similar hold-back, parts 1 and 3 add nothing measurable.
- **P90 dominates.** If a chance-constrained variant holds back materially less with a similar
  silent-breach count, the pessimistic band is the wrong choice.
- **P2 is false in the field.** If Base's outages do not cluster by region, N-1 is guarding against
  the wrong thing.
- **Base already has it (P3).** If Base already admits against an uncertainty-aware SoC, the
  project is a reproduction. It can still be a useful artifact, but it is not a contribution.

## 8. Therefore: what to build next

Recommended order. **S3 (crash recovery) drops out**: it proves plumbing that P4 says Base
partly has already, and SPEC called it "plumbing, not pitch" from the start.

1. ✅ **`reasonable_plus`, a baseline built from standard practice, plus ablations** (done
   2026-09-23, section 6a; re-run on the fixed code in 6c). Minus-Notices is not yet run.
2. ✅ **Scattered-outage chaos** (done, section 6b). The edge survives, but it comes from sizing
   the reserve as power for a duration, not from region-awareness.
2a. ✅ **Fix the ledger/allocator defects, then re-run everything** (done 2026-09-24, section 6c).
   Four defects, plus the per-device N-1 reserve that the investigation led to.
2b. **A reserve-shaped flat baseline**: a flat kW de-rate backed by `outage_h` of energy, with no
   region logic. If it matches headroom, the region part of N-1 is decoration in any world with
   equal regions. Unequal region sizes are the case where it could matter.
3. **Stale-but-reachable telemetry** (latency, the §6.6 link model that is specified but not
   built), so the band's age-widening is exercised at all. Until then, drop it from the claim.
4. **A P90 comparator** (A1), so the cost of the bound is published, not hidden.
5. ✅ **Fix SPEC §7** so it describes the baseline the code actually runs (SPEC v1.0).
6. **Two regions dark at once.** Beyond N-1 by definition; today it is announced late (seed 76).
   Whether the fleet should carry N-2 on some evenings is a cost question, not a bug.

Packaging is done (README, hackathon docs archived or marked as history). Items 2b, 3, 4 and 6
are the open work.

## 9. Sources

Base and programs:
- Base telemetry post: https://inside.basepowercompany.com/p/building-a-telemetry-stack-for-the
- Base risk model post: https://inside.basepowercompany.com/p/building-a-scalable-risk-model-for
- Base utilities page: https://www.basepowercompany.com/utilities
- Base charge/discharge blog (20% floor): https://www.basepowercompany.com/blog/how-base-charges-and-discharges-its-batteries
- Algorithms Engineer (mirror)†: https://www.dreamworkhq.com/job/b126df0d-7a95-4278-8d01-4247684aaac9
- Latitude Media, Zach Dell†: https://www.latitudemedia.com/news/catalyst-how-base-power-plans-to-use-its-fresh-1b/
- Utility Dive, GVEC: https://www.utilitydive.com/news/base-power-gvec-texas-vpp-virtual-power-plant/752102/
- Austin Energy council action 26-1526: https://services.austintexas.gov/edims/document.cfm?id=471637
- ERCOT ADER Phase 3: https://www.ercot.com/files/docs/2025/06/16/4.3-Aggregate-Distributed-Energy-Resource-ADER-Pilot-Project-Phase-3.pdf
- ERCOT ADER limits notice: https://www.ercot.com/services/comm/mkt_notices/M-A030226-01

Industry:
- Tesla VPP talk: https://www.infoq.com/presentations/tesla-vpp/
- AEMO VPP Knowledge Sharing Report 4: https://www.aemo.com.au/-/media/files/initiatives/der/2021/vpp-demonstrations-knowledge-sharing-report-4.pdf
- Sunverge patent: https://patents.google.com/patent/US9960637B2/en
- NPRR 1186 explainer: https://modoenergy.com/research/ercot-battery-energy-storage-systems-nprr-1186-ancillary-services-emergency-responsive-reserve-operations
- UK Dynamic Containment rules: https://modoenergy.com/research/frequency-response-rule-changes-eso-dynamic-containment-july-2024-battery-energy-storage

Literature:
- Brändle & Hug 2026: https://arxiv.org/abs/2604.12594
- Lunde et al. 2024: https://arxiv.org/abs/2404.12818
- Evans, Tindemans & Angeli 2022: https://arxiv.org/abs/2110.08549
- Elsaadany & Almassalkhi 2026: https://arxiv.org/abs/2606.01562
- Müller et al. 2019: https://arxiv.org/abs/1705.02815
- Vrettos, Oldewurtel & Andersson 2016: https://arxiv.org/abs/1506.05399
- Bertsimas & Sim 2004: https://pubsonline.informs.org/doi/10.1287/opre.1030.0065
- Sinopoli et al. 2004, Kalman filtering with intermittent observations (IEEE TAC 49(9))
- Namor et al. 2018: https://arxiv.org/abs/1803.00978
- Paredes et al. 2026: https://www.sciencedirect.com/science/article/pii/S0378779626009867
- Herre, Kazemi & Söder 2020: https://www.diva-portal.org/smash/get/diva2:1457843/FULLTEXT01.pdf
