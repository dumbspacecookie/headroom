# ASSUMPTIONS

Every **[A]** knob in SPEC v0.4, with its default, its range, and **what moves if you change it**.
Companion to SPEC §17 (which mixes [S] sourced facts with [A] assumptions); this file is the [A] half only.
**[G]** = guess about Base internals, listed here because it is also a knob.

Rules:
- Any number the demo shows on screen traces to a row here or to an [S] row in SPEC §17.
- **Nothing in this file gets retuned at the event.** If a knob has to move to make a result appear,
  that is the result. (SPEC §15: "Do not retune at the event.")
- Sourced values ([S]) are **not** in this file: unit energy 39.2/78.4 kWh, telemetry cadence 2 s,
  tolling slice ~1.5 h, ADER limits, RTC+B go-live, the 2026-07-22 records. See SPEC §17.

Sweep column: **UI** = exposed as a control in the demo · **batch** = swept in the batch runs only ·
**fixed** = a modelling choice, changed only by editing config.

---

## 1. Device and fleet physics

| Knob | Default | Range | Sweep | What moves |
|---|---|---|---|---|
| Unit mix (39.2 / 78.4 kWh) | 70 / 30 | — | fixed | Fleet kWh, so the whole S1 scarcity scale. |
| Unit power per 39.2 kWh unit | 11.5 kW | 8–15 kW | batch | `kw_cap`, so whether `kw_room` ever binds instead of `kwh_slack`. S1 is sized so it does not. |
| Backup floor | 20% | 10–50% | UI | `kwh_above_floor_low`. Raising it shrinks E0 and tightens S1; the analog is Austin Energy Power Partner's 20% [S]. |
| Aux / standby draw | 50 W | 20–150 W | batch | `drain_hi` and `soc_low` decay while a device is dark. At 400 devices, 50 W ≈ 20 kW fleet-wide. |
| Home load p95, evening | 4 kW | 2–7 kW | batch | The `load_hi` term — only where the load term is on (§6.2). This is the number the "your Notice came from fake house load" attack aims at. |
| Conversion efficiency η | 1.0 | 0.9–1.0 | fixed (Must) | η < 1 is a Should. Everything in the Must scope is lossless and says so. |
| `safe_hold_behavior` **[G]** | IDLE | IDLE / SELF_CONSUME | UI | Whether a lease-expired device drains at aux or at house load. Drives §18 Q4. |

## 2. Estimator (SPEC §6.2)

| Knob | Default | Range | Sweep | What moves |
|---|---|---|---|---|
| ε (band padding) | 1% of `e_max` | 0.5–3% | batch | Widens `soc_low`/`soc_high` symmetrically. Promises use `soc_low` only, so ε is a pure haircut. |
| Reachability factor `k` (× `T_tel`) | 5 | 3–15 | batch | How fast a quiet device leaves the reachable set — i.e. how quickly a comms outage becomes visible as lost kW. |
| `assume_house_when_grid_unknown` | **false** | true / false | UI | When true, a comms-dark device is assumed to be serving the house. Off by default precisely so S1's and S2's Notices cannot be blamed on invented load. |
| Haircut mode | `n_minus_1` | `independent` / `n_minus_1` / `both` | UI | `n_minus_1` reserves the largest **reachable** region. Dark regions already contribute 0 kW / 0 kWh, so nothing is double-subtracted. |
| N−1 outage duration (for kWh reserve) | 0.5 h | 0.25–2 h | UI | `kwh_reserve = kw_reserve × outage_h`. This is the single knob that most directly buys or spends S1's slack. |

## 3. Ledger and admission (SPEC §6.3)

| Knob | Default | Range | Sweep | What moves |
|---|---|---|---|---|
| Bucket size | 5 min (`Δh` = 1/12) | 1–15 min | fixed | Resolution of `kw_room` and of lead time. Structural; not a tuning dial. |
| Priority ladder **[A, §18 Q1]** | TOLL › ADER_AS › COOP_PEAK › ADER_ENERGY › STORM_PRECHARGE › ARBITRAGE | any order | UI | Who gets cut in S1. **This is config, not a claim** — the honest answer to "priority is just who paid" is that Base's answer replaces it (§18 Q1). |
| Upgrade hysteresis | +5% held 60 s | 0–10% / 0–300 s | batch | Trades `flap_count` against how fast freed kWh is re-admitted. |
| Delivery tolerance | 5% | 2–10% | batch | Threshold in the silent-breach definition (§10). Loosening it hides breaches in **both** controllers, so it is reported next to every breach count. |
| Feeder export cap (proxy) | 80% of feeder kW, S1 | 30–100% | UI | Called a proxy for distribution limits, not power flow. **S1 sets it loose on purpose** so the binding reason is `kwh_slack`, never `feeder`. |

## 4. Allocator and control loop (SPEC §6.1, §6.4)

| Knob | Default | Range | Sweep | What moves |
|---|---|---|---|---|
| `dt_ctrl` | 10 s | 5–60 s | fixed | Control tick. |
| Physics `dt` | 2 s demo / 10 s batch | — | fixed | Batch runs coarser for speed; metrics are computed on truth either way. |
| `δ_kw` (send threshold) | 0.5 kW | 0.1–2 kW | batch | Setpoint churn, so `msgs_per_device_day`. |
| PI gains | Kp 0.5, Ki 0.05 | — | fixed | Closed loop on `delivered_est`, clamped by the per-device caps. Never allowed to exceed them. |
| Overprovision | reserve-backed only | — | fixed | **No ad-hoc percentage.** Any margin is the N−1 reserve, already subtracted in the ledger. |

