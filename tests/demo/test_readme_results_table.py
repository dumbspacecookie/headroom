"""HEADROOM-0929-S2: pin README's Results table to the committed sweep artifacts.

The Results table (README.md lines ~58-66) is typed prose next to numbers produced by
`runner/compare.py`. Re-running the sweep and forgetting to update the table currently fails
silently - nothing reads the two together. This module parses the table straight out of
README.md text (never hardcoding the expected numbers) and checks every numeric cell against the
`aggregate` block of the committed `prep/out/compare_results*.json` files that produced it.

This test makes NO sweep calls (no run_scenario, no runner.batch, no runner.compare.main/run_seed)
and no network access (AC-6): it opens exactly four files - README.md and the three
prep/out/compare_results*.json named in the spec's AC-2 - and writes nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README_PATH = ROOT / "README.md"

# The three committed artifacts named in AC-2 - the only files besides README.md this module
# reads (AC-6). Paths are relative, for readable failure messages.
ARTIFACT_RELPATHS: dict[str, str] = {
    "regional": "prep/out/compare_results.json",
    "scattered": "prep/out/compare_results_scattered.json",
    "fragmented": "prep/out/compare_results_fragmented.json",
}

# Column -> (world the source file covers, field name in that file's `aggregate[subject]`).
# Field names match runner.compare.summarise's own keys, so the mapping tracks the code
# (Approved design) without ever importing or calling it.
HELD_BACK_FIELD = "held_back_pct_median"
SILENT_FIELD = "seeds_with_any_silent"
LATE_FIELD = "seeds_with_any_late"
FLOOR_FIELD = "seeds_with_floor_breach"

# AC-11 (owner answer): the explicit README row -> compare-subject mapping. Order does not
# matter for correctness here (the patterns do not overlap), but specific-before-generic keeps
# it readable. A row that matches none of these FAILS the test, naming the row.
ROW_MAP: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"stage 2 off"), "headroom_no_stage2"),
    (re.compile(r"no N-1 reserve"), "headroom_no_n1"),
    (re.compile(r"flat 12% de-rate"), "reasonable_plus_d12"),
    (re.compile(r"flat 15% de-rate"), "reasonable_plus_d15"),
    (re.compile(r"flat 20% de-rate"), "reasonable_plus_d20"),
    (re.compile(r"first-draft baseline"), "reasonable"),
    (re.compile(r"^\**headroom\**$"), "headroom"),
)

# HEADROOM-0930-F1/AC-1: the 7 subjects ROW_MAP maps to, in the order given in the spec. Used by
# check_table_complete to detect a deleted or duplicated row - independent of ROW_MAP's own
# iteration order (which is specific-before-generic, for matching, not enumeration).
ALL_SUBJECTS: tuple[str, ...] = (
    "headroom",
    "headroom_no_stage2",
    "headroom_no_n1",
    "reasonable_plus_d12",
    "reasonable_plus_d15",
    "reasonable_plus_d20",
    "reasonable",
)

# A dash cell means "no source expected" (AC-4): both the plain hyphen-minus and the en dash the
# README actually uses (`1000 / – / –`).
DASH_VALUES = {"-", "–"}

HELD_BACK_TOL = 0.05   # percentage points (AC-2: half the displayed digit)
COUNT_TOL = 0          # counts must match exactly (AC-2)


class TableCheckError(Exception):
    """A single cell or row the checker cannot parse or map. Always surfaced as a failure
    (never swallowed), so an unparseable/unmapped README row still fails loudly (AC-3, AC-11)."""


# --------------------------------------------------------------------------- parsing (AC-1)
def extract_results_table(readme_text: str) -> str:
    """Pull just the markdown table out of the README text, by its header row.

    Deliberately dumb: find the header, take every contiguous `|`-prefixed line after it. No
    hardcoded row/column content, only the table's own markdown structure.
    """
    lines = readme_text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("| controller |")), None)
    if start is None:
        raise TableCheckError("README.md has no '| controller |' results table header")
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    return "\n".join(lines[start:end])


def parse_table(table_text: str) -> list[tuple[str, list[str]]]:
    """-> [(row_label, [held_back_cell, silent_cell, late_cell, floor_cell]), ...].

    Cell text is returned as-is (still markdown, e.g. `**headroom**`); numeric parsing happens
    later, per column, so every value asserted on comes from the README text itself.
    """
    lines = [l for l in table_text.splitlines() if l.strip().startswith("|")]
    if len(lines) < 3:
        raise TableCheckError(
            f"expected a header, a separator and at least one data row in the results table, "
            f"got {len(lines)} pipe-prefixed line(s)")
    rows = []
    for line in lines[2:]:                       # skip header + `|---|---|...` separator
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append((cells[0], cells[1:]))
    return rows


def map_row(row_label: str) -> str:
    """README row label -> runner.compare subject key. Raises if nothing matches (AC-11)."""
    stripped = row_label.strip()
    for pattern, subject in ROW_MAP:
        if pattern.search(stripped):
            return subject
    raise TableCheckError(
        f"README results table row {row_label!r} has no entry in the row mapping (AC-11); "
        f"add it to ROW_MAP in tests/demo/test_readme_results_table.py or fix the row")


def check_table_complete(table_text: str) -> list[str]:
    """HEADROOM-0930-F1/AC-1: every one of the 7 ALL_SUBJECTS has exactly one row in
    `table_text`. Uses only the table text and ROW_MAP - no artifact I/O, so a deleted or
    duplicated row is caught even before any numeric cell is checked (and even for a table with
    no artifacts loaded at all).

    A subject no row maps to (a deleted row) is reported by subject name. A subject two (or more)
    rows map to is reported naming both (all) of the matching rows, distinguished by their
    position in the table so identical row text still yields a message that points at two
    occurrences, not one. Rows that map to nothing in ROW_MAP are silently skipped here - that
    failure mode is check_readme_table's map_row (AC-11), not this completeness check.

    AC-1 says this function "returns a list of failures" - it never raises. A table_text that
    parse_table itself cannot parse (e.g. only a header and separator, with every data row
    deleted - the most extreme case of the exact scenario this function exists to catch) is
    treated the same as a table with zero data rows: every one of the 7 ALL_SUBJECTS is reported
    missing, rather than letting parse_table's TableCheckError escape.
    """
    try:
        parsed_rows = parse_table(table_text)
    except TableCheckError:
        parsed_rows = []

    rows_by_subject: dict[str, list[tuple[int, str]]] = {subject: [] for subject in ALL_SUBJECTS}
    for index, (row_label, _cells) in enumerate(parsed_rows, start=1):
        try:
            subject = map_row(row_label)
        except TableCheckError:
            continue
        rows_by_subject.setdefault(subject, []).append((index, row_label))

    failures: list[str] = []
    for subject in ALL_SUBJECTS:
        matches = rows_by_subject.get(subject, [])
        if not matches:
            failures.append(
                f"no README results-table row maps to subject {subject!r} - the row appears to "
                f"have been deleted")
        elif len(matches) > 1:
            named = ", ".join(f"row #{i} {label!r}" for i, label in matches)
            failures.append(
                f"subject {subject!r} is mapped by {len(matches)} README rows: {named}")
    return failures


def is_dash(cell: str) -> bool:
    return cell.strip() in DASH_VALUES


def parse_pct(cell: str) -> float:
    m = re.fullmatch(r"([-−]?\d+(?:\.\d+)?)%", cell.strip())
    if not m:
        raise TableCheckError(f"cannot parse {cell!r} as a percentage cell")
    return float(m.group(1).replace("−", "-"))          # README uses U+2212 for negatives


def parse_int(cell: str) -> int:
    m = re.fullmatch(r"\d+", cell.strip())
    if not m:
        raise TableCheckError(f"cannot parse {cell!r} as an integer cell")
    return int(m.group(0))


def parse_triple(cell: str) -> list[str]:
    parts = [p.strip() for p in cell.split("/")]
    if len(parts) != 3:
        raise TableCheckError(
            f"cannot parse {cell!r} as a 'regional / scattered / fragmented' triple")
    return parts


# --------------------------------------------------------------------------- checking (AC-2..AC-6)
def _check_scalar(row_label: str, col_name: str, cell: str, *, world: str, field: str,
                   subject: str, data: dict[str, dict], parse, tol: float) -> list[str]:
    """Check one numeric cell against one artifact field. Returns failure message(s), never
    raises: a missing artifact/field is reported the same way a value drift is - as a test
    failure naming the cell (AC-3, AC-5)."""
    path = ARTIFACT_RELPATHS[world]
    if is_dash(cell):
        # HEADROOM-0930-F1/AC-2: a dash passes only if the artifact was actually loaded AND
        # genuinely has no value for this subject/field (subject absent from `aggregate`, or the
        # field absent from the subject). If the artifact was never loaded at all (`world` absent
        # from `data`), that is not a verified absence - it is unknown - so the dash cannot be
        # excused; fail the same way the non-dash branch below does for a missing payload. If the
        # artifact holds a value - including 0 - the dash is also wrong: fail, naming the row, the
        # column and the artifact's value.
        if world not in data:
            return [f"README row {row_label!r} column {col_name!r}: cell is a dash but no data "
                    f"was loaded for {path!r} - a dash can only be excused by a loaded artifact "
                    f"that verifiably lacks a value, not by an artifact that was never loaded"]
        payload = data[world]
        subj = (payload or {}).get("aggregate", {}).get(subject)
        if subj is not None and field in subj:
            return [f"README row {row_label!r} column {col_name!r}: cell is a dash but "
                    f"{path} aggregate[{subject!r}][{field!r}] = {subj[field]!r} - the "
                    f"artifact holds a value for this cell"]
        return []
    try:
        readme_val = parse(cell)
    except TableCheckError as e:
        return [f"README row {row_label!r} column {col_name!r}: {e}"]

    payload = data.get(world)
    if payload is None:
        return [f"README row {row_label!r} column {col_name!r}: no data loaded for {path!r}"]
    agg = payload.get("aggregate", {})
    if subject not in agg:
        return [f"README row {row_label!r} column {col_name!r}: {path} has no "
                f"aggregate[{subject!r}] - missing artifact subject for this cell's source of "
                f"truth"]
    subj = agg[subject]
    if field not in subj:
        return [f"README row {row_label!r} column {col_name!r}: {path} aggregate[{subject!r}] "
                f"has no field {field!r} - missing artifact field for this cell's source of "
                f"truth"]
    artifact_val = subj[field]
    if abs(readme_val - artifact_val) > tol + 1e-9:
        return [f"README row {row_label!r} column {col_name!r}: README says {readme_val}, "
                f"{path} aggregate[{subject!r}][{field!r}] says {artifact_val} "
                f"(|diff| {abs(readme_val - artifact_val):.4g} > tolerance {tol})"]
    return []


def check_readme_table(table_text: str, data: dict[str, dict]) -> list[str]:
    """Parse `table_text` and check every numeric cell against `data` (world -> loaded JSON).

    Pure function of its two arguments - no file I/O - so the mutation test (AC-12) and the
    missing-source test (AC-3) can feed it doctored input without ever touching README.md or the
    committed artifacts on disk.
    """
    failures: list[str] = []
    for row_label, cells in parse_table(table_text):
        try:
            subject = map_row(row_label)
        except TableCheckError as e:
            failures.append(str(e))
            continue
        if len(cells) != 4:
            failures.append(
                f"README row {row_label!r} has {len(cells)} data cell(s), expected 4")
            continue
        held_cell, silent_cell, late_cell, floor_cell = cells

        failures.extend(_check_scalar(
            row_label, "held back (median, regional)", held_cell,
            world="regional", field=HELD_BACK_FIELD, subject=subject, data=data,
            parse=parse_pct, tol=HELD_BACK_TOL))

        try:
            regional_c, scattered_c, fragmented_c = parse_triple(silent_cell)
        except TableCheckError as e:
            failures.append(
                f"README row {row_label!r} column 'evenings with a silent miss': {e}")
        else:
            for world, cell in (("regional", regional_c), ("scattered", scattered_c),
                                 ("fragmented", fragmented_c)):
                failures.extend(_check_scalar(
                    row_label, f"evenings with a silent miss: {world}", cell,
                    world=world, field=SILENT_FIELD, subject=subject, data=data,
                    parse=parse_int, tol=COUNT_TOL))

        failures.extend(_check_scalar(
            row_label, "evenings with a late miss (regional)", late_cell,
            world="regional", field=LATE_FIELD, subject=subject, data=data,
            parse=parse_int, tol=COUNT_TOL))

        failures.extend(_check_scalar(
            row_label, "evenings below the 20% floor (regional)", floor_cell,
            world="regional", field=FLOOR_FIELD, subject=subject, data=data,
            parse=parse_int, tol=COUNT_TOL))
    return failures


def _load_artifacts() -> dict[str, dict]:
    """The only I/O in this module besides reading README.md (AC-6)."""
    return {world: json.loads((ROOT / relpath).read_text(encoding="utf-8"))
            for world, relpath in ARTIFACT_RELPATHS.items()}


# --------------------------------------------------------------------------- the real test
def test_readme_results_table_matches_committed_sweep_artifacts():
    """The README Results table equals its committed sweep artifacts, within tolerance.

    REQ: HEADROOM-0929-S2/AC-1
    REQ: HEADROOM-0929-S2/AC-2
    REQ: HEADROOM-0929-S2/AC-4
    REQ: HEADROOM-0929-S2/AC-5
    REQ: HEADROOM-0929-S2/AC-6
    REQ: HEADROOM-0929-S2/AC-7
    REQ: HEADROOM-0929-S2/AC-9
    REQ: HEADROOM-0929-S2/AC-11
    REQ: HEADROOM-0930-F1/AC-1

    Parses README.md's table as text (AC-1), maps each row to a compare subject (AC-11) and each
    column to a committed artifact field (AC-2), skips dash cells (AC-4), and fails naming the
    row/column of any drift or any missing source (AC-3/AC-5). Reads only README.md and the
    three prep/out/compare_results*.json files, calls no sweep code, touches the network never
    (AC-6); pure parse-and-compare of already-committed files, so re-running it on an unchanged
    tree always gives the same answer (AC-7). Lives in tests/demo/, the existing lens 12
    directory, so it changes no lens count (AC-9). Also asserts (HEADROOM-0930-F1/AC-1) that the
    real table has no deleted or duplicated mapped row.
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    table_text = extract_results_table(readme_text)
    data = _load_artifacts()

    failures = check_readme_table(table_text, data)
    assert not failures, (
        "README.md's Results table has drifted from the committed sweep artifacts (or a cell's "
        "source is missing):\n- " + "\n- ".join(failures))

    completeness_failures = check_table_complete(table_text)
    assert not completeness_failures, (
        "README.md's Results table is missing or duplicates a mapped row:\n- "
        + "\n- ".join(completeness_failures))


