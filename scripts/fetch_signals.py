"""
Fetch quantifiable MSTR sell-high signals and persist them.

Signals are organized to mirror the 6-point sell-high playbook from the
deep-research report (see README):

  P1  mNAV ≥ 2.5–3.0x = euphoria zone
  P2  MSTR tops BEFORE BTC — lead/lag divergence
  P3  Accelerated ATM equity issuance at high mNAV
  P4  Catalyst exhaustion          (MANUAL — see app.py)
  P5  Gamma-squeeze blow-off       (compound: RV30 > 100% AND mNAV > 2.5x)
  P6  MSTR/BTC ratio at multi-year extreme (multiple off cycle low)
  P7  STRC/STRF/STRK/STRD credit-spread stress (capital-structure health)
  P8  BTC perp funding extremely positive (leverage saturation)

Runs every 30 min via GitHub Actions.
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
PRICE_HISTORY = DATA / "price_history.csv"

# Fallback BTC holdings — updated by the scraper below when reachable.
# Source of truth: https://www.strategy.com/purchases
FALLBACK_BTC_HOLDINGS = 818_334
FALLBACK_AVG_COST = 70_982  # USD per BTC, average acquisition cost

# Diluted share count fallback (Class A + Class B + assumed convert/preferred dilution).
FALLBACK_DILUTED_SHARES = 350_000_000

# Strategy preferred tickers — used for capital-structure stress signal.
PREFERRED_TICKERS = ["STRC", "STRF", "STRK", "STRD"]


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"  warn: call failed: {e}", file=sys.stderr)
        return default


# ─────────────────────────── data fetchers ──────────────────────────────
def fetch_btc_holdings() -> tuple[int, float]:
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


def fetch_prices() -> dict:
    mstr = yf.Ticker("MSTR")
    btc = yf.Ticker("BTC-USD")

    mstr_hist = mstr.history(period="2y", interval="1d", auto_adjust=True)
    btc_hist = btc.history(period="2y", interval="1d", auto_adjust=True)

    mstr_close = mstr_hist["Close"].copy()
    btc_close = btc_hist["Close"].copy()
    mstr_close.index = pd.to_datetime(mstr_close.index).tz_localize(None).normalize()
    btc_close.index = pd.to_datetime(btc_close.index).tz_localize(None).normalize()

    info = _safe(lambda: mstr.fast_info, {}) or {}
    shares = int(info.get("shares") or FALLBACK_DILUTED_SHARES)

    return {
        "mstr_price": float(mstr_close.iloc[-1]),
        "btc_price": float(btc_close.iloc[-1]),
        "mstr_shares": shares,
        "mstr_history": mstr_close,
        "btc_history": btc_close,
    }


def fetch_shares_history() -> pd.Series | None:
    """Daily diluted-shares history for MSTR — drives the ATM-pace signal."""
    try:
        end = datetime.utcnow().date()
        start = end - timedelta(days=400)
        s = yf.Ticker("MSTR").get_shares_full(start=start, end=end)
        if s is None or len(s) == 0:
            return None
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        return s.astype(float)
    except Exception as e:  # noqa: BLE001
        print(f"  warn: shares history fetch failed: {e}", file=sys.stderr)
        return None


def fetch_preferred_yields() -> dict:
    """Pull current price + estimated yield for each MSTR preferred."""
    out = {}
    for sym in PREFERRED_TICKERS:
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="60d", interval="1d", auto_adjust=False)
            if hist.empty:
                out[sym] = {"price": None, "yield_pct": None, "drawdown_30d_pct": None}
                continue
            price = float(hist["Close"].iloc[-1])
            high_30d = float(hist["Close"].tail(30).max())
            dd = (price / high_30d - 1) * 100 if high_30d else None
            # Annual dividend from yfinance info — preferred is fixed-coupon par $100.
            info = _safe(lambda: t.info, {}) or {}
            div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
            yld = (float(div_rate) / price * 100) if (div_rate and price) else None
            out[sym] = {
                "price": round(price, 2),
                "yield_pct": round(yld, 2) if yld else None,
                "drawdown_30d_pct": round(dd, 2) if dd is not None else None,
            }
        except Exception as e:  # noqa: BLE001
            print(f"  warn: preferred {sym} failed: {e}", file=sys.stderr)
            out[sym] = {"price": None, "yield_pct": None, "drawdown_30d_pct": None}
    return out


def fetch_btc_funding() -> dict:
    """
    Latest + trailing-7d BTC perpetual funding rate.
    OKX is used (Binance and Bybit are geo-blocked from US/CloudFront).
    """
    try:
        url = "https://www.okx.com/api/v5/public/funding-rate-history"
        r = requests.get(url, params={"instId": "BTC-USDT-SWAP", "limit": 30}, timeout=10)
        r.raise_for_status()
        rows = r.json().get("data", [])
        if not rows:
            raise RuntimeError("OKX returned no funding rows")
        # OKX returns newest first; convert to oldest→newest for clarity.
        rows = list(reversed(rows))
        rates = [float(x["realizedRate"]) * 100 for x in rows]  # pct per 8h
        latest = rates[-1]
        avg_7d = float(np.mean(rates[-21:])) if len(rates) >= 21 else float(np.mean(rates))
        annualized = avg_7d * 1095  # 3 settles/day * 365
        return {
            "latest_pct_per_8h": round(latest, 4),
            "avg_7d_pct_per_8h": round(avg_7d, 4),
            "annualized_pct": round(annualized, 2),
        }
    except Exception as e:  # noqa: BLE001
        print(f"  warn: BTC funding fetch failed: {e}", file=sys.stderr)
        return {"latest_pct_per_8h": None, "avg_7d_pct_per_8h": None, "annualized_pct": None}


# ─────────────────────── signal computations ────────────────────────────
def realized_vol(close: pd.Series, window: int = 30) -> float:
    rets = np.log(close / close.shift(1)).dropna().tail(window)
    if len(rets) < 5:
        return float("nan")
    return float(rets.std() * math.sqrt(252) * 100)


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


def lead_lag_divergence(mstr: pd.Series, btc: pd.Series) -> dict:
    """
    Playbook Signal #2: MSTR tops BEFORE BTC.

    Detect by tracking the MSTR/BTC ratio's drawdown from its rolling 90d peak
    versus BTC's drawdown from its all-time high. When the ratio is rolling
    over (≥10% off its 90d peak) while BTC is still pinned to ATH (≤5% off),
    that divergence = MSTR cycle top is in.
    """
    pair = pd.concat([mstr.rename("mstr"), btc.rename("btc")], axis=1).dropna()
    pair["ratio"] = pair["mstr"] / pair["btc"]
    ratio = pair["ratio"]
    btc_close = pair["btc"]

    ratio_peak_90d = float(ratio.tail(90).max())
    ratio_dd_pct = float((ratio.iloc[-1] / ratio_peak_90d - 1) * 100)
    btc_ath = float(btc_close.max())
    btc_dd_pct = float((btc_close.iloc[-1] / btc_ath - 1) * 100)

    diverging = (ratio_dd_pct <= -10) and (btc_dd_pct >= -5)

    return {
        "ratio_dd_from_90d_peak_pct": round(ratio_dd_pct, 2),
        "btc_dd_from_ath_pct": round(btc_dd_pct, 2),
        "lead_lag_divergence_flag": bool(diverging),
    }


def ratio_off_cycle_low(mstr: pd.Series, btc: pd.Series) -> dict:
    """
    Playbook Signal #6: MSTR/BTC ratio at multi-year extreme (3–5x off cycle low).
    """
    pair = pd.concat([mstr.rename("mstr"), btc.rename("btc")], axis=1).dropna()
    ratio = pair["mstr"] / pair["btc"]
    cycle_low = float(ratio.tail(730).min())
    multiple = float(ratio.iloc[-1] / cycle_low) if cycle_low else float("nan")
    return {
        "ratio_multiple_off_2y_low": round(multiple, 2),
        "ratio_2y_low": cycle_low,
    }


def atm_issuance_pace(shares_hist: pd.Series | None, current_shares: int) -> dict:
    """
    Playbook Signal #3: Accelerated ATM equity issuance.

    yfinance.get_shares_full returns only filing-date entries, so we reindex
    to a daily grid and forward-fill, then compute share growth over trailing
    30/90/365 days. Acceleration (30d annualized growth > 90d) means Saylor
    is monetizing the premium more aggressively recently.
    """
    null = {
        "shares_30d_growth_pct": None,
        "shares_90d_growth_pct": None,
        "shares_30d_annualized_pct": None,
        "shares_90d_annualized_pct": None,
        "shares_365d_growth_pct": None,
        "shares_acceleration_flag": None,
    }
    if shares_hist is None or len(shares_hist) < 5:
        return null

    s = shares_hist.copy()
    # Append today as the most recent share count if it differs materially.
    today = pd.Timestamp.utcnow().normalize().tz_localize(None)
    if current_shares and (today not in s.index or abs(s.iloc[-1] - current_shares) / max(current_shares, 1) > 0.005):
        s.loc[today] = float(current_shares)
    s = s.sort_index()

    # Reindex to daily and forward-fill so windowed comparisons work.
    daily = pd.date_range(s.index.min(), s.index.max(), freq="D")
    s_daily = s.reindex(daily).ffill()

    if len(s_daily) < 31:
        return null

    last = float(s_daily.iloc[-1])

    def growth(days: int) -> float | None:
        if len(s_daily) <= days:
            return None
        prior = float(s_daily.iloc[-days - 1])
        return (last / prior - 1) * 100 if prior else None

    g30 = growth(30)
    g90 = growth(90)
    g365 = growth(365)
    g30_ann = g30 * (365 / 30) if g30 is not None else None
    g90_ann = g90 * (365 / 90) if g90 is not None else None
    accel = (g30_ann is not None and g90_ann is not None
             and g90_ann > 0 and g30_ann > g90_ann * 1.2)

    return {
        "shares_30d_growth_pct": round(g30, 2) if g30 is not None else None,
        "shares_90d_growth_pct": round(g90, 2) if g90 is not None else None,
        "shares_30d_annualized_pct": round(g30_ann, 2) if g30_ann is not None else None,
        "shares_90d_annualized_pct": round(g90_ann, 2) if g90_ann is not None else None,
        "shares_365d_growth_pct": round(g365, 2) if g365 is not None else None,
        "shares_acceleration_flag": bool(accel) if accel is not None else None,
    }


def gamma_squeeze_signal(rv30: float, mnav: float) -> dict:
    """
    Playbook Signal #5: Blow-off gamma squeeze.
    Compound trigger from the report: RV30 > 100% AND mNAV > 2.5x.
    """
    armed = (not math.isnan(rv30)) and (not math.isnan(mnav)) and rv30 > 100 and mnav > 2.5
    elevated = (not math.isnan(rv30)) and (not math.isnan(mnav)) and rv30 > 80 and mnav > 2.0
    return {
        "gamma_squeeze_armed": bool(armed),
        "gamma_squeeze_elevated": bool(elevated),
    }


def credit_stress(prefs: dict) -> dict:
    """
    Playbook Signal #7: Capital structure stress.
    Aggregate the worst-case yield + max 30d drawdown across the four prefs.
    """
    yields = [v["yield_pct"] for v in prefs.values() if v.get("yield_pct") is not None]
    drawdowns = [v["drawdown_30d_pct"] for v in prefs.values() if v.get("drawdown_30d_pct") is not None]
    max_yield = max(yields) if yields else None
    worst_dd = min(drawdowns) if drawdowns else None
    flag = (max_yield is not None and max_yield > 12) or (worst_dd is not None and worst_dd < -8)
    return {
        "preferred_max_yield_pct": max_yield,
        "preferred_worst_30d_drawdown_pct": worst_dd,
        "credit_stress_flag": bool(flag),
    }


# ──────────────────────────── zoning ────────────────────────────────────
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


# ──────────────────────────── orchestrator ──────────────────────────────
def build_snapshot() -> tuple[dict, pd.Series, pd.Series]:
    print("Fetching BTC holdings…")
    holdings, avg_cost = fetch_btc_holdings()
    print(f"  holdings={holdings:,} BTC")

    print("Fetching prices…")
    p = fetch_prices()
    mstr_px, btc_px, shares = p["mstr_price"], p["btc_price"], p["mstr_shares"]
    mstr_hist, btc_hist = p["mstr_history"], p["btc_history"]

    nav = holdings * btc_px
    market_cap = mstr_px * shares
    mnav = market_cap / nav if nav else float("nan")

    rv30 = realized_vol(mstr_hist, 30)
    rv90 = realized_vol(mstr_hist, 90)
    iv30 = implied_vol_atm("MSTR")

    print("Fetching shares history (for ATM pace)…")
    shares_hist = fetch_shares_history()
    atm = atm_issuance_pace(shares_hist, shares)

    print("Fetching preferred yields…")
    prefs = fetch_preferred_yields()
    credit = credit_stress(prefs)

    print("Fetching BTC funding…")
    funding = fetch_btc_funding()

    lead_lag = lead_lag_divergence(mstr_hist, btc_hist)
    cycle = ratio_off_cycle_low(mstr_hist, btc_hist)
    gamma = gamma_squeeze_signal(rv30, mnav)
    zone, color, action = mnav_zone(mnav)

    snapshot = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # core
        "mstr_price": round(mstr_px, 2),
        "btc_price": round(btc_px, 2),
        "btc_holdings": holdings,
        "btc_avg_cost": round(avg_cost, 2),
        "diluted_shares": shares,
        "market_cap_b": round(market_cap / 1e9, 3),
        "btc_nav_b": round(nav / 1e9, 3),
        # P1
        "mnav": round(mnav, 4),
        "mnav_zone": zone,
        "mnav_color": color,
        "mnav_action": action,
        # P2
        **lead_lag,
        # P3
        **atm,
        # P5
        "realized_vol_30d_pct": round(rv30, 2),
        "realized_vol_90d_pct": round(rv90, 2),
        "implied_vol_30d_pct": round(iv30, 2) if not math.isnan(iv30) else None,
        **gamma,
        # P6
        **cycle,
        # P7
        "preferreds": prefs,
        **credit,
        # P8
        "btc_funding": funding,
    }
    return snapshot, mstr_hist, btc_hist


def append_history(row: dict) -> None:
    cols = [
        "timestamp_utc", "mstr_price", "btc_price", "btc_holdings",
        "diluted_shares", "market_cap_b", "btc_nav_b",
        "mnav",
        "ratio_dd_from_90d_peak_pct", "btc_dd_from_ath_pct", "lead_lag_divergence_flag",
        "shares_30d_growth_pct", "shares_90d_growth_pct", "shares_30d_annualized_pct", "shares_90d_annualized_pct", "shares_acceleration_flag",
        "realized_vol_30d_pct", "realized_vol_90d_pct", "implied_vol_30d_pct",
        "gamma_squeeze_armed", "gamma_squeeze_elevated",
        "ratio_multiple_off_2y_low",
        "preferred_max_yield_pct", "preferred_worst_30d_drawdown_pct", "credit_stress_flag",
    ]
    df_row = pd.DataFrame([{k: row.get(k) for k in cols}])
    if HISTORY.exists():
        existing = pd.read_csv(HISTORY)
        df_row = pd.concat([existing, df_row], ignore_index=True)
        df_row["bucket"] = pd.to_datetime(df_row["timestamp_utc"]).dt.floor("30min")
        df_row = df_row.drop_duplicates("bucket", keep="last").drop(columns=["bucket"])
    df_row.to_csv(HISTORY, index=False)


def write_price_history(mstr_hist: pd.Series, btc_hist: pd.Series) -> None:
    df = pd.concat([mstr_hist.rename("mstr"), btc_hist.rename("btc")], axis=1).dropna()
    df.index.name = "date"
    df = df.tail(730)
    df.to_csv(PRICE_HISTORY)


def main() -> int:
    snapshot, mstr_hist, btc_hist = build_snapshot()
    LATEST.write_text(json.dumps(snapshot, indent=2))
    append_history(snapshot)
    write_price_history(mstr_hist, btc_hist)
    print(f"\nP1 mNAV={snapshot['mnav']}  zone={snapshot['mnav_zone']}")
    print(f"P2 lead-lag div={snapshot['lead_lag_divergence_flag']}  ratio_dd={snapshot['ratio_dd_from_90d_peak_pct']}%  btc_dd={snapshot['btc_dd_from_ath_pct']}%")
    print(f"P3 ATM accel={snapshot['shares_acceleration_flag']}  30d_ann={snapshot['shares_30d_annualized_pct']}%")
    print(f"P5 gamma armed={snapshot['gamma_squeeze_armed']}  elevated={snapshot['gamma_squeeze_elevated']}")
    print(f"P6 ratio off cycle low={snapshot['ratio_multiple_off_2y_low']}x")
    print(f"P7 credit stress={snapshot['credit_stress_flag']}  max_yield={snapshot['preferred_max_yield_pct']}%")
    print(f"P8 BTC funding ann={snapshot['btc_funding']['annualized_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
