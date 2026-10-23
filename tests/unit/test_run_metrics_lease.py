"""runner.run_scenario carries metrics.lease.lease_cost(cfg) in every run's metrics dict, and
web/out/runs.json + runs.js are re-baked once to publish it. HEADROOM-1014-S5."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

from config import DEFAULTS
from metrics.lease import lease_cost
from runner.run import run_scenario

ROOT = Path(__file__).resolve().parents[2]
RUNS_JSON = ROOT / "web" / "out" / "runs.json"


# REQ: HEADROOM-1014-S5/AC-1
def test_default_headroom_run_carries_lease_cost_and_unchanged_hash():
    result = run_scenario("headroom")
    assert result.metrics["msgs_per_device_day"] == 43200.0
    assert result.metrics["bytes_per_device_day"] == 2073600.0
    assert (result.metrics["msgs_per_device_day"],
            result.metrics["bytes_per_device_day"]) == (
        lease_cost(DEFAULTS)["msgs_per_device_day"],
        lease_cost(DEFAULTS)["bytes_per_device_day"])
    assert (result.events_sha256
            == "374c99b558a5815025497f980d329089f83952a34b6b42531d8324ede675292b")


# REQ: HEADROOM-1014-S5/AC-1
def test_headroom_run_without_piggyback_reports_its_own_lease_cost():
    cfg = dataclasses.replace(DEFAULTS, lease_piggyback=False)
    result = run_scenario("headroom", cfg=cfg)
    assert result.metrics["msgs_per_device_day"] == 47520.0
    assert result.metrics["bytes_per_device_day"] == 2211840.0


# REQ: HEADROOM-1014-S5/AC-2
def test_baked_runs_json_carries_lease_cost_for_every_run_and_matches_pinned_digest():
    data = json.loads(RUNS_JSON.read_text(encoding="utf-8"))
    d = dict(data)
    for name in ("base", "tower", "reasonable"):
        metrics = d["runs"][name]["metrics"]
        assert metrics["msgs_per_device_day"] == 43200.0
        assert metrics["bytes_per_device_day"] == 2073600.0

    # Drop the two lease-cost keys from every run plus `git_head`, mirroring AC-2's digest
    # recipe, and compare to the pinned literal the spec author computed against the real bake.
    d["runs"] = {
        name: {**run, "metrics": {k: v for k, v in run["metrics"].items()
                                  if k not in ("msgs_per_device_day", "bytes_per_device_day")}}
        for name, run in d["runs"].items()
    }
    d.pop("git_head", None)
    digest = hashlib.sha256(json.dumps(d, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    assert digest == "8a37ba94b50497f6"


# REQ: HEADROOM-1014-S5/AC-2
def test_baked_runs_js_wraps_runs_json():
    from prep.bake_runs import wrap

    runs_js = (ROOT / "web" / "out" / "runs.js").read_text(encoding="utf-8")
    runs_json_text = RUNS_JSON.read_text(encoding="utf-8")
    assert runs_js == wrap(runs_json_text)
