"""Pull EIA-930 hourly ERCOT data (demand, net generation, fuel mix) and build the wide frame.

Reproducible replacement for the 2026-09-15 inline pull. Three windows are named presets:
  july22  the S1 day (reproduces data/raw/2026-07-21_24_eia930_*.parquet)
  calm    the calm reference day 2026-05-29 CT
  fern    the Fern reference days 2026-01-25/26 CT

The API key is read from $EIA_API_KEY, or from a `KEY=value` file named by $HEADROOM_KEYS_FILE.
It is never printed, and it is scrubbed from
every URL and exception this script emits.

Two controls run on every window (both must pass or nothing is written):
  C1 known answer   july22 only: demand at hour ending 18:00 CT on 2026-07-22 is 91,075 MW.
  C2 second source  EIA demand vs ERCOT's own Native_Load_2026 archive, same hours, max |diff| < 2%.
                    C2 is the control that can still fail on calm/fern, where no published
                    number is memorised.

Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe prep/pull_eia930.py july22 --check
     PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe prep/pull_eia930.py calm fern
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
KEYS = Path(os.environ["HEADROOM_KEYS_FILE"]) if os.environ.get("HEADROOM_KEYS_FILE") else None
NATIVE_LOAD = RAW / "Native_Load_2026.zip"

API = "https://api.eia.gov/v2/electricity/rto/{dataset}/data/"
RESPONDENT = "ERCO"
CT = "America/Chicago"
RTC_B = pd.Timestamp("2025-12-05")  # never blend market data across the RTC+B regime change
PAGE = 5000

# UTC hour bounds. The July stem matches the file already in data/raw/ from 2026-09-15.
PRESETS = {
    "july22": ("2026-07-21T00", "2026-07-24T06", "2026-07-21_24"),
    "calm": ("2026-05-28T05", "2026-05-31T05", "2026-05-28_31"),
    "fern": ("2026-01-24T06", "2026-01-30T06", "2026-01-24_30"),
}
FUEL_COLS = ["BAT", "COL", "NG", "NUC", "OTH", "SUN", "WAT", "WND"]


# ---------------------------------------------------------------- key handling
def load_key() -> str:
    """Return EIA_API_KEY from the environment or $HEADROOM_KEYS_FILE. Never logged."""
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key and KEYS is not None and KEYS.exists():
        for line in KEYS.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^EIA_API_KEY=(.*)$", line.strip())
            if m:
                key = m.group(1).strip().strip("'\"")
                break
    if not key:
        sys.exit("EIA_API_KEY not found: set it, or point HEADROOM_KEYS_FILE at a KEY=value file")
    return key


def scrub(text: object, key: str) -> str:
    """Remove the key from anything we are about to print. Substrings count as leaks."""
    out = str(text)
    if key:
        out = out.replace(key, "<redacted>")
    return re.sub(r"api_key=[^&\s]+", "api_key=<redacted>", out)


# ---------------------------------------------------------------- fetch
def fetch(dataset: str, start: str, end: str, key: str) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    while True:
        params = {
            "api_key": key,
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": RESPONDENT,
            "start": start,
            "end": end,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": PAGE,
        }
        try:
            r = requests.get(API.format(dataset=dataset), params=params, timeout=60)
            r.raise_for_status()
            page = r.json()["response"]["data"]
        except Exception as exc:  # noqa: BLE001 - re-raised scrubbed
            sys.exit(f"EIA fetch failed ({dataset}): {scrub(exc, key)}")
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
        time.sleep(0.3)
    if not rows:
        sys.exit(f"EIA returned 0 rows for {dataset} {start}..{end} - refusing to write an empty file")
    return pd.DataFrame(rows)


def to_ct(period: pd.Series) -> pd.Series:
    """EIA periods are UTC hour-ending stamps. Return naive CT hour-ending."""
    return (
        pd.to_datetime(period, format="%Y-%m-%dT%H", utc=True)
        .dt.tz_convert(CT)
        .dt.tz_localize(None)
    )


def build_wide(fuel: pd.DataFrame, region: pd.DataFrame) -> pd.DataFrame:
    f = fuel.copy()
    f["he_ct"] = to_ct(f["period"])
    f["value"] = pd.to_numeric(f["value"], errors="coerce")
    wide = f.pivot_table(index="he_ct", columns="fueltype", values="value", aggfunc="sum")

    r = region.copy()
    r["he_ct"] = to_ct(r["period"])
    r["value"] = pd.to_numeric(r["value"], errors="coerce")
    demand = r[r["type"] == "D"].set_index("he_ct")["value"].sort_index()

    wide.insert(0, "demand", demand)
    for col in FUEL_COLS:
        if col not in wide.columns:
            wide[col] = 0.0
    wide = wide[["demand"] + FUEL_COLS].sort_index()
    wide["net_load"] = wide["demand"] - wide["SUN"] - wide["WND"]
    wide.columns.name = None
    return wide


# ---------------------------------------------------------------- controls
def ercot_native_load() -> pd.Series:
    """ERCOT's own hourly ERCOT-wide load, hour ending CT. Independent of EIA."""
    with zipfile.ZipFile(NATIVE_LOAD) as z:
        raw = pd.read_excel(io.BytesIO(z.read("Native_Load_2026.xlsx")))
    he = raw["Hour Ending"].astype(str).str.strip()
    # ERCOT writes the last hour of the day as 24:00; pandas needs 00:00 of the next day.
    day = pd.to_datetime(he.str.slice(0, 10), format="%m/%d/%Y")
    hour = he.str.slice(11, 13).astype(int)
    idx = day + pd.to_timedelta(hour, unit="h")
    return pd.Series(pd.to_numeric(raw["ERCOT"], errors="coerce").values, index=idx).sort_index()


