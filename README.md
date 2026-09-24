# Headroom

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

1,000 seeded evenings, 1,000 devices, randomly timed comms outages. Held back is measured against a
**measured** oracle ceiling: the largest admission a perfect-information controller could promise
and keep.

| controller | held back (median, regional) | evenings with a silent miss: regional / scattered / fragmented | evenings below the 20% floor (regional) |
|---|---|---|---|
| **headroom** | 6.9% | 0 / 0 / 0 | 0 |
| headroom, stage 2 off (aggregate N-1 only) | 5.5% | 4 / 2 / 0 | 0 |
| headroom, no N-1 reserve at all | 0.9% | 2 / 7 / 0 | 0 |
| standard practice, flat 12% de-rate | 8.1% | 0 / 0 / 0 | 76 |
| standard practice, flat 15% de-rate | 11.1% | 0 / 0 / 0 | 15 |
| standard practice, flat 20% de-rate | 17.3% | 0 / 0 / 0 | 0 |
| first-draft baseline (`reasonable`) | −13.2% | 1000 / – / – | 0 |

What these numbers do and do not show is in `RATIONALE.md` §6c. In short:

- **Headroom had no silent miss and no floor breach on any of the 3,000 evenings** across the
  three fault shapes, holding back 6.9% of the ceiling. The cheapest flat
  de-rate that matches that record is 20%, which holds back 17.3%. A 12%
  de-rate avoids silent misses but leaves 76 regional evenings below the
  homeowner's floor.
- **The reserve is insurance, and it has a price.** Without any N-1 reserve the same ledger holds
  back only 0.9% and misses silently on 9 of 3,000 evenings.
  On a quiet evening the reserve costs 6.8% against 0.6%.
- **The per-device stage adds a little on top of the aggregate reserve:** switching it off
  leaves 6 silent evenings in 3,000 and saves about
  1.3 points of held-back energy.
- **The zero depends on outages looking like the ones simulated.** Every result above is from a
  simulator, with comms outages only.

## Known limits and open findings

- **The band's "trust old data less" half is untested.** The runner ticks every 60 s and a device
  is unreachable after 10 s, so telemetry is either fresh or gone. A latency and degradation model
  is specified (SPEC §6.6) and not built.
- **The fault model is comms outages only**, by region or scattered, discharge-only evenings, and
  lossless conversion (η = 1). No grid islanding, no charging, no home load in the ledger.
- **With one region already dark, the reserve still protects against losing a second.** Whether it
  should relax is a policy question (RATIONALE §2).
- **Two regions dark at once is beyond the reserve.** The N-1 check protects against losing one
  region; a second outage in the same evening can still leave a bucket in delivery short. It is
  announced late rather than missed silently (seed 76 in `tests/demo`), but it is a miss.
- **The primitives are not new.** Bands, reserves, leases and admission control are standard. The
  contribution is the composition, the Notice contract and the measurement. Prior art is surveyed
  in `RATIONALE.md` §3.

## Running it

Python 3.11.

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # or .venv/bin/pip on macOS/Linux

python run.py test-fast        # the quick lenses, under a minute
python run.py gate             # all 12 lenses, counted: a lens that did not run is a failure
python run.py gate-clean       # the same, from a fresh clone of HEAD
python run.py demo             # re-bake and open web/demo.html (static, no server)
python run.py batch 1000       # the seeded sweep against the oracle ceiling (~15 min, all cores)
python run.py compare 1000 [regional|scattered|fragmented]   # vs standard practice + ablations
```

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
