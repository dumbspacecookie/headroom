"""`config.py`'s docstring promises its defaults agree with `ASSUMPTIONS.md`. This file is that
promise, kept: it reads the knob tables out of `ASSUMPTIONS.md` (never hand-copies a number into an
assertion) and compares each one, converted to the field's unit, against `config.DEFAULTS`.
HEADROOM-1003-C2.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

import config
from config import ct

_TOL = 1e-9


# ---------------------------------------------------------------------------
# AC-1: knob_defaults(text) -> dict[str, str]
# ---------------------------------------------------------------------------

def _cells(line: str) -> list[str]:
    """Split one markdown table row into stripped cells, dropping the empty cells created by the
    row's leading and trailing `|`."""
    parts = line.strip().split("|")
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _table_blocks(text: str) -> list[list[str]]:
    """Every maximal run of contiguous `|`-prefixed lines, as a list of raw lines."""
    lines = text.splitlines()
    blocks: list[list[str]] = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        start = i
        while i < n and lines[i].strip().startswith("|"):
            i += 1
        blocks.append(lines[start:i])
    return blocks


def knob_defaults(text: str) -> dict[str, str]:
    """Knob -> Default for every markdown table in `text` whose header's first two cells are
    exactly "Knob" and "Default". The `|---|` separator row is not a data row. A table headed
    anything else is ignored entirely."""
    result: dict[str, str] = {}
    for block in _table_blocks(text):
        header = _cells(block[0])
        if len(header) < 2 or header[0] != "Knob" or header[1] != "Default":
            continue
        for row_line in block[1:]:
            cells = _cells(row_line)
            if _is_separator_row(cells):
                continue
            if len(cells) >= 2:
                result[cells[0]] = cells[1]
    return result


# ---------------------------------------------------------------------------
# AC-2..AC-5: mismatches(text, cfg) -> list[str]
# ---------------------------------------------------------------------------

def _all_data_rows(text: str) -> list[list[str]]:
    """Every row of every markdown table in `text` (any table, not just Knob/Default ones),
    excluding each table's own header row and separator row."""
    rows: list[list[str]] = []
    for block in _table_blocks(text):
        for row_line in block[1:]:
            cells = _cells(row_line)
            if _is_separator_row(cells):
                continue
            if cells:
                rows.append(cells)
    return rows


def _find_row(text: str, knob_substring: str) -> list[str] | None:
    """The one row whose first (Knob) cell contains `knob_substring`, or `None` if zero or more
    than one row match - callers turn `None` into a mismatch, never a silent skip."""
    matches = [row for row in _all_data_rows(text) if len(row) >= 2 and knob_substring in row[0]]
    if len(matches) != 1:
        return None
    return matches[0]


_NUM = r"[-+]?\d+(?:\.\d+)?"


def _first_float(s: str) -> float:
    m = re.search(_NUM, s)
    if not m:
        raise ValueError(f"no number in {s!r}")
    return float(m.group(0))


def _percent(s: str) -> float:
    m = re.search(rf"({_NUM})\s*%", s)
    if not m:
        raise ValueError(f"no percent in {s!r}")
    return float(m.group(1)) / 100.0


def _kw(s: str) -> float:
    m = re.search(rf"({_NUM})\s*kW\b", s)
    if not m:
        raise ValueError(f"no kW value in {s!r}")
    return float(m.group(1))


def _watts_to_kw(s: str) -> float:
    m = re.search(rf"({_NUM})\s*W\b", s)
    if not m:
        raise ValueError(f"no W value in {s!r}")
    return float(m.group(1)) / 1000.0


def _seconds(s: str) -> float:
    m = re.search(rf"({_NUM})\s*s\b", s)
    if not m:
        raise ValueError(f"no seconds value in {s!r}")
    return float(m.group(1))


def _minutes_to_s(s: str) -> float:
    m = re.search(rf"({_NUM})\s*min\b", s)
    if not m:
        raise ValueError(f"no minutes value in {s!r}")
    return float(m.group(1)) * 60.0


