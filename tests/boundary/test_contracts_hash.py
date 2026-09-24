"""contracts/ is frozen at H2. This test is the enforcement.

Workflow: at H2, run `python tools/freeze_contracts.py` to write the baseline, then
`git tag contracts-v1`. From then on any edit to contracts/ fails here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "contracts" / ".frozen-sha256"


def _digest() -> str:
    h = hashlib.sha256()
    for py in sorted((ROOT / "contracts").glob("*.py")):
        h.update(py.name.encode())
        h.update(py.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def test_contracts_unchanged_since_freeze():
    if not BASELINE.exists():
        pytest.skip("contracts not frozen yet - run tools/freeze_contracts.py at H2")
    expected = BASELINE.read_text(encoding="utf-8").strip()
    assert _digest() == expected, (
        "contracts/ changed after the freeze. A lane may not edit contracts/. If the change is "
        "genuinely needed, ash makes it, re-freezes, and records it in DECISIONS.md."
    )
