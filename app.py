"""
MSTR Sell-High Signal Dashboard
================================
Each panel below maps to one of the 8 sell-high playbook signals derived from
the deep-research report. Quantifiable signals are computed by
scripts/fetch_signals.py every 30 min. Non-quantifiable signals are surfaced
as a checklist for manual tracking.

Playbook signals (source: report on MSTR cycle tops, Feb-2021 + Nov-2024):
  P1  mNAV ≥ 2.5–3.0x = euphoria zone                              [auto]
  P2  MSTR tops BEFORE BTC — lead-lag divergence                   [auto]
  P3  Accelerated ATM equity issuance at high mNAV                 [auto]
  P4  Catalyst calendar exhaustion                                 [manual]
  P5  Gamma-squeeze blow-off (RV30 > 100% AND mNAV > 2.5x)         [auto]
  P6  MSTR/BTC ratio at multi-year extreme (off cycle low)         [auto]
  P7  STRC/STRF/STRK/STRD credit-spread stress                     [auto]
  P8  BTC perp funding extremely positive (leverage saturation)    [auto]
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
        margin: 26px 0 10px 0; padding-bottom: 6px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
    }

    .pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.72rem; font-weight:600; letter-spacing:0.04em; }
    .pill-green  { background:#10b98122; color:#34d399; border:1px solid #10b98155; }
    .pill-yellow { background:#facc1522; color:#fde047; border:1px solid #facc1555; }
    .pill-orange { background:#fb923c22; color:#fdba74; border:1px solid #fb923c55; }
    .pill-red    { background:#ef444422; color:#fca5a5; border:1px solid #ef444455; }
    .pill-grey   { background:#64748b22; color:#cbd5e1; border:1px solid #64748b55; }

    .signal-card {
        background: rgba(255,255,255,0.025);
        border: 1px solid rgba(255,255,255,0.07);
        border-left-width: 4px;
        border-radius: 10px;
        padding: 14px 18px; margin-bottom: 12px;
    }
    .signal-card .pnum { font-size:0.74rem; font-weight:700; letter-spacing:0.08em;
        color:#94a3b8; text-transform:uppercase; }
    .signal-card .name { color:#f1f5f9; font-size:1.05rem; font-weight:600; margin: 2px 0 6px 0; }
    .signal-card .read { color:#f1f5f9; font-size:1.6rem; font-weight:700; font-variant-numeric:tabular-nums; }
    .signal-card .meta { color:#94a3b8; font-size:0.83rem; margin-top:6px; }
    .signal-card .why  { color:#cbd5e1; font-size:0.84rem; margin-top:8px; line-height:1.5; }

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


# ─────────────────── signal-card helpers ──────────────────────
COLORS = {
    "green":  "#34d399",
    "yellow": "#fde047",
    "orange": "#fdba74",
    "red":    "#fca5a5",
    "grey":   "#94a3b8",
}
PILLS = {
    "green":  "pill-green",  "yellow": "pill-yellow",
    "orange": "pill-orange", "red":    "pill-red", "grey": "pill-grey",
}


def card(p_num: str, name: str, reading: str, status_color: str, status_label: str,
         meta: str, why: str, side_color: str | None = None) -> str:
    border = COLORS[side_color or status_color]
    return f"""
    <div class="signal-card" style="border-left-color:{border};">
      <div class="pnum">Playbook Signal {p_num}</div>
      <div class="name">{name}</div>
      <div class="read" style="color:{COLORS[status_color]};">{reading}
        &nbsp;<span class="pill {PILLS[status_color]}" style="vertical-align:middle;">{status_label}</span>
      </div>
      <div class="meta">{meta}</div>
      <div class="why">{why}</div>
    </div>
    """


def grade(value, ladder: list[tuple], reverse: bool = False) -> tuple[str, str]:
    """
    ladder: list of (threshold, label, color) in *ascending* threshold order.
    For ascending values (e.g. mNAV), the LAST tier matched is the status.
    For reverse=True (lower = worse), invert.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "grey", "N/A"
    if reverse:
        for thr, label, color in reversed(ladder):
            if value <= thr:
                return color, label
        return ladder[0][2], ladder[0][1]
    chosen_label, chosen_color = ladder[0][1], ladder[0][2]
    for thr, label, color in ladder:
        if value >= thr:
            chosen_label, chosen_color = label, color
    return chosen_color, chosen_label


