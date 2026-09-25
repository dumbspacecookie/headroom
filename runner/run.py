"""The tick loop. SPEC §6.1.

Simplifications in the Must scope, stated rather than assumed:
  * **Control tick == physics tick == 60 s.** §6.1 specifies 10 s control / 2 s physics. 390 ticks
    over the evening is one frame per second of a 60x demo, and nothing in S1-S3 resolves below a
    minute. Recorded so nobody reads a 2-second guarantee out of a 60-second run.
  * **Comms are perfect except for scripted region outages.** The Markov link model (§6.6) is not
    here yet. S1 needs no faults and S2's fault is scripted anyway, precisely so the scenario is
    not at the mercy of an RNG.
  * **Lease expiry / SAFE_HOLD IS implemented** (SPEC §6.6), as of FINDING-24/25. This bullet used
    to read: *"a dark region's devices simply hold their last setpoint. That is the CONSERVATIVE
    direction for `headroom` (it keeps delivering) and the generous one for the metric, so it
    cannot flatter us."* **Both halves of that were false**, and it sat here asserting a safety
    property the code did not have. Holding forever meant the dark region's share went out TWICE
    (the allocator re-spread the full booking over only the devices it could still reach), and
    the surplus drained devices onto their floor where `aux` ate through the homeowner's reserve:
    **96 of 1,000 seeded evenings**. A comment is not a control.

Reconcile: admission is recomputed every bucket for not-yet-started buckets. A drop produces a
Notice with its lead time. That is Beat B of the demo and the reason S2 exists.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace

import numpy as np

from config import DEFAULTS, Config
from contracts.types import DeviceMode, Product, Telemetry
from control.admission import Admission, bucket_ends
from control.allocator import allocate
from control.baselines import Reasonable
from control.deliverability import FleetArrays, in_flight_kw
from control.estimator import estimate_device, scope_belief
from control.ledger import Ledger
from control.oracle import oracle_view
from metrics.score import Scoreboard, capacity_deliverable, energy_breach
from scenarios.s1 import build_s1
from sim.fleet import build_fleet, step
from sim.link import LinkModel
from sim.topology import build_topology

TICK_S = 60.0
CONTROLLERS = ("headroom", "reasonable", "oracle")

# RATIONALE.md s6-8. `reasonable` never nets out what it already admitted, and that is public
# prior art (Sunverge's patent, ERCOT NPRR 1186), so beating it mostly measures the standard
# part. These run the SAME ledger, re-admission and Notices as headroom with parts removed:
#   reasonable_plus_dN  standard practice: point SoC + timeout exclusion + cumulative ledger +
#                       energy behind capacity + re-admission, a flat N% de-rate, NO band, NO N-1
#   headroom_no_band / headroom_no_n1   one part off at a time
VARIANTS: dict[str, dict] = {
    "reasonable_plus_d0": {"band": False, "haircut": "independent", "deliverability": "off",
                           "flat_derate_frac": 0.00},
    "reasonable_plus_d5": {"band": False, "haircut": "independent", "deliverability": "off",
                           "flat_derate_frac": 0.05},
    "reasonable_plus_d10": {"band": False, "haircut": "independent", "deliverability": "off",
                            "flat_derate_frac": 0.10},
    "reasonable_plus_d12": {"band": False, "haircut": "independent", "deliverability": "off",
                            "flat_derate_frac": 0.12},
    "reasonable_plus_d15": {"band": False, "haircut": "independent", "deliverability": "off",
                            "flat_derate_frac": 0.15},
    "reasonable_plus_d20": {"band": False, "haircut": "independent", "deliverability": "off",
                            "flat_derate_frac": 0.20},
    "headroom_no_band": {"band": False},
    "headroom_eps_only": {"band": False, "point_eps": True},
    "headroom_no_n1": {"haircut": "independent", "deliverability": "off"},
    # Stage 1's aggregate N-1 reserve kept, stage 2's per-device check off: what stage 2 alone buys.
    "headroom_no_stage2": {"deliverability": "off"},
    # Keep counting a quiet device, at the band's widening discount, for 5 or 15 minutes instead
    # of dropping it after 10 s (reach_k x t_tel). Only meaningful in the `flaky` world.
    "headroom_keep5m": {"reach_k": 150.0},
    "headroom_keep15m": {"reach_k": 450.0},
}


def _hhmm(ts: float) -> str:
    """Seconds-from-midnight CT as HH:MM. Notices are read aloud; 20.33h is not a time."""
    return f"{int(ts // 3600) % 24:02d}:{int(ts % 3600) // 60:02d}"


@dataclass(frozen=True)
class RegionOutage:
    """A scripted fault. `kind` "comms" = links down, grid up (S2). "grid" = ISLANDED (S4)."""
    region_id: str
    start_ts: float
    end_ts: float
    kind: str = "comms"
    # Empty = the whole region. Non-empty = exactly these devices, wherever they sit; then
    # `region_id` is only a label. Frozenset so the tick loop's membership test stays cheap.
    device_ids: frozenset[str] = frozenset()


@dataclass
class RunResult:
    scenario: str
    seed: int
    controller: str
    frames: list[dict]
    events: list[dict]
    metrics: dict
    admissions: list[Admission] = field(default_factory=list)

    @property
    def events_sha256(self) -> str:
        return self.metrics["events_sha256"]


def _sha(events: list[dict]) -> str:
    payload = json.dumps([{k: e[k] for k in ("ts", "kind", "text")} for e in events],
                         sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def run_scenario(controller: str = "headroom", seed: int = 42,
                 faults: tuple[RegionOutage, ...] = (), cfg: Config = DEFAULTS,
                 scenario: str = "S1", oracle_scale: float = 1.0,
                 flaky: bool = False) -> RunResult:
    if controller in VARIANTS:
        # Same code path as headroom, different knobs. Named in `metrics["controller"]` so a
        # variant can never be read back as headroom.
        cfg = replace(cfg, **VARIANTS[controller])
    elif controller not in CONTROLLERS:
        # An unknown name used to fall through to headroom's branch, because the only test was
        # `!= "reasonable"`. `tests/determinism` had been passing "S1" - a SCENARIO - into this
        # argument for two days and asserting the determinism of a controller that does not
        # exist. A typo that silently means "the good one" is how a fake result gets published.
        raise KeyError(f"unknown controller {controller!r}; have {CONTROLLERS}")
    t_wall = time.perf_counter()
    # The oracle holds no N-1 reserve (control/oracle.py, point 2). Its BELIEF already said so -
    # oracle_view builds it with haircut "independent" - but the ledger read `cfg`, which still
    # said "n_minus_1". Harmless while the reserve lived only in the belief. On 2026-09-24 the
    # per-device N-1 stage started reading `cfg.haircut`, ran inside the oracle, lowered the
    # ceiling, and the first sweep reported headroom holding back 0.96% instead of ~6%. The
    # unit test that claimed to guard this checked the config path, which the oracle never
    # takes. The rule is now stated once, here, where the oracle's ledger is built.
    # And (same day, later) the oracle got the per-device check WITHOUT a region removed: with
    # none at all its full-information bookings broke their own promises, the ceiling search
    # shrank everything uniformly, and headroom "beat the ceiling" on 87 of 1,000 evenings.
    # Better information, same rules, no reserve - that is what makes it a ceiling.
    ledger_cfg = (replace(cfg, haircut="independent", deliverability="n0")
                  if controller == "oracle" else cfg)
    s1 = build_s1(cfg=cfg)
    topo = build_topology(cfg)
    feeder_caps = {f.feeder_id: f.export_cap_kw for f in topo.feeders}

    a1 = topo.in_scope("aggregation:A1")
    idx = {d.device_id: i for i, d in enumerate(topo.devices)}
    a1_idx = np.array([idx[d.device_id] for d in a1])
    statics = {s.device_id: s for s in s1.statics}

    fleet = build_fleet(topo, cfg)
    # `flaky`: every device's link runs the GOOD/DEGRADED/DOWN chain of sim/link.py on top of the
    # scripted outages, so devices go quiet for minutes at a time. Off by default - S1, S2, the
    # bake and every recorded sweep are unchanged.
    links = LinkModel(len(a1), seed) if flaky else None
    last_tel: dict[str, Telemetry] = {}
    last_setpoint: dict[str, float] = {d.device_id: 0.0 for d in a1}
    # FINDING-25. `build_fleet` sets every lease to +inf, so leases NEVER expired and SAFE_HOLD
    # never happened: a dark device held a discharge setpoint for the rest of the evening,
    # drove itself onto its floor, and `aux` - which no guard clamps, by design - ate through
    # it for hours. 96 of 1,000 seeded evenings crossed the homeowner's backup reserve.
    # SPEC 6.6: "last setpoint runs until the lease expires, then SAFE_HOLD", and renewal
    # "piggybacks on the telemetry ack" - so the lease is renewed at INGEST, on the devices we
    # can actually hear from, which is the only place we could renew it in a real fleet.
    lease_expiry: dict[str, float] = {d.device_id: s1.t0 + cfg.lease_s for d in a1}

    events: list[dict] = []
    frames: list[dict] = []
    sb = Scoreboard()
    admissions: list[Admission] = []
    noticed_claims: set[str] = set()
    late_noticed: set[str] = set()      # one late Notice per claim, not one per tick

    def dark(d, t: float) -> bool:
        # A fault names a region, or (scattered/fragmented chaos, RATIONALE s8 item 2) an explicit
        # device set that ignores region boundaries - same darkness, different shape.
        return any(f.kind == "comms" and f.start_ts <= t < f.end_ts
                   and (d.device_id in f.device_ids if f.device_ids else f.region_id == d.region_id)
                   for f in faults)

    t = s1.t0
    first = True
    while t < s1.horizon_ts:
        t += TICK_S
        at_bucket = abs((t - s1.t0) % cfg.bucket_s) < 1e-6

        # ---- 1. ingest (perfect comms except scripted outages)
        up = links.step(TICK_S) if links is not None else None
        heard_now: set[str] = set()
        for j, d in enumerate(a1):
            if dark(d, t) or (up is not None and not up[j]):
                continue
            heard_now.add(d.device_id)
            i = idx[d.device_id]
            last_tel[d.device_id] = Telemetry(
                device_id=d.device_id, seq=int(t), device_ts=t,
                soc_kwh=float(fleet.soc_kwh[i]), p_kw=float(fleet.p_actual_kw[i]),
                home_load_kw=0.0, mode=DeviceMode.GRID,
                last_applied_epoch=0, last_applied_seq=int(t), recv_ts=t)
            lease_expiry[d.device_id] = t + cfg.lease_s      # piggybacked on the ack
            fleet.lease_expiry_ts[i] = t + cfg.lease_s

        # ---- 2. estimate
        # The real lease, not `t + lease_s` for everyone. SPEC 6.2 defines tau_cmd as stopping
        # AT lease expiry; feeding it an always-live lease meant a dark device's setpoint
        # uncertainty grew forever even though the device had long since stopped. On a
        # fault-free evening every device is heard every tick, so this is identical to the old
        # value and S1 does not move.
        estimates = [estimate_device(statics[d.device_id], last_tel.get(d.device_id), t,
                                     last_setpoint[d.device_id], lease_expiry[d.device_id], cfg)
                     for d in a1]
        if controller == "oracle":
            # The one controller allowed to read fleet truth (SPEC 7; control/oracle.py). It is
            # the CEILING that `capacity_held_back_vs_oracle_pct` is measured against, and its
            # right to be called one is asserted, not assumed - see `is_a_valid_ceiling`.
            estimates, belief = oracle_view(estimates, fleet, idx, feeder_caps,
                                            "aggregation:A1", cfg, oracle_scale, now=t,
                                            tick_s=TICK_S)
        else:
            belief = scope_belief(estimates, feeder_caps, "aggregation:A1", cfg)

        # ---- 3. admit / reconcile
        # `reasonable` admits ONCE and never revisits. SPEC §7: per-claim kWh checks, not
        # across time. Re-admission IS across-time reasoning, and handing it to the baseline
        # made the baseline out-perform the thing it exists to be a baseline for - S1's whole
        # comparison vanished (0 silent, 100% availability, both controllers). A baseline given
        # the feature under test is not a baseline.
        reconciles = controller != "reasonable"
        if first or (at_bucket and reconciles):
            if controller == "reasonable":
                ctl = Reasonable(belief, now=t, cfg=cfg)
                for c in sorted(s1.claims, key=lambda c: c.priority):
                    ctl.admit(c)
                fresh = ctl.admitted
            else:
                led = Ledger(belief, now=t, cfg=ledger_cfg, horizon_ts=s1.horizon_ts,
                             prior={a.claim_id: a for a in admissions})
                led.admit_all(s1.claims)
                fresh = led.admitted

            if first:
                admissions = fresh
                for a in admissions:
                    events.append({"ts": t, "kind": "admit", "controller": controller,
                                   "text": f"{a.claim_id} admitted {a.admitted_kw:,.0f} of "
                                           f"{a.requested_kw:,.0f} kW"
                                           + (f": binding {a.reason.value}" if not a.is_full else "")})
            else:
                # FINDING-14, found 2026-09-17, CLOSED by per-bucket admission the same day.
                # The first version locked a claim once its window had OPENED. SPEC 6.3 locks
                # started BUCKETS, not started claims - the buckets from 20:20 onward have not
                # started just because 20:00 has. Locking the whole claim meant that when R3
                # went dark mid-delivery the ledger could not downgrade the booking it could no
                # longer keep, so it under-delivered 94.7% of an admitted 928 kW for thirty
                # ticks and called every one of them SILENT. The controller whose entire pitch
                # is "we tell you early" sat on it. The interim fix let a started claim be
                # lowered wholesale and said so - honest, but the Notice was always LATE.
                #
                # Now the lock unit is the bucket. `Ledger.locks` freezes every bucket whose
                # start has passed, including the one being dispatched right now, and the pass
                # revises only the tail. So the Notice names buckets that have not happened yet
                # and carries a real lead time - which is what DEMO.md Beat B promises.

                # THE OTHER HALF OF THE RULE. FINDING-22, found by the 1000-seed sweep.
                # SPEC 6.3 says "a lowered NOT-YET-STARTED bucket -> Notice; a lowered STARTED
                # bucket -> LATE Notice". Per-bucket admission built the first clause and not
                # the second. On seeds 60 and 911 a region went dark at 20:55 and 21:00 -
                # inside the LAST bucket of the 20:00-21:00 award - so there was no free bucket
                # left to revise, no Notice fired at all, and the shortfall scored SILENT.
                # 7 silent buckets across 2 of 1000 evenings, in the controller whose entire
                # pitch is that it does not miss quietly. S1 and S2 could never have found it:
                # it needs a fault inside a claim's final bucket, which no hand-written
                # scenario contains because it is not a story anyone thinks to tell.
                #
                # You cannot un-sell the bucket you are delivering. You can still say so. The
                # counterfactual re-admits with NO locks purely to ask "knowing what I know
                # now, would I still have sold this bucket?" - and if not, says so late rather
                # than not at all. The booking is untouched; only the operator's knowledge
                # changes.
                #
                # Built LAZILY: it is read only for a claim mid-delivery that has not been
                # late-noticed yet, a handful of ticks in a 390-tick evening. Building it
                # unconditionally cost 27% of run time (0.59s -> 0.75s) and pushed the sweep
                # from 13 to ~17 minutes against a 20-minute bar - paying for an answer nobody
                # asked for, on the one lens whose job is to keep batch off the critical path.
                counterfactual = None
                if any(a.bucket_end_at(t + cfg.bucket_s / 2.0) is not None
                       and a.claim_id not in late_noticed for a in admissions):
                    counterfactual = Ledger(belief, now=t, cfg=ledger_cfg, horizon_ts=s1.horizon_ts,
                                            warm_kw={a.claim_id: a.admitted_kw for a in admissions})
                    counterfactual.admit_all(s1.claims)

                kept: list[Admission] = []
                for new in fresh:
                    old = next(a for a in admissions if a.claim_id == new.claim_id)
                    # Downgrade-only. SPEC 6.3 puts hysteresis on upgrades [+5% for 60 s] and
                    # the Must scope does not implement it; letting a booking climb back
                    # un-damped would flap it every bucket. A booking that has been cut stays
                    # cut for the evening, and that is the conservative direction.
                    #
                    # `replace(old, ...)` and not `new.admitted_kw = old.admitted_kw`: a
                    # decision is FOUR fields - the kW, the binding reason, and the two
                    # at-decision diagnostics - and carrying them one at a time drops one.
                    # It dropped one here. When R3 came back at 20:45 the ledger re-admitted
                    # ADER_ENERGY in full, the clamp held the kW at 124 and let the reason go
                    # to NONE, and the booking ended the evening 124 of 2,000 kW blaming
                    # nothing. That is the PARTIAL-with-reason-NONE the property lens calls
                    # "a UI that lies", arriving through the one path the property lens does
                    # not reach: reconcile, which it never runs.
                    if new.admitted_kw > old.admitted_kw:
                        new = replace(old, locked_kw=new.locked_kw)
                    kept.append(new)

                    # The bucket in delivery right now, if any: locked, so `cut` below can
                    # never contain it.
                    live_b = new.bucket_end_at(t + cfg.bucket_s / 2.0)
                    if (counterfactual is not None and live_b is not None
                            and new.claim_id not in late_noticed):
                        would_be = counterfactual.admitted_by_id(new.claim_id).kw_at(live_b)
                        sold = new.kw_at(live_b)
                        # Two questions, both must say "short" (2026-09-24, RATIONALE.md s6c).
                        # The counterfactual asks whether the rest of the window is still
                        # sellable - it is what caught FINDING-22's final-bucket outages, so it
                        # stays the trigger. But its flat figure is a window answer; quoting it as
                        # the bucket's made the Notice cry wolf on S2 ("209 kW deliverable" while
                        # the bucket was delivered in full). `in_flight_kw` asks the bucket's own
                        # question, and the Notice quotes that.
                        can_do = in_flight_kw(FleetArrays(belief), fresh, new.claim_id, t, cfg)
                        if (would_be < sold - 1.0
                                and can_do < (1.0 - cfg.tolerance_frac) * sold):
                            sb.notices += 1
                            late_noticed.add(new.claim_id)
                            noticed_claims.add(new.claim_id)
                            events.append({
                                "ts": t, "kind": "notice", "controller": controller,
                                "text": f"{new.claim_id} IN DELIVERY - late: the "
                                        f"{_hhmm(live_b - cfg.bucket_s)} bucket is sold at "
                                        f"{sold:,.0f} kW and only "
                                        f"{can_do:,.0f} kW is now deliverable "
                                        f"({counterfactual.admitted_by_id(new.claim_id).reason.value})"})

                    cut = [b for b in bucket_ends(new.start_ts, new.end_ts, cfg.bucket_s)
                           if new.kw_at(b) < old.kw_at(b) - 1.0]
                    if not cut:
                        continue
                    first_ts = min(cut)
                    lead = max(0.0, (first_ts - cfg.bucket_s) - t)   # to that bucket's START
                    sb.notices += 1
                    sb.downgrades += 1
                    sb.lead_times_s.append(lead)
                    noticed_claims.add(new.claim_id)
                    events.append({
                        "ts": t, "kind": "notice", "controller": controller,
                        "text": f"{new.claim_id} cut {old.kw_at(first_ts):,.0f} -> "
                                f"{new.admitted_kw:,.0f} kW ({new.reason.value}) from "
                                f"{_hhmm(first_ts - cfg.bucket_s)}, "
                                + (f"{lead/60:,.0f} min ahead of delivery" if lead > 0
                                   else "IN DELIVERY - late")})
                admissions = kept
            first = False

        # ---- 4. allocate
        # A dark device cannot be commanded AT ALL - not even to zero. SPEC §6.6: the last
        # setpoint runs until the lease expires. Zeroing it was a bug that made every comms
        # outage look like an instant total loss of that region's output, which flattered
        # nothing and broke S2.
        #
        # It is also still DELIVERING, and until FINDING-24 nothing subtracted that. The
        # allocator re-spread the full booked kW over only the devices we could still reach,
        # so the dark region's share went out twice - 120% of what was sold. `last_setpoint`
        # is only updated for reachable devices, so for a dark one it is exactly what that
        # device is still doing, and it is a number the CONTROLLER already owns: no truth is
        # read and `control/` still imports nothing from `sim/`.
        # Commands reach only devices whose link is up THIS tick. The estimator may still count a
        # quiet device as reachable - that is what a long `reach_k` means, and it is a statement
        # about what may be PROMISED - but a command cannot be delivered over a link that is down.
        # With the default reach (10 s) and a 60 s tick the two sets are identical, which is why
        # nothing recorded moves.
        reachable_ids = heard_now & {e.device_id for e in estimates if e.reachable}
        stranded_kw = sum(last_setpoint[d.device_id] for d in a1
                          if d.device_id not in reachable_ids and t < lease_expiry[d.device_id])
        alloc_ests = [e if e.device_id in reachable_ids else replace(e, reachable=False, kw_cap=0.0)
                      for e in estimates]
        plan = allocate(alloc_ests, admissions, t, feeder_caps, cfg,
                        earmark_capacity=(controller != "reasonable"),
                        stranded_kw=stranded_kw, tick_s=TICK_S)
        for d in a1:
            if d.device_id not in reachable_ids:
                continue
            kw = plan.setpoint_kw[d.device_id]
            fleet.setpoint_kw[idx[d.device_id]] = kw
            last_setpoint[d.device_id] = kw

        # ---- 5. physics
        before = fleet.soc_kwh.copy()
        step(fleet, now=t, dt_s=TICK_S, home_load_kw=np.zeros(fleet.n), cfg=cfg)
        below = fleet.soc_kwh[a1_idx] < fleet.floor_kwh[a1_idx] - 1e-9
        sb.floor_breach_dev_s += float(below.sum()) * TICK_S

        # ---- 6. score, on TRUTH
        delivered_kw = float(fleet.p_actual_kw[a1_idx].sum())
        booked_kw = sum(a.kw_at(t) for a in admissions if a.product is Product.ENERGY)
        if booked_kw > 0:
            dt_h = TICK_S / 3600.0
            sb.committed_kwh += booked_kw * dt_h
            sb.delivered_kwh += delivered_kw * dt_h
            # A Notice excuses only the claim it was about (2026-09-24, RATIONALE.md s6c). This
            # was `bool(noticed_claims)`: a Notice cutting the CAPACITY hold turned every later
            # ENERGY shortfall "late" too, which flatters any controller that issues Notices.
            energy_noticed = any(a.claim_id in noticed_claims for a in admissions
                                 if a.product is Product.ENERGY and a.kw_at(t) > 0)
            verdict = energy_breach(delivered_kw, booked_kw,
                                    had_prior_notice=energy_noticed, cfg=cfg)
            if verdict == "silent":
                sb.silent_energy_buckets += 1
            elif verdict == "late":
                sb.late_breach_buckets += 1

        cap_ok = None
        for a in admissions:
            if a.product is not Product.CAPACITY:
                continue
            hold_kw = a.kw_at(t)
            if hold_kw <= 0:
                continue
            reach = [e.reachable for e in estimates]
            ok = capacity_deliverable(
                fleet.soc_kwh[a1_idx], fleet.floor_kwh[a1_idx], fleet.p_max[a1_idx],
                fleet.p_actual_kw[a1_idx], reach, hold_kw, a.max_deploy_h)
            cap_ok = ok if cap_ok is None else (cap_ok and ok)
            sb.capacity_buckets += 1
            if ok:
                sb.capacity_ok_buckets += 1
            elif a.claim_id not in noticed_claims:
                sb.silent_capacity_buckets += 1

        owed = {}
        for a in admissions:
            if a.product is Product.ENERGY:
                # The PROFILE's remaining energy, not the headline kW times the hours left -
                # once a tail has been cut those two disagree, and the chart reads this one.
                owed[a.claim_id] = a.kwh_between(t, a.end_ts)
            elif t <= a.end_ts:
                owed[a.claim_id] = a.kw_at(max(t, a.start_ts + 1e-9)) * a.max_deploy_h

        margin = np.maximum(0.0, fleet.soc_kwh[a1_idx] - fleet.floor_kwh[a1_idx])
        frames.append({
            "ts": t, "controller": controller,
            "booked_kw": booked_kw, "delivered_truth_kw": delivered_kw,
            "belief_low_kw": belief.KW_kw - belief.kw_reserve_kw, "belief_high_kw": belief.KW_kw,
            "kw_room": belief.KW_kw - belief.kw_reserve_kw,
            "energy_left_low_kwh": belief.E0_kwh, "energy_left_truth_kwh": float(margin.sum()),
            "committed_future_kwh": owed, "oracle_kw_room": 0.0,
            "floor_breach_dev_s": sb.floor_breach_dev_s, "feeder_breach_kw_s": 0.0,
            "silent_breaches": sb.silent_breach_buckets, "notices": sb.notices,
            "region_freshness": {}, "log_seq_range": [0, len(events)],
            "cap_ok": cap_ok,
            # The booking register as it stands at THIS tick - what the demo page draws.
            # Derived from the live admissions rather than replayed by the baker, so the
            # register on the projector and the register the ledger holds cannot disagree.
            # Frames are not hashed (`_sha` reads events only), so this moves no signature.
            # `admitted_kw` is the DECISION - what the booking is worth, which is what Beat A
            # reads at 15:00, five hours before any of it is delivered. `kw_now` is what that
            # booking is delivering in THIS bucket, which is 0 until its window opens. They are
            # different questions and the register asks both, so they get different names.
            "bookings": [{"claim_id": a.claim_id, "product": a.product.value,
                          "requested_kw": a.requested_kw,
                          "admitted_kw": a.admitted_kw, "kw_now": a.kw_at(t),
                          "reason": a.reason.value, "is_full": a.is_full,
                          "start_ts": a.start_ts, "end_ts": a.end_ts,
                          "max_deploy_h": a.max_deploy_h} for a in admissions],
        })
        del before

    sha = _sha(events)
    return RunResult(scenario, seed, controller, frames, events,
                     sb.as_dict(scenario, seed, controller, "none", sha,
                                time.perf_counter() - t_wall),
                     admissions)
