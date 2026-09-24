"""Turning admissions into per-device setpoints. SPEC §6.4.

Order matters and is not negotiable: **CAPACITY is earmarked before ENERGY is filled.** A hold
that has been sold is not spare capacity that ENERGY may borrow against and hand back later - by
the time the hold is called, the energy is gone. That ordering is invariant I8.

The per-device kWh cap (I9) is the other half: a device with 0.4 kWh above its floor and an hour
of window left may be given at most 0.4 kW, no matter how much aggregate room the ledger says
exists. Aggregate room is not a promise any individual battery can keep.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import DEFAULTS, Config
from contracts.types import Estimate, Product
from control.admission import Admission


@dataclass
class Plan:
    setpoint_kw: dict[str, float]
    earmark_kw: dict[str, float]
    earmark_kwh: dict[str, float]
    shortfall_kw: float          # asked for by admitted claims, not placeable on any device


def allocate(estimates: list[Estimate], admissions: list[Admission], bucket_ts: float,
             feeder_caps: dict[str, float], cfg: Config = DEFAULTS,
             earmark_capacity: bool = True, stranded_kw: float = 0.0,
             tick_s: float = 60.0) -> Plan:
    live = [e for e in estimates if e.reachable and e.kw_cap > 0]
    setpoint = {e.device_id: 0.0 for e in estimates}
    earmark_kw = {e.device_id: 0.0 for e in estimates}
    earmark_kwh = {e.device_id: 0.0 for e in estimates}

    # ---- 1. earmark CAPACITY holds, per device, before anything else touches the fleet.
    # `reasonable` does not do this (SPEC §7) - that omission is precisely what loses it the AS
    # hold on S1 while its ENERGY delivery stays ~perfect.
    for adm in (admissions if earmark_capacity else []):
        if adm.product is not Product.CAPACITY:
            continue
        # Per-bucket: `kw_at` answers "what is this booking worth in THIS bucket", and returns
        # 0 outside the window, so it is the window test as well as the value.
        hold_kw = adm.kw_at(bucket_ts)
        if hold_kw <= 0:
            continue
        need_kw = hold_kw
        need_kwh = hold_kw * adm.max_deploy_h
        # PROPORTIONAL, not greedy (2026-09-24, RATIONALE.md s6c). Every live device gives the
        # same SHARE of its free kW and of its free kWh. The greedy version took the whole
        # hold from the richest devices first - all of their kWh - so ENERGY then drained
        # every other device together, and late in the window the free energy sat on too few
        # devices to carry the booked kW: 343 of 400 empty at 21:00 with 75.8 kWh still free.
        # The ledger checks kW and kWh for the scope as a whole; the allocator has to keep them
        # on the SAME devices or the aggregate "yes" is not a promise any battery can keep.
        room_kw = {e.device_id: max(0.0, e.kw_cap - earmark_kw[e.device_id]) for e in live}
        room_kwh = {e.device_id: max(0.0, e.kwh_above_floor_low - earmark_kwh[e.device_id])
                    for e in live}
        tot_kw, tot_kwh = sum(room_kw.values()), sum(room_kwh.values())
        f_kw = min(1.0, need_kw / tot_kw) if tot_kw > 0 else 0.0
        f_kwh = min(1.0, need_kwh / tot_kwh) if tot_kwh > 0 else 0.0
        for e in live:
            earmark_kw[e.device_id] += f_kw * room_kw[e.device_id]
            earmark_kwh[e.device_id] += f_kwh * room_kwh[e.device_id]

    # ---- 2. fill ENERGY on what is left
    #
    # FINDING-24, closed 2026-09-17. `stranded_kw` is what devices we can no longer COMMAND are
    # still putting on the grid: a dark device cannot be told to stop, so it runs its last
    # setpoint until its lease lapses (SPEC 6.6). Without this term the allocator re-spread the
    # FULL booked kW across only the devices it could still reach, and the dark region's share
    # went out TWICE.
    #
    # Measured before the fix, seed 52 (`R2 18:15+30m`): delivery jumped 3,000 -> 3,600 kW at
    # 18:15 - exactly R2's 80/400 share, delivered twice. The scope put out 120% of what it
    # sold, drained faster than the ledger believed, parked devices on their floor, and `aux`
    # (which no guard clamps, by design) pushed them under it: floor breaches on 96 of 1,000
    # seeded evenings for `headroom` and 115 for `reasonable`.
    #
    # THE ESTIMATOR IS NOT WRONG AND IS NOT CHANGED. SPEC 6.2's "dark devices contribute 0 kW
    # and 0 kWh" is about CAPABILITY - what may be promised. This is about DISPATCH - what is
    # already flowing. The build had one number answering both questions, and they have
    # different answers the moment comms drop.
    #
    # The controller needs no new information for this: it is the sum of setpoints IT SENT,
    # to devices it can no longer reach. No truth is read; `control/` still imports nothing
    # from `sim/`.
    shortfall = 0.0
    live_energy = [a for a in admissions
                   if a.product is Product.ENERGY and a.kw_at(bucket_ts) > 0]
    booked_energy_kw = sum(a.kw_at(bucket_ts) for a in live_energy)

    for adm in live_energy:
        want = adm.kw_at(bucket_ts)
        if stranded_kw > 0 and booked_energy_kw > 0:
            # Apportioned by share of what is booked right now. In the Must scope at most one
            # ENERGY window is ever open at a time, so this is a one-claim division today; it
            # is written generally so a second overlapping award does not silently get all of
            # the credit.
            want = max(0.0, want - stranded_kw * (want / booked_energy_kw))
        if want <= 0:
            continue
        # Remaining duration measured from NOW, floored at one control tick (2026-09-24,
        # RATIONALE.md s6c). FINDING-8 measured it from the bucket's start, which is right for
        # an allocator that runs once per bucket - but the runner calls this every tick and
        # passes the TICK time as `bucket_ts`, so "bucket start" became "now minus 5 minutes"
        # on every tick. Telemetry is live, so energy already spent had left the numerator
        # while its minutes stayed in the divisor: at 20:05, 1,337.7 kWh over 1.0 h instead of
        # 55 min, capping placeable kW below the 1,406.9 the ledger had sold. Silent misses on
        # every quiet evening for any controller without a reserve to hide it.
        #
        # PLUS THIS TICK. The window's ticks are (start, end], and the tick AT `end` still
        # delivers - its setpoint is held for the next `tick_s`. So at 18:29 two ticks remain,
        # not one. Dividing by (end - now) alone emptied the small units at 18:29 and left the
        # 18:30 tick 792 kW short (seed 19, the oracle, which had no margin to hide it). The
        # five-minute pad the first fix removed had been covering this too.
        h_remaining = max(adm.end_ts - bucket_ts, 0.0) / 3600.0 + tick_s / 3600.0

        # WATER-FILL, not greedy. FINDING-13, 2026-09-17: filling devices in order to their caps
        # drains the ones at the front of the list hardest, so late in a long window they sit at
        # their floor contributing 0 kW while others still have energy. Measured on S1: the last
        # 25 minutes of the co-op's 3 h window delivered 93-95% of an admitted 3,000 kW with
        # 4,373 kWh still in the fleet - seven SILENT breaches, on `headroom`, caused entirely by
        # how the power was spread rather than by any shortage.
        #
        # Water-filling gives every device the same kW until it hits its own cap, so they all
        # approach their floors together and no straggler is created. It is also what a real
        # fleet controller does, for the same reason.
        caps = {}
        for e in live:
            did = e.device_id
            kw_head = e.kw_cap - earmark_kw[did] - setpoint[did]
            kwh_head = e.kwh_above_floor_low - earmark_kwh[did] - setpoint[did] * h_remaining
            caps[did] = max(0.0, min(kw_head, kwh_head / h_remaining))      # I9, per device

        remaining, pool = want, dict(caps)
        while remaining > 1e-9 and pool:
            share = remaining / len(pool)
            filled = {d: c for d, c in pool.items() if c <= share}
            if not filled:                       # everyone can take an equal share
                for d in pool:
                    setpoint[d] += share
                remaining = 0.0
                break
            for d, c in filled.items():          # cap these, redistribute the rest
                setpoint[d] += c
                remaining -= c
                pool.pop(d)
        shortfall += max(0.0, remaining)

    # ---- 3. feeder caps (proxy, §5)
    by_feeder: dict[str, list[str]] = {}
    for e in estimates:
        by_feeder.setdefault(e.feeder_id, []).append(e.device_id)
    for fid, ids in by_feeder.items():
        cap = feeder_caps.get(fid)
        total = sum(setpoint[i] for i in ids)
        if cap is not None and total > cap + 1e-9 and total > 0:
            scale = cap / total
            for i in ids:
                setpoint[i] *= scale
            shortfall += total - cap

    return Plan(setpoint, earmark_kw, earmark_kwh, shortfall)