## 5. Network and leases (SPEC §6.6)

| Knob | Default | Range | Sweep | What moves |
|---|---|---|---|---|
| Link chain rates | G→D 0.5/h · D→G 6/h · D→DOWN 0.3/h · DOWN→G 2/h | — | batch | Background flakiness. S1/S2 faults are *scripted on top* of this, so the scenario is not at the mercy of the RNG. |
| Latency (median) | 300 ms good / 3 s degraded | — | batch | Telemetry age, so band width. |
| Loss / duplicates / reorder / skew | 0.5% / 15% · 0.5% · 5 s · ±30 s | — | batch | Skew is a Should. I5 (dedupe) is asserted at scale in S5. |
| Buffer flush rate `B` | 50 msgs/s | 10–200 | fixed | How long a recovered region takes to catch up, so recovery time in S2. |
| Lease TTL | 60 s, piggybacked | 10–900 s | UI | Time from comms loss to SAFE_HOLD. Short = safe and chatty, long = more exposure. |
| Message sizes | 48 B telemetry / 32 B ack | — | fixed | Only feeds `bytes_per_device_day` and the lease-cost chart. |
| Lease delivery | piggyback on telemetry ack | piggyback / standalone 20 s | UI | Piggyback = **0 extra messages**; standalone adds 4,320/device/day ≈ 7% of the 2 s telemetry stream. Both are on the chart. |

## 6. Scenario S1 (SPEC §8.1, sized in `scenarios/S1_sizing.md`)

| Knob | Default | Range | Sweep | What moves |
|---|---|---|---|---|
| Devices in aggregation T1 | 400 | — | fixed | The kWh pool that both claims draw on. Scoped small on purpose: ADER awards sit in one aggregation, and the kWh can exist in the fleet but not in **that** aggregation [S]. |
| Regions | 5 | — | fixed | Sets the size of the N−1 reserve (largest reachable region). |
| Evening window | 15:00–21:30 CT (6.5 h) | — | fixed | Spans the 2026-07-22 load peak (HE 18:00) and net-load/battery peak (HE 21:00), both from our own pulled data. |
| **Start SoC at 15:00** | **80%** | **50–100%** | **UI (headline)** | The demo's main slider. See the sweep below. |
| **Co-op shave duration** | **3 h** (15:30–18:30) | **1–4 h** | **UI (headline)** | The other half of the knife edge. |
| Crash duration `D` (S3) | 90 s | 30–300 s | batch | `crash_recovery_s`. |
| Comms outage (S2) | one A1 region, 20:15–20:45, grid up | — | fixed | Scripted so the N−1 cover and the Notice are reproducible. |

### The two knobs that decide the headline — state this out loud

From `scenarios/S1_sizing.md` (regenerate with `prep/size_s1.py`, do not hand-edit):

| start SoC \ shave | 1 h | 2 h | 3 h | 4 h |
|---|---|---|---|---|
| 50% | partial | zero | zero | zero |
| 60% | full | zero | zero | zero |
| 70% | full | partial | zero | zero |
| 80% | full | full | **partial (base case, 928 kW, cut 1,072 kWh)** | zero |
| 90% | full | full | full | zero |
| 100% | full | full | full | **full** |

> **Table change, 2026-09-16 (announced):** the **100% / 4 h** cell moved from *partial* to *full*.
> Cause: `prep/size_s1.py` charged its aux drain over the full 6.5 h window, but §6.3 evaluates
> `drain_hi(now → t)` at the time the kWh term binds (21:00, 6.0 h). The 10 kWh difference
> exceeded D1's ±1 kWh bar. Fixed and regenerated; the base case moved 918 → **928 kW** and the cut
> 1,082 → **1,072 kWh**. This is a defect fix, not a knob moved to make a result appear —
> the practice build (full 5-minute bucket sweep, 50 devices) independently produces 116.0 kW and
> a 134.0 kWh cut, which is exactly ×8 the corrected figures. See `PRACTICE-NOTES.md` FINDING-9.

**The PARTIAL band is one cell wide at the default 3 h shave.** D1's formula
(`cut = clip(co-op kWh − slack without co-op, 0, requested)`) holds on *every* cell, including the
zeros and the fulls — that is what the known-answer test asserts. What is narrow is the
*presentational* band, not the invariant.

**Demo consequence (SPEC §9):** show the slider sweeping full → partial → zero and say the
transition is sharp. Do not present the 80% / 3 h cell as "the" result.

---

## 7. Review checklist — run before the event

- [ ] Every number on a slide traces to a row here or an [S] row in SPEC §17.
- [ ] The UI shows the current value of every **UI** knob next to the result it produced.
- [ ] `assume_house_when_grid_unknown` is **off** in the recorded demo and in the backup video.
- [ ] The feeder cap in S1 is loose enough that the binding reason reads `kwh_slack`, and the UI
      displays the binding reason, not just the number.
- [ ] Held-back % vs oracle appears next to every zero (SPEC §10), so "zero by timidity" has an answer.
- [ ] RT1 (H20) is given this file, and item 2 of its prompt — "is S1's scarcity real or an artifact
      of a chosen parameter" — is answered with the sweep table above, not with the base case.
