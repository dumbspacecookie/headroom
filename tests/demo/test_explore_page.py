"""web/explore.html: static, current, and replaying the same evenings the sweep counted.

The explorer is a second surface over the recorded sweeps, so it inherits the demo page's two
failure modes - a network request that dies on a conference laptop, and a bake older than the
results it draws - and adds a third: a replay that disagrees with the sweep that scored it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "explore.html"
DATA = ROOT / "web" / "out" / "explore.js"


@pytest.fixture(scope="module")
def data():
    if not DATA.exists():
        pytest.fail("web/out/explore.js is missing. Run: python run.py explore")
    text = DATA.read_text(encoding="utf-8")
    return json.loads(text[text.index("=") + 1:].rstrip().rstrip(";"))


def test_the_explorer_makes_no_network_request():
    body = re.sub(r"<!--.*?-->", "", PAGE.read_text(encoding="utf-8"), flags=re.S)
    for pattern in ("http://", "https://", "//fonts.", "cdn.", "@import", "fetch("):
        assert pattern not in body, f"{pattern!r} in explore.html - that is a network request"
    assert '<script src="out/explore.js">' in body


def test_the_bake_is_not_older_than_the_sweeps(data):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bake_explore", ROOT / "prep" / "bake_explore.py")
    bake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bake)
    fresh = [bake.world_summary(*w) for w in bake.WORLDS]
    assert [w["rules"] for w in data["worlds"]] == [w["rules"] for w in fresh], (
        "web/out/explore.js draws numbers the sweeps no longer say. Run: python run.py explore")


def test_every_replayed_evening_misses_exactly_what_the_sweep_counted(data):
    """The replay is re-run from the seed; the sweep scored the same seed. They must agree, minute
    for minute - or the page is a drawing of a result, not the result."""
    rows = {r["seed"]: r for r in json.loads(
        (ROOT / "prep" / "out" / "compare_results.json").read_text(encoding="utf-8"))["rows"]}
    checked = 0
    for e in data["evenings"]:
        if e["kind"] == "demo":
            continue
        for rule, run in e["runs"].items():
            row = rows[e["seed"]].get(rule)
            if row is None:
                continue
            # the sweep counts misses; a minute can hold two (energy and capacity), so the
            # replay's own totals are compared, and its minutes may only be fewer
            assert run["m"]["silent_breach_buckets"] == row["silent"], (e["seed"], rule, "silent")
            assert run["m"]["late_breach_buckets"] == row["late"], (e["seed"], rule, "late")
            assert len(run["silentAt"]) <= row["silent"] and len(run["lateAt"]) <= row["late"]
            assert (len(run["silentAt"]) > 0) == (row["silent"] > 0), (e["seed"], rule)
            checked += 1
    # headroom, no reserve and flat 20% are in the regional sweep; P90 has its own worlds
    want = 3 * sum(1 for e in data["evenings"] if e["kind"] != "demo")
    assert checked >= want, f"only {checked} of {want} replays could be checked against the sweep"


def test_the_late_quest_states_what_the_replays_show(data):
    q = next(q for q in data["quests"] if q["id"] == "late")
    late = [e for e in data["evenings"] if e["kind"] == "late"]
    fewest = min((bin(e["runs"]["headroom"]["dark"][i]).count("1")
                  for e in late for i in e["runs"]["headroom"]["lateAt"]), default=0)
    if "Two regions dark" in q["title"]:
        assert fewest >= 2, "the quest says two regions were dark; a replay says otherwise"
