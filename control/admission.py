"""The shared admission result. Both `reasonable` and `headroom` speak this.

It lives here, not in `contracts/`, because contracts/ was frozen (contracts-v1) before the
ledger existed - freezing at "H2" is right when three lanes build in parallel against a moving
contract, and premature when the build is sequential. Recorded in DECISIONS.md rather than
quietly unfrozen: an early freeze is cheap to work around and expensive to pretend did not happen.

PER-BUCKET ADMISSION (SPEC 6.1 step 3, 6.3 Reconcile; FINDING-14, closed 2026-09-17).

An Admission is a **profile**, not a number: one admitted kW per 5-minute bucket of its window.
It is stored as one live decision plus the buckets that have been frozen, rather than a full
dict, because those are two different KINDS of fact and collapsing them into one dict loses which
is which:

  * `admitted_kw` - the CURRENT decision. It applies to every bucket that has not started.
  * `locked_kw`   - buckets whose start has already passed. History. Never recomputed.

At first admission nothing has started, `locked_kw` is empty, and the profile is flat at
`admitted_kw` - which is why every ledger-level number in the build (D1's 928.2 kW, the 1,071.8
kWh cut) is untouched by this file existing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from config import DEFAULTS
from contracts.types import BindingReason, Product


def bucket_ends(start_ts: float, end_ts: float, bucket_s: float) -> list[float]:
    """Bucket END stamps in (start, end]. A bucket is labelled by its end.

    THE ONE definition of the bucket grid in the build. `Ledger.buckets` delegates here and so
    does `kwh_between`, because this project's recurring bug is two window computations that do
    not agree (FINDING-3, FINDING-11, FINDING-17). A second grid would be the fourth.

    The grid is anchored at `start_ts`, not at the scenario's t0: a claim's buckets are its own.
    A window that is not a whole number of buckets gets a short final bucket, added by
    `_edges` below rather than silently dropped.
    """
    n = int(round((end_ts - start_ts) / bucket_s))
    return [start_ts + bucket_s * (i + 1) for i in range(max(0, n))]


@dataclass
class Admission:
    """One claim's admission decision, as a per-bucket profile.

    Shared with the ledger so D1 and the API can speak one language regardless of controller.
    """
    claim_id: str
    product: Product
    requested_kw: float
    admitted_kw: float
    reason: BindingReason
    start_ts: float
    end_ts: float
    max_deploy_h: float = 0.0
    kw_room_at_decision: float = 0.0
    kwh_slack_at_decision: float = 0.0
    bucket_s: float = DEFAULTS.bucket_s
    locked_kw: dict[float, float] = field(default_factory=dict)

    # ------------------------------------------------------------ the profile
    def _edges(self) -> list[float]:
        edges = bucket_ends(self.start_ts, self.end_ts, self.bucket_s)
        if not edges or edges[-1] < self.end_ts - 1e-9:
            edges.append(self.end_ts)            # short final bucket, not a dropped one
        return edges

    def bucket_end_at(self, t: float) -> float | None:
        """The end stamp of the bucket instant `t` falls in, or None if `t` is outside the window.

        Half-open (start, end], the SAME convention as `hold_kw` and `capacity_reservation`.
        FINDING-17 was two adjacent windows both counting at their shared instant because one of
        them closed `<=` and the other `<`; every window test in this build now answers this
        question the same way. At the instant one window ends and the next begins, the instant
        belongs to the one that is ENDING.
        """
        if not (self.start_ts < t <= self.end_ts + 1e-9):
            return None
        n = math.ceil((t - self.start_ts) / self.bucket_s - 1e-9)
        return min(self.start_ts + self.bucket_s * n, self.end_ts)

    def kw_at(self, t: float) -> float:
        """Admitted kW for the bucket containing `t`. 0 outside the window."""
        b = self.bucket_end_at(t)
        if b is None:
            return 0.0
        return self.locked_kw.get(b, self.admitted_kw)

    def kwh_between(self, lo: float, hi: float) -> float:
        """Energy this profile delivers over (lo, hi], clipped to the window.

        THE one integrator: the ledger's `consumed_cum` and the runner's "still owed" both call
        it, so a profile can never be summed two ways.
        """
        lo, hi = max(lo, self.start_ts), min(hi, self.end_ts)
        if hi <= lo:
            return 0.0
        total, prev = 0.0, self.start_ts
        for b in self._edges():
            seg = min(hi, b) - max(lo, prev)
            if seg > 0:
                total += self.kw_at(b) * seg / 3600.0
            prev = b
        return total

    # ------------------------------------------------------------ headline views
    @property
    def duration_h(self) -> float:
        return (self.end_ts - self.start_ts) / 3600.0

    @property
    def requested_kwh(self) -> float:
        return self.requested_kw * self.duration_h

    @property
    def admitted_kwh(self) -> float:
        """The CURRENT decision over the whole window - the headline, and what D1 reads.

        Not the same as `kwh_between(start, end)` once buckets are locked: that is what the
        profile actually delivers. Both are wanted; they are asked for by name.
        """
        return self.admitted_kw * self.duration_h

    @property
    def is_full(self) -> bool:
        return self.admitted_kw >= self.requested_kw - 1e-9
