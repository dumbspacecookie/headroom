"""The booking ledger. SPEC §6.3.

~200 lines, and it is the project. The optimizer proposes; this admits only what the fleet can
still keep. Every term below maps to a bullet in §6.3 and is named after it.

THE ONE THING TO GET RIGHT (SPEC §6.3 v0.4, PRACTICE-NOTES.md FINDING-3):

    the ENERGY kWh term is evaluated over bucket ends from start(W) to `horizon_ts`
    -- the end of the SCENARIO -- not to end(W).

Because a CAPACITY reservation is subtracted only at `t` inside the CAPACITY window, an ENERGY
claim that ends BEFORE that window never meets the reservation at any `t` inside its own window.
Stop the search at end(W) -- the natural reading -- and the ledger admits an earlier claim that
eats a later hold's kWh and reports nothing wrong: measured 0% capacity availability, 24 silent
buckets, strictly worse than `reasonable`. Control C6 plants exactly that and fails the build.
"""
from __future__ import annotations

from dataclasses import replace

from config import DEFAULTS, Config
from contracts.types import BindingReason, Claim, Product
from control.admission import Admission, bucket_ends
from control.deliverability import FleetArrays, worst_shortfall
from control.estimator import ScopeBelief
from control.reserve import regions_to_hold


class Ledger:
    """Admits claims in priority order against one scope's belief."""

    def __init__(self, belief: ScopeBelief, now: float, cfg: Config = DEFAULTS,
                 horizon_ts: float | None = None, slack_upper: str = "horizon",
                 prior: dict[str, Admission] | None = None,
                 warm_kw: dict[str, float] | None = None) -> None:
        self.belief = belief
        self.now = now
        self.cfg = cfg
        self.horizon_ts = cfg.horizon_ts if horizon_ts is None else horizon_ts
        # What the previous pass decided. Only its ALREADY-STARTED buckets are honoured, and
        # this class decides which those are (see `locks`) - the caller does not get a vote,
        # because two places deciding what "started" means is how FINDING-3/11/17 happened.
        self.prior = prior or {}
        # A STARTING GUESS for enforce_n1's search, never a constraint. The counterfactual
        # ledger has no prior on purpose (it asks "would I sell this, fresh?"), so without a
        # hint it bisected from zero on every reconcile - 810 probes on one S1 run.
        self.warm_kw = warm_kw or {}
        # "horizon" is the spec. "window" is the natural misreading, kept ONLY so control C6 can
        # plant it; nothing in the product may set it.
        self.slack_upper = slack_upper
        self.admitted: list[Admission] = []
        # None = the deterministic N-1 reserve in `belief`. Otherwise k(bucket end), and the
        # reserve at t is the k(t) largest reachable regions (RATIONALE.md s6d).
        self.k_at = regions_to_hold(cfg, now, belief)
        self._kwh_after: dict[int, float] = {}
        self._kwh_max = 0.0
        if self.k_at is not None:
            # The kWh reserve at t must cover every later bucket's: energy spent before a bucket
            # that needs it is not there when it does (FINDING-11, the same hole in time).
            later = 0.0
            for b in reversed(self.buckets(now, self.horizon_ts)):
                later = max(later, self._reserve_kw(b) * cfg.outage_h)
                self._kwh_after[round(b)] = later
            self._kwh_max = later

    def _reserve_kw(self, bucket_ts: float) -> float:
        if self.k_at is None:
            return self.belief.kw_reserve_kw
        return sum(self.belief.region_kw_desc[:self.k_at(bucket_ts)])

    # ---------------------------------------------------------------- grids
    def buckets(self, start_ts: float, end_ts: float) -> list[float]:
        """Bucket END stamps in (start, end]. One definition, in control/admission.py."""
        return bucket_ends(start_ts, end_ts, self.cfg.bucket_s)

    def evalgrid(self, claim) -> list[float]:
        """Where the ENERGY kWh term looks. §6.3 v0.4: to horizon_ts, not to end(W)."""
        upper = self.horizon_ts if self.slack_upper == "horizon" else claim.end_ts
        return self.buckets(claim.start_ts, max(upper, claim.end_ts))

    # ---------------------------------------------------------------- §6.3 terms
    def drain_hi(self, t: float) -> float:
        """aux (+ load where on) of REACHABLE devices over now -> t."""
        return self.belief.drain_kw * (t - self.now) / 3600.0

    def hold_kw(self, adm: Admission, bucket_ts: float) -> float:
        """ENERGY holds inside its window. CAPACITY holds in EVERY bucket of its window,
        called or not - that is what makes it a reservation rather than a hope.

        Per-bucket: a booking whose early buckets are locked at 928 kW and whose tail was cut to
        610 holds a different number in each, and `kw_at` is the only thing that knows which.
        """
        return adm.kw_at(bucket_ts)

    def consumed_cum(self, adm: Admission, t: float) -> float:
        """Energy this booking will take between NOW and t - not since its window opened.

        FINDING-12, found by the first end-to-end run, 2026-09-17. On re-admission the belief's
        E0 is read from telemetry, so energy already delivered is ALREADY reflected in the
        observed SoC. Charging the window from its start double-counts it: at 15:35 the co-op
        had delivered 250 kWh, the meter had seen it, and the ledger subtracted it again.

        The visible symptom was worse than the arithmetic: the ledger manufactured Notices that
        nothing in the world had caused - 928 -> 728 -> 478 kW at 15:35, four hours before the
        fault that was supposed to explain them. A Notice that fires without a cause is exactly
        the notice-spam SPEC §14 says to expose, generated by us.
        """
        if adm.product is Product.CAPACITY:
            return 0.0                      # not deployed in the Must scope
        return adm.kwh_between(self.now, t)

    def capacity_reservation(self, t: float) -> float:
        """Half-open (start, end], the same convention as hold_kw.

        FINDING-17, found by the property lens at close on 2026-09-17. This used a CLOSED
        interval while hold_kw used half-open, so two ADJACENT capacity windows - one ending at
        19:00, the next starting at 19:00 - both counted at that shared instant and the ledger
        promised 2.0 kWh against 1.0 it believed it had. Repro: E0 1.0 kWh, three CAPACITY
        claims of 10 kW / max_deploy_h 0.5 at 15:30-16:00, 17:00-19:00 and 19:00-19:30.

        Third instance of this build's recurring shape: two window-membership tests that do not
        agree. FINDING-3 and FINDING-11 were the others. Any new window test inherits the
        question - which convention, and does it match its neighbours?
        """
        return sum(a.kw_at(t) * a.max_deploy_h for a in self.admitted
                   if a.product is Product.CAPACITY)

    def kwh_reserve_at(self, t: float, claim) -> float:
        """The N-1 kWh reserve, held at EVERY t from now to the horizon.

        SPEC v0.3-v0.5 said "applied only at times inside an active discharge or CAPACITY
        window". That conditionality is a hole, found by the property lens on 2026-09-16
        (PRACTICE-NOTES.md FINDING-11): a claim whose own window sits outside every other
        window is charged no reserve at those times, spends against it, and continuing aux
        drain then eats into what was supposed to be untouchable. A reserve that evaporates
        between windows is not a reserve.

        Holding it unconditionally does not move a single S1 number (verified before the
        change: 928.2 kW admitted, 1,071.8 kWh cut, both readings) - it only closes the hole.
        """
        del claim
        if self.k_at is None:
            return self.belief.kwh_reserve_kwh
        # off the grid ahead (a window that started in the past): the strictest, never zero
        return self._kwh_after.get(round(t), self._kwh_max)

    def kw_room(self, bucket_ts: float) -> float:
        return (self.belief.KW_kw - self._reserve_kw(bucket_ts)
                - sum(self.hold_kw(a, bucket_ts) for a in self.admitted))

    def slack(self, t: float, claim) -> float:
        return (self.belief.E0_kwh
                - self.drain_hi(t)
                - sum(self.consumed_cum(a, t) for a in self.admitted)
                - self.capacity_reservation(t)
                - self.kwh_reserve_at(t, claim))

    # ---------------------------------------------------------------- locks
    def locks(self, claim: Claim) -> dict[float, float]:
        """The buckets of `claim` that have already STARTED, at the kW they were sold for.

        SPEC 6.1 step 3: "recompute admission for not-yet-started buckets. Started buckets are
        locked." The unit is the BUCKET, not the claim - that distinction is the whole of
        FINDING-14. A bucket has started once `now` has reached its start, so the bucket that is
        being dispatched RIGHT NOW is locked too: the allocator is already placing it on devices
        and there is nothing left to decide about it. The earliest bucket this pass may revise
        therefore starts one full bucket in the future, which is where the Notice's lead time
        comes from - 5 minutes, and never zero.
        """
        old = self.prior.get(claim.claim_id)
        if old is None:
            return {}
        return {b: old.kw_at(b) for b in self.buckets(claim.start_ts, claim.end_ts)
                if b - self.cfg.bucket_s <= self.now + 1e-9}

    def split_hours(self, claim: Claim, locked: dict[float, float], t: float
                    ) -> tuple[float, float]:
        """Over (max(start, now), min(t, end)]: (hours in FREE buckets, kWh in LOCKED ones).

        The two halves of the ENERGY term's arithmetic. Locked energy is already sold, so it
        moves to the numerator as a subtraction; free hours stay in the divisor as the thing
        being solved for. With no locks this returns exactly the old `elapsed_h` and 0.0, which
        is why first admission - and therefore D1 - is bit-identical to before per-bucket.
        """
        origin, hi = max(claim.start_ts, self.now), min(t, claim.end_ts)
        free_h = locked_kwh = 0.0
        prev = claim.start_ts
        edges = self.buckets(claim.start_ts, claim.end_ts)
        if not edges or edges[-1] < claim.end_ts - 1e-9:
            edges = edges + [claim.end_ts]          # short final bucket, not a dropped one
        for b in edges:
            seg = min(hi, b) - max(origin, prev)
            prev = b
            if seg <= 0:
                continue
            if b in locked:
                locked_kwh += locked[b] * seg / 3600.0
            else:
                free_h += seg / 3600.0
        return free_h, locked_kwh

    # ---------------------------------------------------------------- admission
    def admit(self, claim: Claim) -> Admission:
        locked = self.locks(claim)
        window = self.buckets(claim.start_ts, claim.end_ts)
        free = [b for b in window if b not in locked]

        if window and not free:
            # Every bucket has started: there is nothing left to decide, so decide nothing.
            # Recomputing here is what produced a Notice for COOP_PEAK at 20:15 on S2 - a claim
            # whose window had closed at 18:30. Its kWh term was vacuous (no hours left to
            # divide by, so +inf) and its kW room was read off buckets that were an hour and
            # three quarters in the past, so the pass "downgraded" a booking that had already
            # been delivered in full. A Notice about a settled bucket is notice-spam of exactly
            # the kind SPEC 14 says to expose, and we were generating it.
            old = self.prior[claim.claim_id]
            adm = Admission(
                claim_id=claim.claim_id, product=claim.product,
                requested_kw=claim.power_kw, admitted_kw=old.admitted_kw, reason=old.reason,
                start_ts=claim.start_ts, end_ts=claim.end_ts, max_deploy_h=claim.max_deploy_h,
                kw_room_at_decision=old.kw_room_at_decision,
                kwh_slack_at_decision=old.kwh_slack_at_decision,
                bucket_s=self.cfg.bucket_s, locked_kw=locked,
            )
            self.admitted.append(adm)
            return adm

        kw_room_min = min((self.kw_room(b) for b in free), default=float("inf"))

        if claim.product is Product.ENERGY:
            candidates = []
            # The divisor must share consumed_cum's origin: max(start(W), now). Measuring the
            # numerator from `now` (FINDING-12) and the denominator from start(W) means a claim
            # already half-delivered is asked to fit its REMAINING energy into its WHOLE
            # duration - so it re-admits lower every bucket and chips itself down. Measured:
            # 47 spurious Notices on an S1 with no faults at all, COOP_PEAK walked from
            # 3,000 kW to 2,829 by 16:00 with nothing whatsoever happening in the world.
            # Two uses of "hours of W elapsed" that do not agree is one bug wearing two hats.
            for t in self.evalgrid(claim):
                free_h, locked_kwh = self.split_hours(claim, locked, t)
                if free_h <= 0:
                    continue
                candidates.append((self.slack(t, claim) - locked_kwh) / free_h)
            kwh_term = min(candidates, default=float("inf"))
        else:
            # A CAPACITY claim's own reservation is what `/max_deploy_h` solves for, so a locked
            # bucket contributes no separate term - only the free buckets are evaluated.
            kwh_term = min((self.slack(t, claim) / claim.max_deploy_h for t in free),
                           default=float("inf"))

        terms = {
            BindingReason.NONE: claim.power_kw,
            BindingReason.KW_ROOM: kw_room_min,
            BindingReason.KWH_SLACK: kwh_term,
        }
        admitted_kw = max(0.0, min(terms.values()))
        # FINDING-27. The slack term is a difference of large numbers, so "nothing left" came out
        # as 1.1e-13 kW instead of 0. That residue was locked into every later bucket, the
        # Notice printed it as "0 kW", and the scorer saw a booking of 1e-13 delivered at 0 and
        # counted a late miss for every minute of the window: 29 of the 36 evenings the sweep
        # called "warned, and still short". Float noise is not a booking.
        if admitted_kw < 1e-6:
            admitted_kw = 0.0
        if admitted_kw >= claim.power_kw - 1e-9:
            reason = BindingReason.NONE
        else:
            reason = min(terms, key=lambda k: terms[k])

        adm = Admission(
            claim_id=claim.claim_id, product=claim.product,
            requested_kw=claim.power_kw, admitted_kw=admitted_kw, reason=reason,
            start_ts=claim.start_ts, end_ts=claim.end_ts, max_deploy_h=claim.max_deploy_h,
            kw_room_at_decision=kw_room_min, kwh_slack_at_decision=kwh_term,
            bucket_s=self.cfg.bucket_s, locked_kw=locked,
        )
        self.admitted.append(adm)
        return adm

    def admit_all(self, claims) -> list[Admission]:
        """The whole decision: the priority pass, then the per-device N-1 check.

        Two stages, kept separate on purpose. `admit_priority` is SPEC 6.3's arithmetic - kW and
        kWh for the scope as a whole - and the planted-bug controls C1-C6 probe it directly,
        because once `enforce_n1` binds (it does on S1: 763 kW, reason `reserve`) it would hide
        a broken energy term behind a smaller number. Control C8 probes the second stage.
        """
        order = self.admit_priority(claims)
        self.enforce_n1(order)
        return self.admitted

    def admit_priority(self, claims) -> list[Claim]:
        """Priority order, AFTER every claim's locked buckets are charged. Returns the order.

        2026-09-24, RATIONALE.md s6c. Locked buckets are already sold and being delivered; no
        priority can take them back. Admitting senior claims first without them meant a senior
        CAPACITY hold was decided while a junior ENERGY claim's in-flight bucket was not yet in
        the ledger: seed 246, R3 dark at 20:25, ADER_AS kept 1,500 kW against slack that was
        -20.7 kW once the 928 kW in delivery was counted, and its miss went out silent. Each
        claim swaps its locked-only placeholder for its real decision when its turn comes, so
        nothing is counted twice.
        """
        order = sorted(claims, key=lambda c: (c.priority, c.created_ts))
        placeholders: dict[str, Admission] = {}
        for claim in order:
            locked = self.locks(claim)
            if locked:
                ph = Admission(
                    claim_id=claim.claim_id, product=claim.product,
                    requested_kw=claim.power_kw, admitted_kw=0.0, reason=BindingReason.NONE,
                    start_ts=claim.start_ts, end_ts=claim.end_ts,
                    max_deploy_h=claim.max_deploy_h, kw_room_at_decision=0.0,
                    kwh_slack_at_decision=0.0, bucket_s=self.cfg.bucket_s, locked_kw=locked,
                )
                self.admitted.append(ph)
                placeholders[claim.claim_id] = ph
        for claim in order:
            ph = placeholders.pop(claim.claim_id, None)
            if ph is not None:
                self.admitted.remove(ph)
            self.admit(claim)
        return order

    # ---------------------------------------------------------------- N-1, per device
    def enforce_n1(self, order: list[Claim]) -> None:
        """Second stage: every booking must be deliverable per device, and every CAPACITY hold
        must survive losing any one region.

        The priority pass above answers "is there enough kW and kWh in the scope?". It cannot
        answer "is there enough kW that still has energy behind it, once the energy claims are
        placed where the allocator will actually place them?" - that needs devices, not sums.
        `control/deliverability.py` explains the two evenings that made it visible: seed 259
        (a hold that could not survive losing a region) and seed 19 (a co-op bucket that needed
        3,000 kW when only 2,208 kW had energy behind it).

        Who yields: the most junior claim that is actually in the failing bucket, first, and only
        by enough to fix the buckets it is in. A junior ENERGY award giving way to a senior AS
        hold is the priority ladder doing its job; the hold itself is only cut once every junior
        in its buckets is already at zero.

        `cfg.deliverability` says which question is asked: "n1" (headroom), "n0" (the oracle -
        deliverable per device with no region removed, because a ceiling may not count empty
        batteries as power any more than headroom may), or "off" (the flat de-rate baselines and
        the -N-1 ablation). It is set per controller, never inferred.

        Cost: one forward projection per pass when nothing is short. When something is, the
        claim's previous admission is tried first, because reconcile runs every bucket and the
        answer rarely moves; only if that fails does it bisect. The runner's downgrade-only clamp
        would discard any higher answer anyway, so the warm start loses nothing.
        """
        mode = self.cfg.deliverability
        if mode == "off":
            return
        if mode not in ("n1", "n0"):
            raise ValueError(f"deliverability must be n1, n0 or off, not {mode!r}")
        fleet = FleetArrays(self.belief)
        locked = frozenset(round(b) for c in order for b in self.locks(c))
        drop = mode == "n1"

        def worst(only: frozenset[int] | None = None, skip: frozenset[int] = frozenset()):
            return worst_shortfall(fleet, self.admitted, self.now, locked, self.cfg,
                                   drop_regions=drop, only=only, skip=skip,
                                   k_at=self.k_at if drop else None)

        # Each round: find the worst bucket, and trim the most junior claim that is actually in
        # it - and only by enough to fix the buckets THAT claim is in. Trimming a claim for a
        # bucket it is not in would zero an unrelated award (the 20:00 ADER_ENERGY "fixing" an
        # 18:30 co-op shortfall) and still fix nothing. A claim is trimmed at most once per
        # pass: its bisection already covers every bucket it is in.
        #
        # A bucket nobody left can relieve is set aside, not allowed to end the pass. The first
        # version stopped there, so on seed 19 the oracle spent every round on 21:00 and never
        # looked at the co-op's 18:30 bucket - the one that actually failed.
        done: set[str] = set()
        unfixable: set[int] = set()
        for _ in range(4 * len(order) + 64):
            w = worst(skip=frozenset(unfixable))
            if w is None or w.margin_kw >= -1e-6:
                return
            b_fail = round(w.bucket_ts)
            cands = [c for c in sorted(order, key=lambda c: (-c.priority, -c.created_ts))
                     if c.claim_id not in done
                     and self.admitted_by_id(c.claim_id).admitted_kw > 0.0
                     and b_fail in self._free_ends(c, locked)]
            if not cands:
                unfixable.add(b_fail)
                continue
            claim = cands[0]
            done.add(claim.claim_id)
            adm = self.admitted_by_id(claim.claim_id)
            i = self.admitted.index(adm)
            mine = frozenset(self._free_ends(claim, locked))

            def fits(kw: float) -> bool:
                self.admitted[i] = replace(adm, admitted_kw=kw)
                w2 = worst(mine)
                return w2 is None or w2.margin_kw >= -1e-6

            prior = self.prior.get(claim.claim_id)
            guess = prior.admitted_kw if prior is not None else self.warm_kw.get(claim.claim_id)
            if guess is not None and guess < adm.admitted_kw and fits(guess):
                # For a real pass this is exact: the runner keeps the lower of old and new anyway.
                # For the counterfactual it is a LOWER BOUND, and deliberately so. The only
                # question the counterfactual answers is "would I still sell what is already
                # sold?" - and if the sold amount fits, the answer is yes whatever the exact
                # figure above it is. When it does not fit, we fall through to a full search and
                # the late Notice quotes an exact number.
                kw = guess
            elif not fits(0.0):
                kw = 0.0                      # not enough on its own; the next claim up yields too
            else:
                lo, hi = 0.0, adm.admitted_kw
                while hi - lo > 0.5:          # half a kW is below anything the metric can resolve
                    mid = (lo + hi) / 2.0
                    lo, hi = (mid, hi) if fits(mid) else (lo, mid)
                kw = lo
            # "reserve" only when the N-1 reserve is what bound it; a shortage of kW with energy
            # behind it (the ENERGY check, or the oracle's N-0) is kw_room.
            why = BindingReason.RESERVE if (drop and w.region_id not in ("energy", "none")) \
                else BindingReason.KW_ROOM
            self.admitted[i] = replace(adm, admitted_kw=kw, reason=why)

    def _free_ends(self, claim: Claim, locked: frozenset[int]) -> list[int]:
        """Bucket ends of `claim` this pass may still decide, as whole seconds."""
        return [round(b) for b in self.buckets(claim.start_ts, claim.end_ts)
                if round(b) not in locked and b > self.now]

    def admitted_by_id(self, claim_id: str) -> Admission:
        for adm in self.admitted:
            if adm.claim_id == claim_id:
                return adm
        raise KeyError(f"{claim_id} was never offered to the ledger")
