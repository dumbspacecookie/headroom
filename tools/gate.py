"""`python run.py gate` - the 8-lens gate. docs/GATE.md is the spec.

THE RULE THAT MAKES THIS A GATE AND NOT A DECORATION:
    count the lenses. A lens that did not run is a FAILURE, not a pass.

A gate that silently skips a lens reports green while checking less than you think. So the
expected count is declared here, asserted at the end, and a mismatch fails the run even if every
lens that did run passed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

EXPECTED_LENSES = 12


@dataclass
class Lens:
    n: int
    name: str
    paths: tuple[str, ...]
    why: str
    due: str = "now"            # "now" | "H20" | "freeze" - when this lens MUST be green
    # A lens whose test directory does not exist yet is PENDING, not passing, and not skipped.
    # Before H2 every lens is pending; the gate says so and fails, which is correct at H2.


LENSES = [
    # SPEC §11 names all eight test layers. The first version of this gate carried only four of
    # them plus its own four, and said nothing about the gap - so "8 of 8 passed" claimed more
    # coverage than it had. A gate built to catch a missing lens must not be missing lenses.
    Lens(1, "known_answer", ("tests/known_answer",), "D1, I8, I9, per-device kWh cap, band cases", "H20"),
    Lens(2, "unit", ("tests/unit",), "band monotonic, feeder clip, partitions, N-1, FINDING-1"),
    Lens(3, "boundary", ("tests/boundary",), "control/ imports nothing from sim/"),
    Lens(4, "determinism", ("tests/determinism",), "same seed -> same events_sha256"),
    Lens(5, "controls", ("tests/controls",), "C1-C7: a planted bug scoring 0 FAILS the build", "H20"),
    Lens(6, "chart_fixture", ("tests/chart_fixture",), "truth line inside the axes, non-zero y-range", "H20"),
    Lens(7, "contracts_hash", ("tests/boundary/test_contracts_hash.py",), "contracts/ unchanged since contracts-v1"),
    Lens(8, "fakes", ("tests/meta",), "no raw module.attr assignment without a restoring fixture"),
    # SPEC §11 layers the first version of this gate silently omitted:
    Lens(9, "property", ("tests/property",), "I1-I9 under hypothesis, 200 examples", "H20"),
    Lens(10, "metamorphic", ("tests/metamorphic",), "more staleness -> less room; more devices -> more", "H20"),
    Lens(11, "perf", ("tests/perf",), "1k x 1000 seeds <= 20 min; batch is not a demo-day surprise", "freeze"),
    Lens(12, "demo", ("tests/demo",), "DEMO.md §2's 'must be on screen' asserted via the API", "H20"),
]



def _py(root: Path) -> str:
    v = root / ".venv" / "Scripts" / "python.exe"
    return str(v) if v.exists() else sys.executable


def _run_lens(root: Path, lens: Lens) -> tuple[str, str]:
    """-> (state, detail). state in {PASS, FAIL, PENDING}."""
    existing = [p for p in lens.paths if (root / p).exists()]
    if not existing:
        return "PENDING", "no test directory yet"
    proc = subprocess.run(
        [_py(root), "-m", "pytest", "-q", "--no-header", *existing],
        cwd=str(root), capture_output=True, text=True,
    )
    out = proc.stdout
    tail = [ln for ln in out.strip().splitlines() if ln.strip()]
    detail = tail[-1][:90] if tail else f"exit {proc.returncode}"

    # pytest exit 5 = no tests collected. The directory exists but is empty: PENDING, not a
    # mysterious failure, and definitely not a pass.
    if proc.returncode == 5:
        return "PENDING", "directory exists, no tests in it"

    # A lens whose tests ALL skipped has evaluated nothing. Reporting that as PASS is how a
    # guard goes green on input it cannot evaluate - the single cheapest way to believe you are
    # covered when you are not. Skips are PENDING.
    if proc.returncode == 0 and not re.search(r"\d+ passed", out):
        n_skipped = re.search(r"(\d+) skipped", out)
        n = n_skipped.group(1) if n_skipped else "all"
        return "PENDING", f"{n} skipped, 0 passed - nothing was actually evaluated"

    return ("PASS" if proc.returncode == 0 else "FAIL"), detail


def run_gate(root: Path, clean: bool = False) -> int:
    print(f"\nGATE  {root}")
    print("=" * 78)
    ran = passed = 0
    pending: list[str] = []
    failed: list[str] = []

    for lens in LENSES:
        state, detail = _run_lens(root, lens)
        if state == "PENDING":
            pending.append(lens.name)
        else:
            ran += 1
            if state == "PASS":
                passed += 1
            else:
                failed.append(lens.name)
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "PENDING": "----"}[state]
        due = "" if state == "PASS" else f"  [due {lens.due}]"
        print(f"  {lens.n} {mark} {lens.name:<16} {detail}{due}")
        if state != "PASS":
            print(f"         why it exists: {lens.why}")

    print("=" * 78)
    print(f"GATE: {EXPECTED_LENSES} lenses expected, {ran} ran, {passed} passed"
          + (f", {len(pending)} PENDING ({', '.join(pending)})" if pending else ""))

    if len(LENSES) != EXPECTED_LENSES:
        print(f"GATE FAIL: lens table has {len(LENSES)}, expected {EXPECTED_LENSES} - "
              f"a lens was added or removed without updating the count")
        return 1
    due_now = [l.name for l in LENSES if l.name in pending and l.due == "now"]
    if due_now:
        print(f"GATE FAIL: {len(due_now)} lens(es) due NOW did not run: {', '.join(due_now)}")
        return 1
    if pending:
        print("GATE FAIL: a lens that did not run is a failure, not a pass.")
        print("           Before H2 this is correct and expected - the gate is telling you")
        print("           the truth about how much is actually being checked.")
        return 1
    if failed:
        print(f"GATE FAIL: {', '.join(failed)}")
        return 1
    print("GATE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run_gate(Path(__file__).resolve().parents[1]))