# ─────────────────────────── render ───────────────────────────
latest = load_latest()
hist = load_history()
prices = load_prices()

if not latest:
    st.error("No data yet. Run `python scripts/fetch_signals.py` once to seed.")
    st.stop()

ts = datetime.fromisoformat(latest["timestamp_utc"].replace("Z", "+00:00"))
age_min = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)

st.markdown(
    f"""
    <div class="hero">
      <h1>📈 MSTR Sell-High Signal Dashboard</h1>
      <div class="sub">
        Eight playbook signals from the deep-research report on MSTR cycle tops (Feb-2021 + Nov-2024).
        &nbsp;·&nbsp; Refreshed every 30 min via GitHub Actions.
        &nbsp;·&nbsp; Last update: <b>{ts.strftime('%Y-%m-%d %H:%M UTC')}</b> ({age_min} min ago)
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── mNAV hero + base metrics ────────────────────────────────
left, right = st.columns([1, 2], gap="large")

with left:
    color = latest["mnav_color"]
    st.markdown(
        f"""
        <div class="mnav-card" style="border-color:{color}66; box-shadow:0 0 60px {color}22;">
          <div class="metric-label">P1 · Current mNAV (market cap ÷ BTC NAV)</div>
          <div class="mnav-value" style="color:{color};">{latest['mnav']:.2f}x</div>
          <div class="mnav-zone" style="color:{color};">{latest['mnav_zone']}</div>
          <div class="mnav-action">{latest['mnav_action']}</div>
        </div>
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
        ("MSTR Price",      f"${latest['mstr_price']:,.2f}",      f"Diluted shares: {latest['diluted_shares']:,}"),
        ("BTC Spot",        f"${latest['btc_price']:,.0f}",       f"Avg cost ${latest['btc_avg_cost']:,.0f} · holdings {latest['btc_holdings']:,}"),
        ("Market Cap",      f"${latest['market_cap_b']:.2f} B",   f"BTC NAV ${latest['btc_nav_b']:.2f} B"),
        ("Implied Vol 30d", (f"{latest['implied_vol_30d_pct']:.0f}%" if latest.get('implied_vol_30d_pct') else "n/a"), "ATM call IV"),
        ("Realized Vol 30d", f"{latest['realized_vol_30d_pct']:.0f}%", f"90d: {latest['realized_vol_90d_pct']:.0f}%"),
        ("BTC Funding (ann.)", (f"{latest['btc_funding']['annualized_pct']:+.1f}%" if latest['btc_funding'].get('annualized_pct') is not None else "n/a"),
                              f"7d avg per 8h: {latest['btc_funding'].get('avg_7d_pct_per_8h', 'n/a')}%"),
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

# ── PLAYBOOK SIGNAL BOARD ────────────────────────────────────
st.markdown('<div class="section-title">🚦 Quantifiable Playbook Signals</div>', unsafe_allow_html=True)

# P1 — mNAV (already shown as hero card, but include compact version in board for completeness)
mnav = latest["mnav"]
p1_status_color, p1_status_label = grade(mnav, [
    (0,    "FIRE SALE",  "green"),
    (1.5,  "ACCUMULATE", "green"),
    (2.0,  "FAIR",       "yellow"),
    (2.5,  "TRIM",       "orange"),
    (3.0,  "SELL",       "red"),
])

# P2 — Lead-lag divergence
ratio_dd = latest["ratio_dd_from_90d_peak_pct"]
btc_dd = latest["btc_dd_from_ath_pct"]
p2_diverging = latest["lead_lag_divergence_flag"]
if p2_diverging:
    p2_color, p2_label = "red", "DIVERGING"
elif ratio_dd > -3 and btc_dd > -5:
    p2_color, p2_label = "orange", "BOTH HOT"
elif btc_dd > -10:
    p2_color, p2_label = "yellow", "WATCH"
else:
    p2_color, p2_label = "green", "DORMANT"

# P3 — ATM issuance pace
g30_ann = latest["shares_30d_annualized_pct"]
g90_ann = latest["shares_90d_annualized_pct"]
accel_flag = latest["shares_acceleration_flag"]
if accel_flag and (g30_ann or 0) > 30 and mnav > 2.0:
    p3_color, p3_label = "red", "ACCELERATING + HOT"
elif accel_flag:
    p3_color, p3_label = "orange", "ACCELERATING"
elif g30_ann is None:
    p3_color, p3_label = "grey", "N/A"
elif g30_ann > 25:
    p3_color, p3_label = "yellow", "ELEVATED"
else:
    p3_color, p3_label = "green", "NORMAL"

# P5 — Gamma-squeeze blow-off
rv30 = latest["realized_vol_30d_pct"]
if latest["gamma_squeeze_armed"]:
    p5_color, p5_label = "red", "ARMED"
elif latest["gamma_squeeze_elevated"]:
    p5_color, p5_label = "orange", "ELEVATED"
elif rv30 > 60:
    p5_color, p5_label = "yellow", "VOL HIGH"
else:
    p5_color, p5_label = "green", "QUIET"

# P6 — Multiple off cycle low
mult = latest["ratio_multiple_off_2y_low"]
p6_color, p6_label = grade(mult, [
    (0,   "EARLY CYCLE", "green"),
    (1.5, "MID CYCLE",   "yellow"),
    (2.5, "LATE CYCLE",  "orange"),
    (3.5, "EXTREME",     "red"),
])

# P7 — Credit stress
max_yld = latest["preferred_max_yield_pct"]
worst_dd = latest["preferred_worst_30d_drawdown_pct"]
if latest["credit_stress_flag"]:
    p7_color, p7_label = "red", "STRESS"
elif (max_yld or 0) > 10 or (worst_dd or 0) < -5:
    p7_color, p7_label = "orange", "ELEVATED"
elif max_yld is None:
    p7_color, p7_label = "grey", "N/A"
else:
    p7_color, p7_label = "green", "HEALTHY"

# P8 — BTC perp funding
fund_ann = latest["btc_funding"].get("annualized_pct")
p8_color, p8_label = grade(fund_ann, [
    (-1e9, "NEGATIVE",   "green"),
    (5,    "NORMAL",     "green"),
    (15,   "ELEVATED",   "yellow"),
    (30,   "HOT",        "orange"),
    (60,   "EUPHORIC",   "red"),
])

# Render P1 → P8 cards in a 2-column grid
cards_html = []

cards_html.append(card(
    "#1", "mNAV — premium to BTC NAV",
    f"{mnav:.2f}x",
    p1_status_color, p1_status_label,
    f"Trim ≥ 2.0x &nbsp;·&nbsp; Sell ≥ 2.5x &nbsp;·&nbsp; Max sell ≥ 3.0x",
    "Both prior tops printed here — Feb 2021 ≈ 2.7x, Nov 2024 ≈ 3.1x. Premium is the first thing to evaporate."
))

cards_html.append(card(
    "#2", "MSTR tops BEFORE BTC — lead-lag divergence",
    f"{ratio_dd:+.1f}% / {btc_dd:+.1f}%",
    p2_color, p2_label,
    f"MSTR/BTC ratio off its 90d peak: <b>{ratio_dd:+.2f}%</b> &nbsp;·&nbsp; BTC off ATH: <b>{btc_dd:+.2f}%</b><br>"
    f"Trigger: ratio ≤ −10% from 90d peak <i>while</i> BTC ≥ −5% from ATH",
    "In 2021 MSTR topped 9 months before BTC. Divergence (ratio rolling over while BTC pinned to ATH) is the cleanest in-real-time tell."
))

cards_html.append(card(
    "#3", "Accelerated ATM equity issuance",
    (f"{g30_ann:+.0f}% (30d ann.)" if g30_ann is not None else "—"),
    p3_color, p3_label,
    f"30d annualized share growth: <b>{g30_ann if g30_ann is not None else 'n/a'}%</b><br>"
    f"90d annualized: <b>{g90_ann if g90_ann is not None else 'n/a'}%</b> &nbsp;·&nbsp; "
    f"Acceleration trigger: 30d &gt; 90d × 1.2",
    "Accelerating ATM dilution at high mNAV = Saylor confirming the premium is rich. "
    "Verify in SEC 8-K filings (item 8.01); this signal is a proxy."
))

cards_html.append(card(
    "#5", "Gamma-squeeze blow-off (compound)",
    f"RV30 {rv30:.0f}%  ·  mNAV {mnav:.2f}x",
    p5_color, p5_label,
    f"<b>ARMED</b> when RV30 &gt; 100% AND mNAV &gt; 2.5x  &nbsp;·&nbsp; "
    f"<b>ELEVATED</b> when RV30 &gt; 80% AND mNAV &gt; 2.0x",
    "Blow-off vol with rich premium = options-driven top, classic Nov-2024 setup. "
    "When IV crushes after this fires, the same gamma reverses violently."
))

cards_html.append(card(
    "#6", "MSTR/BTC ratio — multiple off cycle low",
    f"{mult:.2f}x",
    p6_color, p6_label,
    f"Trailing-2y ratio low: <b>{latest['ratio_2y_low']:.6f}</b><br>"
    f"Late cycle ≥ 2.5x &nbsp;·&nbsp; Extreme ≥ 3.5x (matches 3–5x off cycle low rule from playbook)",
    "Crowding indicator. When MSTR has outperformed BTC 3–5x off the cycle low, the leverage trade is full — historically mean-reverts hard."
))

cards_html.append(card(
    "#7", "STRC/STRF/STRK/STRD credit-spread stress",
    (f"max yield {max_yld:.1f}%" if max_yld is not None else "n/a"),
    p7_color, p7_label,
    f"Worst preferred 30d drawdown: <b>{worst_dd if worst_dd is not None else 'n/a'}%</b><br>"
    f"Stress trigger: max yield &gt; 12% or any preferred −8% in 30d",
    "MSTR's capital structure depends on rolling cheap converts/preferreds. Widening spreads = funding stress before forced selling — "
    "Dec-2025 STRD scrutiny was an early warning."
))

cards_html.append(card(
    "#8", "BTC perp funding — leverage saturation",
    (f"{fund_ann:+.1f}% (ann.)" if fund_ann is not None else "n/a"),
    p8_color, p8_label,
    f"Latest 8h: <b>{latest['btc_funding'].get('latest_pct_per_8h', 'n/a')}%</b> &nbsp;·&nbsp; "
    f"7d avg 8h: <b>{latest['btc_funding'].get('avg_7d_pct_per_8h', 'n/a')}%</b><br>"
    f"Hot ≥ 30%/yr &nbsp;·&nbsp; Euphoric ≥ 60%/yr &nbsp;·&nbsp; OKX BTC-USDT perp",
    "Persistently positive funding = leveraged longs paying through the nose. "
    "Sustained &gt;0.10%/8h (≈110%/yr) historically marks BTC tops within weeks."
))

c1, c2 = st.columns(2, gap="medium")
for i, html in enumerate(cards_html):
    (c1 if i % 2 == 0 else c2).markdown(html, unsafe_allow_html=True)

# ── Per-preferred breakdown ─────────────────────────────────
prefs = latest.get("preferreds") or {}
if prefs:
    st.markdown('<div class="section-title">💳 Preferreds (P7 detail)</div>', unsafe_allow_html=True)
    cols = st.columns(len(prefs))
    for i, (sym, v) in enumerate(prefs.items()):
        with cols[i]:
            yld_str = f"{v['yield_pct']:.2f}%" if v.get("yield_pct") is not None else "n/a"
            dd_str = f"{v['drawdown_30d_pct']:+.1f}%" if v.get("drawdown_30d_pct") is not None else "n/a"
            px_str = f"${v['price']:.2f}" if v.get("price") is not None else "n/a"
            st.markdown(
                f"""
                <div class="metric-card">
                  <div class="metric-label">{sym}</div>
                  <div class="metric-value">{yld_str}</div>
                  <div class="metric-sub">Price {px_str} &nbsp;·&nbsp; 30d DD {dd_str}</div>
                </div>
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
            title="P1 · mNAV over time", title_font_color="#f1f5f9",
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
            x=hist["timestamp_utc"], y=hist["ratio_multiple_off_2y_low"],
            mode="lines", line=dict(color="#fbbf24", width=2.2),
            name="P6 ratio multiple",
        ), secondary_y=False)
        fig.add_trace(go.Scatter(
            x=hist["timestamp_utc"], y=hist["realized_vol_30d_pct"],
            mode="lines", line=dict(color="#f472b6", width=1.6, dash="dot"),
            name="P5 RV30",
        ), secondary_y=True)
        fig.update_layout(
            title="P6 ratio multiple + P5 vol", title_font_color="#f1f5f9",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.06)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.06)", title="Ratio multiple"),
            yaxis2=dict(title="RV30 %", showgrid=False),
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

# ── P4 + qualitative manual checklist ────────────────────────
st.markdown('<div class="section-title">📝 P4 + Qualitative Signals (track manually)</div>', unsafe_allow_html=True)

st.markdown(
    "<div style='color:#94a3b8; font-size:0.86rem; margin-bottom:12px;'>"
    "These signals are either inherently subjective or require sources without reliable APIs. "
    "Tick them off as confirmation alongside the quantifiable signals above."
    "</div>",
    unsafe_allow_html=True,
)

manual = [
    ("P4 · Catalyst calendar exhausted",
     "The KEY MANUAL signal from the playbook. Are the major structural catalysts (ETF launch, options launch, index inclusion, major split, large announcement) all *behind* us with nothing big left in the queue? In Nov-2024, after IBIT options + Nasdaq-100 inclusion talks, the queue was empty and the market topped within weeks."),
    ("Saylor media tour intensity",
     "Is Saylor on every podcast / CNBC / X-spaces in a single week? Peak media presence historically clusters within ~30 days of the cycle top."),
    ("Retail mania / social sentiment",
     "Are MSTR memes on r/wallstreetbets, FinTwit, TikTok daily? Are random people asking you about MSTR? Confirms crowding."),
    ("Index inclusion narrative peaking",
     "S&P 500 / Nasdaq-100 inclusion stories tend to peak right at the top — the news IS the catalyst, post-inclusion is usually a fade."),
    ("Macro liquidity turning hawkish",
     "Fed pivot to hawkish, DXY ripping, real yields rising — risk-asset top conditions."),
    ("Copycat treasuries proliferating",
     "Are 10+ new public-co BTC treasuries being announced per month? Late-cycle behavior — peak supply of leveraged BTC vehicles."),
    ("Insider Form 4 selling",
     "Any unusual sales by Saylor or other officers (SEC EDGAR Form 4 filings)."),
    ("Convertible refinancing deteriorating",
     "Stalled converts, wider conversion premiums, or new issues priced at higher coupons. Complementary to P7 — preferred yields catch the same stress."),
]

cols = st.columns(2, gap="large")
for i, (title, body) in enumerate(manual):
    with cols[i % 2]:
        with st.container():
            st.checkbox(f"**{title}**", key=f"manual_{i}")
            st.markdown(
                f"<div style='color:#94a3b8; font-size:0.85rem; margin:-6px 0 12px 28px;'>{body}</div>",
                unsafe_allow_html=True,
            )

st.markdown(
    """
    <div class="footer-note">
      Data: yfinance (MSTR, BTC-USD, preferreds, share-count history) · bitcointreasuries.net (BTC holdings) · OKX (BTC perp funding).<br>
      Signal calibration anchored to Feb-2021 (~2.7x mNAV) and Nov-2024 (~3.1x mNAV) tops. Future cycles may differ.<br>
      <b>Not financial advice.</b> Snapshots refresh every 30 min via GitHub Actions; Streamlit Cloud auto-redeploys on commit.
    </div>
    """,
    unsafe_allow_html=True,
)
