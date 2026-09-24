"""Freeze contracts/ at H2. Run once, then `git tag contracts-v1`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.boundary.test_contracts_hash import BASELINE, _digest  # noqa: E402

d = _digest()
BASELINE.write_text(d + "\n", encoding="utf-8")
print(f"frozen: {d}\nnow run:  git add contracts/.frozen-sha256 && git tag contracts-v1")
