"""
Fetch quantifiable MSTR sell-high signals and persist them.

Architecture
------------
We maintain TWO time-series files:

  data/daily_history.csv   — one row PER DAY going back to 2015 (or as far as
                             yfinance allows). This is the source for the long
                             historical charts in the dashboard.
                             Updated incrementally: we only recompute today's
                             row on each cron run; older rows are preserved.

  data/history.csv         — 30-min snapshot stream, one row per cron tick.
                             Used for fine-grained recent monitoring.

  data/latest.json         — most recent snapshot (mirror of last daily row +
                             intraday-only fields like options IV, funding).

Signals are organized to mirror the 8-point sell-high playbook from the
deep-research report:

  P1  mNAV ≥ 2.5–3.0x = euphoria zone
  P2  MSTR tops BEFORE BTC — lead/lag divergence
  P3  Accelerated ATM equity issuance at high mNAV
  P4  Catalyst exhaustion          (MANUAL — see app.py)
  P5  Gamma-squeeze blow-off       (compound: RV30 > 100% AND mNAV > 2.5x)
  P6  MSTR/BTC ratio at multi-year extreme (multiple off cycle low)
  P7  STRC/STRF/STRK/STRD credit-spread stress (capital-structure health)
  P8  BTC perp funding extremely positive (leverage saturation)
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

LATEST = DATA / "latest.json"
HISTORY = DATA / "history.csv"
DAILY = DATA / "daily_history.csv"

sys.path.insert(0, str(ROOT / "scripts"))
from historical_milestones import daily_btc_holdings, daily_diluted_shares  # noqa: E402

# Fallback BTC holdings for live mNAV when scrape fails.
FALLBACK_BTC_HOLDINGS = 818_334
FALLBACK_AVG_COST = 70_982
FALLBACK_DILUTED_SHARES = 350_386_842

PREFERRED_TICKERS = ["STRC", "STRF", "STRK", "STRD"]


# ───────────────────── helpers / fetchers ───────────────────────────────
def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"  warn: call failed: {e}", file=sys.stderr)
        return default


def fetch_btc_holdings_live() -> tuple[int, float]:
    url = "https://bitcointreasuries.net/embed/style2"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and "Strategy" in r.text:
            m = re.search(r"Strategy.*?([\d,]{6,})\s*BTC", r.text, re.DOTALL)
            if m:
                count = int(m.group(1).replace(",", ""))
                if 100_000 < count < 5_000_000:
                    return count, FALLBACK_AVG_COST
    except Exception as e:  # noqa: BLE001
        print(f"  warn: holdings scrape failed: {e}", file=sys.stderr)
    return FALLBACK_BTC_HOLDINGS, FALLBACK_AVG_COST


def fetch_long_prices() -> tuple[pd.Series, pd.Series]:
    """Pull the longest available daily history for MSTR and BTC."""
    mstr_hist = yf.Ticker("MSTR").history(period="max", interval="1d", auto_adjust=True)
    btc_hist = yf.Ticker("BTC-USD").history(period="max", interval="1d", auto_adjust=True)
    mstr = mstr_hist["Close"].copy()
    btc = btc_hist["Close"].copy()
    mstr.index = pd.to_datetime(mstr.index).tz_localize(None).normalize()
    btc.index = pd.to_datetime(btc.index).tz_localize(None).normalize()
    return mstr.astype(float), btc.astype(float)


def fetch_preferred_history() -> dict[str, pd.DataFrame]:
    """Daily price history for each Strategy preferred (since listing)."""
    out = {}
    for sym in PREFERRED_TICKERS:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="max", interval="1d", auto_adjust=False)
            if hist.empty:
                out[sym] = pd.DataFrame()
                continue
            df = hist[["Close"]].copy()
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df.columns = ["price"]
            info = _safe(lambda: t.info, {}) or {}
            div = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
            df["yield_pct"] = (float(div) / df["price"] * 100) if div else np.nan
            df["sym"] = sym
            out[sym] = df
        except Exception as e:  # noqa: BLE001
            print(f"  warn: preferred {sym}: {e}", file=sys.stderr)
            out[sym] = pd.DataFrame()
    return out


def fetch_funding_history(days: int = 365) -> pd.DataFrame:
    """OKX BTC-USDT-SWAP funding history (paginated). Returns DataFrame indexed by funding_time."""
    url = "https://www.okx.com/api/v5/public/funding-rate-history"
    rows: list[dict] = []
    after: str | None = None  # OKX: 'after' = return rows with fundingTime < this
    cutoff_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
    for page in range(60):  # cap pagination
        params = {"instId": "BTC-USDT-SWAP", "limit": 100}
        if after:
            params["after"] = after
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            chunk = r.json().get("data", [])
            if not chunk:
                break
            rows.extend(chunk)
            oldest_ts = min(int(x["fundingTime"]) for x in chunk)
            if oldest_ts <= cutoff_ms:
                break
            after = str(oldest_ts)
        except Exception as e:  # noqa: BLE001
            print(f"  warn: funding history page {page}: {e}", file=sys.stderr)
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["funding_time"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
    df["rate_pct_per_8h"] = df["realizedRate"].astype(float) * 100
    df = df[["funding_time", "rate_pct_per_8h"]].drop_duplicates("funding_time").sort_values("funding_time")
    df = df.set_index("funding_time")
    return df


def implied_vol_atm(ticker: str = "MSTR") -> float:
    try:
        t = yf.Ticker(ticker)
        spot = float(t.fast_info.get("lastPrice") or t.history(period="1d")["Close"].iloc[-1])
        expirations = t.options
        if not expirations:
            return float("nan")
        today = datetime.utcnow().date()
        target = min(
            expirations,
            key=lambda d: abs((datetime.strptime(d, "%Y-%m-%d").date() - today).days - 30),
        )
        chain = t.option_chain(target)
        calls = chain.calls.copy()
        valid = calls[calls["impliedVolatility"] > 0.30].copy()
        if valid.empty:
            return float("nan")
        valid["dist"] = (valid["strike"] - spot).abs()
        atm = valid.nsmallest(3, "dist")
        iv = float(atm["impliedVolatility"].mean() * 100)
        return iv if iv > 30 else float("nan")
    except Exception as e:  # noqa: BLE001
        print(f"  warn: IV fetch failed: {e}", file=sys.stderr)
        return float("nan")


# ─────────────────── daily-resolution signal table ──────────────────────
def build_daily_history(
    mstr: pd.Series,
    btc: pd.Series,
    pref_hist: dict[str, pd.DataFrame],
    funding: pd.DataFrame,
) -> pd.DataFrame:
    """Compute every signal at daily resolution, since BTC pivot when applicable."""

    holdings = daily_btc_holdings(end_date=mstr.index.max())
    shares = daily_diluted_shares(end_date=mstr.index.max())

    # Master daily index — union of all dates from MSTR's full history.
    idx = pd.date_range(mstr.index.min(), mstr.index.max(), freq="D")
    df = pd.DataFrame(index=idx)
    df.index.name = "date"

    df["mstr"] = mstr.reindex(idx).ffill()
    df["btc"] = btc.reindex(idx).ffill()
    df["btc_holdings"] = holdings.reindex(idx).ffill()
    df["diluted_shares"] = shares.reindex(idx).ffill()

    df["btc_nav"] = df["btc_holdings"] * df["btc"]
    df["market_cap"] = df["mstr"] * df["diluted_shares"]
    df["mnav"] = df["market_cap"] / df["btc_nav"]
    df["ratio"] = df["mstr"] / df["btc"]

    # P2 — lead/lag: ratio drawdown from rolling-90d peak vs BTC drawdown from running ATH.
    df["ratio_peak_90d"] = df["ratio"].rolling(90, min_periods=10).max()
    df["ratio_dd_from_90d_peak_pct"] = (df["ratio"] / df["ratio_peak_90d"] - 1) * 100
    df["btc_ath_running"] = df["btc"].cummax()
    df["btc_dd_from_ath_pct"] = (df["btc"] / df["btc_ath_running"] - 1) * 100
    df["lead_lag_diverging"] = (df["ratio_dd_from_90d_peak_pct"] <= -10) & (df["btc_dd_from_ath_pct"] >= -5)

    # P3 — ATM issuance: 30d / 90d annualized share growth.
    df["shares_30d_growth"] = df["diluted_shares"].pct_change(30) * 100
    df["shares_90d_growth"] = df["diluted_shares"].pct_change(90) * 100
    df["shares_30d_annualized_pct"] = df["shares_30d_growth"] * (365 / 30)
    df["shares_90d_annualized_pct"] = df["shares_90d_growth"] * (365 / 90)
    df["shares_acceleration"] = (
        (df["shares_30d_annualized_pct"] > df["shares_90d_annualized_pct"] * 1.2)
        & (df["shares_90d_annualized_pct"] > 0)
    )

    # P5 — realized vol + compound gamma flag.
    rets = np.log(df["mstr"] / df["mstr"].shift(1))
    df["realized_vol_30d_pct"] = rets.rolling(30).std() * math.sqrt(252) * 100
    df["realized_vol_90d_pct"] = rets.rolling(90).std() * math.sqrt(252) * 100
    df["gamma_armed"] = (df["realized_vol_30d_pct"] > 100) & (df["mnav"] > 2.5)
    df["gamma_elevated"] = (df["realized_vol_30d_pct"] > 80) & (df["mnav"] > 2.0)

    # P6 — multiple off trailing-2y low.
    df["ratio_2y_low"] = df["ratio"].rolling(730, min_periods=60).min()
    df["ratio_multiple_off_2y_low"] = df["ratio"] / df["ratio_2y_low"]

    # P7 — preferred yield (max across the four).
    pref_yield = pd.DataFrame(index=idx)
    for sym, h in pref_hist.items():
        if h.empty:
            continue
        pref_yield[sym] = h["yield_pct"].reindex(idx).ffill()
    if not pref_yield.empty:
        df["preferred_max_yield_pct"] = pref_yield.max(axis=1)
    else:
        df["preferred_max_yield_pct"] = np.nan
    df["credit_stress"] = df["preferred_max_yield_pct"] > 12

    # P8 — BTC perp funding (annualized, smoothed 7d).
    if not funding.empty:
        f_daily = funding["rate_pct_per_8h"].resample("1D").mean()
        df["btc_funding_8h_pct"] = f_daily.reindex(idx).ffill()
        df["btc_funding_annualized_pct"] = df["btc_funding_8h_pct"].rolling(7, min_periods=1).mean() * 1095
    else:
        df["btc_funding_8h_pct"] = np.nan
        df["btc_funding_annualized_pct"] = np.nan

    # mNAV becomes meaningful only after the BTC pivot.
    df.loc[df.index < pd.Timestamp("2020-08-11"), [
        "mnav", "ratio_dd_from_90d_peak_pct", "ratio_multiple_off_2y_low",
        "lead_lag_diverging", "gamma_armed", "gamma_elevated",
    ]] = np.nan

    keep = [
        "mstr", "btc", "btc_holdings", "diluted_shares", "market_cap", "btc_nav",
        "mnav", "ratio",
        "ratio_dd_from_90d_peak_pct", "btc_dd_from_ath_pct", "lead_lag_diverging",
        "shares_30d_annualized_pct", "shares_90d_annualized_pct", "shares_acceleration",
        "realized_vol_30d_pct", "realized_vol_90d_pct", "gamma_armed", "gamma_elevated",
        "ratio_multiple_off_2y_low",
        "preferred_max_yield_pct", "credit_stress",
        "btc_funding_8h_pct", "btc_funding_annualized_pct",
    ]
    return df[keep].round(6)


# ─────────────────────────── snapshot ───────────────────────────────────
def mnav_zone(mnav: float) -> tuple[str, str, str]:
    if math.isnan(mnav):
        return "Unknown", "#6b7280", "Wait for data"
    if mnav < 1.0:
        return "Fire Sale", "#10b981", "Aggressive accumulate"
    if mnav < 1.5:
        return "Accumulate", "#22c55e", "Buy"
    if mnav < 2.0:
        return "Fair", "#facc15", "HODL"
    if mnav < 2.5:
        return "Trim", "#fb923c", "Start scaling out (25%)"
    if mnav < 3.0:
        return "Sell", "#ef4444", "Heavy trim (50–75%)"
    return "Max Sell", "#dc2626", "Dump aggressively"


def build_snapshot(daily: pd.DataFrame, live_holdings: int, live_shares: int) -> dict:
    last = daily.iloc[-1]
    iv = implied_vol_atm("MSTR")

    # Per-preferred current snapshot
    prefs_now = {}
    for sym in PREFERRED_TICKERS:
        try:
            t = yf.Ticker(sym)
            h = t.history(period="60d", interval="1d", auto_adjust=False)
            if h.empty:
                prefs_now[sym] = {"price": None, "yield_pct": None, "drawdown_30d_pct": None}
                continue
            p = float(h["Close"].iloc[-1])
            high30 = float(h["Close"].tail(30).max())
            dd = (p / high30 - 1) * 100 if high30 else None
            info = _safe(lambda: t.info, {}) or {}
            div = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
            yld = (float(div) / p * 100) if div and p else None
            prefs_now[sym] = {
                "price": round(p, 2),
                "yield_pct": round(yld, 2) if yld else None,
                "drawdown_30d_pct": round(dd, 2) if dd is not None else None,
            }
        except Exception as e:  # noqa: BLE001
            print(f"  warn: preferred snapshot {sym}: {e}", file=sys.stderr)
            prefs_now[sym] = {"price": None, "yield_pct": None, "drawdown_30d_pct": None}

    # Funding snapshot
    fund_8h = float(last["btc_funding_8h_pct"]) if pd.notna(last["btc_funding_8h_pct"]) else None
    fund_ann = float(last["btc_funding_annualized_pct"]) if pd.notna(last["btc_funding_annualized_pct"]) else None

    mnav = float(last["mnav"]) if pd.notna(last["mnav"]) else float("nan")
    zone, color, action = mnav_zone(mnav)

    snap = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mstr_price": round(float(last["mstr"]), 2),
        "btc_price": round(float(last["btc"]), 2),
        "btc_holdings": int(last["btc_holdings"]),
        "btc_avg_cost": FALLBACK_AVG_COST,
        "diluted_shares": int(last["diluted_shares"]),
        "market_cap_b": round(float(last["market_cap"]) / 1e9, 3),
        "btc_nav_b": round(float(last["btc_nav"]) / 1e9, 3),
        "mnav": round(mnav, 4),
        "mnav_zone": zone, "mnav_color": color, "mnav_action": action,
        "ratio_dd_from_90d_peak_pct": round(float(last["ratio_dd_from_90d_peak_pct"]), 2),
        "btc_dd_from_ath_pct": round(float(last["btc_dd_from_ath_pct"]), 2),
        "lead_lag_divergence_flag": bool(last["lead_lag_diverging"]),
        "shares_30d_annualized_pct": round(float(last["shares_30d_annualized_pct"]), 2) if pd.notna(last["shares_30d_annualized_pct"]) else None,
        "shares_90d_annualized_pct": round(float(last["shares_90d_annualized_pct"]), 2) if pd.notna(last["shares_90d_annualized_pct"]) else None,
        "shares_acceleration_flag": bool(last["shares_acceleration"]),
        "realized_vol_30d_pct": round(float(last["realized_vol_30d_pct"]), 2),
        "realized_vol_90d_pct": round(float(last["realized_vol_90d_pct"]), 2),
        "implied_vol_30d_pct": round(iv, 2) if not math.isnan(iv) else None,
        "gamma_squeeze_armed": bool(last["gamma_armed"]),
        "gamma_squeeze_elevated": bool(last["gamma_elevated"]),
        "ratio_multiple_off_2y_low": round(float(last["ratio_multiple_off_2y_low"]), 2),
        "ratio_2y_low": float(daily["ratio"].rolling(730, min_periods=60).min().iloc[-1]),
        "preferreds": prefs_now,
        "preferred_max_yield_pct": round(float(last["preferred_max_yield_pct"]), 2) if pd.notna(last["preferred_max_yield_pct"]) else None,
        "preferred_worst_30d_drawdown_pct": min(
            (v["drawdown_30d_pct"] for v in prefs_now.values() if v["drawdown_30d_pct"] is not None),
            default=None,
        ),
        "credit_stress_flag": bool(last["credit_stress"]),
        "btc_funding": {
            "latest_pct_per_8h": round(fund_8h, 4) if fund_8h is not None else None,
            "annualized_pct": round(fund_ann, 2) if fund_ann is not None else None,
        },
    }
    return snap


# ─────────────────────────── persistence ────────────────────────────────
def append_intraday(snap: dict) -> None:
    cols = [
        "timestamp_utc", "mstr_price", "btc_price", "btc_holdings",
        "diluted_shares", "market_cap_b", "btc_nav_b", "mnav",
        "ratio_dd_from_90d_peak_pct", "btc_dd_from_ath_pct", "lead_lag_divergence_flag",
        "shares_30d_annualized_pct", "shares_90d_annualized_pct", "shares_acceleration_flag",
        "realized_vol_30d_pct", "realized_vol_90d_pct", "implied_vol_30d_pct",
        "gamma_squeeze_armed", "gamma_squeeze_elevated",
        "ratio_multiple_off_2y_low",
        "preferred_max_yield_pct", "preferred_worst_30d_drawdown_pct", "credit_stress_flag",
    ]
    df_row = pd.DataFrame([{k: snap.get(k) for k in cols}])
    if HISTORY.exists():
        existing = pd.read_csv(HISTORY)
        df_row = pd.concat([existing, df_row], ignore_index=True)
        df_row["bucket"] = pd.to_datetime(df_row["timestamp_utc"]).dt.floor("30min")
        df_row = df_row.drop_duplicates("bucket", keep="last").drop(columns=["bucket"])
    df_row.to_csv(HISTORY, index=False)


def write_daily(daily: pd.DataFrame, lookback_years: int = 10) -> None:
    """Write rolling lookback_years of daily data to disk."""
    cutoff = pd.Timestamp.utcnow().normalize().tz_localize(None) - pd.DateOffset(years=lookback_years)
    rolling = daily[daily.index >= cutoff].copy()
    rolling.to_csv(DAILY, index_label="date")


# ─────────────────────────── orchestrator ───────────────────────────────
def main() -> int:
    print("Fetching live BTC holdings…")
    live_holdings, _ = fetch_btc_holdings_live()
    print(f"  holdings={live_holdings:,} BTC")

    print("Fetching long-window prices (MSTR, BTC)…")
    mstr, btc = fetch_long_prices()
    print(f"  MSTR rows: {len(mstr)}  span: {mstr.index.min().date()} → {mstr.index.max().date()}")
    print(f"  BTC  rows: {len(btc)}  span: {btc.index.min().date()} → {btc.index.max().date()}")

    print("Fetching preferred history…")
    pref_hist = fetch_preferred_history()
    for sym, h in pref_hist.items():
        print(f"  {sym}: {len(h)} rows")

    print("Fetching BTC funding history…")
    funding = fetch_funding_history(days=400)
    print(f"  funding rows: {len(funding)}")

    print("Computing daily history table…")
    daily = build_daily_history(mstr, btc, pref_hist, funding)
    # Patch the most recent row with live values where stale.
    last_idx = daily.index[-1]
    daily.loc[last_idx, "btc_holdings"] = max(int(daily.loc[last_idx, "btc_holdings"]), int(live_holdings))
    # Live shares from yfinance fast_info
    live_shares_info = _safe(lambda: yf.Ticker("MSTR").fast_info.get("shares"), None)
    if live_shares_info:
        live_shares = int(live_shares_info)
        daily.loc[last_idx, "diluted_shares"] = max(float(daily.loc[last_idx, "diluted_shares"]), float(live_shares))
    else:
        live_shares = FALLBACK_DILUTED_SHARES

    # Recompute today's market_cap, btc_nav, mnav, ratio with patched values.
    daily.loc[last_idx, "market_cap"] = daily.loc[last_idx, "mstr"] * daily.loc[last_idx, "diluted_shares"]
    daily.loc[last_idx, "btc_nav"] = daily.loc[last_idx, "btc_holdings"] * daily.loc[last_idx, "btc"]
    daily.loc[last_idx, "mnav"] = daily.loc[last_idx, "market_cap"] / daily.loc[last_idx, "btc_nav"]
    daily.loc[last_idx, "ratio"] = daily.loc[last_idx, "mstr"] / daily.loc[last_idx, "btc"]

    print("Writing daily_history.csv…")
    write_daily(daily)

    print("Building snapshot…")
    snap = build_snapshot(daily, live_holdings, live_shares)
    LATEST.write_text(json.dumps(snap, indent=2))
    append_intraday(snap)

    print(f"\nSummary:")
    print(f"  daily rows: {len(daily)}  ({daily.index.min().date()} → {daily.index.max().date()})")
    print(f"  P1 mNAV={snap['mnav']}  zone={snap['mnav_zone']}")
    print(f"  P2 ratio_dd={snap['ratio_dd_from_90d_peak_pct']}%  btc_dd={snap['btc_dd_from_ath_pct']}%  diverging={snap['lead_lag_divergence_flag']}")
    print(f"  P3 shares 30d ann={snap['shares_30d_annualized_pct']}%  accel={snap['shares_acceleration_flag']}")
    print(f"  P5 RV30={snap['realized_vol_30d_pct']}%  armed={snap['gamma_squeeze_armed']}")
    print(f"  P6 multiple off 2y low={snap['ratio_multiple_off_2y_low']}x")
    print(f"  P7 max preferred yield={snap['preferred_max_yield_pct']}%  stress={snap['credit_stress_flag']}")
    print(f"  P8 funding ann={snap['btc_funding']['annualized_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
