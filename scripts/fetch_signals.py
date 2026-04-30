"""
Fetch quantifiable MSTR sell-high signals and persist them.
Runs on a schedule (GitHub Actions) every 30 min.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
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
PRICE_HISTORY = DATA / "price_history.csv"

# Fallback BTC holdings — updated by the scraper below when reachable.
# Source of truth: https://www.strategy.com/purchases
FALLBACK_BTC_HOLDINGS = 818_334
FALLBACK_AVG_COST = 70_982  # USD per BTC, average acquisition cost

# Diluted share count fallback (Class A + Class B + assumed convert/preferred dilution).
# Update if a 10-Q changes the picture meaningfully.
FALLBACK_DILUTED_SHARES = 295_000_000


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"  warn: {fn.__name__ if hasattr(fn, '__name__') else 'call'} failed: {e}", file=sys.stderr)
        return default


def fetch_btc_holdings() -> tuple[int, float]:
    """Try bitcointreasuries.net public JSON, then fall back."""
    url = "https://bitcointreasuries.net/embed/style2"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and "Strategy" in r.text:
            # crude parse — page lists Strategy first with a number
            import re
            m = re.search(r"Strategy.*?([\d,]{6,})\s*BTC", r.text, re.DOTALL)
            if m:
                count = int(m.group(1).replace(",", ""))
                if 100_000 < count < 5_000_000:
                    return count, FALLBACK_AVG_COST
    except Exception as e:  # noqa: BLE001
        print(f"  warn: holdings scrape failed: {e}", file=sys.stderr)
    return FALLBACK_BTC_HOLDINGS, FALLBACK_AVG_COST


def fetch_prices() -> dict:
    """Pull MSTR + BTC spot + history from yfinance."""
    mstr = yf.Ticker("MSTR")
    btc = yf.Ticker("BTC-USD")

    mstr_hist = mstr.history(period="2y", interval="1d", auto_adjust=True)
    btc_hist = btc.history(period="2y", interval="1d", auto_adjust=True)

    mstr_px = float(mstr_hist["Close"].iloc[-1])
    btc_px = float(btc_hist["Close"].iloc[-1])

    info = _safe(lambda: mstr.fast_info, {}) or {}
    shares = int(info.get("shares") or FALLBACK_DILUTED_SHARES)

    # Normalize indexes to plain dates so concat aligns even when one source is tz-aware.
    mstr_close = mstr_hist["Close"].copy()
    btc_close = btc_hist["Close"].copy()
    mstr_close.index = pd.to_datetime(mstr_close.index).tz_localize(None).normalize()
    btc_close.index = pd.to_datetime(btc_close.index).tz_localize(None).normalize()

    return {
        "mstr_price": mstr_px,
        "btc_price": btc_px,
        "mstr_shares": shares,
        "mstr_history": mstr_close.to_frame(name="mstr"),
        "btc_history": btc_close.to_frame(name="btc"),
    }


def realized_vol(close: pd.Series, window: int = 30) -> float:
    """Annualized realized vol from daily log returns."""
    rets = np.log(close / close.shift(1)).dropna().tail(window)
    if len(rets) < 5:
        return float("nan")
    return float(rets.std() * math.sqrt(252) * 100)


def implied_vol_atm(ticker: str = "MSTR") -> float:
    """ATM 30-day implied vol, best-effort from yfinance options chain."""
    try:
        t = yf.Ticker(ticker)
        spot = float(t.fast_info.get("lastPrice") or t.history(period="1d")["Close"].iloc[-1])
        expirations = t.options
        if not expirations:
            return float("nan")
        # pick expiration ~30 days out
        today = datetime.utcnow().date()
        target = min(
            expirations,
            key=lambda d: abs((datetime.strptime(d, "%Y-%m-%d").date() - today).days - 30),
        )
        chain = t.option_chain(target)
        calls = chain.calls.copy()
        calls["dist"] = (calls["strike"] - spot).abs()
        atm = calls.nsmallest(1, "dist")
        # Drop rows with junk IV (yfinance often returns ~0 for illiquid strikes).
        # MSTR realistic IV floor is ~30%; below that is bad data.
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


def days_since_btc_ath(btc_close: pd.Series) -> tuple[int, float, float]:
    ath = float(btc_close.max())
    ath_date = btc_close.idxmax()
    spot = float(btc_close.iloc[-1])
    drawdown = (spot / ath - 1) * 100
    last_date = btc_close.index[-1]
    days = int((last_date - ath_date).days)
    return days, drawdown, ath


def relative_perf(mstr: pd.Series, btc: pd.Series, days: int) -> float:
    """MSTR return minus BTC return over `days`."""
    if len(mstr) < days + 1 or len(btc) < days + 1:
        return float("nan")
    m_ret = float(mstr.iloc[-1] / mstr.iloc[-days - 1] - 1)
    b_ret = float(btc.iloc[-1] / btc.iloc[-days - 1] - 1)
    return (m_ret - b_ret) * 100


def mnav_zone(mnav: float) -> tuple[str, str, str]:
    """Return (zone label, color hex, action)."""
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


def build_snapshot() -> dict:
    print("Fetching BTC holdings…")
    holdings, avg_cost = fetch_btc_holdings()
    print(f"  holdings={holdings:,} BTC")

    print("Fetching prices…")
    p = fetch_prices()
    mstr_px = p["mstr_price"]
    btc_px = p["btc_price"]
    shares = p["mstr_shares"]
    mstr_hist = p["mstr_history"]["mstr"]
    btc_hist = p["btc_history"]["btc"]

    nav = holdings * btc_px
    market_cap = mstr_px * shares
    mnav = market_cap / nav if nav else float("nan")
    mstr_btc_ratio = mstr_px / btc_px if btc_px else float("nan")

    rv30 = realized_vol(mstr_hist, 30)
    rv90 = realized_vol(mstr_hist, 90)
    iv30 = implied_vol_atm("MSTR")

    btc_idx = btc_hist.copy()
    btc_idx.index = pd.to_datetime(btc_idx.index)
    days_ath, btc_dd, btc_ath = days_since_btc_ath(btc_idx)

    rel_30 = relative_perf(mstr_hist, btc_hist, 30)
    rel_90 = relative_perf(mstr_hist, btc_hist, 90)
    rel_180 = relative_perf(mstr_hist, btc_hist, 180)

    # MSTR/BTC ratio z-score over trailing 365d
    pair = pd.concat([mstr_hist, btc_hist], axis=1).dropna()
    pair["ratio"] = pair["mstr"] / pair["btc"]
    trailing = pair["ratio"].tail(365)
    z = float((trailing.iloc[-1] - trailing.mean()) / trailing.std()) if len(trailing) > 30 else float("nan")

    zone, color, action = mnav_zone(mnav)

    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mstr_price": round(mstr_px, 2),
        "btc_price": round(btc_px, 2),
        "btc_holdings": holdings,
        "btc_avg_cost": round(avg_cost, 2),
        "diluted_shares": shares,
        "market_cap_b": round(market_cap / 1e9, 3),
        "btc_nav_b": round(nav / 1e9, 3),
        "mnav": round(mnav, 4),
        "mnav_zone": zone,
        "mnav_color": color,
        "mnav_action": action,
        "mstr_btc_ratio": round(mstr_btc_ratio, 6),
        "mstr_btc_ratio_z365": round(z, 3) if not math.isnan(z) else None,
        "realized_vol_30d_pct": round(rv30, 2),
        "realized_vol_90d_pct": round(rv90, 2),
        "implied_vol_30d_pct": round(iv30, 2) if not math.isnan(iv30) else None,
        "btc_ath": round(btc_ath, 2),
        "btc_drawdown_from_ath_pct": round(btc_dd, 2),
        "days_since_btc_ath": days_ath,
        "mstr_minus_btc_30d_pct": round(rel_30, 2),
        "mstr_minus_btc_90d_pct": round(rel_90, 2),
        "mstr_minus_btc_180d_pct": round(rel_180, 2),
        "btc_underwater_pct": round(((mstr_px * shares) / (holdings * avg_cost) - 1) * 100, 2),
    }
    return snapshot, mstr_hist, btc_hist


def append_history(row: dict) -> None:
    cols = [
        "timestamp_utc", "mstr_price", "btc_price", "btc_holdings",
        "diluted_shares", "market_cap_b", "btc_nav_b", "mnav",
        "mstr_btc_ratio", "mstr_btc_ratio_z365",
        "realized_vol_30d_pct", "realized_vol_90d_pct", "implied_vol_30d_pct",
        "btc_drawdown_from_ath_pct", "days_since_btc_ath",
        "mstr_minus_btc_30d_pct", "mstr_minus_btc_90d_pct", "mstr_minus_btc_180d_pct",
    ]
    df_row = pd.DataFrame([{k: row.get(k) for k in cols}])
    if HISTORY.exists():
        existing = pd.read_csv(HISTORY)
        df_row = pd.concat([existing, df_row], ignore_index=True)
        # de-dup on minute precision so close-together runs collapse
        df_row["bucket"] = pd.to_datetime(df_row["timestamp_utc"]).dt.floor("30min")
        df_row = df_row.drop_duplicates("bucket", keep="last").drop(columns=["bucket"])
    df_row.to_csv(HISTORY, index=False)


def write_price_history(mstr_hist: pd.Series, btc_hist: pd.Series) -> None:
    df = pd.concat([mstr_hist.rename("mstr"), btc_hist.rename("btc")], axis=1).dropna()
    df.index.name = "date"
    df = df.tail(730)  # 2y daily
    df.to_csv(PRICE_HISTORY)


def main() -> int:
    snapshot, mstr_hist, btc_hist = build_snapshot()
    LATEST.write_text(json.dumps(snapshot, indent=2))
    append_history(snapshot)
    write_price_history(mstr_hist, btc_hist)
    print(f"\nmNAV={snapshot['mnav']}  zone={snapshot['mnav_zone']}  action={snapshot['mnav_action']}")
    print(f"Wrote {LATEST}, {HISTORY}, {PRICE_HISTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