def _hours(s: str) -> float:
    m = re.search(rf"({_NUM})\s*h\b", s)
    if not m:
        raise ValueError(f"no hours value in {s!r}")
    return float(m.group(1))


def _hhmm_to_s(s: str) -> float:
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError(f"no HH:MM value in {s!r}")
    return ct(int(m.group(1)), int(m.group(2)))


def _backtick(s: str) -> str:
    m = re.search(r"`([^`]+)`", s)
    if not m:
        raise ValueError(f"no backtick text in {s!r}")
    return m.group(1)


def _bool_word(s: str) -> bool:
    t = s.strip().strip("*").strip().lower()
    if t not in ("true", "false"):
        raise ValueError(f"no true/false in {s!r}")
    return t == "true"


def _parse_unit_mix(knob_cell: str, default_cell: str) -> dict[str, object]:
    m = re.search(rf"\(\s*({_NUM})\s*/\s*({_NUM})\s*kWh\s*\)", knob_cell)
    if not m:
        raise ValueError(f"no (small / large kWh) in {knob_cell!r}")
    small_kwh, large_kwh = float(m.group(1)), float(m.group(2))
    m2 = re.search(rf"({_NUM})\s*/\s*({_NUM})", default_cell)
    if not m2:
        raise ValueError(f"no split in {default_cell!r}")
    a, b = float(m2.group(1)), float(m2.group(2))
    return {
        "unit_small_kwh": small_kwh,
        "unit_large_kwh": large_kwh,
        "small_share": a / (a + b),
    }


def _parse_hysteresis(knob_cell: str, default_cell: str) -> dict[str, object]:
    return {"hysteresis_frac": _percent(default_cell), "hysteresis_hold_s": _seconds(default_cell)}


def _parse_physics_dt(knob_cell: str, default_cell: str) -> dict[str, object]:
    nums = re.findall(rf"({_NUM})\s*s\b", default_cell)
    if len(nums) < 2:
        raise ValueError(f"expected two second values in {default_cell!r}")
    return {"dt_phys_demo_s": float(nums[0]), "dt_phys_batch_s": float(nums[1])}


def _parse_pi_gains(knob_cell: str, default_cell: str) -> dict[str, object]:
    kp = re.search(rf"Kp\s*({_NUM})", default_cell)
    ki = re.search(rf"Ki\s*({_NUM})", default_cell)
    if not kp or not ki:
        raise ValueError(f"no Kp/Ki in {default_cell!r}")
    return {"pi_kp": float(kp.group(1)), "pi_ki": float(ki.group(1))}


def _parse_message_sizes(knob_cell: str, default_cell: str) -> dict[str, object]:
    nums = re.findall(rf"(\d+)\s*B\b", default_cell)
    if len(nums) < 2:
        raise ValueError(f"expected two byte values in {default_cell!r}")
    return {"msg_telemetry_bytes": int(nums[0]), "msg_ack_bytes": int(nums[1])}


def _parse_ladder(knob_cell: str, default_cell: str) -> dict[str, object]:
    names = [p.strip() for p in default_cell.split("›")]
    names = ["UTILITY_TOLL" if n == "TOLL" else n for n in names]
    return {"ladder": tuple(names)}


def _parse_evening_window(knob_cell: str, default_cell: str) -> dict[str, object]:
    pairs = re.findall(r"(\d{1,2}):(\d{2})", default_cell)
    if len(pairs) < 2:
        raise ValueError(f"expected two HH:MM values in {default_cell!r}")
    (h0, m0), (h1, m1) = pairs[0], pairs[1]
    return {"t0": ct(int(h0), int(m0)), "horizon_ts": ct(int(h1), int(m1))}


