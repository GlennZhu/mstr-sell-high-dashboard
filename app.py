"""
MSTR Sell-High Signal Dashboard
================================
Tracks every quantifiable signal that historically marked MSTR cycle tops
(Feb 2021 and Nov 2024) and surfaces non-quantifiable signals as a manual
checklist.

Refreshed every 30 min via GitHub Action (see .github/workflows/update_data.yml).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# ─────────────────────────── config ───────────────────────────
st.set_page_config(
    page_title="MSTR Sell-High Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history.csv"
PRICES = DATA / "price_history.csv"

# ─────────────────────── styling (CSS) ────────────────────────
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #0b1020 0%, #0f1530 100%); }
    .block-container { padding-top: 1.2rem; padding-bottom: 4rem; }

    .hero {
        background: linear-gradient(135deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01));
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 28px 32px;
        margin-bottom: 18px;
    }
    .hero h1 { font-size: 2.0rem; margin: 0 0 6px 0; color: #f5f7ff; letter-spacing: -0.01em; }
    .hero .sub { color: #94a3b8; font-size: 0.92rem; }

    .mnav-card {
        border-radius: 14px; padding: 24px; text-align: center;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.08);
    }
    .mnav-value { font-size: 4.2rem; font-weight: 700; line-height: 1; margin: 4px 0; }
    .mnav-zone  { font-size: 1.4rem; font-weight: 600; letter-spacing: 0.02em; }
    .mnav-action{ font-size: 0.95rem; color: #cbd5e1; margin-top: 6px; }

    .metric-card {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 12px; padding: 14px 16px;
    }
    .metric-label { color: #94a3b8; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; }
    .metric-value { color: #f5f7ff; font-size: 1.5rem; font-weight: 600; margin-top: 4px; }
    .metric-sub   { color: #64748b; font-size: 0.78rem; margin-top: 2px; }

    .section-title {
        color: #f5f7ff; font-size: 1.15rem; font-weight: 600;
        margin: 22px 0 10px 0; padding-bottom: 6px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
    }

    .pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.72rem; font-weight:600; letter-spacing:0.04em; }
    .pill-green  { background:#10b98122; color:#34d399; border:1px solid #10b98155; }
    .pill-yellow { background:#facc1522; color:#fde047; border:1px solid #facc1555; }
    .pill-orange { background:#fb923c22; color:#fdba74; border:1px solid #fb923c55; }
    .pill-red    { background:#ef444422; color:#fca5a5; border:1px solid #ef444455; }
    .pill-grey   { background:#64748b22; color:#cbd5e1; border:1px solid #64748b55; }

    .footer-note { color:#64748b; font-size:0.78rem; margin-top:30px; text-align:center; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────── data loaders ─────────────────────────
@st.cache_data(ttl=300)
def load_latest() -> dict | None:
    if not LATEST.exists():
        return None
    return json.loads(LATEST.read_text())


@st.cache_data(ttl=300)
def load_history() -> pd.DataFrame:
    if not HISTORY.exists():
        return pd.DataFrame()
    df = pd.read_csv(HISTORY, parse_dates=["timestamp_utc"])
    return df.sort_values("timestamp_utc")


@st.cache_data(ttl=300)
def load_prices() -> pd.DataFrame:
    if not PRICES.exists():
        return pd.DataFrame()
    df = pd.read_csv(PRICES, parse_dates=["date"])
    return df.sort_values("date")


# ─────────────────────────── render ───────────────────────────
latest = load_latest()
hist = load_history()
prices = load_prices()

if not latest:
    st.error("No data yet. Run `python scripts/fetch_signals.py` once to seed.")
    st.stop()

ts = datetime.fromisoformat(latest["timestamp_utc"].replace("Z", "+00:00"))
age_min = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)

# Hero
st.markdown(
    f"""
    <div class="hero">
      <h1>📈 MSTR Sell-High Signal Dashboard</h1>
      <div class="sub">
        Every quantifiable signal that marked the Feb-2021 and Nov-2024 cycle tops, refreshed every 30&nbsp;minutes.
        &nbsp;·&nbsp; Last update: <b>{ts.strftime('%Y-%m-%d %H:%M UTC')}</b> ({age_min} min ago)
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Top row: mNAV hero + key metrics ─────────────────────────
left, right = st.columns([1, 2], gap="large")

