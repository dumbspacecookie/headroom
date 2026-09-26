# Headroom

[![gate](https://github.com/dumbspacecookie/headroom/actions/workflows/gate.yml/badge.svg)](https://github.com/dumbspacecookie/headroom/actions/workflows/gate.yml)

**Admission control for a home-battery fleet that is sold more than once.** An optimizer proposes
bookings; Headroom admits only what the fleet can still deliver if a region's telemetry goes dark,
and turns a future miss into an early Notice instead of a silent under-delivery.

This is a preliminary research prototype: one Python process, a simulated fleet of 1,000 devices,
and real ERCOT/EIA data for 2026-07-22. It is independent work built from public information. It is
not affiliated with Base Power or ERCOT and does not describe anyone's production system.

## The problem

The same residential batteries can be committed to a wholesale market (ERCOT's ADER pilot: energy
and ancillary services), to a co-op's peak shaving, and as a fixed slice to a utility. Those claims
can want the same evening's kWh. The controller decides what to promise from telemetry carried over
cellular backhaul, which is sometimes stale or missing.

A controller that treats the last reported state of charge as inventory, and checks each claim on
its own, can promise energy that will not exist when the claim comes due. Nobody finds out until it
is missed.

`RATIONALE.md` sets out the premises, the evidence for each, and what is already known in industry
and the literature. Read it before the code.

## What Headroom does

Admission runs in two stages every five minutes (`control/ledger.py`):

1. **The energy ledger (SPEC §6.3).** kW and kWh are tracked over time in 5-minute buckets.
   ENERGY bookings consume kWh; CAPACITY holds reserve kW in every bucket of their window, plus
   `kW × max_deploy_h` of energy. Earlier commitments visibly shrink later room. Promises are made
   from the pessimistic edge of a state-of-charge band.
2. **The per-device check (SPEC §6.3b, `control/deliverability.py`).** The fleet is projected
   forward the way the allocator will actually drain it, counting only kW that still has energy
   behind it. Every booking must be deliverable by the devices, and every CAPACITY hold must
   survive the loss of any one reachable region. If not, the most junior booking in the failing
   bucket yields, hours ahead.

When a later pass lowers a booking, the operator gets a **Notice** at least one bucket (5 minutes)
ahead. A bucket already in delivery is never rewritten; if it will fall short, it gets a *late*
Notice instead of none.

On the base scenario, the co-op's afternoon shave empties the small units. The energy arithmetic
alone would sell 928 kW of the evening wholesale award. The N-1 check sees at 15:00 that losing a
region at 20:20 would leave the ancillary-service hold short, and sells 763 kW instead.

## Results

1,000 seeded evenings, 1,000 devices, randomly timed comms outages that start at a random minute.
Held back is measured against a **measured** oracle ceiling: the largest admission a
perfect-information controller could promise and keep. A miss is **silent** if delivery fell
short with no Notice, and **late** if it fell short after one. Every number here is from the
harness as corrected on 2026-09-25 (`PRACTICE-NOTES.md` FINDING-26): until then the fault clock
disagreed with the window clock, and the published figures were measuring that.

| controller | held back (median, regional) | evenings with a silent miss: regional / scattered / fragmented | evenings with a late miss (regional) | evenings below the 20% floor (regional) |
|---|---|---|---|---|
| **headroom** | 6.8% | 1 / 3 / 0 | 7 | 0 |
| headroom, stage 2 off (aggregate N-1 only) | 5.4% | 56 / 38 / 0 | 69 | 0 |
| headroom, no N-1 reserve at all | 0.6% | 61 / 65 / 2 | 103 | 0 |
| standard practice, flat 12% de-rate | 7.4% | 4 / 3 / 0 | 6 | 80 |
| standard practice, flat 15% de-rate | 11.1% | 4 / 4 / 0 | 3 | 19 |
| standard practice, flat 20% de-rate | 17.3% | 3 / 3 / 0 | 1 | 0 |
| first-draft baseline (`reasonable`) | −13.1% | 1000 / – / – | 0 | 0 |

What these numbers do and do not show is in `RATIONALE.md` §6e. In short:

- **Headroom missed silently on 1 regional evening in 1,000 and never crossed the floor,**
  holding back 6.8% of the ceiling. That evening (seed 333) has three regions dark within 11
  minutes, which is beyond an N-1 reserve. Every flat de-rate that stays off the floor misses
  silently at least as often: flat 20% on 3 evenings, holding back 17.3%.
- **Headroom's late misses are two regions dark at once.** On 7 regional evenings a bucket ran
  short after a Notice had gone out, and on every one two or three regions were dark at the same
  moment, which is beyond an N-1 reserve. The energy is small (0.29 kWh a night on average), and
  flat 15-20% has fewer (3 and 1). Until 2026-09-25 this read 36: the other 29 were a rounding
  artefact (`PRACTICE-NOTES.md` FINDING-27).
- **The reserve covers the minutes before anyone notices.** The controller re-plans every five
  minutes, so an outage goes unseen for up to four. Without the reserve the same ledger holds
  back 0.6%, misses silently on 61 evenings and leaves 10 kWh a night undelivered. Most of that
  is the per-device stage: switching it off alone leaves 56 silent evenings.
- **Flaky links, too.** With each device's link also dropping on its own (mean 30 min down),
  headroom misses silently on 1 evening in 1,000 at 6.8%. Keeping a quiet device counted for
  5 / 15 minutes instead of dropping it after 10 s misses silently on 3 / 10, so a plain yes/no
  timeout is the better rule.
- **A P90 reserve is not worth it here.** On 10,000 evenings at the assumed outage rate, holding
  only as many regions as keep the chance of a new outage under 10% saves 0.2 to 0.6 points, and
  misses silently on 29 to 36 evenings against headroom's 15, late on 136 to 204 against 100,
  with two to four times the undelivered energy (`RATIONALE.md` §6e).
- **The numbers depend on outages looking like the ones simulated.** Every result above is from
  a simulator, with comms outages only.

## Known limits and open findings

- **The band's "trust old data less" half is only half tested.** With per-device dropouts wired
  in, keeping a quiet device counted at the widened discount is *worse* than dropping it after
  10 s (above). Telemetry that arrives late but can still be commanded is the case the widening is
  for, and the latency model for it is specified (SPEC §6.6) and not built.
- **The fault model is comms outages only**, by region or scattered, discharge-only evenings, and
  lossless conversion (η = 1). No grid islanding, no charging, no home load in the ledger.
- **With one region already dark, the reserve still protects against losing a second.** Whether it
  should relax is a policy question (RATIONALE §2).
- **Outages are noticed at the next five-minute re-plan, not when they start.** The reserve is
  what covers the gap. A controller that re-plans the moment a region goes quiet would need less
  of it; it is not built.
- **Two regions dark at once is beyond the reserve.** The N-1 check protects against losing one
  region; a second outage in the same evening can still leave a bucket in delivery short. It is
  announced late rather than missed silently (seed 76 in `tests/demo`), but it is a miss.
- **The primitives are not new.** Bands, reserves, leases and admission control are standard. The
  contribution is the composition, the Notice contract and the measurement. Prior art is surveyed
  in `RATIONALE.md` §3.

## Running it

Python 3.11 and the exact versions in `requirements.txt`. The gate byte-compares committed
results, and a different interpreter or library moves the floats: Python 3.14, or numpy 2.5
with pandas 3.0, fails the demo lens on an unchanged tree.

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # or .venv/bin/pip on macOS/Linux

python run.py test-fast        # the quick lenses, under a minute
python run.py gate             # all 12 lenses, counted: a lens that did not run is a failure
python run.py gate-clean       # the same, from a fresh clone of HEAD
python run.py demo             # re-bake and open web/demo.html (static, no server)
python run.py batch 1000       # the seeded sweep against the oracle ceiling (~15 min, all cores)
python run.py compare 1000 [regional|scattered|fragmented]   # vs standard practice + ablations
python run.py explore          # bake + open web/explore.html: every result, and 46 evenings to replay
python run.py show 333         # any evening, minute by minute, in the terminal (or `show s2`)
```

**The explorer** (`web/explore.html`, static like the demo page) has three screens. **Arena**
plots every rule as held back against missed promises, one world at a time. **Replay** plays an
evening minute by minute over the five regions: what went dark, when the controller saw it,
each Notice, and every miss. **Quests** is what is done and what is next. Keys: `1 2 3` switch
screens, `space` plays, `J` jumps to the next miss, `?` lists the rest. A link like
`web/explore.html#replay/333/headroom/16:10/play` opens a moment and plays it.

`python run.py data` re-checks the committed ERCOT/EIA data against its sources. It needs an EIA
API key in `$EIA_API_KEY`, or a `KEY=value` file named by `$HEADROOM_KEYS_FILE`. Nothing else
needs a key.

## Layout

```
contracts/     frozen message and record types (hash-checked)
sim/           fleet physics, topology, seeded chaos; the truth the controller never reads
control/       estimator (SoC band), ledger (stage 1), deliverability (stage 2), allocator,
               baselines, oracle
runner/        the tick loop, the 1,000-seed batch, the baseline comparison
metrics/       scoring, on truth
scenarios/     S1 (the co-op evening) and its worked sizing
web/, prep/    the static demo page, charts, data pulls
tests/         12 lenses: known answers, unit, boundary, determinism, planted-bug controls,
               chart fixtures, contract hash, fakes, property, metamorphic, perf, demo
data/raw/      ERCOT and EIA data for 2026-07-22 and two reference days
```

## Documents

| | |
|---|---|
| `RATIONALE.md` | Why this approach: premises, prior art, alternatives, results, what would prove it wrong |
| `SPEC.md` | The design, versioned, with every change announced |
| `ASSUMPTIONS.md` | Every tunable assumption, its default, its range and what it moves |
| `DECISIONS.md` | One line per decision or trap, dated |
| `PRACTICE-NOTES.md` | The numbered findings, and the traps behind them |
| `docs/GATE.md` | The test gate and the planted-bug controls |
| `DEMO.md` | Historical: the pitch script from the hackathon prep sprint |

## Data

ERCOT and EIA-930 public data, pulled once and committed as parquet. Nothing is written unless its
controls pass: a known answer (demand at 18:00 CT on 2026-07-22 is 91,075 MW), and on every window a
second source (EIA demand against ERCOT's own load archive, hour by hour, within 2%). Sources are
listed in SPEC §17.

## License

MIT for the code (`LICENSE`). The ERCOT and EIA data in `data/raw/` are public data from those
agencies and remain under their own terms.
