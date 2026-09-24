"""Slide 1: ERCOT 2026-07-22 load peak vs net-load peak, and what grid batteries did.

Two small multiples on a shared time axis (never a dual axis):
  top    demand and net load (MW)
  bottom grid battery output (MW; negative = charging)
Source: EIA-930 hourly (hour ending, CT), pulled 2026-09-15. Writes prep/out/slide1_2026-07-22.png
Run: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe prep/slide1_chart.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WIDE = ROOT / "data" / "raw" / "2026-07-21_24_eia930_hourly_wide.parquet"
OUT = ROOT / "prep" / "out" / "slide1_2026-07-22.png"

# dataviz reference palette (light): slots 1-3 validated (CVD ΔE 9.2); aqua <3:1 → direct labels carry it
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
DEMAND, NET_LOAD, BATTERY = "#2a78d6", "#eb6834", "#1baf7a"
WINDOW_WASH = "#f0efec"
DAY = "2026-07-22"


def at(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{DAY} {hhmm}")


def clock(ts: pd.Timestamp) -> str:  # "6 PM" without the non-portable %-I
    return f"{ts.hour % 12 or 12} {'AM' if ts.hour < 12 else 'PM'}"


def gw(v: float, signed: bool = False) -> str:
    s = f"{v / 1000:+.1f}" if signed else f"{v / 1000:.1f}"
    return s.replace("-", "−") + " GW"


def dot(ax, x, y, color):
    ax.plot([x], [y], "o", markersize=8, color=color, markeredgecolor=SURFACE, markeredgewidth=2, zorder=5)


h = pd.read_parquet(WIDE).loc[f"{DAY} 01:00":"2026-07-23 00:00"]
t = h.index
d_he, n_he, b_he = h["demand"].idxmax(), h["net_load"].idxmax(), h["BAT"].idxmax()

plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 11,
                     "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": MUTED})
fig, (top, bot) = plt.subplots(2, 1, figsize=(12.8, 7.2), dpi=150, sharex=True,
                               gridspec_kw={"height_ratios": [2.2, 1], "hspace": 0.12})
fig.patch.set_facecolor(SURFACE)
leader = dict(arrowstyle="-", color=MUTED, linewidth=1, shrinkA=2, shrinkB=6)

for ax in (top, bot):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    ax.axvspan(at("15:30"), at("18:30"), color=WINDOW_WASH, zorder=0)
    ax.axvspan(at("20:00"), at("21:00"), color=WINDOW_WASH, zorder=0)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: gw(v).replace(".0 GW", " GW")))

# top: demand vs net load
top.plot(t, h["demand"], color=DEMAND, linewidth=2, solid_capstyle="round", label="Demand")
top.plot(t, h["net_load"], color=NET_LOAD, linewidth=2, solid_capstyle="round", label="Net load (demand − solar − wind)")
dot(top, d_he, h.loc[d_he, "demand"], DEMAND)
dot(top, n_he, h.loc[n_he, "net_load"], NET_LOAD)
top.annotate(f"Load peak {gw(h.loc[d_he, 'demand'])}, hour ending {clock(d_he)}",
             (d_he, h.loc[d_he, "demand"]), xytext=(at("17:40"), 97500), textcoords="data",
             ha="right", va="center", color=INK, fontsize=11, arrowprops=leader)
top.annotate(f"Net-load peak {gw(h.loc[n_he, 'net_load'])}\nhour ending {clock(n_he)}",
             (n_he, h.loc[n_he, "net_load"]), xytext=(at("19:40"), 75500), textcoords="data",
             ha="right", va="center", color=INK, fontsize=11, arrowprops=leader)
top.text(at("17:00"), 43000, "afternoon peak shave\n(4CP-style)", ha="center", color=INK_2, fontsize=10)
top.text(at("20:30"), 43000, "evening\nADER window", ha="center", color=INK_2, fontsize=10)
top.set_ylim(35000, 101000)
top.legend(loc="upper left", frameon=False, labelcolor=INK_2)
top.set_title("ERCOT, July 22 2026: the grid's hardest hour came three hours after its biggest one",
              loc="left", color=INK, fontsize=15, pad=14)

# bottom: grid battery output
bot.axhline(0, color=AXIS, linewidth=1)
bot.plot(t, h["BAT"], color=BATTERY, linewidth=2, solid_capstyle="round")
bot.fill_between(t, h["BAT"], 0, color=BATTERY, alpha=0.10, linewidth=0)
dot(bot, b_he, h.loc[b_he, "BAT"], BATTERY)
dot(bot, d_he, h.loc[d_he, "BAT"], BATTERY)
bot.annotate(f"{gw(h.loc[b_he, 'BAT'])} discharging", (b_he, h.loc[b_he, "BAT"]),
             xytext=(14, -2), textcoords="offset points", va="center", color=INK, fontsize=11)
bot.annotate(f"{gw(h.loc[d_he, 'BAT'], signed=True)} at the load peak (net charging)", (d_he, h.loc[d_he, "BAT"]),
             xytext=(at("15:10"), 8500), textcoords="data", ha="right", va="center", color=INK, fontsize=11,
             arrowprops=leader)
bot.set_ylim(-13000, 15000)
bot.set_ylabel("Grid batteries", color=INK_2)
bot.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
bot.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
bot.set_xlim(at("01:00"), pd.Timestamp("2026-07-23 00:00"))
fig.text(0.125, 0.015, "Source: EIA-930 hourly, ERCOT balancing authority, hour ending CT. Net load = demand − solar − wind. "
         "Shaded: Headroom S1 claim windows.", color=MUTED, fontsize=9)

OUT.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT, facecolor=SURFACE, bbox_inches="tight")
print("wrote", OUT)
