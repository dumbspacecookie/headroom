"""AST: control/ imports nothing from sim/, except control/oracle.py which is flagged.

The controller may only know what the network delivered. An import from sim/ is the controller
reading the truth it is supposed to be uncertain about - and it would make every number in the
demo a lie that no other test can see.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"oracle.py"}          # perfect-foresight upper bound; flagged in the UI as an oracle


def _sim_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] == "sim"]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] == "sim" or node.level and mod.startswith("sim"):
                bad.append(mod)
    return bad


def test_control_does_not_import_sim():
    offenders = {}
    for py in sorted((ROOT / "control").glob("*.py")):
        if py.name in ALLOWED:
            continue
        found = _sim_imports(py)
        if found:
            offenders[py.name] = found
    assert not offenders, (
        f"control/ must not import sim/: {offenders}. Only {sorted(ALLOWED)} may, and it is "
        f"flagged as an oracle."
    )


def test_oracle_is_the_only_exception_and_it_exists_or_not_at_all():
    """If oracle.py is deleted the allowlist must shrink with it - an allowlist entry for a file
    that does not exist is how an exception quietly widens later."""
    oracle = ROOT / "control" / "oracle.py"
    if not oracle.exists():
        assert ALLOWED == {"oracle.py"}, "allowlist drifted while oracle.py does not exist"