# (knob-cell substring, Config field names it governs, parser(knob_cell, default_cell) -> dict)
_ROW_SPECS: list[tuple[str, list[str], object]] = [
    ("Unit mix", ["unit_small_kwh", "unit_large_kwh", "small_share"], _parse_unit_mix),
    ("Unit power", ["unit_small_kw"], lambda k, d: {"unit_small_kw": _kw(d)}),
    ("Backup floor", ["floor_frac"], lambda k, d: {"floor_frac": _percent(d)}),
    ("Aux / standby draw", ["aux_kw"], lambda k, d: {"aux_kw": _watts_to_kw(d)}),
    ("Home load p95", ["home_load_p95_kw"], lambda k, d: {"home_load_p95_kw": _kw(d)}),
    ("Conversion efficiency", ["eta"], lambda k, d: {"eta": _first_float(d)}),
    ("band padding", ["eps_frac"], lambda k, d: {"eps_frac": _percent(d)}),
    ("Reachability factor", ["reach_k"], lambda k, d: {"reach_k": _first_float(d)}),
    (
        "assume_house_when_grid_unknown",
        ["assume_house_when_grid_unknown"],
        lambda k, d: {"assume_house_when_grid_unknown": _bool_word(d)},
    ),
    ("Haircut mode", ["haircut"], lambda k, d: {"haircut": _backtick(d)}),
    ("outage duration", ["outage_h"], lambda k, d: {"outage_h": _hours(d)}),
    ("Bucket size", ["bucket_s"], lambda k, d: {"bucket_s": _minutes_to_s(d)}),
    ("Priority ladder", ["ladder"], _parse_ladder),
    ("Upgrade hysteresis", ["hysteresis_frac", "hysteresis_hold_s"], _parse_hysteresis),
    ("Delivery tolerance", ["tolerance_frac"], lambda k, d: {"tolerance_frac": _percent(d)}),
    ("Feeder export cap", ["feeder_cap_frac"], lambda k, d: {"feeder_cap_frac": _percent(d)}),
    ("dt_ctrl", ["dt_ctrl_s"], lambda k, d: {"dt_ctrl_s": _seconds(d)}),
    ("Physics", ["dt_phys_demo_s", "dt_phys_batch_s"], _parse_physics_dt),
    ("send threshold", ["delta_kw"], lambda k, d: {"delta_kw": _kw(d)}),
    ("PI gains", ["pi_kp", "pi_ki"], _parse_pi_gains),
    ("Buffer flush rate", ["flush_msgs_per_s"], lambda k, d: {"flush_msgs_per_s": _first_float(d)}),
    ("Lease TTL", ["lease_s"], lambda k, d: {"lease_s": _seconds(d)}),
    ("Message sizes", ["msg_telemetry_bytes", "msg_ack_bytes"], _parse_message_sizes),
    (
        "Lease delivery",
        ["lease_piggyback"],
        lambda k, d: {"lease_piggyback": d.strip().lower().startswith("piggyback")},
    ),
    (
        "Devices in aggregation",
        ["n_devices_a1"],
        lambda k, d: {"n_devices_a1": int(_first_float(d))},
    ),
    ("Regions", ["n_regions_a1"], lambda k, d: {"n_regions_a1": int(_first_float(d))}),
    ("Evening window", ["t0", "horizon_ts"], _parse_evening_window),
    ("Start SoC", ["start_soc_frac"], lambda k, d: {"start_soc_frac": _percent(d)}),
    ("Co-op shave duration", ["coop_shave_h"], lambda k, d: {"coop_shave_h": _hours(d)}),
    ("Crash duration", ["crash_duration_s"], lambda k, d: {"crash_duration_s": _seconds(d)}),
]


