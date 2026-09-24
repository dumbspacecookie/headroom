# Headroom — SPEC v1.0

> **The optimizer proposes. Headroom admits only what the fleet can still keep, even when a region goes quiet.**

Written 2026-09-15 as a preliminary design for admission control on a residential battery fleet.
v0.2 and v0.3 fold in two rounds of external review the same day.
**v0.4 (2026-09-16) folds in the practice build**: §6.3 and §6.4 were typed in literally
at 1:8 scale and run against truth. Full findings in `PRACTICE-NOTES.md`. **v0.7 (2026-09-17) makes admission per-bucket; v0.8 adds the late Notice, a live seed axis and a measured oracle ceiling.**
Why this design and not another, and what the evidence does and does not show: `RATIONALE.md`.

Status markers: **[S]** sourced (§17) · **[A]** assumption (tunable, shown in UI) · **[G]** guess about Base internals.

### v1.0 changes (2026-09-24) — a fair baseline, four defects, and an N-1 reserve that is checked per device

Every change below is written up with its evidence in `RATIONALE.md` s6-6c.

- **§6.3b (new): the N-1 reserve is checked per device, projected forward.** The old reserve —
  largest region's nameplate kW, subtracted from the scope's nameplate kW — counted empty
  batteries as power. After S1's co-op shave the small units are near their floor, so the kW
  that still has energy behind it is a fraction of `KW(scope)`. The ledger now walks the fleet
  forward the way the allocator will drain it and requires every CAPACITY hold to survive the
  loss of any one reachable region. **S1's ADER_ENERGY moves 928 -> 763 kW, binding reason
  `reserve`, decided at 15:00.** D1 is unchanged: it is a statement about the energy term, and
  the energy term still says 928 (§8.1).
- **§6.3: locked buckets are charged before the priority pass.** Senior claims were admitted
  before a junior's in-flight bucket was in the ledger, so a senior AS hold could be kept against
  slack that was already spent.
- **§6.4: `h_remaining` is measured from now and includes the current tick.** The runner calls
  the allocator every tick with the tick time; "bucket start" had silently become "five minutes
  ago", and without the current tick the last tick of every window ran short.
- **§6.3b also checks ENERGY per device, and the oracle runs it with no region removed.** Before
  that, the oracle's own bookings broke on 87 of 1,000 evenings and the ceiling was not a ceiling.
- **§6.4: CAPACITY earmarks are proportional, not greedy.** Greedy earmarking emptied the richest
  devices first, leaving ENERGY's last buckets on too few devices to carry the booked kW.
- **§10: a Notice excuses only its own claim.** Any Notice used to turn every ENERGY shortfall late.
- **§7: `reasonable` is described as built.** The table said "point estimate, fixed 10% kW,
  15 min leases"; the code uses the pessimistic band, no reserve, and the shared 60 s lease.
  A fairer baseline, `reasonable_plus`, now lives in `runner/compare.py`.

### v0.9 changes (2026-09-17) — §6.6's lease expiry is finally BUILT, and it was load-bearing

- **§6.6 SAFE_HOLD is implemented.** `sim/fleet.build_fleet` set every lease to `+inf`, so
  leases never expired, `lease_live` was permanently true and SAFE_HOLD never happened. A device
  that went dark held a **discharge** setpoint for the rest of the evening, drove itself onto its
  floor, and `aux` — which no guard clamps, deliberately — ate through the homeowner's backup
  reserve. **96 of 1,000 seeded evenings.** Renewal now piggybacks the telemetry ack, exactly as
  this section already specified. **FINDING-25.**
- **§6.4: the allocator nets out stranded output.** It re-spread the FULL booking across only
  the devices it could still reach, while the dark ones kept running their last command — so the
  dark region's share went out **twice** and the scope delivered 120% of what it sold. **§6.2's
  "dark devices contribute 0 kW and 0 kWh" is about CAPABILITY, not DISPATCH**; the build had one
  number answering both questions and they differ the moment comms drop. **FINDING-24.**
- **§6.2: the estimator is given a real lease.** `tau_cmd` is defined here as stopping *at* lease
  expiry and had been fed a hardcoded always-live value.
- **Both were needed.** Lease expiry alone leaves the double-spread during the 60 s the lease is
  still live; the allocator fix alone leaves the device discharging onto its floor for hours.
  **Floor breaches 96/1000 → 0; S1 byte-identical.**

### v0.8 changes (2026-09-17) — the seed axis, the late Notice, and a ceiling that is measured

- **§6.3 Reconcile: the LATE Notice is implemented.** v0.7 built "a lowered not-yet-started
  bucket → Notice" and not "a lowered started bucket → late Notice". A region going dark inside
  a claim's **final** bucket therefore produced no Notice at all and the miss scored **silent** —
  7 buckets on 2 of 1,000 seeded evenings. Reconcile now re-admits without locks to ask whether
  it would still have sold the bucket it is delivering, and says so late if not. **FINDING-22.**
- **New `sim/chaos.py`: the `seed` argument now does something.** Until today nothing in `sim/`
  read it and every seed produced a byte-identical run, so §11's "1k devices × 1000 seeds" would
  have been one run reported a thousand times. Opt-in: `run_scenario`'s `faults` still defaults
  to `()`, so S1, S2 and the demo bake are untouched. **FINDING-21.**
- **§7 `oracle` is built (`control/oracle.py`), with two departures from this table, both
  deliberate.** It does **not** get foresight of faults — prophecy plus better present
  information is two advantages in one number. And a flat-out oracle **is not a ceiling**: with
  the haircut off it breaches on quiet seeds (**FINDING-23**), so `runner/batch.py` bisects per
  seed for the largest admission it can promise *and keep*. `ceiling_is_valid` gates every
  `capacity_held_back_vs_oracle_pct` before it is allowed to mean anything.
- **§11 `perf` is live and the gate is FULL: 12 expected, 12 ran, 12 passed.** Measured
  1,000 evenings / 6,388 runs / 23 workers in ~11 min against the 20 min bar.
- **An unknown controller name now raises.** `run_scenario`'s first positional argument is
  `controller`; `tests/determinism` had been passing `"S1"` into it for a fortnight, where it
  silently meant headroom.

### v0.7 changes (2026-09-17) — the lock unit moves from the claim to the bucket

- **§6.1 step 3 and §6.3 Reconcile: admission is PER-BUCKET.** An `Admission` is a profile, one
  admitted kW per bucket. A bucket is locked once its start has passed, the one being dispatched
  included. Consequences, each with a finding behind it: every Notice now leads its buckets by at
  least one bucket (**300 s**, never 0) — a claim with no free buckets decides nothing, which
  stopped us issuing Notices about claims settled two hours earlier (**FINDING-20**) — the
  in-flight bucket stays sold and may under-deliver, which is a **late** breach and is the honest
  scoring (**FINDING-18**) — and a downgrade carries the **whole** prior decision, not just its kW
  (**FINDING-19**).
- **No ledger arithmetic changed.** With no locks the new terms reduce to the old ones exactly:
  S1 `headroom` and S1 `reasonable` reproduce their pre-change `events_sha256`, and D1 is
  unmoved at **928.2 kW / 1,071.8 kWh**. This closes FINDING-14 and with it `DEMO.md`'s last
  live over-claim.

### v0.6 changes (2026-09-16) — one defect, found by a test

- **§6.3: `kwh_reserve(t)` is now held at every `t`, not only inside an active window.** The old conditionality let a claim outside every other window spend against the N−1 reserve, after which aux drain ate into it. Found by the property lens the day it was written; **no S1 number moves.** FINDING-11.

### v0.4 changes (every list change announced) — all from the practice build, 2026-09-16

**The headline: S1 holds.** D1 passes against the §6.3 formulas (cut 134.0 kWh = expected 134.0 at
1:8 scale), binding reason `kwh_slack`, headroom 100% capacity availability / 0 silent vs
`reasonable` 75% / 6 silent. The scarcity is real and the mechanism is the one the pitch claims.