# --------------------------------------------------------------------------- the guard can fail
def test_readme_results_table_checker_catches_a_drifted_held_back_and_a_drifted_count():
    """REQ: HEADROOM-0929-S2/AC-12

    Feeds the checker an in-memory copy of the real table with one held-back cell nudged by 0.1
    (over the +/-0.05 tolerance) and one count nudged by 1, and asserts both are reported. Never
    writes to README.md - `table_text` here is a local string, not the file on disk (AC-10).
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    table_text = extract_results_table(readme_text)
    data = _load_artifacts()

    # Sanity: the unmutated table has no drift, so any failures below come from the mutation.
    assert not check_readme_table(table_text, data)

    mutated = table_text.replace(
        "| **headroom** | 6.8% | 1 / 3 / 0 | 7 | 0 |",
        "| **headroom** | 6.9% | 1 / 3 / 0 | 7 | 0 |",     # held-back nudged by 0.1pp
        1,
    ).replace(
        "| headroom, stage 2 off (aggregate N-1 only) | 5.4% | 56 / 38 / 0 | 69 | 0 |",
        "| headroom, stage 2 off (aggregate N-1 only) | 5.4% | 56 / 38 / 0 | 70 | 0 |",  # +1
        1,
    )
    assert mutated != table_text, "the string replacements above did not match the real table"

    failures = check_readme_table(mutated, data)
    assert any("'headroom'" in f and "held back (median, regional)" in f for f in failures), (
        f"the checker did not flag the drifted held-back cell: {failures}")
    assert any("stage 2 off" in f and "late miss" in f for f in failures), (
        f"the checker did not flag the drifted late-miss count: {failures}")
    assert len(failures) == 2, f"expected exactly the two mutated cells to drift, got: {failures}"


# --------------------------------------------------------------------------- missing source
def test_readme_results_table_checker_fails_loudly_on_a_missing_artifact_subject():
    """REQ: HEADROOM-0929-S2/AC-3

    Every real README cell already maps to a present artifact field (AC-2's 35/35 hand-check), so
    this exercises the missing-source path directly: a table with a real row but an artifact
    payload missing that row's subject entirely. The checker must FAIL naming the artifact path
    and the subject - not skip, not pass.
    """
    table_text = (
        "| controller | held back (median, regional) | "
        "evenings with a silent miss: regional / scattered / fragmented | "
        "evenings with a late miss (regional) | evenings below the 20% floor (regional) |\n"
        "|---|---|---|---|---|\n"
        "| **headroom** | 6.8% | 1 / 3 / 0 | 7 | 0 |\n"
    )
    data = {
        "regional": {"aggregate": {}},        # 'headroom' subject absent entirely
        "scattered": {"aggregate": {"headroom": {SILENT_FIELD: 3}}},
        "fragmented": {"aggregate": {"headroom": {SILENT_FIELD: 0}}},
    }
    failures = check_readme_table(table_text, data)
    assert failures, "a missing artifact subject must fail loudly, not pass silently"
    named = [f for f in failures if ARTIFACT_RELPATHS["regional"] in f and "headroom" in f]
    assert named, (
        f"failure message must name the missing artifact path "
        f"({ARTIFACT_RELPATHS['regional']!r}) and subject ('headroom'): {failures}")


def test_readme_results_table_checker_fails_loudly_on_a_missing_artifact_field():
    """REQ: HEADROOM-0929-S2/AC-3

    Same as above, but the subject is present while the specific field the column maps to is
    not - the other way a source can be missing.
    """
    table_text = (
        "| controller | held back (median, regional) | "
        "evenings with a silent miss: regional / scattered / fragmented | "
        "evenings with a late miss (regional) | evenings below the 20% floor (regional) |\n"
        "|---|---|---|---|---|\n"
        "| **headroom** | 6.8% | 1 / 3 / 0 | 7 | 0 |\n"
    )
    data = {
        "regional": {"aggregate": {"headroom": {
            # held_back_pct_median deliberately absent
            SILENT_FIELD: 1, LATE_FIELD: 7, FLOOR_FIELD: 0,
        }}},
        "scattered": {"aggregate": {"headroom": {SILENT_FIELD: 3}}},
        "fragmented": {"aggregate": {"headroom": {SILENT_FIELD: 0}}},
    }
    failures = check_readme_table(table_text, data)
    assert failures, "a missing artifact field must fail loudly, not pass silently"
    named = [f for f in failures
             if ARTIFACT_RELPATHS["regional"] in f and HELD_BACK_FIELD in f]
    assert named, (
        f"failure message must name the missing field ({HELD_BACK_FIELD!r}) and its artifact "
        f"({ARTIFACT_RELPATHS['regional']!r}): {failures}")


# --------------------------------------------------------------------------- row mapping
def test_readme_results_table_checker_fails_loudly_on_an_unmapped_row():
    """REQ: HEADROOM-0929-S2/AC-11

    A row this checker's mapping does not recognise must fail, naming the row - it must not be
    silently ignored.
    """
    table_text = (
        "| controller | held back (median, regional) | "
        "evenings with a silent miss: regional / scattered / fragmented | "
        "evenings with a late miss (regional) | evenings below the 20% floor (regional) |\n"
        "|---|---|---|---|---|\n"
        "| some new controller nobody mapped yet | 1.0% | 0 / 0 / 0 | 0 | 0 |\n"
    )
    failures = check_readme_table(table_text, {})
    assert failures, "an unmapped row must fail loudly, not pass silently"
    assert any("some new controller nobody mapped yet" in f for f in failures), (
        f"failure message must name the unmapped row: {failures}")


# --------------------------------------------------------------------------- dash handling
def test_dash_cells_need_no_source_and_are_never_reported_missing():
    """REQ: HEADROOM-0929-S2/AC-4

    `reasonable` is absent from the scattered/fragmented artifacts entirely (it is not a
    shape-world subject) - exactly the case the dashes in its README row exist for. The checker
    must tolerate that instead of treating it as a missing source.
    """
    table_text = (
        "| controller | held back (median, regional) | "
        "evenings with a silent miss: regional / scattered / fragmented | "
        "evenings with a late miss (regional) | evenings below the 20% floor (regional) |\n"
        "|---|---|---|---|---|\n"
        "| first-draft baseline (`reasonable`) | −13.1% | 1000 / – / – | 0 | 0 |\n"
    )
    data = {
        "regional": {"aggregate": {"reasonable": {
            HELD_BACK_FIELD: -13.07, SILENT_FIELD: 1000, LATE_FIELD: 0, FLOOR_FIELD: 0,
        }}},
        "scattered": {"aggregate": {}},           # 'reasonable' genuinely absent here
        "fragmented": {"aggregate": {}},          # and here
    }
    assert check_readme_table(table_text, data) == []


# --------------------------------------------------------------------------- HEADROOM-0930-F1: completeness
def test_check_table_complete_catches_a_deleted_row():
    """REQ: HEADROOM-0930-F1/AC-3

    Deletes the real 'headroom, no N-1 reserve at all' row from an in-memory copy of the real
    table - README.md on disk is never edited - and asserts check_table_complete reports the
    now-orphaned subject, headroom_no_n1, by name.
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    table_text = extract_results_table(readme_text)
    assert not check_table_complete(table_text), "sanity: the real table should be complete"

    n1_row = "| headroom, no N-1 reserve at all | 0.6% | 61 / 65 / 2 | 103 | 0 |\n"
    assert n1_row in table_text, "the row text to delete did not match the real table"
    mutated = table_text.replace(n1_row, "", 1)
    assert mutated != table_text

    failures = check_table_complete(mutated)
    assert any("headroom_no_n1" in f for f in failures), (
        f"deleting the mapped row did not report subject headroom_no_n1: {failures}")


def test_check_table_complete_reports_all_seven_subjects_when_every_row_is_deleted():
    """REQ: HEADROOM-0930-F1/AC-1

    The degenerate, most extreme case of a "deleted row": every mapped data row is gone, leaving
    only the header and separator line. check_table_complete must still return a list of
    failures (one per ALL_SUBJECTS) rather than raise - parse_table alone would raise on a
    table_text with fewer than 3 pipe-prefixed lines, but AC-1 says this function "returns a
    list of failures", with no carve-out for the all-rows-deleted case.
    """
    header_only = (
        "| controller | held back (median, regional) | "
        "evenings with a silent miss: regional / scattered / fragmented | "
        "evenings with a late miss (regional) | evenings below the 20% floor (regional) |\n"
        "|---|---|---|---|---|\n"
    )
    failures = check_table_complete(header_only)
    for subject in ALL_SUBJECTS:
        assert any(subject in f for f in failures), (
            f"subject {subject!r} not reported missing for an all-rows-deleted table: {failures}")
    assert len(failures) == len(ALL_SUBJECTS), (
        f"expected exactly {len(ALL_SUBJECTS)} failures, got {len(failures)}: {failures}")


def test_check_table_complete_catches_a_duplicated_row():
    """REQ: HEADROOM-0930-F1/AC-3

    Duplicates the real '**headroom**' row in an in-memory copy of the real table and asserts
    check_table_complete reports the duplicate, naming both occurrences (the two rows are
    textually identical, so they are distinguished by their position in the table instead).
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    table_text = extract_results_table(readme_text)

    headroom_row = "| **headroom** | 6.8% | 1 / 3 / 0 | 7 | 0 |"
    lines = table_text.splitlines()
    idx = lines.index(headroom_row)
    mutated = "\n".join(lines[:idx + 1] + [headroom_row] + lines[idx + 1:])
    assert mutated.count(headroom_row) == 2, "the duplication above did not match the real row"

    failures = check_table_complete(mutated)
    duplicate_failures = [f for f in failures if "'headroom'" in f and "2 README rows" in f]
    assert duplicate_failures, f"the duplicated row was not reported: {failures}"
    assert duplicate_failures[0].count("row #") == 2, (
        f"the failure must name both occurrences of the duplicated row distinctly: "
        f"{duplicate_failures[0]}")


# --------------------------------------------------------------------------- HEADROOM-0930-F1: dash rule
def test_check_readme_table_catches_a_dash_where_the_artifact_holds_a_value():
    """REQ: HEADROOM-0930-F1/AC-2
    REQ: HEADROOM-0930-F1/AC-3

    Replaces the real '**headroom**' row's scattered silent-miss count - 3, a real, non-zero
    value in the committed scattered artifact - with a dash, in an in-memory copy of the real
    table (README.md is never edited). check_readme_table must report that cell: a dash where the
    artifact holds a value is wrong, even though dashes are otherwise allowed (AC-4).
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    table_text = extract_results_table(readme_text)
    data = _load_artifacts()
    assert not check_readme_table(table_text, data), "sanity: the real table has no drift"
    assert data["scattered"]["aggregate"]["headroom"][SILENT_FIELD] == 3, (
        "this test relies on the committed scattered artifact holding a non-zero value here")

    headroom_row = "| **headroom** | 6.8% | 1 / 3 / 0 | 7 | 0 |"
    mutated_row = "| **headroom** | 6.8% | 1 / – / 0 | 7 | 0 |"
    assert headroom_row in table_text, "the row text to mutate did not match the real table"
    mutated = table_text.replace(headroom_row, mutated_row, 1)
    assert mutated != table_text

    failures = check_readme_table(mutated, data)
    assert any("'headroom'" in f and "silent miss: scattered" in f and "= 3" in f
               for f in failures), (
        f"a dash replacing a real artifact value (3) was not reported: {failures}")


def test_check_readme_table_still_passes_the_real_reasonable_row_dashes():
    """REQ: HEADROOM-0930-F1/AC-3

    check_readme_table on the real, unmodified table returns no failures under the stricter
    dash rule (AC-2) too: the 'reasonable' row's two dashes (scattered/fragmented silent-miss
    counts) still pass, because 'reasonable' is genuinely absent from those artifacts'
    `aggregate` blocks, not because the checker is lax about dashes in general.
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    table_text = extract_results_table(readme_text)
    data = _load_artifacts()

    assert "reasonable" not in data["scattered"]["aggregate"], (
        "this test relies on 'reasonable' being genuinely absent from the scattered artifact")
    assert "reasonable" not in data["fragmented"]["aggregate"], (
        "this test relies on 'reasonable' being genuinely absent from the fragmented artifact")

    assert check_readme_table(table_text, data) == []


def test_check_readme_table_catches_a_dash_when_the_artifact_was_never_loaded():
    """REQ: HEADROOM-0930-F1/AC-2

    AC-2 excuses a dash only when "the artifact for that column has no value for that subject"
    - i.e. a *loaded* artifact whose `aggregate` verifiably lacks the subject/field. If the whole
    artifact/world is missing from `data` (e.g. a loader bug that failed to populate it), that is
    not a verified absence, so the dash must NOT be excused - the same way the equivalent non-dash
    cell already fails loudly with "no data loaded for" in that case.
    """
    table_text = (
        "| controller | held back (median, regional) | "
        "evenings with a silent miss: regional / scattered / fragmented | "
        "evenings with a late miss (regional) | evenings below the 20% floor (regional) |\n"
        "|---|---|---|---|---|\n"
        "| first-draft baseline (`reasonable`) | -13.1% | 1000 / - / - | 0 | 0 |\n"
    )
    data = {
        "regional": {"aggregate": {"reasonable": {
            HELD_BACK_FIELD: -13.1, SILENT_FIELD: 1000, LATE_FIELD: 0, FLOOR_FIELD: 0,
        }}},
        "fragmented": {"aggregate": {}},
        # 'scattered' deliberately absent from data - not even loaded, let alone checked.
    }
    failures = check_readme_table(table_text, data)
    assert any("silent miss: scattered" in f and "no data was loaded" in f for f in failures), (
        f"a dash cell for a world entirely absent from `data` must fail, not pass silently: "
        f"{failures}")
