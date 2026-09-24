# GATE.md — `make gate`

The mechanical half of integration. Run it before handing back any change, and before
every merge. It exists so the human's time goes to the one file that cannot be gated
(`control/ledger.py`) instead of to the things that can.

## The rule that makes it a gate and not a decoration

**Count the lenses. A lens that did not run is a FAILURE, not a pass.**

```
GATE: 8 lenses expected, 8 ran, 8 passed   -> PASS
GATE: 8 lenses expected, 7 ran, 7 passed   -> FAIL (lens 4 did not run)
```

A gate that silently skips a lens reports green while checking less than you think. Print the
count, assert it, and fail on a mismatch — this is the cheapest bug-class elimination in the build.

## The 8 lenses

| # | Lens | Fails when | Notes |
|---|---|---|---|
| 1 | `known_answer` | D1, I8, I9, the per-device kWh cap, the comms-down band cases | **D1 must FAIL at H2** and pass from H20. A D1 that passes at H2 is testing nothing. |
| 2 | `unit` | band monotonic, feeder clip, partition subtraction, hysteresis, epoch fencing, N−1 over reachable regions, **ENERGY-ends-before-CAPACITY-window** | the last one is FINDING-1 |
| 3 | `boundary` | AST: `control/` (minus `oracle.py`) imports anything from `sim/` | cheap, catches a whole class |
| 4 | `determinism` | same seed → different `events_sha256` | run twice in-process, once from a **fresh process** |
| 5 | `controls` | **any planted bug scores 0** | C1–C8. See below — this is the lens most likely to be quietly wrong |
| 6 | `chart fixture` | the hero chart's truth line is not inside the axes, or has zero y-range | FINDING-7: a chart rendered clean, styled and **blank**, and exited 0 |
| 7 | `contracts hash` | `contracts/` changed after `contracts-v1` | — |
| 8 | `fakes` | any `module.attr = …` assignment in tests without a fixture that restores it | a raw monkeypatch leaks for the whole session and poisons every later test |

## Lens 5 in detail — the controls

A planted bug that scores **0** means the metric cannot see the damage. That fails the build.

| Control | Plant | Must go non-zero |
|---|---|---|
| C1 | drop kWh-over-time | capacity silent breach |
| C2 | drop the CAPACITY kW hold | I8 / availability |
| C3 | drop the per-device kWh cap | floor clamp hits / under-delivery |
| C4 | drop N−1 | S2 silent breach |
| C5 | drop the drain term | I2 with the guard OFF |
| **C6** | set the ENERGY kWh term's upper bound to `end(W)` instead of `horizon_ts` | **`silent_capacity_buckets` > 0** on the S1-stress seed |
| **C7** | shift the hero chart's x data by one window length | lens 6 must fail |
| **C8** | make `Ledger.enforce_n1` a no-op | seed 259 silent AS buckets > 0 |

C6 and C7 are the two the practice build earned. **Neither may be cut** — they are the controls for
the two failures that actually happened, as opposed to the ones we imagined.

> `reasonable` must show non-zero `silent_breach_buckets` on S1 **and** S2. On S1 the non-zero part
> is **capacity**, not energy — its ENERGY promise is ~100%. A controls lens that only counts energy
> breaches scores `reasonable` at 0 on S1 and **fails the build on a correct implementation**
> (FINDING-5).

## Run it from a fresh clone at the checkpoints

`make gate` in your working tree is the per-cycle check. At **H20, H26 and H38**, run it from a
**fresh `git clone` of the pushed branch** into a clean directory. A tree you have been editing can
pass because of an untracked file, a stale `__pycache__`, or a fixture left behind. What ships is
what is committed, not what is on your disk.

```
git clone . /tmp/headroom-verify && cd /tmp/headroom-verify && make gate
```

Stale bytecode is a known way to get a false green — `find . -name __pycache__ -prune -exec rm -rf {} +`
before the checkpoint runs.

## What the gate does NOT check, and never will

- whether `ledger.py` implements the *right* arithmetic — the tests come from the same spec the
  code does, so they share its misreadings (FINDING-3)
- whether a number is wrong in a way no test encodes
- whether the UI reads clearly to someone who has never seen it
- whether the demo is persuasive

**Those four are ash's, at every integration.** The gate exists to make room for them, not to
replace them.

## Targets

```
make test-fast   lenses 1, 2, 3, 8 + 1-seed determinism   # every handback, every merge
make gate        all 8 lenses, counted                    # every integration
make gate-clean  all 8, from a fresh clone                # H20, H26, H38
make test        everything incl. property + metamorphic  # H20, H38
```