- **🔴 §6.3 now bounds the ENERGY kWh term above, at `horizon_ts`, not at `end(W)`** — and says why, with numbers. The natural reading (stop at the claim's own window end) admits an earlier ENERGY claim that eats a later CAPACITY hold's kWh **and reports nothing wrong**: measured 0% capacity availability and 24 silent buckets, *strictly worse than `reasonable`*. New control **C6** plants it.
- **§6.3 states the evaluation grid:** bucket ends, skipping `elapsed_h_W(t) = 0`, which is a division by zero at `t = start(W)` as v0.3 was written.
- **§8.1's D1 is now written as arithmetic, not prose.** `slack_without` is the slack **after** ADER_ENERGY is subtracted. The other reading fails D1 with a plausible-looking wrong number, which is how it got past a first typing.
- **§6.4 pins `h_remaining(W)`** to the current bucket's **start**, floored at `Δh`. An 8% difference in the final bucket, exactly where the floor clamp bites.
- **§10 splits the silent-breach metric** into `silent_energy_buckets` + `silent_capacity_buckets`, both reported. On S1 `reasonable` keeps **~100%** of its ENERGY promise, so an ENERGY-only count scores it **0 silent** and would fail §11's own control on a *correct* build.
- **§12 / §16: the `reasonable` comparison is reworded.** On S1 it does not under-deliver energy; the ancillary-service hold it is paid to keep quietly stops being deliverable. The old wording was wrong and invited the strawman attack.
- **§11 gains two controls and a test:** C6 (above), C7 (shift the hero chart's x data → the fixture must fail), a chart fixture assertion that the truth line is **inside the axes**, and a unit test for the ENERGY-ends-before-CAPACITY-window case (FINDING-1).
- **§13's SILENT tile is energy + capacity** and reads `reasonable 6`, not 9.
- **§12: the hero chart must annotate the crossing.** At S1's sizing it is ~40 kWh on a 1,500 kWh axis — a few pixels on a projector, and it is the whole thesis.
- **S1's figures move, because `prep/size_s1.py` was wrong, not because anything was tuned.** It charged aux drain over the full 6.5 h window; §6.3 binds at 21:00 (6.0 h). 10 kWh, against a D1 bar of ±1 kWh — **D1 would have failed at H0 for a reason unrelated to the ledger.** Fixed and regenerated: **ADER_ENERGY 918 → 928 kW, cut 1,082 → 1,072 kWh, E0 11.42 → 11.43 MWh.** Still `kwh_slack`, still inside the 800–1,300 kWh demo target, `reasonable` still MISS. In the sweep, **100% / 4 h moved partial → full** (also 50%/1h 803→813, 70%/2h 1,880→1,890). The corrected figures are confirmed by two independent implementations (closed-form sizing script and the practice build's 5-minute bucket sweep) agreeing to the decimal.
- **Unchanged and worth saying:** every scope tier, every [A] default, every claim size, the priority ladder, the S1 day. **Nothing was retuned to make a result appear.**

### v0.3 changes (every list change announced)
- **S1 now forces kWh to be the binding constraint** (§8.1 worked arithmetic).
  - The scenario day is pinned to **2026-07-22**.
  - ADER claims are scoped to the co-op aggregation T1 (400 devices) with a multi-hour co-op peak shave.
  - Feeder caps are loosened so they can't be the hidden cause.
  - **Removed from S1:** ARBITRAGE and TOLL_A. Partitions stay in the types and a unit test; TOLL_A moves to S4.
- **New demo invariant D1:** removing the co-op claim flips 20:00 ADER_ENERGY from PARTIAL to ADMITTED. **The cut equals co-op kWh minus the slack that existed without it, clipped to [0, requested]**, not co-op kWh itself (correction to the reviewer's wording).
- **Admission is now written with units** (§6.3): kW room per bucket, kWh slack over time, ENERGY divides by elapsed hours, CAPACITY by max_deploy_h. A CAPACITY hold subtracts MW in every bucket of its window whether or not it's called.
- **Allocator caps each device by its own kWh** and earmarks CAPACITY per device before filling ENERGY (§6.4).
- **`load_hi` in the band** applies only when last known mode is ISLANDED or the device is configured SELF_CONSUME. "Unknown grid → assume the house" is a sweep knob, default off (§6.2).
- **N−1 haircut:** default `n_minus_1` (was `both`). The kW reserve is computed over *currently reachable* regions. The kWh reserve = largest-region kW × assumed outage duration, not the region's whole kWh. Dark regions contribute 0 kW and 0 kWh, so nothing is double-subtracted.
- **Scope moves:**
  - **Must:** discharge-only evening, no recharge.
  - **Must → Should:** CAPACITY deployments and charge-shaped claims.
  - S2 now tests a tower outage *during* ADER_ENERGY delivery, so cutting deployments no longer kills the demo.
- **`capacity_availability_pct`** is now truth-based ("could the held MW have been delivered for max_deploy_h at this bucket"), so it survives cutting deployments.
- **`reasonable`** does per-claim kWh checks (it isn't blind), but not across time and without reserving capacity. Its S1 miss is capacity silently gone after 20:00, not a feeder bug.
- **GVEC stacking** now counts as evidence; the H0 question becomes "who wins the kWh: code or contract?" (§18).
- **Must: η = 1** (no conversion losses), stated as an assumption.

v0.2 changes (kept): layering pitch · ENERGY/CAPACITY · partitions · drain term + comms-vs-grid faults · I2 in GRID/SAFE_HOLD only · naive out of the live demo · 1k-device demo · pre-computed forks · piggybacked leases.

---

## 0. What we claim, and what we don't

**Claim.** When two paid claims want the same evening's kWh and a cell region goes dark, **stop treating last-known SoC as inventory.** Admit bookings only against the pessimistic SoC band and the energy already committed earlier in the evening. Hold an N−1 reserve on the largest reachable region. Turn a future miss into a Notice with lead time, at a measured % of oracle capacity.

**Layering:** an optimizer (MPC/RL; Base is hiring for it [S]) proposes claims. Headroom admits, downgrades and notifies. Notices are the human API for the on-call scheduling engineer.

**We do NOT claim:** that any primitive is new (bands, leases, fencing, N−1 and admission control are all standard) · that Base lacks them [G] · ERCOT settlement accuracy · distribution power flow · that the sim is reality. **The contribution is the composition plus the ops contract,** measured against a competent baseline and an oracle.

---

## 1. The problem (evidence)

- **Base's Algorithms Engineer job:** *"integrate wholesale energy market operations algorithms with grid-service control loops for voltage regulation and system peak shaving"* · *"sequential decision making problem"* · *"on-call scheduling engineer"*. [S]
- **Base's telemetry post:** *"often unreliable and expensive 4G backhaul"*, SQLite store-and-forward, 2-second visibility. [S]
- **ADER:** distribution limitations *"will not explicitly be enforced by ERCOT's systems"*. Under the ALR model all sites sit in one load zone with the same LSE and DSP. [S] **The kWh can exist in the fleet but not in the aggregation that holds the award.**
- **Co-op stacking is real:** the same Base fleet in GVEC territory participates in ADER, and GVEC values it for reducing transmission costs. [S] The article doesn't say how dispatch control is split. That's the H0 question.
- **Austin Energy's 40 MW tolling slice** (~1.5 h, fixed $/kW-month) is a contractual partition. [S] Modelled in S4, not S1.
- **2026-07-22, ERCOT** [S]:
  - load record ~91.3 GW in the early evening (EIA: 91.1 GW hourly, 6pm CT hour)
  - **net-load record 75,733 MW around 8pm CT**
  - **battery discharge record 11,980 MW**
  - ERCOT's April 2026 board deck: 4CP-driven reductions don't line up with net-load peaks.
  - **This is an energy-inventory collision across time.** A battery that shaved the afternoon peak may be empty at 8pm with perfect radios. Stale telemetry makes it worse.

---

## 2. Core ideas (the contribution)

1. **Optimizer proposes, ledger admits.** Floor and feeder cap are **constraints checked before any command**, not weights.
2. **Charge is inventory across time.** ENERGY bookings consume kWh. CAPACITY bookings hold MW in every bucket of their window *and* reserve MW × max_deploy_h kWh. Earlier commitments visibly shrink later room.
3. **Staleness shrinks what you may promise.** The SoC band widens with data age and *plausible* drain; promises come from its pessimistic edge.
4. **Correlated backhaul failure is the reserve object:** N−1 on the largest reachable cell-tower region.
5. **Broken promises become early Notices,** paired with the capacity they cost (anti-gaming).

**Plumbing we rely on, not the pitch:** leases → SAFE_HOLD · (epoch, seq) fencing · max-seq ingest vs store-and-forward floods. Base likely has equivalents [G].

---

## 3. Scope

| Tier | Items |
|---|---|
| **Must** | Frozen contracts · vectorized fleet sim (1k devices) · **discharge-only evening window, no recharge** · network model (latency, loss, duplicates, reordering, comms outage by region, store-and-forward) · grid-outage/ISLANDED mode in the sim (used in S4) · leases + (epoch, seq) · estimator with mode-aware drain · ledger (ENERGY + CAPACITY *holds*, kWh over time, partitions in types, admit/preempt/downgrade/notice, append-only log) · allocator with per-device kWh cap + CAPACITY earmark + feeder caps · controllers **reasonable + headroom + oracle** · S1–S3 · D1 demo invariant · metrics · determinism · property tests with planted-bug controls · batch runner · energy-left band-vs-truth chart + tiles + chaos keys (pre-computed forks) + event log · DEMO.md + backup video |
| **Should** | CAPACITY *deployments* · charge-shaped claims (storm pre-charge, charge arbitrage) · S4 storm (incl. TOLL_A partition), S5, S6 · live fork · 10k perf test · I7 ledger replay · regions grid · bookings panel polish · batch tab (ablations + where we lose) · assumptions tab · knobs drawer · lease-vs-bytes chart · clock skew · η < 1 |
| **Could** | naive controller (test fixture) · proof panel · signed telemetry chain · out-of-process device sim · Playwright smoke |
| **Won't** | Real MPC/RL · settlement engine · voltage/power flow · geo map · auth · DB server · cloud deploy · "the world's smallest DERMS" |

---

## 4. Architecture

**One Python process, simulated clock.** "Distributed" lives in the network model. The controller only receives contract-typed messages the network delivers.

```
                ┌──────────────── SIM (truth) ────────────────┐
 ERCOT parquet ─► scenario ─► faults ─► network ◄──► fleet (numpy) ◄── homes (load), grid state
                └───────────────────────┬─────────────────────┘
                     telemetry/acks ▲   │ ▼ commands (lease, epoch, seq)
                ┌───────────────── CONTROL (belief) ──────────┐
 proposals ────►│ ingest → estimator → ledger → allocator → send│
 (scenario claims stand in for an optimizer)                  │
                └───────────────────────┬─────────────────────┘
                              event log (JSONL) + frames
                                        ▼
                metrics (reads TRUTH) · runner/batch/fork · FastAPI · web
```

**Boundary test:** `control/` must not import `sim/`, except `control/oracle.py` (excluded, flagged).

```
headroom/
  contracts/types.py      # FROZEN after H2
  data/ercot_pull.py      # raises on any date < 2025-12-05
  data/raw/2026-07-22_*.parquet (+ calm day, Fern day)
  scenarios/*.yaml   scenarios/S1_sizing.md (worked arithmetic, §8.1)
  sim/{clock,fleet,homes,grid,network,faults}.py
  control/{estimator,ledger,allocator,controller,baselines,oracle}.py
  metrics/score.py   runner/{run,batch,fork}.py   server/app.py   web/{index.html,app.js,style.css}
  tests/{unit,property,determinism,metamorphic,known_answer,controls,boundary,perf,demo}/
  ASSUMPTIONS.md  DEMO.md  DECISIONS.md  Makefile   # targets: data, test, test-fast, batch, demo
```

**Determinism:** one `numpy.random.Generator` per subsystem, seeded from `hash(seed, subsystem)`. No wall-clock reads in `sim/` or `control/`.

---

## 5. Data contracts (fields)

```python
# ---- enums
LinkState = GOOD | DEGRADED | DOWN          # comms only
GridState = UP | OUTAGE                     # per feeder; independent fault
DeviceMode = GRID | SAFE_HOLD | ISLANDED
SafeHoldBehavior = IDLE | SELF_CONSUME      # [G]; default IDLE; swept
Product = ENERGY | CAPACITY
ClaimSource = COOP_PEAK | UTILITY_TOLL | ADER_AS | ADER_ENERGY | STORM_PRECHARGE | ARBITRAGE
Hardness = FIRM | SOFT
BookingStatus = PENDING | ADMITTED | PARTIAL | DOWNGRADED | PREEMPTED | REJECTED | DEPLOYED | COMPLETE | BROKEN
AckReason = OK | DUP | STALE_SEQ | STALE_EPOCH | FLOOR_GUARD | LEASE_EXPIRED

# ---- static topology
Device:    device_id, region_id, feeder_id, load_zone, territory, aggregation_id?, partition_id?,
           e_max_kwh, p_max_kw, floor_frac, aux_kw, safe_hold_behavior,
           firmware {lease, seq_check, guard}
Feeder:    feeder_id, region_id, export_cap_kw            # PROXY
Aggregation: aggregation_id, load_zone, lse, dsp, device_ids   # ADER scope
Partition: partition_id, owner, device_ids | territory, reserved_kw, max_deploy_h

# ---- sim truth (never sent to control)
soc_kwh, p_actual_kw, mode, setpoint_kw, lease_expiry_ts, last_epoch, last_seq,
home_load_kw, grid_state, link_state, clock_skew_s, sf_buffer

# ---- uplink / downlink
Telemetry: device_id, seq, device_ts, soc_kwh, p_kw, home_load_kw, mode, last_applied(epoch, seq), buffered
Ack:       device_id, cmd_epoch, cmd_seq, reason, device_ts      # network adds recv_ts
Command:   device_id, epoch, seq, setpoint_kw, issued_ts, lease_s, booking_ids[]
LeaseRenew: device_id, epoch, lease_expiry_ts                    # piggybacks on the telemetry ack

# ---- ledger
Claim:     claim_id, source, product, priority, hardness,
           scope ("fleet"|"aggregation:A"|"territory:Y"|"partition:P"|"feeder:Z"),
           start_ts, end_ts, power_kw (>0, discharge; Must is discharge-only),
           max_deploy_h (CAPACITY), tolerance_frac, price_ref?, created_ts
Booking:   booking_id, claim_id, bucket_ts, requested_kw, admitted_kw, hold_kw, reserved_kwh, consumed_kwh,
           status, reason, kw_room_at_decision, kwh_slack_at_decision, decided_ts
Deployment (Should): deployment_id, booking_id, start_ts, end_ts, kw
Notice:    notice_id, booking_id, ts, old_kw, new_kw, lead_time_s, cause   # plain words

# ---- controller belief
Estimate:  device_id, recv_age_s, soc_low_kwh, soc_high_kwh, reachable, lease_live, load_term_on,
           kw_cap, kwh_above_floor_low, earmark_kw, earmark_kwh, region_id, feeder_id, aggregation_id

# ---- event log / frames / metrics
Event:  ts, seq, kind, controller, payload, text
Frame:  ts, controller, booked_kw, delivered_truth_kw, belief_low_kw, belief_high_kw, kw_room,
        energy_left_low_kwh, energy_left_truth_kwh, committed_future_kwh (stacked by claim), oracle_kw_room,
        counters{floor_breach_dev_s, feeder_breach_kw_s, silent_breaches, notices},
        region_freshness{region_id: FRESH|AGING|STALE|LEASE_EXPIRED}, log_seq_range
metrics.json: scenario, seed, controller, ablation, promise_kept_pct, capacity_availability_pct,
        committed_kwh, delivered_kwh, silent_breach_buckets, silent_energy_buckets,
        silent_capacity_buckets, late_breach_buckets, downgrades,
        median_lead_time_s, flap_count, floor_breach_dev_s, feeder_breach_kw_s, crash_recovery_s,
        capacity_held_back_vs_oracle_pct, msgs_per_device_day, bytes_per_device_day,
        revenue_indicative_usd, runtime_s, events_sha256
```

---

## 6. Runtime dynamics

### 6.1 Control tick (`dt_ctrl` 10 s; physics `dt` 2 s demo / 10 s batch)
1. **Ingest:** dedupe `(device, seq)`; estimator uses only the max-seq message per device.
2. **Estimate** (§6.2).
3. **Ledger:** recompute admission for **not-yet-started buckets** from scratch, in priority order (§6.3). Started buckets are **locked**, the bucket currently being dispatched among them — the lock unit is the bucket, never the claim (v0.7; §6.3 Reconcile).
4. **Allocate** (§6.4).
5. **Send** on setpoint change > `δ_kw` [A: 0.5 kW]. Lease renewal piggybacks on the telemetry ack.
6. **Physics:**
   - Apply a command if its `(epoch, seq)` is newer.
   - Lease expired → SAFE_HOLD: grid setpoint 0. IDLE drains `aux_kw`; SELF_CONSUME serves home load down to the floor.
   - Grid OUTAGE → ISLANDED: serves home load, floor may be spent, not dispatchable.
   - GRID with guard on: clamp at the floor.
   - `soc -= (p_actual + aux + served_home_load)·dt/3600`, with η = 1 [A, Must].
   - Telemetry every `T_tel` [S: 2 s]; DOWN → buffer; flush at `B` [A: 50 msgs/s].
   - **Must: no charging** anywhere in the evening window.
7. **Metrics** on truth; write events and frames.

### 6.2 Estimator (per device *i*)
- `a = now − recv_ts_last`; `reachable = a ≤ k·T_tel` [A: k 5]. ISLANDED is never dispatchable.
- `τ_cmd = clip(min(now, lease_expiry) − recv_ts_last, 0)` (setpoint uncertainty stops at lease expiry).
- `τ_aux = clip(now − recv_ts_last, 0)` (standby drain never stops).
- **Load term (mode-aware, v0.3):**
  - `load_term_on` = last reported mode is ISLANDED, **or** (`safe_hold_behavior == SELF_CONSUME` and lease expired).
  - Knob `assume_house_when_grid_unknown` (default **false**; sweep true) also turns it on for devices whose comms are down.
  - `τ_load` = for ISLANDED `τ_aux`; for SELF_CONSUME `clip(now − max(recv_ts_last, lease_expiry), 0)`.
  - `load_hi` = p95 home load for the hour [A].
- `soc_low = soc_rep − max(p_rep, c_last, 0)·τ_cmd/3600 − aux_kw·τ_aux/3600 − load_term_on·load_hi·τ_load/3600 − ε`
- `soc_high = soc_rep − min(p_rep, c_last, 0)·τ_cmd/3600 + ε` (ignores aux; promises only use `soc_low`) [A: ε 1% e_max]
- `kwh_above_floor_low = max(0, soc_low − floor)`; `kw_cap = reachable · p_max`.
- **Scope capability** (per aggregation/territory/partition, reachable devices only; dark devices contribute **0 kW and 0 kWh**):
  - `KW(scope) = Σ_f min(export_cap_f, Σ_{i∈f∩scope} kw_cap_i)`
  - `E0(scope) = Σ kwh_above_floor_low`
- **Haircut** (default `n_minus_1`; sweep `independent`, `both`):
  - `kw_reserve = max over reachable regions r in scope of KW(r ∩ scope)`
  - `kwh_reserve = kw_reserve × outage_h` [A: 0.5 h], applied only at times inside an active discharge or CAPACITY window.

### 6.3 Ledger: admission with units
Buckets `b` are 5 min (`Δh = 1/12`). For scope S, bucket end t, and the set of already-admitted bookings A (higher priority first, ties by `created_ts`):

- **kW room:** `kw_room(b) = KW(S) − kw_reserve − Σ_{a∈A active in b} hold_kw_a(b)`
  - ENERGY `hold_kw` = admitted_kw inside its window.
  - **CAPACITY `hold_kw` = admitted_kw in every bucket of its window, called or not.**
- **kWh slack:** `slack(t) = E0(S) − drain_hi(now→t) − Σ_{a∈A} consumed_cum_a(t) − Σ_{a∈A CAPACITY, t∈window_a} admitted_kw_a·max_deploy_h_a − kwh_reserve(t)`
  - `drain_hi` = aux (+ load where on) of reachable devices over the horizon.
  - **`kwh_reserve(t)` is held at EVERY `t` from `now` to `horizon_ts`** (v0.6). v0.3-v0.5 said "applied only at times inside an active discharge or CAPACITY window"; that is a hole. **[Found by the property lens, 2026-09-16, `PRACTICE-NOTES.md` FINDING-11.]** A claim whose window sits outside every other window is charged no reserve at those times, spends against it, and continuing aux drain then eats what was supposed to be untouchable. **A reserve that evaporates between windows is not a reserve.** Verified before the change: holding it unconditionally moves no S1 number (928.2 kW, 1,071.8 kWh cut, both readings).
  - ENERGY `consumed_cum(t)` = admitted_kw × hours of its window elapsed by t.
  - CAPACITY `consumed_cum` = 0 unless deployed (Should).
- **Where `t` is evaluated (v0.4; this is not a detail — see below):** `t` ranges over **bucket ends**, from `start(W) + Δ` to **`horizon_ts`, the end of the scenario window** — *not* the end of W. Skip any `t` with `elapsed_h_W(t) = 0`; at `t = start(W)` the divisor is zero.
- **ENERGY admission:** `admitted_kw = min( requested_kw, min_{b∈W} kw_room(b), min_{t ∈ evalgrid(W)} slack(t) / elapsed_h_W(t) )`, where `evalgrid(W) = { bucket ends in (start(W), horizon_ts] }` and `elapsed_h_W(t)` is the hours of W elapsed by t, capped at W's duration.
- **CAPACITY admission:** `admitted_kw = min( requested_kw, min_{b∈W} kw_room(b), min_{t∈W} slack(t) / max_deploy_h )`

> **Why the upper bound is load-bearing [verified 2026-09-16, `PRACTICE-NOTES.md` FINDING-3].**
> Because a CAPACITY reservation is subtracted only at `t ∈ window_a`, an ENERGY claim that *ends
> before* a CAPACITY window never meets that reservation at any `t` inside its own window. Stopping
> the search at W's end — the natural reading — therefore admits an earlier ENERGY claim that
> consumes the kWh a later CAPACITY hold was keeping, **and the ledger reports nothing wrong.**
> Measured on S1 with COOP_PEAK raised to 460 kW (1:8 scale): stopping at W's end gives
> **capacity availability 0%, 24 silent buckets**; evaluating to `horizon_ts` gives **100% and 0**.
> Same paragraph, two readings, opposite answers — and the wrong one makes Headroom strictly worse
> than `reasonable`. A planted-bug control asserts this (§11).
- **Status:** ADMITTED if `= requested`; PARTIAL if `0 < admitted < requested` (reason = the binding term: `kw_room` | `kwh_slack` | `feeder` | `reserve`); REJECTED if 0.
- **Reconcile (v0.7, implemented 2026-09-17):** an `Admission` is a **profile** — one admitted kW per bucket — carried as the live decision plus the buckets already frozen. A bucket is **locked once its start has passed**, which includes **the bucket being dispatched right now**: the allocator is already placing it on devices, so there is nothing left to decide about it. A later pass revises only the free tail, so the earliest bucket it can move starts one full bucket in the future and **every Notice carries a lead time of at least one bucket — 300 s — never zero**.
  - A pass that lowers a not-yet-started bucket → **Notice** (cause names the binding term and the booking or fault that consumed it).
  - **A claim with no free buckets decides nothing.** Recomputing a settled claim produced a Notice for `COOP_PEAK` at 20:15 about a window that closed at 18:30 — its kWh term was vacuous (`+inf`) and its kW room was read off buckets in the past. **[FINDING-20.]**
  - The in-flight bucket stays sold at the number it was sold for, and may therefore under-deliver: that is a **late** breach, not a silent one. Cutting it instead scores zero breaches by un-promising energy already being delivered. **[FINDING-18.]**
  - **Downgrade-only** in the Must scope: hysteresis on upgrades [A: +5% for 60 s] is specified and not implemented, so a booking that has been cut stays cut. Carry the **whole prior decision** forward, not just its kW — kW, binding reason and both at-decision diagnostics are one record, and updating them piecemeal left a PARTIAL blaming `NONE`. **[FINDING-19.]**
  - Count flaps.
- **Priority ladder** [A; confirm §18 Q1]:
  1. UTILITY_TOLL partitions
  2. ADER_AS
  3. COOP_PEAK
  4. ADER_ENERGY
  5. STORM_PRECHARGE (Should)
  6. ARBITRAGE
- **Partitions:** a claim scoped to `partition:P` uses only P's devices. P's reserved kW and kWh are subtracted from every other scope containing those devices.

### 6.3b N-1 deliverability, per device (v1.0)

§6.3 decides with sums: `KW(S)` and `E0(S)`. Both can be large while the kW that has energy
behind it is small, because an empty battery still reports its full `kw_cap`. Stage 2 of
admission (`Ledger.enforce_n1`, `control/deliverability.py`) closes that gap.

- **Project.** From `now` to the last CAPACITY bucket, drain a per-device copy of the reachable
  fleet bucket by bucket, placing admitted ENERGY exactly as §6.4 will: water-fill, each device
  capped at `min(kw_cap_i, kwh_i / h_remaining)`, plus aux.
- **Check ENERGY.** At every bucket the ledger may still decide (not locked): after the CAPACITY
  earmarks, `Σ_i min(kw_cap_i, kwh_i / h_remaining) ≥ energy_kw`, on the reachable fleet as it is.
  ENERGY is not a reserve product; losing a region mid-delivery is handled by Notices.
- **Check CAPACITY.** For each reachable region `r` (mode `n1`), or for none (mode `n0`):
  re-place ENERGY on the devices outside `r`, then require
  `Σ_{i∉r} min(kw_cap_i − energy_kw_i, kwh_i / max_deploy_h) ≥ Σ hold_kw`.
  This is the same expression `metrics.score.capacity_deliverable` scores, on purpose.
- **Yield.** The most junior claim that is *in the failing bucket* is lowered (previous admission
  tried first, else bisected to 0.5 kW) until the buckets it is in pass. A bucket no remaining
  claim can relieve is set aside and the next one is taken. Binding reason `reserve` when the
  region check bound it, `kw_room` when the energy check or `n0` did.
- **Mode per controller, never inferred** (`cfg.deliverability`): `n1` headroom; `n0` the oracle
  (no reserve, but it may not count empty batteries as power either — that is what makes it a
  ceiling); `off` the `reasonable_plus_*` baselines and `headroom_no_n1`.
- **Not scored:** locked buckets. A bucket already in delivery is drained through but cannot be
  re-decided; its shortfall is the late Notice's job (§6.3 Reconcile).
- **Open (owner):** with one region already dark, stage 2 still protects against losing a second.
  Releasing that would admit more on Beat B-style evenings (§18 Q3).

### 6.4 Allocator (per control tick, active buckets)
1. **Earmark CAPACITY first, proportionally (v1.0):** every reachable device gives the same share of its free kW and of its free kWh, so `Σ earmark_kw = hold_kw` and `Σ earmark_kwh = hold_kw·max_deploy_h`. (v0.x picked devices greedily by freshness then kWh margin; that emptied the richest devices and stranded ENERGY on the rest late in the window.)
2. **Fill ENERGY on what's left:** `kw_i ≤ min( kw_cap_i − earmark_kw_i, (kwh_above_floor_low_i − earmark_kwh_i) / h_remaining(W) )` (per-device kWh cap over the claim's remaining duration, not the tick). Respect feeder caps. Order by freshness, then remaining kWh margin.
   - **`h_remaining(W)` = `(end(W) − now + tick) / 3600`** (v1.0). The window's ticks are `(start, end]` and the tick *at* `end` still delivers for one tick, so the current tick counts. v0.4 measured it from the start of the bucket being allocated, which is right for an allocator that runs once per bucket; the runner calls it every tick with the tick time, so it became `now − Δ` and over-divided by five minutes on every tick. The v0.4 text follows for the record: Measured from the **start** of the bucket being allocated, not its end, so the device is capped over energy it has not yet spent. At 5-minute buckets the two readings differ by 8% of the cap in the final bucket — which is exactly where the floor clamp bites. Floor at `Δh` so the last bucket cannot divide by zero.
3. **Overprovision** by `kw_reserve`-backed margin only (no ad-hoc %). Closed loop: PI on `delivered_est` (telemetry age ≤ 2·T_tel + acked setpoints) [A: Kp 0.5, Ki 0.05], clamped by step 2 caps.
4. **Deployments (Should):** convert earmarks into setpoints, then re-earmark.

### 6.5 Controller crash + recovery
Crash for `D` s [A: 90]. On restart: `epoch += 1`, replay the log, rebuild the estimator after `k·T_tel`, resend. Devices reject stale-epoch commands. `crash_recovery_s` = crash end → delivered within tolerance.

### 6.6 Network, outages, lease cost [A]
- **Link Markov chain:** GOOD→DEGRADED 0.5/h, DEGRADED→GOOD 6/h, DEGRADED→DOWN 0.3/h, DOWN→GOOD 2/h. Latency lognormal (median 300 ms / 3 s), loss 0.5% / 15%, duplicates 0.5%, reorder window 5 s, skew ±30 s (Should).
- **Comms outage** `region_down`: links DOWN, grid UP. Last setpoint runs until the lease expires, then SAFE_HOLD (IDLE by default).
- **Grid outage** `feeder_outage` → ISLANDED. Storm = both, correlated (S4).
- **Lease cost:** 2 s telemetry = 43,200 msgs/device/day (~2.1 MB at 48 B). A standalone 20 s heartbeat adds 4,320 (~0.14 MB, ~7%). **Default: piggyback on telemetry acks → 0 extra.** A chart shows both.

---

## 7. Controllers compared

| Controller | Belief | Energy logic | Products | Feeder/scope | Leases | Reserve | Notices | Where |
|---|---|---|---|---|---|---|---|---|
| **reasonable** (first draft) | pessimistic band, as built (v1.0 correction) | **per-claim** check `kW·h ≤ current kWh` (independent claims, not cumulative, no CAPACITY kWh reserve), admitted once | single kW | yes | shared 60 s | none | no | demo + batch |
| **reasonable_plus_dN** (standard practice) | point estimate + timeout exclusion | §6.3 ledger, cumulative, energy behind capacity, re-admission | ENERGY + CAPACITY | yes | 60 s | flat N% of kW and kWh | yes | `runner/compare.py` |
| **headroom** | band, mode-aware drain | kWh slack over time (§6.3) | ENERGY + CAPACITY holds | yes | 60 s piggybacked | N−1 reachable region | yes | demo + batch |
| **oracle** | truth, knows future faults | perfect-info §6.3 | ENERGY + CAPACITY | yes | — | none | — | ceiling + batch |
| naive (Could) | last *received* | none | single kW | no | ∞ | none | no | tests only |

**Ablations** (Should): − band (point estimate) · − kWh-over-time (per-claim like reasonable) · − N−1 · − per-device kWh cap · − notices.

---

## 8. Scenarios

### 8.1 S1 sizing: kWh must bind (worked arithmetic; a `known_answer` test asserts it)

**Day:** 2026-07-22. **Window:** 15:00–21:30 CT, discharge only, **no recharge anywhere in the window**. Justification: net load and battery discharge were still climbing into 8pm on that day [S], and afternoon charging at those prices defeats the peak shave.

**Fleet:** 1,000 devices, unit mix 70% 39.2 kWh / 30% 78.4 kWh (avg 50.96 kWh, avg p_max 14.95 kW [A]).
- **T1 = ADER aggregation A1:** 400 devices, 5 regions × 80 devices, feeder cap 80% [A for S1].
- **T2/T3:** 600 devices outside A1, idle in S1. They show that *the fleet has kWh the aggregation can't use*.
- **T1 at 15:00:** SoC 80% [A: fleet near-full after midday] → above floor 60% → **12.23 MWh**.

**Belief components at admission (15:00, fresh data):**
| Term | Value |
|---|---|
| ε: 1% × 20.38 MWh nameplate | −0.20 MWh |
| aux: 400 × 50 W × **6.0 h** (15:00 → 21:00, the binding `t`, not the 6.5 h window) | −0.12 MWh |
| N−1 kWh: largest region kW 80 × 14.95 × 0.8 = 0.957 MW × 0.5 h | −0.48 MWh |
| **E0 − drain − reserve** | **≈ 11.43 MWh** |
| **kW:** 400 × 14.95 × 0.8 = 4.78 MW − N−1 0.96 | **≈ 3.83 MW room** |

**Claims:**
| Claim | Product | Scope | Window | kW | kWh | Priority |
|---|---|---|---|---|---|---|
| COOP_PEAK | ENERGY | territory:T1 | 15:30–18:30 (multi-hour 4CP-day shave) | 3,000 | 9.0 MWh | 3 |
| ADER_AS | CAPACITY | aggregation:A1 | 19:00–21:00, max_deploy_h 1.0 | 1,500 | 1.5 MWh reserved | 2 |
| ADER_ENERGY | ENERGY | aggregation:A1 | 20:00–21:00 | 2,000 | 2.0 MWh | 4 |

**kW check (not binding):** 19:00–21:00 holds 1.5 + 2.0 = 3.5 MW ≤ 3.83 MW. At 15:30, 3.0 MW ≤ 3.83 MW.

**kWh (binding):**
1. **AS (priority 2):** slack 11.43 ≥ 1.5 → **ADMITTED 1,500 kW**.
2. **Co-op (priority 3):** slack 11.43 − 1.5 = 9.93 ≥ 9.0 → **ADMITTED 3,000 kW**.
3. **ADER_ENERGY (priority 4):** slack at 21:00 = 11.43 − 9.0 − 1.5 = **0.93** → **PARTIAL ≈ 928 kW**, reason `kwh_slack`, consumed by COOP_PEAK.

**D1 invariant — stated as arithmetic, because the prose reading is ambiguous.**

Let `A` be the ledger's admitted set in the **with-co-op** world and `A'` the admitted set in the
**co-op-removed** world (same seed, same fleet, same `now`, everything else identical).

```
cut_kwh        = ( requested_kw(ADER_ENERGY) − admitted_kw_A(ADER_ENERGY) ) × dur_h(ADER_ENERGY)
coop_kwh       = admitted_kw_A(COOP_PEAK) × dur_h(COOP_PEAK)
slack_without  = min over t ∈ evalgrid(W_ADER_ENERGY) of slack_A'(t)
                 # A' ALREADY CONTAINS ADER_ENERGY, so slack_A'(t) has already subtracted its
                 # own consumed_cum. Do NOT subtract ADER_ENERGY again.
assert abs( cut_kwh − clip(coop_kwh − slack_without, 0, requested_kwh(ADER_ENERGY)) ) ≤ 1.0
assert binding_reason_A(ADER_ENERGY) == "kwh_slack"      # not kw_room, not feeder, not reserve
```

- **Worked, 400 devices:** without co-op, slack before ADER_ENERGY = 11.43 − 1.5 = 9.93 ≥ 2.0 →
  **ADMITTED 2,000 kW**, and `slack_without = 9.93 − 2.0 = 7.93`.
  **Cut** = clip(9.0 − 7.93, 0, 2.0) = **1.07 MWh** (2,000 → ~928 kW over 1 h). ✓
- **[Trap, cost me a failing D1 on 2026-09-16]** Reading "the slack that existed without it" as the
  slack *before* ADER_ENERGY is subtracted double-counts and D1 fails with a **plausible-looking
  wrong number** (1.07 vs 2.00 MWh at full scale) — nothing about it looks like a bug. This is the
  most load-bearing test in the build; it is written as the block above, in `contracts/`, not in prose.
- Both figures are computed from the ledger's own components, never from `scenarios/S1_sizing.md`.
  The sizing file is an independent closed-form check of the same arithmetic, and the two agreeing
  is itself a control (they disagreed by 10 kWh until FINDING-9 was fixed).

**Reasonable on S1** (per-claim check against *current* 12.2 MWh): admits all three in full. Truth at 20:00 ≈ 12.23 − 9.0 − 0.1 ≈ 3.1 MWh; ADER_ENERGY takes 2.0 → ~1.1 MWh left, while AS needs 1.5 MWh for its window. **Silent capacity unavailability 20:00–21:00** (`capacity_availability_pct` < 100% with no Notice). The S1 miss is over-committed kWh, not a feeder bug.

Exact values depend on the pulled home-load profile and final [A]s. Re-derive in `scenarios/S1_sizing.md` in prep week, and **size backwards from D1**: pick SoC and claim sizes so the binding reason is `kwh_slack` with a cut of 0.8–1.3 MWh.

### 8.2 Scenario table
| ID | Story | Expected (asserted by demo tests) | Tier |
|---|---|---|---|
| **S1 co-op evening** | As §8.1 | headroom: ADER_ENERGY PARTIAL at 15:00 (5 h lead), 0 silent · reasonable: silent capacity loss 20:00–21:00 · D1 holds | Must |
| **S2 tower down in delivery** | S1 + comms outage on one A1 region 20:15–20:45 (grid up) | headroom: current bucket covered by N−1, **Notice** cutting ADER_ENERGY's remaining buckets once N−1 is recomputed on the next region; 0 silent · reasonable: under-delivery 20:15+ with no notice · **ablation −N−1 turns on a silent breach** | Must |
| **S3 controller crash** | S1 + crash 20:05 for 90 s | epoch bump; recovery time; 0 stale-epoch commands applied | Must |
| S4 storm | pre-charge (charge claims) + correlated grid + comms outage → ISLANDED; TOLL_A partition honestly unavailable → Notice | — | Should |
| S5 duplicate storm | retries/duplicates/reorders ×10 | I5 holds at scale | Should |
| S6 chaos monkey | random fault composition | batch only | Should |

---

## 9. Real ERCOT data (prep week)

- **Pin `ercot_day: 2026-07-22`.** Calm day **2026-05-29**; Fern days **2026-01-25/26** (see DECISIONS.md). **Pulled 2026-09-15** into `data/raw/`: EIA-930 hourly, ERCOT native load, 15-min fuel mix, and MIS RT/DAM/AS 2026 price archives. None of it needs an ERCOT API key.
- **The calm day is the "where we lose" case** (v0.5, 2026-09-16). On **2026-05-29** (load peak 77.8 GW, net-load peak 50.0 GW, batteries +5.8 GW — a third of July 22's stress) `headroom` holds an N−1 reserve and tightens admission for an evening that never gets tight, while `reasonable` is fine. That is slide 3's honest losing case, and it is why the calm day was pulled. **Fern (2026-01-25/26) is S4-only** — a winter **morning** peak (75,599 MW at HE 09:00) inverts the collision, so the scarce thing is the overnight charge.
- **Sizing result (`scenarios/S1_sizing.md`):** D1's formula holds on every point of the sweep, but the PARTIAL band is **narrow**. At 3 h: 70% SoC → ADER_ENERGY rejected, 80% → partial (928 kW, cut 1,072 kWh), 90% → full. **Demo consequence:** don't present one tuned point as "the" result. Show the start-SoC slider sweeping the 20:00 booking from full → partial → zero, and say plainly that the transition is sharp.
- **Pull:** RT settlement point prices (load zones, 15-min) · 5-min SCED hub LMPs · system load, wind and solar actuals · RT/DA AS prices.
- **Guard:** raise on dates before 2025-12-05. Rate limit 30 req/min [S]: pull once, commit parquet.
- **Check the pulled data** confirms the load peak early evening and the net-load peak ~8pm. If the timestamps disagree with the blog/EIA figures, trust the data and update slide 1.
- **Use:** net-load curve times windows and drives slide 1; prices feed `revenue_indicative_usd` only (labelled).

---

## 10. Metrics

- **ENERGY silent breach bucket:** `delivered_truth < (1 − tol)·admitted_at_bucket_start` with no prior Notice [A: tol 5%]. Late = Notice inside the bucket.
- **CAPACITY availability (truth-based, no deployments needed):** for each bucket in a CAPACITY window, could truth reachable devices in scope deliver `hold_kw` for `max_deploy_h` from `soc − floor`, given concurrent ENERGY? `capacity_availability_pct` = share of yes. **CAPACITY silent breach bucket** = a no with no prior Notice.
- **`silent_breach_buckets` = `silent_energy_buckets` + `silent_capacity_buckets`, and all three are reported** (v0.4).
  > **[Verified 2026-09-16, `PRACTICE-NOTES.md` FINDING-5.]** On S1 `reasonable` keeps **99.97%** of its
  > ENERGY promise — its ENERGY delivery is essentially perfect. Everything it loses is the AS hold
  > (capacity availability 75%, 6 silent capacity buckets). An ENERGY-only count therefore scores
  > `reasonable` at **0 silent on S1**, which would **fail §11's own control on a correct
  > implementation** and leave the demo's headline tile reading zero. The tile in §13 sums both.
- **promise_kept_pct** (ENERGY). **Not the S1 headline** — see above; on S1 both controllers are ≈100%.
- **floor_breach_dev_s** in GRID/SAFE_HOLD (ISLANDED excluded), guard OFF and ON.
- **feeder_breach_kw_s** (proxy).
- **capacity_held_back_vs_oracle_pct:** shown next to every zero.
- **flap_count, downgrades, median_lead_time_s:** anti-gaming.
- **msgs_per_device_day, bytes_per_device_day.**

---

## 11. Tests

| Layer | What | Pass bar |
|---|---|---|
| **known_answer** | **D1** (§8.1) with binding reason `kwh_slack` · a CAPACITY hold subtracts kW from **every** window bucket before any call · a 17:00 ENERGY booking lowers 20:00 slack by exactly kW·h · per-device kWh cap: a device with 0.4 kWh above floor and 1 h remaining gets ≤ 0.4 kW · comms-down IDLE device: kW → 0 at lease expiry, band widens at `aux_kw` only · SELF_CONSUME device widens by `load_hi` after lease expiry · a flush never overwrites fresher SoC | exact / ±1 kWh |
| **unit** | band monotonic · feeder clip · partition subtraction · hysteresis · epoch fencing · N−1 over reachable regions only · **an ENERGY claim that ENDS BEFORE a CAPACITY window must not be jointly admissible beyond E0** (v0.4; §6.3 subtracts a CAPACITY reservation only at `t ∈ window_a`, so this is the case the formula does not protect on its own — FINDING-1) | — |
| **property** (hypothesis, 200 ex.) | **I1** plan never exceeds kW room or kWh slack · **I2** truth floor never breached in GRID/SAFE_HOLD with guard OFF · **I3** proxy feeder cap never exceeded · **I4** every silent breach attributable (loss beyond configured reserve) · **I5** duplicate/reorder = max-(epoch, seq) · **I6** same seed → same `events_sha256` · **I6b** fork with no fault = continuous · **I8** ENERGY never consumes CAPACITY earmarks (kW or kWh) · **I9** per-device setpoint ≤ its kWh cap over remaining window · (Should) I7 replay | all hold |
| **metamorphic** | more staleness → room ↓ · more devices → ↑ · lower cap → ↓ · higher floor → ↓ · adding an earlier ENERGY booking → later slack ↓ · moving a device out of the aggregation → aggregation slack ↓, fleet total unchanged | all hold |
| **controls** | Planted bugs that must turn a metric non-zero on S1–S2 seeds: drop kWh-over-time → capacity silent breach · drop CAPACITY kW hold → I8/availability · drop per-device kWh cap → floor clamp hits / under-delivery · drop N−1 → S2 silent breach · drop drain term → I2 with guard OFF · **C6 (v0.4) set the ENERGY kWh term's upper bound to `end(W)` instead of `horizon_ts` → `silent_capacity_buckets` > 0 on the S1-stress seed** (COOP_PEAK 460 kW at 1:8 scale gave availability 0% and 24 silent buckets; §6.3) · **C7 (v0.4) shift the hero chart's x data by one window length → the fixture assertion below must fail.** `reasonable` must show non-zero **`silent_breach_buckets` (energy + capacity)** on S1 **and** S2 — on S1 the non-zero part is **capacity**, because its ENERGY promise is ≈100% (§10). | a planted bug scoring 0 fails the build |
| **boundary** | AST: `control/` (minus `oracle.py`) imports nothing from `sim` | none |
| **chart fixture** (v0.4) | The hero chart is on the never-cut list, so it gets a test: render from a fixture frame set and assert the **truth line has ≥ 90% of its points inside the axis limits** and a non-zero y-range. **[Verified 2026-09-16, FINDING-7]** the practice chart plotted minutes-from-15:00 against a clock-hour axis and matplotlib produced a clean, correctly styled, **entirely blank** figure and exited 0. "The PNG was written" is not the assertion. | inside the axes |
| **perf** | batch 1k devices × 1000 seeds × (reasonable, headroom, oracle) ≤ 20 min · (Should) 10k, dt 2 s, 6.5 h ≤ 90 s | meets |
| **demo** | DEMO.md via API asserts §8.2 expectations · (Could) Playwright screenshot looked at | green |

`make test-fast` (known_answer, unit, boundary, 1-seed determinism) before every merge; `make test` before RT passes and freeze.

---

## 12. UX

**Personas:** first-time reviewer (3 min) · Base engineer (knobs, where we lose) · fiction: Base's on-call scheduling engineer at 19:55.

**Principles**
- **The band is the promise.** Hero chart = aggregation energy left: belief band (pessimistic edge bold) vs truth line, with **future committed kWh stacked by claim**. At 15:00 the co-op block visibly squeezes ADER_ENERGY to PARTIAL, 5 hours ahead. A faint line shows T2/T3's unusable kWh.
  - **Annotate the crossing explicitly** (v0.4). The moment the commitment stack rises above the truth line *is* the thesis, and at S1's sizing it is a ~40 kWh gap on a 1,500 kWh axis — a few pixels on a projector. It needs a marker, a label and the kWh number, not the judge's eyesight. **[Verified 2026-09-16, `PRACTICE-NOTES.md` FINDING-10.]**
- **Three states:** OK · DOWNGRADED/PARTIAL (honest, lead time, binding reason) · BROKEN (silent). Icon + text.
- **One-sentence log lines naming the binding term and what consumed it.**
- **Units everywhere;** CT times. One-key chaos (1–6), `R` reset, deterministic.
- **Projector-safe:** light high-contrast, hero numbers ≥ 48 px, 1280×720.
- **Ops console, not a dashboard template:** dense, monospace numerals, no card grids or eyebrow labels.

**Demo flow**
1. S1 at 60×: PARTIAL at admission, reason `kwh_slack`. Toggle the co-op claim off and on to show D1 live.
2. Key `2`: tower down 20:15. N−1 covers, Notice for the remaining buckets; reasonable silently under-delivers.
   - **On S1, say it precisely** (v0.4): `reasonable` does **not** under-deliver energy — it keeps ~100% of its ENERGY promise. What it loses is the **ancillary-service hold it is paid to keep**, which stops being deliverable without anyone being told (availability 75% vs 100%). Saying "it under-delivers" on S1 is wrong and invites the strawman attack. **[FINDING-6.]**
3. Key `4`: crash; recovery.
4. Batch tab: zeros next to held-back %, where headroom loses, lease-vs-bytes.

The naive flush bug is not in the live demo.

---

## 13. UI

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ HEADROOM  S1 co-op evening · 2026-07-22 ▾  seed 42  ⏵60×  20:18:10 CT   headroom ⇄ reasonable  ⚙ │
├───────────────────────────────────────────────────────────┬──────────────────────────┤
│ A1 ENERGY LEFT (MWh)                                      │ BOOKINGS (A1)             │
│  ░░ belief band (bold pessimistic)  ━━ truth              │ [x] CO-OP PEAK 15:30–18:30│
│  ▤ future commitments: CO-OP ▤ ADER_AS reserve ▤ ADER_ENERGY│   3.0 MW  COMPLETE        │
│  ┄ T2/T3 kWh (not in A1)   ┆ oracle   ▼ faults ◆ notices  │ ADER AS 19–21 1.5 MW hold │
│ MW strip: booked · delivered · kW room                    │   OK · avail 100%         │
│ inset: ERCOT 2026-07-22 load vs net load (5pm vs 8pm)     │ ADER ENERGY 20–21         │
│                                                           │   2.0→0.93 PARTIAL kwh_slack│
│                                                           │   (co-op used 9.0 MWh)    │
├──────────────┬──────────────┬─────────────────────────────┤   ◆ 20:15 →0.61 (5m ahead)│
│ SILENT E+C   │ FLOOR        │ FEEDER (proxy)              ├──────────────────────────┤
│   0          │   0          │   0                         │ REGIONS A1: R1■ R2■ R3░   │
│ reasonable 6 │ reasonable 0 │ reasonable 0                │ R4■ R5■  (R3 dark 3m)     │
│ held back 7.8% of oracle    (SILENT = energy + capacity;  │                          │
│  on S1 reasonable's 6 are ALL capacity — §10, FINDING-5) │                          │
├───────────────────────────────────────────────────────────┴──────────────────────────┤
│ [1 toggle co-op] [2 tower down▾] [3 delay telemetry▾] [4 crash controller] [5 dup storm] [R] │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ 15:00:00 ADER ENERGY 20:00 admitted 928 of 2000 kW: kWh slack 0.93 MWh after CO-OP PEAK 9.0 MWh + AS reserve 1.5 │
│ 20:15:00 R3 dark → 80 devices unreachable → ADER ENERGY cut 928→124 kW (kwh_slack) from 20:20, 5 min ahead │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Surface (v0.7, built 2026-09-17): a STATIC PAGE, not a server.**

`web/demo.html` is a local file. `prep/bake_runs.py` pre-computes every run the demo can show
and writes `web/out/runs.js`, which the page loads with a `<script src>` - **not** `fetch`,
which Chrome blocks on a `file://` origin as cross-origin, so a fetch-based page works when
served and dies on the demo laptop. `python run.py demo` re-bakes and opens it.

There is no port, no process and no startup race between the pitch and the judges; pre-flight
check 3's criterion of **0 external requests** holds by construction rather than by inspection.
The cost is that a committed bake can go stale, so `prep/bake_runs.py --check` and a `demo`
lens both assert the bake matches the checked-out code.

**The HTTP API below is DEFERRED, not built.** It is the Should-tier shape for a batch UI:
- `GET /scenarios` · `POST /run {scenario, seed, controllers[], overrides?}`
- `GET /runs/{id}/frames` · `GET /runs/{id}/events` · `POST /runs/{id}/fork`
- `GET /batch/results`

---

## 14. Red team

### A. Claim attacks
| Attack | Answer |
|---|---|
| "None of this is new" | Agreed (§0). Composition + ops contract, measured. |
| "The evening was never scarce" | D1 test + §8.1 arithmetic; binding reason `kwh_slack` shown in the UI. |
| "80% SoC at 15:00 and a 3-hour shave are cherry-picked" | Sweep start SoC 50–100% and shave 1–4 h; show where D1 stops binding. |
| "You flattened products" | ENERGY/CAPACITY holds in kW and kWh, I8, per-device earmarks. |
| "The allocator dumps duration on empty units" | Per-device kWh cap (I9) and its planted-bug control. |
| "The tolling slice is contractual" | Partition, S4. |
| "Base already has leases/fencing" | Probably [G]; plumbing, not pitch. |
| "Strawman baseline" | `reasonable` does per-claim kWh checks; the live demo uses it only; ablations. **And it is not a strawman on S1: it keeps ~100% of its ENERGY promise. The only thing it loses is the capacity hold.** [FINDING-6] |
| "The Notice came from fake house load" | `load_hi` is mode-aware; `assume_house_when_grid_unknown` off by default; S2's Notice cause names N−1/reachability. |
| "Zero by timidity" | Held-back % next to every zero; oracle ceiling; where we lose. |
| "Floor math ignores the house" | Mode-aware drain; ISLANDED excluded from I2 by design. |
| "Leases burn 4G" | Piggybacked = 0 extra; standalone ≈ 7% (chart). |
| "Feeder cap isn't physics" | Called a proxy; S1 loosens it so it can't bind. |
| "Priority is who paid" | Ladder is config; §18 Q1. |
| "Two regions fail" | Silent breaches appear, attributable (I4). |

### B. Metrics
Planted-bug controls fail the build on dead zeros · notice-spam exposed by held-back %, downgrades, flaps · I6 from a cold run · capacity availability computed on truth.

### C. Demo
Local assets · power plan · 1280×720 · safe keys + `R` · pre-computed forks · versioned assets · backup video by H40 · static batch PNGs in slides.

### D. Honesty
Base internals labelled [G]. If code written in advance is banned, bring data + docs only.

## 17. Assumptions & sources

| Parameter | Default | Range | Basis |
|---|---|---|---|
| Unit energy | 39.2 / 78.4 kWh (70/30 mix) | — | [S] Base Core launch; mix [A] |
| Unit power | 14.95 kW avg (11.5 per 39.2 kWh) | 8–15 per unit | [A] |
| Backup floor | 20% | 10–50% | [A]; analog Power Partner 20% [S] |
| Start SoC (S1, 15:00) | 80% | 50–100% (sweep) | [A] |
| Co-op shave window | 15:30–18:30 | 1–4 h (sweep) | [A]; multi-hour 4CP-day shaving |
| Aux / standby | 50 W | 20–150 W | [A] |
| Home load p95 (evening) | 4 kW | 2–7 kW | [A] |
| SAFE_HOLD behaviour | IDLE | IDLE / SELF_CONSUME | [G] |
| assume_house_when_grid_unknown | false | true/false | [A] sweep |
| Outage duration for N−1 kWh | 0.5 h | 0.25–2 h | [A] |
| Conversion efficiency η | 1.0 (Must) | 0.9–1.0 (Should) | [A] |
| Telemetry cadence | 2 s | 2–60 s | [S] |
| Tolling slice | ~1.5 h | — | [S] APPA |
| ADER limits | 500 MW energy; 100 MW ECRS+Non-Spin; ≤90%/QSE; ALR: single load zone, same LSE + DSP | — | [S] CRA report / ADER docs |
| RTC+B go-live | 2025-12-05 | — | [S] |
| 2026-07-22 records | load ~91.3 GW (EIA hourly 91.1 GW, 6pm hour) · net load 75,733 MW ~8pm · battery discharge 11,980 MW | — | [S] Grid Status, EIA; verify against pulled data |
| Lease | 60 s, piggybacked | 10–900 s | [A] |
| Msg sizes | 48 B / 32 B | — | [A] |
| Feeder export cap (proxy) | 80% in S1 | 30–100% | [A] |
| Tolerance | 5% | 2–10% | [A] |

**Sources:**
- Base Algorithms Engineer: https://zapply.jobs/jobs/a23d173f-c768-4ca9-893b-d5d2e23228d8/
- Base telemetry post: https://inside.basepowercompany.com/p/building-a-telemetry-stack-for-the
- Base Merge, Don't Queue: https://inside.basepowercompany.com/p/merge-dont-queue
- Utility Dive, GVEC: https://www.utilitydive.com/news/base-power-partnership-to-mitigate-price-spikes-load-peaks-for-south-texas/818221/
- ERCOT ADER: https://www.ercot.com/mktrules/pilots/ader
- ADER Phase 3: https://www.ercot.com/files/docs/2025/06/16/4.3-Aggregate-Distributed-Energy-Resource-ADER-Pilot-Project-Phase-3.pdf
- ERCOT DR deck + CRA: https://www.ercot.com/files/docs/2026/04/13/11.1-Strategic-Discussion-on-Resource-Adequacy-and-the-Role-of-Demand-Response.pdf
- Grid Status, July 2026 records: https://blog.gridstatus.io/ercot-record-july-2026/
- EIA, 91 GW on July 22: https://www.eia.gov/todayinenergy/detail.php?id=67906
- APPA, Austin Energy: https://www.publicpower.org/periodical/article/austin-energy-enters-agreement-with-base-power-deploy-40-mw-residential-battery-storage
- Power Partner Battery: https://austinenergy.com/energy-efficiency/rebates-incentives/residential/appliances-equipment/pp-battery
- ERCOT API limits: https://developer.ercot.com/applications/pubapi/known-limits/
- RTC+B: https://www.ercot.com/news/release/12052025-ercot-goes-live

---

## 18. Questions only Base can answer

1. **The same fleet serves ADER and the co-op's peak/transmission goal.** When both want the same evening's kWh, who wins, and is that decided in code or by contract?
2. Is a utility tolling slice (Austin Energy) a fixed device partition or a floating kW amount?
3. How stale does telemetry really get, and what happens to a quiet battery?
4. Grid up, command link lost: does a device idle or self-consume?
5. Which rule, if broken, pages someone?

The answers set the ladder, partitions, `safe_hold_behavior`, scenario names and slide 4.

---

## 19. Pre-mortem

| Failure | Prevention |
|---|---|
| S1 not scarce → thesis is only narration | D1 test from H0 (failing first); binding reason in UI; sizing done in prep |
| Must scope is a quarter's work | 1k devices, pre-computed forks, discharge-only, deployments Should; H8 checkpoint |
| Lane B implements admission three ways | §6.3 formulas with units; D1 + I8 + I9 known answers first |
| Contract churn | Freeze at H2 + hash test |
| Dead zero | Planted-bug controls fail the build |
| Product/physics flattening spotted | ENERGY/CAPACITY holds, per-device cap, mode-aware drain; RT1 items 2 + 4 |
| Strawman accusation | `reasonable` with per-claim kWh; oracle; where we lose; its S1 ENERGY promise_kept is ~100% |
| Pitch sounds like "don't optimize" | §0 layering, slide 4 |
| Demo breaks | Pre-computed forks, local assets, Reset, video by H40 |
| Exhaustion | Two sleep blocks |
