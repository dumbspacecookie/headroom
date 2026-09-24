"""Lens 8: a test may not assign to a module attribute without a fixture that restores it.

A raw `module.attr = fake` is never undone. It leaks for the rest of the session and every
later test runs against the fake - including the ones that are supposed to be proving the real
thing works. Use monkeypatch (pytest restores it) or a fixture with an explicit teardown.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
SAFE_RECEIVERS = {"self", "cls", "monkeypatch", "mp", "request", "tmp_path", "config"}


def _bad_assignments(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported_modules.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                imported_modules.add(a.asname or a.name)

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            base = target.value
            if not isinstance(base, ast.Name):
                continue
            if base.id in SAFE_RECEIVERS:
                continue
            if base.id in imported_modules:
                bad.append(f"line {node.lineno}: {base.id}.{target.attr} = ...")
    return bad


def test_no_raw_module_attribute_assignment_in_tests():
    offenders = {}
    for py in sorted(TESTS.rglob("test_*.py")):
        if py.name == Path(__file__).name:
            continue
        found = _bad_assignments(py)
        if found:
            offenders[str(py.relative_to(ROOT))] = found
    assert not offenders, (
        "raw module-attribute assignment in a test - it is never undone and leaks for the whole "
        f"session. Use monkeypatch.setattr instead:\n{offenders}"
    )