def control_c1(wide: pd.DataFrame) -> bool:
    stamp, expected = pd.Timestamp("2026-07-22 18:00"), 91075.0
    if stamp not in wide.index:
        print(f"  C1 known answer   SKIP  ({stamp} not in this window)")
        return True
    got = float(wide.loc[stamp, "demand"])
    ok = abs(got - expected) < 1.0
    print(
        f"  C1 known answer   {'PASS' if ok else 'FAIL'}  demand HE18:00 CT 2026-07-22 = "
        f"{got:,.0f} MW (expect {expected:,.0f})"
    )
    return ok


def control_c2(wide: pd.DataFrame) -> bool:
    if not NATIVE_LOAD.exists():
        print("  C2 second source  FAIL  Native_Load_2026.zip missing - no independent check possible")
        return False
    native = ercot_native_load()
    both = pd.concat(
        [wide["demand"].rename("eia"), native.rename("ercot")], axis=1, join="inner"
    ).dropna()
    if both.empty:
        print("  C2 second source  FAIL  no overlapping hours with the ERCOT archive")
        return False
    pct = (both["eia"] - both["ercot"]).abs() / both["ercot"] * 100
    worst = pct.idxmax()
    ok = bool(pct.max() < 2.0)
    print(
        f"  C2 second source  {'PASS' if ok else 'FAIL'}  {len(both)} hours vs ERCOT Native_Load; "
        f"max |diff| {pct.max():.2f}% at {worst} "
        f"(EIA {both.loc[worst, 'eia']:,.0f} vs ERCOT {both.loc[worst, 'ercot']:,.0f} MW)"
    )
    return ok


# ---------------------------------------------------------------- write / check
def compare_or_write(path: Path, df: pd.DataFrame, check_only: bool, index: bool) -> bool:
    if not check_only:
        df.to_parquet(path, index=index)
        print(f"  wrote {path.name} ({df.shape[0]} rows)")
        return True
    if not path.exists():
        print(f"  {path.name}: MISSING (check mode writes nothing)")
        return False
    old = pd.read_parquet(path)
    new = df if index else df.reset_index(drop=True)
    if old.shape != new.shape or set(old.columns) != set(new.columns):
        print(f"  {path.name}: DIFFERS  on disk {old.shape} vs fetched {new.shape}")
        return False
    cols = old.columns.tolist()
    if index:
        old_cmp, new_cmp = old.sort_index(), new[cols].sort_index()
    else:
        # The 2026-09-15 inline pull sorted period descending; this script sorts ascending.
        # Compare as sets of rows so ordering alone is not reported as a data difference.
        old_cmp = old.sort_values(cols).reset_index(drop=True)
        new_cmp = new[cols].sort_values(cols).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(old_cmp, new_cmp, check_dtype=False, rtol=1e-6)
    except AssertionError as exc:
        print(f"  {path.name}: DIFFERS from the file on disk -> {str(exc).splitlines()[0]}")
        return False
    print(f"  {path.name}: matches the file on disk ({old.shape[0]} rows)")
    return True


def run(preset: str, key: str, check_only: bool) -> bool:
    start, end, stem = PRESETS[preset]
    if pd.Timestamp(start[:10]) < RTC_B:
        sys.exit(f"{preset} starts {start}, before RTC+B ({RTC_B.date()}) - refusing")
    print(f"\n== {preset}  UTC {start} .. {end}")
    fuel = fetch("fuel-type-data", start, end, key)
    region = fetch("region-data", start, end, key)
    wide = build_wide(fuel, region)
    print(
        f"  fetched fuel {fuel.shape[0]} rows, region {region.shape[0]} rows, "
        f"wide {wide.shape[0]} hours ({wide.index[0]} .. {wide.index[-1]} CT)"
    )

    if not (control_c1(wide) & control_c2(wide)):
        print(f"  {preset}: CONTROLS FAILED - nothing written")
        return False

    ok = True
    ok &= compare_or_write(RAW / f"{stem}_eia930_fuel.parquet", fuel, check_only, index=False)
    ok &= compare_or_write(RAW / f"{stem}_eia930_region.parquet", region, check_only, index=False)
    ok &= compare_or_write(RAW / f"{stem}_eia930_hourly_wide.parquet", wide, check_only, index=True)
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("presets", nargs="*", choices=list(PRESETS), help="windows to pull")
    ap.add_argument(
        "--check", action="store_true", help="fetch and compare against data/raw/, write nothing"
    )
    args = ap.parse_args()
    presets = args.presets or ["july22"]
    key = load_key()
    results = {p: run(p, key, args.check) for p in presets}
    print("\n" + " | ".join(f"{p}: {'ok' if v else 'FAILED'}" for p, v in results.items()))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