with left:
    color = latest["mnav_color"]
    st.markdown(
        f"""
        <div class="mnav-card" style="border-color:{color}66; box-shadow:0 0 60px {color}22;">
          <div class="metric-label">Current mNAV (market cap ÷ BTC NAV)</div>
          <div class="mnav-value" style="color:{color};">{latest['mnav']:.2f}x</div>
          <div class="mnav-zone" style="color:{color};">{latest['mnav_zone']}</div>
          <div class="mnav-action">{latest['mnav_action']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Zone legend
    st.markdown(
        """
        <div style="margin-top:14px; font-size:0.78rem; color:#94a3b8; line-height:1.7;">
          <b style="color:#cbd5e1;">mNAV zones</b><br>
          <span class="pill pill-green">&lt;1.0 Fire Sale</span>
          <span class="pill pill-green">1.0–1.5 Accumulate</span>
          <span class="pill pill-yellow">1.5–2.0 Fair</span><br>
          <span class="pill pill-orange">2.0–2.5 Trim</span>
          <span class="pill pill-red">2.5–3.0 Sell</span>
          <span class="pill pill-red">&gt;3.0 Max Sell</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with right:
    cols = st.columns(3)
    metric_specs = [
        ("MSTR Price",            f"${latest['mstr_price']:,.2f}",           f"Diluted shares: {latest['diluted_shares']:,}"),
        ("BTC Spot",              f"${latest['btc_price']:,.0f}",           f"ATH ${latest['btc_ath']:,.0f} · {latest['days_since_btc_ath']}d ago"),
        ("BTC Holdings",          f"{latest['btc_holdings']:,} BTC",        f"Avg cost ${latest['btc_avg_cost']:,.0f}"),
        ("Market Cap",            f"${latest['market_cap_b']:.2f} B",       f"BTC NAV ${latest['btc_nav_b']:.2f} B"),
        ("MSTR / BTC ratio",      f"{latest['mstr_btc_ratio']:.6f}",        (f"z-score 365d: {latest['mstr_btc_ratio_z365']:+.2f}σ" if latest.get('mstr_btc_ratio_z365') is not None else "z-score: n/a")),
        ("BTC drawdown from ATH", f"{latest['btc_drawdown_from_ath_pct']:+.1f}%", "0% = at ATH (cycle peak proximity)"),
        ("MSTR 30d realized vol", f"{latest['realized_vol_30d_pct']:.1f}%", f"90d: {latest['realized_vol_90d_pct']:.1f}%"),
        ("MSTR 30d implied vol",  (f"{latest['implied_vol_30d_pct']:.1f}%" if latest.get('implied_vol_30d_pct') else "n/a"), "ATM call IV"),
        ("MSTR – BTC, 90d",       f"{latest['mstr_minus_btc_90d_pct']:+.1f}%", f"30d {latest['mstr_minus_btc_30d_pct']:+.1f}% · 180d {latest['mstr_minus_btc_180d_pct']:+.1f}%"),
    ]
    for i, (label, value, sub) in enumerate(metric_specs):
        with cols[i % 3]:
            st.markdown(
                f"""
                <div class="metric-card" style="margin-bottom:12px;">
                  <div class="metric-label">{label}</div>
                  <div class="metric-value">{value}</div>
                  <div class="metric-sub">{sub}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

# ── Signal table ─────────────────────────────────────────────
st.markdown('<div class="section-title">🚦 Quantifiable Signal Board</div>', unsafe_allow_html=True)


def status(value: float, thresholds: list[tuple[float, str]]) -> str:
    """Pick a pill class given thresholds [(upper, class), ...] in ascending order."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "pill-grey"
    for upper, klass in thresholds:
        if value < upper:
            return klass
    return thresholds[-1][1]


signals = [
    {
        "Signal": "mNAV (premium to BTC NAV)",
        "Reading": f"{latest['mnav']:.2f}x",
        "Trim line": "≥ 2.0x",
        "Sell line": "≥ 2.5x",
        "Status": status(latest["mnav"], [(1.5, "pill-green"), (2.0, "pill-yellow"), (2.5, "pill-orange"), (99, "pill-red")]),
        "Why it works": "Both prior tops (2.7x Feb-21, ~3.1x Nov-24) printed here; premium is the first thing to evaporate.",
    },
    {
        "Signal": "MSTR/BTC 365-d z-score",
        "Reading": (f"{latest['mstr_btc_ratio_z365']:+.2f}σ" if latest.get('mstr_btc_ratio_z365') is not None else "n/a"),
        "Trim line": "≥ +1.5σ",
        "Sell line": "≥ +2.0σ",
        "Status": status(latest.get("mstr_btc_ratio_z365"), [(1.0, "pill-green"), (1.5, "pill-yellow"), (2.0, "pill-orange"), (99, "pill-red")]),
        "Why it works": "MSTR outperforming BTC by an extreme margin = premium expansion phase, mean-reverts hard.",
    },
    {
        "Signal": "MSTR – BTC return, trailing 90d",
        "Reading": f"{latest['mstr_minus_btc_90d_pct']:+.1f}%",
        "Trim line": "≥ +50%",
        "Sell line": "≥ +100%",
        "Status": status(latest["mstr_minus_btc_90d_pct"], [(30, "pill-green"), (50, "pill-yellow"), (100, "pill-orange"), (1e9, "pill-red")]),
        "Why it works": "Crowding indicator — cycle 1 & 2 both showed extreme MSTR>BTC outperformance into the top.",
    },
    {
        "Signal": "30-d realized vol",
        "Reading": f"{latest['realized_vol_30d_pct']:.0f}%",
        "Trim line": "≥ 80%",
        "Sell line": "≥ 100%",
        "Status": status(latest["realized_vol_30d_pct"], [(60, "pill-green"), (80, "pill-yellow"), (100, "pill-orange"), (1e9, "pill-red")]),
        "Why it works": "Blow-off volatility + high mNAV is the classic gamma-squeeze top combo (Nov-24).",
    },
    {
        "Signal": "30-d ATM implied vol",
        "Reading": (f"{latest['implied_vol_30d_pct']:.0f}%" if latest.get("implied_vol_30d_pct") else "n/a"),
        "Trim line": "≥ 90%",
        "Sell line": "≥ 120%",
        "Status": status(latest.get("implied_vol_30d_pct"), [(70, "pill-green"), (90, "pill-yellow"), (120, "pill-orange"), (1e9, "pill-red")]),
        "Why it works": "Expensive options = market pricing in continued melt-up. Crush after = top in.",
    },
    {
        "Signal": "BTC drawdown from ATH",
        "Reading": f"{latest['btc_drawdown_from_ath_pct']:+.1f}%",
        "Trim line": "0 to -5%",
        "Sell line": "At new ATH",
        "Status": status(latest["btc_drawdown_from_ath_pct"], [(-15, "pill-green"), (-5, "pill-yellow"), (0, "pill-orange"), (1e9, "pill-red")]),
        "Why it works": "MSTR tends to top BEFORE BTC. If BTC is at ATH and mNAV is hot, the equity peak is imminent.",
    },
    {
        "Signal": "Days since BTC ATH",
        "Reading": f"{latest['days_since_btc_ath']} d",
        "Trim line": "< 30 d",
        "Sell line": "< 7 d",
        "Status": status(-latest["days_since_btc_ath"], [(-90, "pill-green"), (-30, "pill-yellow"), (-7, "pill-orange"), (1e9, "pill-red")]),
        "Why it works": "Recency to ATH compounds the urgency of the trim/sell signal.",
    },
]

# Render as HTML table for nicer styling
rows_html = "".join(
    f"""
    <tr>
      <td style="padding:10px 12px; color:#f1f5f9;">{s['Signal']}</td>
      <td style="padding:10px 12px; color:#f1f5f9; font-variant-numeric:tabular-nums;"><b>{s['Reading']}</b></td>
      <td style="padding:10px 12px; color:#94a3b8;">{s['Trim line']}</td>
      <td style="padding:10px 12px; color:#94a3b8;">{s['Sell line']}</td>
      <td style="padding:10px 12px;"><span class="pill {s['Status']}">{s['Status'].split('-')[1].upper()}</span></td>
      <td style="padding:10px 12px; color:#94a3b8; font-size:0.86rem;">{s['Why it works']}</td>
    </tr>
    """
    for s in signals
)
st.markdown(
    f"""
    <table style="width:100%; border-collapse:collapse; background:rgba(255,255,255,0.02);
                  border:1px solid rgba(255,255,255,0.08); border-radius:12px; overflow:hidden;">
      <thead>
        <tr style="background:rgba(255,255,255,0.04); color:#cbd5e1; text-transform:uppercase; font-size:0.74rem; letter-spacing:0.06em;">
          <th style="padding:12px; text-align:left;">Signal</th>
          <th style="padding:12px; text-align:left;">Reading</th>
          <th style="padding:12px; text-align:left;">Trim line</th>
          <th style="padding:12px; text-align:left;">Sell line</th>
          <th style="padding:12px; text-align:left;">Status</th>
          <th style="padding:12px; text-align:left;">Rationale</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    """,
    unsafe_allow_html=True,
)

# ── Charts ───────────────────────────────────────────────────
st.markdown('<div class="section-title">📊 History</div>', unsafe_allow_html=True)

if not hist.empty and len(hist) > 1:
    c1, c2 = st.columns(2, gap="large")

    with c1:
        fig = go.Figure()
        fig.add_hrect(y0=0, y1=1.0, fillcolor="#10b981", opacity=0.10, line_width=0)
        fig.add_hrect(y0=1.0, y1=1.5, fillcolor="#22c55e", opacity=0.08, line_width=0)
        fig.add_hrect(y0=1.5, y1=2.0, fillcolor="#facc15", opacity=0.08, line_width=0)
        fig.add_hrect(y0=2.0, y1=2.5, fillcolor="#fb923c", opacity=0.10, line_width=0)
        fig.add_hrect(y0=2.5, y1=4.0, fillcolor="#ef4444", opacity=0.12, line_width=0)
        fig.add_trace(go.Scatter(
            x=hist["timestamp_utc"], y=hist["mnav"],
            mode="lines", line=dict(color="#a5b4fc", width=2.2),
            name="mNAV",
        ))
        fig.update_layout(
            title="mNAV over time", title_font_color="#f1f5f9",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)", title="mNAV"),
            height=320, margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(
            x=hist["timestamp_utc"], y=hist["realized_vol_30d_pct"],
            mode="lines", line=dict(color="#f472b6", width=2),
            name="30d RV",
        ), secondary_y=False)
        if "implied_vol_30d_pct" in hist:
            fig.add_trace(go.Scatter(
                x=hist["timestamp_utc"], y=hist["implied_vol_30d_pct"],
                mode="lines", line=dict(color="#60a5fa", width=2, dash="dot"),
                name="30d IV",
            ), secondary_y=False)
        fig.add_trace(go.Scatter(
            x=hist["timestamp_utc"], y=hist["mnav"],
            mode="lines", line=dict(color="#fbbf24", width=1.6),
            name="mNAV",
        ), secondary_y=True)
        fig.update_layout(
            title="Vol & mNAV", title_font_color="#f1f5f9",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)", title="Vol %"),
            yaxis2=dict(title="mNAV", showgrid=False),
            height=320, margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Snapshot history will appear after a few scheduled runs (every 30 min via GitHub Actions).")

if not prices.empty:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(
        x=prices["date"], y=prices["mstr"], mode="lines",
        line=dict(color="#a5b4fc", width=2), name="MSTR",
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=prices["date"], y=prices["btc"], mode="lines",
        line=dict(color="#fbbf24", width=2), name="BTC",
    ), secondary_y=True)
    fig.update_layout(
        title="MSTR vs BTC — daily close (2y)", title_font_color="#f1f5f9",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cbd5e1"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.06)", title="MSTR ($)"),
        yaxis2=dict(title="BTC ($)", showgrid=False),
        height=360, margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