def _values_match(expected: object, actual: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= _TOL
    return expected == actual


def mismatches(text: str, cfg: config.Config) -> list[str]:
    """One string per disagreement between the 30 named rows of `text` and `cfg`, naming the
    Config field. A row that cannot be found, or that is ambiguous (the substring selects more than
    one row), is reported as a mismatch for every field it governs - never silently skipped."""
    out: list[str] = []
    for knob_substring, fields, parser in _ROW_SPECS:
        row = _find_row(text, knob_substring)
        if row is None:
            for f in fields:
                out.append(
                    f"{f}: no single row matching {knob_substring!r} found in ASSUMPTIONS.md"
                )
            continue
        knob_cell, default_cell = row[0], row[1]
        try:
            parsed = parser(knob_cell, default_cell)
        except ValueError as exc:
            for f in fields:
                out.append(f"{f}: could not parse row {knob_substring!r}: {exc}")
            continue
        for f, expected in parsed.items():
            actual = getattr(cfg, f)
            if not _values_match(expected, actual):
                out.append(
                    f"{f}: ASSUMPTIONS.md ({knob_substring!r} row) says {expected!r}, "
                    f"config.DEFAULTS has {actual!r}"
                )
    return out


def _real_text() -> str:
    root = Path(__file__).resolve().parent.parent.parent
    return (root / "ASSUMPTIONS.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# REQ: HEADROOM-1003-C2/AC-1
def test_knob_defaults_on_the_real_assumptions_file_has_36_entries():
    assert len(knob_defaults(_real_text())) == 36


# REQ: HEADROOM-1003-C2/AC-1
def test_knob_defaults_ignores_a_table_with_a_different_header():
    text = (
        "| Knob | Default |\n"
        "|---|---|\n"
        "| A | 1 |\n"
        "\n"
        "| X | Y |\n"
        "|---|---|\n"
        "| B | 2 |\n"
    )
    assert knob_defaults(text) == {"A": "1"}


# REQ: HEADROOM-1003-C2/AC-2
def test_mismatches_is_empty_against_the_real_assumptions_file():
    assert mismatches(_real_text(), config.DEFAULTS) == []


# REQ: HEADROOM-1003-C2/AC-3
@pytest.mark.parametrize("field, value", [
    ("floor_frac", 0.25),
    ("aux_kw", 0.06),
    ("bucket_s", 600.0),
    ("lease_piggyback", False),
    ("horizon_ts", ct(22, 0)),
])
def test_a_single_moved_config_default_is_caught(field, value):
    cfg = dataclasses.replace(config.DEFAULTS, **{field: value})
    found = mismatches(_real_text(), cfg)
    assert len(found) == 1
    assert field in found[0]


# REQ: HEADROOM-1003-C2/AC-3
def test_a_reversed_ladder_is_caught():
    cfg = dataclasses.replace(config.DEFAULTS, ladder=tuple(reversed(config.DEFAULTS.ladder)))
    found = mismatches(_real_text(), cfg)
    assert len(found) == 1
    assert "ladder" in found[0]


# REQ: HEADROOM-1003-C2/AC-4
@pytest.mark.parametrize("old, new, field", [
    ("| 20% |", "| 25% |", "floor_frac"),
    ("60 s, piggybacked", "90 s, piggybacked", "lease_s"),
    ("| 90 s |", "| 120 s |", "crash_duration_s"),
])
def test_a_single_moved_document_value_is_caught(old, new, field):
    text = _real_text()
    assert old in text
    changed = text.replace(old, new, 1)
    assert changed != text
    found = mismatches(changed, config.DEFAULTS)
    assert len(found) == 1
    assert field in found[0]


# REQ: HEADROOM-1003-C2/AC-5
def test_a_missing_row_is_a_mismatch_not_a_skip():
    text = _real_text()
    line = next(l for l in text.splitlines() if l.strip().startswith("| Backup floor |"))
    changed = text.replace(line + "\n", "", 1)
    assert "Backup floor" not in changed
    found = mismatches(changed, config.DEFAULTS)
    assert any("floor_frac" in m for m in found)


# REQ: HEADROOM-1003-C2/AC-5
def test_an_ambiguous_row_is_a_mismatch_not_a_skip():
    text = _real_text()
    line = next(l for l in text.splitlines() if l.strip().startswith("| Backup floor |"))
    dup_row = "| Backup floor (alt) | 20% | 10-50% | UI | synthetic duplicate for the test. |"
    changed = text.replace(line, line + "\n" + dup_row, 1)
    assert changed != text
    found = mismatches(changed, config.DEFAULTS)
    assert any("floor_frac" in m for m in found)
