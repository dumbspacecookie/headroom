"""`reasonable` - the competent baseline. SPEC §7, §8.1.

IT IS NOT A STRAWMAN, AND THE DEMO DEPENDS ON THAT.

`reasonable` checks every claim's energy against the inventory it can currently see. That is what
a careful engineer writes first, and on S1 it keeps ~100% of its ENERGY promise. What it does not
do is:
  * carry commitments ACROSS TIME - it never asks "will this still be there at 9pm?"
  * reserve anything for a CAPACITY hold it has already granted
  * hold an N-1 reserve

So on S1 it admits all three claims in full and the ancillary-service hold quietly stops being
deliverable around 20:00 - a CAPACITY silent breach, with no Notice. Measured in the practice
build: capacity availability 75%, 6 silent capacity buckets, ENERGY promise ~100%.

If you ever find yourself saying "reasonable under-delivers" on S1, re-read that paragraph.
"""
from __future__ import annotations

from config import DEFAULTS, Config
from contracts.types import BindingReason, Claim, Product
from control.admission import Admission
from control.estimator import ScopeBelief


class Reasonable:
    """Per-claim energy check against current inventory. No across-time, no reserve."""

    name = "reasonable"

    def __init__(self, belief: ScopeBelief, now: float, cfg: Config = DEFAULTS) -> None:
        self.belief = belief
        self.now = now
        self.cfg = cfg
        self.admitted: list[Admission] = []

    def admit(self, claim: Claim) -> Admission:
        need_kwh = (claim.power_kw * claim.max_deploy_h if claim.product is Product.CAPACITY
                    else claim.requested_kwh)
        # NOTE: E0 is read fresh each time and never decremented by what was already admitted.
        # That single omission is the whole difference on S1. It is not a bug in this file - it
        # is the baseline behaving exactly as specified.
        fits_kwh = need_kwh <= self.belief.E0_kwh
        fits_kw = claim.power_kw <= self.belief.KW_kw

        if fits_kwh and fits_kw:
            admitted_kw, reason = claim.power_kw, BindingReason.NONE
        elif not fits_kw:
            admitted_kw, reason = max(0.0, self.belief.KW_kw), BindingReason.KW_ROOM
        else:
            admitted_kw, reason = 0.0, BindingReason.KWH_SLACK

        adm = Admission(
            claim_id=claim.claim_id, product=claim.product,
            requested_kw=claim.power_kw, admitted_kw=admitted_kw, reason=reason,
            start_ts=claim.start_ts, end_ts=claim.end_ts, max_deploy_h=claim.max_deploy_h,
            kw_room_at_decision=self.belief.KW_kw, kwh_slack_at_decision=self.belief.E0_kwh,
            bucket_s=self.cfg.bucket_s,
        )
        self.admitted.append(adm)
        return adm

    def admitted_by_id(self, claim_id: str) -> Admission:
        for adm in self.admitted:
            if adm.claim_id == claim_id:
                return adm
        raise KeyError(f"{claim_id} was never offered to the ledger")
