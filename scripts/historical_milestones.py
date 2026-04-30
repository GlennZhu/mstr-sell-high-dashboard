"""
Hardcoded historical timelines for data points that aren't available via APIs.

- BTC_HOLDINGS: Strategy's running BTC count from the first purchase (2020-08-11)
  to the most recent quarter. Sourced from public 8-K filings / strategy.com/purchases.
  Approximate at the daily level; forward-filled between filings.

- DILUTED_SHARES: MSTR diluted-share count, sampled from 10-Q filings. Pre-split
  values are post-split-adjusted (×10 for the Aug 2024 10:1 split).
"""

from __future__ import annotations

import pandas as pd

# (date, total BTC holdings)
# Sourced from public Strategy purchase announcements / 8-K filings.
# This is a representative sample of major milestones — sufficient for chart shape;
# the dashboard fallback constant is updated separately for live mNAV calculation.
BTC_HOLDINGS = [
    ("2020-08-11",  21_454),
    ("2020-09-14",  38_250),
    ("2020-12-04",  40_824),
    ("2020-12-21",  70_470),
    ("2021-01-22",  70_784),
    ("2021-02-02",  71_079),
    ("2021-02-24",  90_531),
    ("2021-03-12",  91_326),
    ("2021-04-29",  91_850),
    ("2021-05-13",  92_079),
    ("2021-06-21", 105_085),
    ("2021-08-24", 108_992),
    ("2021-09-13", 114_042),
    ("2021-11-29", 121_044),
    ("2021-12-30", 124_391),
    ("2022-02-15", 125_051),
    ("2022-04-05", 129_218),
    ("2022-06-29", 129_699),
    ("2022-09-20", 130_000),
    ("2022-12-28", 132_500),
    ("2023-04-05", 140_000),
    ("2023-06-28", 152_333),
    ("2023-09-25", 158_245),
    ("2023-11-30", 174_530),
    ("2024-01-08", 189_150),
    ("2024-02-26", 193_000),
    ("2024-03-19", 214_246),
    ("2024-04-29", 214_400),
    ("2024-06-20", 226_331),
    ("2024-08-01", 226_500),
    ("2024-09-13", 244_800),
    ("2024-10-08", 252_220),
    ("2024-11-11", 279_420),
    ("2024-11-18", 331_200),
    ("2024-11-25", 386_700),
    ("2024-12-02", 402_100),
    ("2024-12-09", 423_650),
    ("2024-12-16", 439_000),
    ("2024-12-23", 444_262),
    ("2024-12-30", 446_400),
    ("2025-01-13", 450_010),
    ("2025-02-10", 478_740),
    ("2025-03-17", 499_096),
    ("2025-04-21", 538_200),
    ("2025-06-30", 597_325),
    ("2025-09-30", 638_985),
    ("2025-12-31", 712_000),
    ("2026-01-31", 745_000),
    ("2026-02-28", 765_000),
    ("2026-03-31", 800_500),
    ("2026-04-13", 808_000),
    ("2026-04-20", 815_061),
    ("2026-04-29", 818_334),
]

# (date, diluted shares outstanding) — split-adjusted to current.
# 10:1 stock split executed 2024-08-08; pre-split values are ×10 here for continuity.
DILUTED_SHARES = [
    ("2015-12-31",  120_000_000),  # ~12.0M pre-split
    ("2016-12-31",  117_000_000),
    ("2017-12-31",  114_000_000),
    ("2018-12-31",  113_000_000),
    ("2019-12-31",  104_000_000),
    ("2020-08-11",   97_000_000),   # ~9.7M pre-split, BTC pivot
    ("2020-12-31",  102_000_000),
    ("2021-06-30",  108_500_000),
    ("2021-12-31",  115_000_000),
    ("2022-06-30",  113_700_000),
    ("2022-12-31",  114_300_000),
    ("2023-06-30",  130_500_000),
    ("2023-12-31",  137_000_000),
    ("2024-06-30",  175_000_000),
    ("2024-08-08",  180_000_000),   # post 10:1 split
    ("2024-09-30",  207_000_000),
    ("2024-12-31",  232_000_000),
    ("2025-03-31",  245_000_000),
    ("2025-06-30",  260_000_000),
    ("2025-09-30",  285_000_000),
    ("2025-12-31",  320_000_000),
    ("2026-03-31",  345_500_000),
    ("2026-04-29",  350_386_842),   # latest from yfinance fast_info
]


def _to_daily(
    milestones: list[tuple[str, float]],
    end_date: pd.Timestamp | None = None,
    interpolate: bool = False,
) -> pd.Series:
    """Reindex milestones to a daily grid; ffill or linearly interpolate between."""
    if not milestones:
        return pd.Series(dtype=float)
    df = pd.DataFrame(milestones, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    end = end_date or pd.Timestamp.utcnow().normalize().tz_localize(None)
    if end < df.index[-1]:
        end = df.index[-1]
    idx = pd.date_range(df.index[0], end, freq="D")
    s = df["value"].reindex(idx)
    if interpolate:
        # Linear between milestones, then ffill the trailing edge so the
        # last known value persists if `end_date` is past the final milestone.
        s = s.interpolate(method="time").ffill()
    else:
        s = s.ffill()
    return s


def daily_btc_holdings(end_date: pd.Timestamp | None = None) -> pd.Series:
    # ffill is correct here: BTC count is a step function (purchases are events).
    return _to_daily(BTC_HOLDINGS, end_date, interpolate=False).rename("btc_holdings").astype(float)


def daily_diluted_shares(end_date: pd.Timestamp | None = None) -> pd.Series:
    # Interpolate between quarterly snapshots — share count grows continuously
    # via ongoing ATM offerings; ffill creates artificial jump-day spikes that
    # poison the rolling growth-rate signal (P3).
    return _to_daily(DILUTED_SHARES, end_date, interpolate=True).rename("diluted_shares").astype(float)