# ── Manual signal checklist ──────────────────────────────────
st.markdown('<div class="section-title">📝 Manual / Qualitative Signals (track yourself)</div>', unsafe_allow_html=True)

manual = [
    ("Catalyst calendar exhausted",
     "Are the major structural catalysts (ETF launch, options launch, index inclusion, major split, large announcement) all *behind* us with nothing big left in the queue? In Nov-24, after IBIT options + Nasdaq-100 inclusion talks, the queue was empty."),
    ("Saylor media tour intensity",
     "Is Saylor on every podcast / CNBC / X-spaces in a single week? Peak media presence historically clusters within ~30 days of the cycle top."),
    ("Retail mania / social sentiment",
     "Are MSTR memes on r/wallstreetbets, FinTwit, TikTok daily? Are random people asking you about MSTR? Confirms crowding."),
    ("Accelerating ATM equity issuance",
     "Watch SEC 8-K filings for weekly ATM common-stock sales. When they spike *while* mNAV is hot (e.g. Nov–Dec 2024), management is monetizing the premium — follow them."),
    ("Convertible bond refinancing health",
     "Are new converts pricing tight (low yield, low conversion premium)? A stalled refi or widening STRC/STRF/STRK/STRD credit spreads = stress; sell before forced selling."),
    ("Index inclusion narrative peaking",
     "S&P 500 / Nasdaq-100 inclusion stories tend to peak right at the top — the news IS the catalyst, post-inclusion is usually a fade."),
    ("Macro liquidity turning",
     "Fed pivot to hawkish, DXY ripping, real yields rising, 2y > 10y inverting further — risk-asset top conditions."),
    ("Copycat treasuries proliferating",
     "Are 10+ new public-co BTC treasuries being announced per month? Late-cycle behavior — peak supply of leveraged BTC vehicles."),
    ("Insider selling at MSTR / Strategy",
     "Form 4 filings — any unusual insider sales by Saylor or other officers."),
    ("BTC futures funding extremely positive",
     "Persistently > +0.10%/8h funding on perps for weeks = leveraged longs at max risk."),
]

cols = st.columns(2, gap="large")
for i, (title, body) in enumerate(manual):
    with cols[i % 2]:
        with st.container():
            checked = st.checkbox(f"**{title}**", key=f"manual_{i}")
            st.markdown(
                f"<div style='color:#94a3b8; font-size:0.85rem; margin:-6px 0 12px 28px;'>{body}</div>",
                unsafe_allow_html=True,
            )

st.markdown(
    """
    <div class="footer-note">
      Data: yfinance (MSTR, BTC-USD) · bitcointreasuries.net (holdings, with hardcoded fallback).<br>
      <b>Not financial advice.</b> The mNAV thresholds are calibrated to the Feb-2021 (~2.7x) and Nov-2024 (~3.1x) tops; future cycles may differ.<br>
      Snapshots refresh every 30 min via GitHub Actions; Streamlit Cloud auto-redeploys on commit.
    </div>
    """,
    unsafe_allow_html=True,
)
