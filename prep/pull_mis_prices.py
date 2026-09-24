"""Pull ERCOT 2026 historical price archives from the public MIS (no key).

- 13061 RTMLZHBSPP: real-time 15-min settlement point prices, load zones + hubs
- 13060 DAMLZHBSPP: day-ahead hourly settlement point prices, load zones + hubs
- 13091 DAMASMCPC:  day-ahead hourly ancillary service clearing prices

Writes data/raw/ercot_<product>_2026.parquet. Refuses any row before RTC+B (2025-12-05).
Run: .venv/Scripts/python.exe prep/pull_mis_prices.py
"""
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
RTC_B = pd.Timestamp("2025-12-05")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
LIST = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId={rtid}"
DOWNLOAD = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={doc_id}"
PRODUCTS = {13061: "rtm_spp", 13060: "dam_spp", 13091: "dam_as"}


def find_doc(rtid: int, year: int) -> str:
    docs = requests.get(LIST.format(rtid=rtid), headers=H, timeout=30).json()["ListDocsByRptTypeRes"]["DocumentList"]
    for d in docs:
        doc = d["Document"]
        if doc["FriendlyName"].endswith(f"_{year}"):
            return doc["DocID"]
    raise LookupError(f"no {year} doc for report {rtid}")


def read_all_sheets(content: bytes) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(content))
    frames = []
    for member in z.namelist():
        with z.open(member) as f:
            data = f.read()
        if member.lower().endswith((".xlsx", ".xls")):
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
            frames += [s for s in sheets.values() if len(s)]
        elif member.lower().endswith(".csv"):
            frames.append(pd.read_csv(io.BytesIO(data)))
    df = pd.concat(frames, ignore_index=True)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def add_interval_start(df: pd.DataFrame) -> pd.DataFrame:
    date = pd.to_datetime(df["delivery_date"])
    hour_col = "delivery_hour" if "delivery_hour" in df.columns else "hour_ending"
    hour = df[hour_col].astype(str).str.split(":").str[0].astype(int)
    start = date + pd.to_timedelta(hour - 1, unit="h")
    if "delivery_interval" in df.columns:
        start = start + pd.to_timedelta((df["delivery_interval"].astype(int) - 1) * 15, unit="min")
    df["interval_start_ct"] = start  # naive, America/Chicago local
    return df


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    for rtid, name in PRODUCTS.items():
        doc_id = find_doc(rtid, 2026)
        resp = requests.get(DOWNLOAD.format(doc_id=doc_id), headers=H, timeout=300)
        resp.raise_for_status()
        (RAW / f"ercot_{name}_2026.zip").write_bytes(resp.content)
        df = add_interval_start(read_all_sheets(resp.content))
        if df["interval_start_ct"].min() < RTC_B:
            raise ValueError(f"{name}: rows before RTC+B go-live; refusing to blend regimes")
        df.to_parquet(RAW / f"ercot_{name}_2026.parquet")
        print(f"{name}: {df.shape} {df['interval_start_ct'].min()} -> {df['interval_start_ct'].max()}")
        print("   columns:", df.columns.tolist())
    return 0


if __name__ == "__main__":
    sys.exit(main())
