"""Task runner. Stands in for `make`, which does not exist on the Windows build machine.

  python run.py test-fast     lenses 1,2,3,8 + 1-seed determinism   (every handback, every merge)
  python run.py gate          all 12 lenses, COUNTED                (every integration)
  python run.py gate-clean    all 12, from a fresh clone            (H20, H26, H38)
  python run.py test          everything                            (H20, H38)
  python run.py demo          re-bake + open the static demo page (no server)
  python run.py batch [N]     seeded sweep vs the oracle ceiling (N seeds, default 200)
  python run.py compare [N]   headroom vs standard practice + ablations (RATIONALE.md s8)
  python run.py data          re-pull + control the ERCOT/EIA data
  python run.py size          regenerate scenarios/S1_sizing.md

`make <target>` is an alias on any machine that has make; see Makefile.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable


def sh(*args: str, cwd: Path | None = None) -> int:
    return subprocess.call([PY, *args], cwd=str(cwd or ROOT))


def pytest(*args: str, cwd: Path | None = None) -> int:
    return sh("-m", "pytest", "-q", *args, cwd=cwd)


# ---------------------------------------------------------------- targets
def test_fast() -> int:
    return pytest("tests/known_answer", "tests/unit", "tests/boundary", "tests/determinism",
                  "-k", "not slow")


def test_all() -> int:
    return pytest("tests")


def gate(clean: bool = False) -> int:
    from tools.gate import run_gate
    return run_gate(root=ROOT, clean=clean)


def gate_clean() -> int:
    """Clone HEAD into a temp dir and gate THERE. What ships is what is committed."""
    tmp = Path(tempfile.mkdtemp(prefix="headroom-verify-"))
    dest = tmp / "repo"
    print(f"cloning HEAD -> {dest}")
    if subprocess.call(["git", "clone", "--quiet", str(ROOT), str(dest)]) != 0:
        print("FAIL: git clone failed"); return 1
    for pc in dest.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    # the clone has no .venv; run it with this interpreter
    rc = subprocess.call([PY, "run.py", "gate"], cwd=str(dest))
    print(f"\n(clean tree left at {dest} for inspection)")
    return rc


def demo() -> int:
    """Re-bake, then open the static page. There is NO server.

    `web/demo.html` is a local file that loads `web/out/runs.js` with a <script src>. Nothing
    listens on a port, nothing has to start, and there is no process to die in front of a judge.
    Baking first means the page can never show a run older than the code that is checked out.
    """
    rc = sh("prep/bake_runs.py")
    if rc:
        return rc
    page = ROOT / "web" / "demo.html"
    print("")
    print(f"opening {page}")
    webbrowser.open(page.as_uri())
    return 0


def compare() -> int:
    n = sys.argv[2] if len(sys.argv) > 2 else "1000"
    return sh("-m", "runner.compare", n, *sys.argv[3:])


def batch() -> int:
    """The seeded sweep behind Slide 3 and the `perf` lens. Default 200; pass 1000 for the bar."""
    n = sys.argv[2] if len(sys.argv) > 2 else "200"
    return sh("-m", "runner.batch", n)


def data() -> int:
    """CHECK, never re-pull. The parquets in data/raw/ passed their controls on 2026-09-15/16;
    a re-pull on a bad network would silently replace verified data with whatever came back.
    Re-pulling is a deliberate act: run the prep script directly and re-read its control output."""
    rc = sh("prep/pull_eia930.py", "july22", "calm", "fern", "--check")
    if rc:
        print("\nEIA data does NOT match what is committed. Do not 'fix' this by re-pulling.")
    print("\nNOTE: prep/pull_mis_prices.py has no --check mode and is NOT run here - it would"
          "\n      overwrite ercot_*_2026.parquet. Run it by hand only when you mean to re-pull.")
    return rc


def size() -> int:
    return sh("prep/size_s1.py")


TARGETS = {
    "test-fast": test_fast, "test": test_all, "gate": gate, "gate-clean": gate_clean,
    "demo": demo, "data": data, "size": size, "batch": batch, "compare": compare,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "gate"
    if name not in TARGETS:
        print(__doc__)
        print(f"unknown target {name!r}; one of: {', '.join(TARGETS)}")
        sys.exit(2)
    sys.exit(TARGETS[name]() or 0)
