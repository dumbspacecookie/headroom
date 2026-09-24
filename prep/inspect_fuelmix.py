"""Print the structure of ERCOT's IntGenbyFuel2026.xlsx July sheet so the parser can be written against reality."""
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
jul = pd.read_excel(RAW / "IntGenbyFuel2026.xlsx", sheet_name="Jul", header=None, nrows=12)
print("shape (first 12 rows):", jul.shape)
print(jul.iloc[:12, :8].to_string())
full = pd.read_excel(RAW / "IntGenbyFuel2026.xlsx", sheet_name="Jul")
print("columns (first 12):", full.columns.tolist()[:12], "... total", len(full.columns))
for col in full.columns[:4]:
    vals = full[col].dropna().astype(str).unique()
    print(f"unique in {col!r} (first 20):", list(vals[:20]))
print("last date-ish values:", full.iloc[-3:, :3].to_string())
