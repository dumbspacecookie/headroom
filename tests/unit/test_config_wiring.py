"""Config tells the truth: every knob is read by the code, or declared NOT_BUILT.

`config.py` is the one place every [A] knob lives. A knob that nothing reads is either a bug (the
code that used to read it was deleted) or a knob that was never wired up and should say so. This
file is the test that keeps those two lists - what the code actually reads, and what `NOT_BUILT`
declares - in sync with each other. HEADROOM-1003-C1.
"""
from __future__ import annotations

import ast
import dataclasses
import subprocess
from pathlib import Path

import pytest

from config import Config, NOT_BUILT


def unread_fields(sources: dict[str, str], fields) -> set[str]:
    """Names in `fields` that no source in `sources` reads.

    A read is an attribute access `<anything>.<name>` (`ast.Attribute` with that `attr`, in Load
    context) or `getattr(<anything>, "<name>")` with a literal string. An assignment target
    (Store context, e.g. `obj.beta = 1`) does not count. A source that fails to parse raises
    `ValueError` naming the offending file - a broken file is never silently treated as "reads
    nothing".
    """
    fields = set(fields)
    read: set[str] = set()
    for path, text in sources.items():
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            raise ValueError(f"{path}: could not parse as Python source: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                if node.attr in fields:
                    read.add(node.attr)
            elif isinstance(node, ast.Call):
                func = node.func
                is_getattr = (isinstance(func, ast.Name) and func.id == "getattr") or (
                    isinstance(func, ast.Attribute) and func.attr == "getattr"
                )
                if is_getattr and len(node.args) >= 2:
                    name_arg = node.args[1]
                    if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
                        if name_arg.value in fields:
                            read.add(name_arg.value)
    return fields - read


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=here, capture_output=True, text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def _non_test_sources() -> dict[str, str]:
    """Every `*.py` tracked by git, except anything under `tests/`. `config.py` is included."""
    root = _repo_root()
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True, check=True,
    )
    paths = [p for p in out.stdout.splitlines() if p and not p.startswith("tests/")]
    return {p: (root / p).read_text(encoding="utf-8") for p in paths}


# REQ: HEADROOM-1003-C1/AC-1
def test_not_built_declares_exactly_these_nine_knobs():
    assert set(NOT_BUILT) == {
        "hysteresis_frac", "hysteresis_hold_s",
        "dt_ctrl_s", "dt_phys_demo_s", "dt_phys_batch_s", "delta_kw", "pi_kp", "pi_ki",
        "flush_msgs_per_s",
    }
    assert NOT_BUILT["hysteresis_frac"] == "ASSUMPTIONS.md §3"
    assert NOT_BUILT["hysteresis_hold_s"] == "ASSUMPTIONS.md §3"
    for name in ("dt_ctrl_s", "dt_phys_demo_s", "dt_phys_batch_s", "delta_kw", "pi_kp", "pi_ki"):
        assert NOT_BUILT[name] == "ASSUMPTIONS.md §4"
    for name in ("flush_msgs_per_s",):
        assert NOT_BUILT[name] == "ASSUMPTIONS.md §5"


# REQ: HEADROOM-1003-C1/AC-2
def test_unread_fields_on_the_real_sources_is_exactly_not_built():
    sources = _non_test_sources()
    assert len(sources) == 44
    fields = [f.name for f in dataclasses.fields(Config)]
    assert unread_fields(sources, fields) == set(NOT_BUILT)


# REQ: HEADROOM-1014-S3/AC-4
def test_web_lease_chart_is_counted_among_the_non_test_sources():
    """`web/lease_chart.py` (HEADROOM-1014-S3) is a new non-test source, which is why the
    literal above moved from 43 to 44. NOT_BUILT itself is untouched by that spec."""
    sources = _non_test_sources()
    assert "web/lease_chart.py" in sources
    assert set(NOT_BUILT) == {
        "hysteresis_frac", "hysteresis_hold_s",
        "dt_ctrl_s", "dt_phys_demo_s", "dt_phys_batch_s", "delta_kw", "pi_kp", "pi_ki",
        "flush_msgs_per_s",
    }


# REQ: HEADROOM-1003-C1/AC-3
def test_a_simple_read_is_found_and_an_unread_name_is_reported():
    assert unread_fields({"a.py": "x = cfg.alpha"}, ["alpha", "beta"]) == {"beta"}


# REQ: HEADROOM-1003-C1/AC-3
@pytest.mark.parametrize("source", [
    "Config(beta=1)",            # keyword argument, not an attribute read
    "# cfg.beta would matter",   # only in a comment
    "x = 'cfg.beta'",            # only in a string literal
])
def test_a_name_that_only_looks_read_is_still_reported_as_unread(source):
    assert unread_fields({"a.py": source}, ["beta"]) == {"beta"}


# REQ: HEADROOM-1003-C1/AC-3
def test_an_assignment_target_alone_does_not_count_as_a_read():
    assert unread_fields({"a.py": "obj.beta = 1"}, ["beta"]) == {"beta"}


# REQ: HEADROOM-1003-C1/AC-3
def test_getattr_with_a_literal_name_counts_as_a_read():
    assert unread_fields({"a.py": 'getattr(cfg, "beta")'}, ["beta"]) == set()


# REQ: HEADROOM-1003-C1/AC-4
def test_a_source_that_does_not_parse_raises_valueerror_naming_the_file():
    with pytest.raises(ValueError, match="bad.py"):
        unread_fields({"bad.py": "def ("}, ["alpha"])
