"""
MSTR Sell-High Signal Dashboard
================================
Each panel below maps to one of the 8 sell-high playbook signals derived from
the deep-research report. Quantifiable signals are computed by
scripts/fetch_signals.py every 30 min into data/daily_history.csv. The app
reads only from disk — no API calls during page load.

Playbook signals:
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
DAILY = DATA / "daily_history.csv"

# ─────────────────────── styling (CSS) ────────────────────────
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg, #0b1020 0%, #0f1530 100%); }
    .block-container { padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1500px; }

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
        color: #f5f7ff; font-size: 1.20rem; font-weight: 600;
        margin: 28px 0 14px 0; padding-bottom: 8px;
        border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    .section-title small { color:#94a3b8; font-weight:400; font-size:0.85rem; margin-left:10px; }

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
        border-radius: 12px;
        padding: 16px 18px 6px 18px;
        margin-bottom: 14px;
    }
    .signal-card .pnum { font-size:0.74rem; font-weight:700; letter-spacing:0.08em;
        color:#94a3b8; text-transform:uppercase; }
    .signal-card .name { color:#f1f5f9; font-size:1.05rem; font-weight:600; margin: 2px 0 6px 0; }
    .signal-card .read { color:#f1f5f9; font-size:1.55rem; font-weight:700; font-variant-numeric:tabular-nums; }
    .signal-card .meta { color:#94a3b8; font-size:0.83rem; margin-top:6px; }
    .signal-card .why  { color:#cbd5e1; font-size:0.83rem; margin-top:8px; line-height:1.5; }

    .footer-note { color:#64748b; font-size:0.78rem; margin-top:30px; text-align:center; line-height:1.6; }

    /* tighten plotly chart margin in cards */
    .stPlotlyChart { margin-top: -10px; }
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
def load_intraday() -> pd.DataFrame:
    if not HISTORY.exists():
        return pd.DataFrame()
    df = pd.read_csv(HISTORY, parse_dates=["timestamp_utc"])
    return df.sort_values("timestamp_utc")


@st.cache_data(ttl=300)
def load_daily() -> pd.DataFrame:
    if not DAILY.exists():
        return pd.DataFrame()
    df = pd.read_csv(DAILY, parse_dates=["date"])
    return df.sort_values("date").set_index("date")


# ─────────────────── plot/style helpers ──────────────────────
COLORS = {
    "green":  "#34d399", "yellow": "#fde047",
    "orange": "#fdba74", "red":    "#fca5a5", "grey": "#94a3b8",
}
PILLS = {
    "green":  "pill-green",  "yellow": "pill-yellow",
    "orange": "pill-orange", "red":    "pill-red", "grey": "pill-grey",
}


def base_layout(title: str | None = None, height: int = 230) -> dict:
    return dict(
        title=dict(text=title, font=dict(color="#cbd5e1", size=12)) if title else None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cbd5e1", size=11),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)", showspikes=False),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", showspikes=False),
        height=height,
        margin=dict(l=10, r=10, t=24 if title else 8, b=8),
        hovermode="x unified",
        showlegend=False,
    )


def card_html(p_num: str, name: str, reading: str, status_color: str,
              status_label: str, meta: str, why: str) -> str:
    border = COLORS[status_color]
    return f"""
    <div class="signal-card" style="border-left-color:{border};">
      <div class="pnum">Playbook Signal {p_num}</div>
      <div class="name">{name}</div>
      <div class="read" style="color:{COLORS[status_color]};">{reading}
        &nbsp;<span class="pill {PILLS[status_color]}" style="vertical-align:middle;">{status_label}</span>
      </div>
      <div class="meta">{meta}</div>
    </div>
    """


def card_footer(why: str) -> str:
    return f"""
    <div style="background: rgba(255,255,255,0.025);
                border: 1px solid rgba(255,255,255,0.07);
                border-top: none;
                border-radius: 0 0 12px 12px;
                padding: 0 18px 14px 18px;
                margin-top: -14px; margin-bottom: 14px;
                color:#cbd5e1; font-size:0.83rem; line-height:1.5;">
      {why}
    </div>
    """


def grade(value, ladder: list[tuple]) -> tuple[str, str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "grey", "N/A"
    chosen_color, chosen_label = ladder[0][2], ladder[0][1]
    for thr, label, color in ladder:
        if value >= thr:
            chosen_label, chosen_color = label, color
    return chosen_color, chosen_label


# ─────────────────── per-signal chart builders ───────────────
def chart_p1_mnav(daily: pd.DataFrame) -> go.Figure:
    s = daily["mnav"].dropna()
    fig = go.Figure()
    # zone bands
    bands = [(0, 1.0, "#10b981"), (1.0, 1.5, "#22c55e"), (1.5, 2.0, "#facc15"),
             (2.0, 2.5, "#fb923c"), (2.5, 4.5, "#ef4444")]
    for y0, y1, c in bands:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=c, opacity=0.07, line_width=0, layer="below")
    fig.add_hline(y=2.5, line_dash="dot", line_color="#ef4444", opacity=0.5,
                  annotation_text="Sell", annotation_position="top right",
                  annotation=dict(font=dict(color="#fca5a5", size=10)))
    fig.add_hline(y=2.0, line_dash="dot", line_color="#fb923c", opacity=0.5,
                  annotation_text="Trim", annotation_position="top right",
                  annotation=dict(font=dict(color="#fdba74", size=10)))
    fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines",
                             line=dict(color="#a5b4fc", width=2), name="mNAV",
                             hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}x<extra></extra>"))
    fig.update_layout(**base_layout())
    fig.update_yaxes(title=None, ticksuffix="x")
    return fig


def chart_p2_lead_lag(daily: pd.DataFrame) -> go.Figure:
    df = daily[["ratio_dd_from_90d_peak_pct", "btc_dd_from_ath_pct"]].dropna(how="all")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ratio_dd_from_90d_peak_pct"], mode="lines",
        line=dict(color="#a5b4fc", width=2), name="MSTR/BTC ratio off 90d peak",
        hovertemplate="%{x|%Y-%m-%d}: ratio dd %{y:+.1f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["btc_dd_from_ath_pct"], mode="lines",
        line=dict(color="#fbbf24", width=1.6, dash="dot"), name="BTC off ATH",
        hovertemplate="%{x|%Y-%m-%d}: BTC dd %{y:+.1f}%<extra></extra>",
    ))
    # divergence shading: ratio_dd <= -10 AND btc_dd >= -5
    div = (df["ratio_dd_from_90d_peak_pct"] <= -10) & (df["btc_dd_from_ath_pct"] >= -5)
    if div.any():
        # Shade individual divergence regions
        for grp in _runs(div):
            fig.add_vrect(x0=grp[0], x1=grp[1], fillcolor="#ef4444",
                          opacity=0.15, line_width=0, layer="below")
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)
    fig.update_layout(**base_layout(), legend=dict(orientation="h", yanchor="bottom",
                                                    y=1.02, xanchor="right", x=1,
                                                    font=dict(size=10)))
    fig.update_layout(showlegend=True)
    fig.update_yaxes(title=None, ticksuffix="%")
    return fig


def chart_p3_atm(daily: pd.DataFrame) -> go.Figure:
    df = daily[["shares_30d_annualized_pct", "shares_90d_annualized_pct"]].dropna(how="all")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["shares_90d_annualized_pct"], mode="lines",
        line=dict(color="#64748b", width=1.4, dash="dot"), name="90d ann",
        hovertemplate="%{x|%Y-%m-%d}: 90d ann %{y:+.1f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["shares_30d_annualized_pct"], mode="lines",
        line=dict(color="#a5b4fc", width=2), name="30d ann",
        hovertemplate="%{x|%Y-%m-%d}: 30d ann %{y:+.1f}%<extra></extra>",
    ))
    # shade where acceleration flag fired
    accel = daily["shares_acceleration"].fillna(False).astype(bool)
    for grp in _runs(accel):
        fig.add_vrect(x0=grp[0], x1=grp[1], fillcolor="#fb923c",
                      opacity=0.12, line_width=0, layer="below")
    fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)
    fig.update_layout(**base_layout(), legend=dict(orientation="h", yanchor="bottom",
                                                    y=1.02, xanchor="right", x=1,
                                                    font=dict(size=10)))
    fig.update_layout(showlegend=True)
    fig.update_yaxes(title=None, ticksuffix="%")
    return fig


def chart_p5_gamma(daily: pd.DataFrame) -> go.Figure:
    df = daily[["realized_vol_30d_pct", "mnav"]].dropna(how="all")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["realized_vol_30d_pct"], mode="lines",
        line=dict(color="#f472b6", width=2), name="RV 30d",
        hovertemplate="%{x|%Y-%m-%d}: RV %{y:.0f}%<extra></extra>",
        yaxis="y1",
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["mnav"], mode="lines",
        line=dict(color="#fbbf24", width=1.6), name="mNAV",
        hovertemplate="%{x|%Y-%m-%d}: mNAV %{y:.2f}x<extra></extra>",
        yaxis="y2",
    ))
    # Shade ARMED regions
    armed = daily["gamma_armed"].fillna(False).astype(bool)
    for grp in _runs(armed):
        fig.add_vrect(x0=grp[0], x1=grp[1], fillcolor="#ef4444",
                      opacity=0.18, line_width=0, layer="below")
    fig.add_hline(y=100, line_dash="dot", line_color="#ef4444", opacity=0.5, yref="y1")
    layout = base_layout()
    layout["yaxis"] = dict(title=None, ticksuffix="%", gridcolor="rgba(255,255,255,0.05)")
    layout["yaxis2"] = dict(title=None, ticksuffix="x", overlaying="y", side="right",
                             showgrid=False)
    layout["legend"] = dict(orientation="h", yanchor="bottom", y=1.02,
                             xanchor="right", x=1, font=dict(size=10))
    layout["showlegend"] = True
    fig.update_layout(**layout)
    return fig


def chart_p6_off_cycle(daily: pd.DataFrame) -> go.Figure:
    s = daily["ratio_multiple_off_2y_low"].dropna()
    fig = go.Figure()
    fig.add_hrect(y0=0, y1=1.5, fillcolor="#10b981", opacity=0.06, line_width=0, layer="below")
    fig.add_hrect(y0=1.5, y1=2.5, fillcolor="#facc15", opacity=0.06, line_width=0, layer="below")
    fig.add_hrect(y0=2.5, y1=3.5, fillcolor="#fb923c", opacity=0.08, line_width=0, layer="below")
    fig.add_hrect(y0=3.5, y1=10, fillcolor="#ef4444", opacity=0.10, line_width=0, layer="below")
    fig.add_hline(y=2.5, line_dash="dot", line_color="#fb923c", opacity=0.5,
                  annotation_text="Late cycle", annotation_position="top right",
                  annotation=dict(font=dict(color="#fdba74", size=10)))
    fig.add_hline(y=3.5, line_dash="dot", line_color="#ef4444", opacity=0.5,
                  annotation_text="Extreme", annotation_position="top right",
                  annotation=dict(font=dict(color="#fca5a5", size=10)))
    fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines",
                             line=dict(color="#fbbf24", width=2), name="multiple off 2y low",
                             hovertemplate="%{x|%Y-%m-%d}: %{y:.2f}x<extra></extra>"))
    fig.update_layout(**base_layout())
    fig.update_yaxes(title=None, ticksuffix="x")
    return fig


def chart_p7_credit(daily: pd.DataFrame) -> go.Figure:
    s = daily["preferred_max_yield_pct"].dropna()
    fig = go.Figure()
    if not s.empty:
        fig.add_hrect(y0=0, y1=10, fillcolor="#10b981", opacity=0.06, line_width=0, layer="below")
        fig.add_hrect(y0=10, y1=12, fillcolor="#facc15", opacity=0.06, line_width=0, layer="below")
        fig.add_hrect(y0=12, y1=30, fillcolor="#ef4444", opacity=0.08, line_width=0, layer="below")
        fig.add_hline(y=12, line_dash="dot", line_color="#ef4444", opacity=0.5,
                      annotation_text="Stress", annotation_position="top right",
                      annotation=dict(font=dict(color="#fca5a5", size=10)))
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines",
                                 line=dict(color="#fb923c", width=2),
                                 hovertemplate="%{x|%Y-%m-%d}: max yield %{y:.2f}%<extra></extra>"))
    else:
        fig.add_annotation(text="Preferreds listed Q3 2024+", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font=dict(color="#94a3b8"))
    fig.update_layout(**base_layout())
    fig.update_yaxes(title=None, ticksuffix="%")
    return fig


def chart_p8_funding(daily: pd.DataFrame) -> go.Figure:
    s = daily["btc_funding_annualized_pct"].dropna()
    fig = go.Figure()
    if not s.empty:
        fig.add_hrect(y0=-50, y1=5, fillcolor="#10b981", opacity=0.06, line_width=0, layer="below")
        fig.add_hrect(y0=5, y1=15, fillcolor="#facc15", opacity=0.06, line_width=0, layer="below")
        fig.add_hrect(y0=15, y1=30, fillcolor="#fb923c", opacity=0.06, line_width=0, layer="below")
        fig.add_hrect(y0=30, y1=200, fillcolor="#ef4444", opacity=0.08, line_width=0, layer="below")
        fig.add_hline(y=30, line_dash="dot", line_color="#ef4444", opacity=0.5,
                      annotation_text="Hot", annotation_position="top right",
                      annotation=dict(font=dict(color="#fca5a5", size=10)))
        fig.add_hline(y=0, line_color="rgba(255,255,255,0.15)", line_width=1)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, mode="lines",
                                 line=dict(color="#34d399", width=2),
                                 hovertemplate="%{x|%Y-%m-%d}: %{y:+.1f}%/yr<extra></extra>"))
    else:
        fig.add_annotation(text="Funding history loading…", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False, font=dict(color="#94a3b8"))
    fig.update_layout(**base_layout())
    fig.update_yaxes(title=None, ticksuffix="%")
    return fig


def _runs(mask: pd.Series) -> list[tuple]:
    """Return list of (start, end) timestamps where mask is True (contiguous runs)."""
    if mask.empty:
        return []
    out = []
    in_run = False
    start = None
    prev_idx = None
    for ts, v in mask.items():
        if v and not in_run:
            start = ts
            in_run = True
        elif not v and in_run:
            out.append((start, prev_idx))
            in_run = False
        prev_idx = ts
    if in_run:
        out.append((start, prev_idx))
    return out


# ─────────────────────────── render ───────────────────────────
latest = load_latest()
intraday = load_intraday()
daily = load_daily()

if not latest:
    st.error("No data yet. Run `python scripts/fetch_signals.py` once to seed.")
    st.stop()

ts = datetime.fromisoformat(latest["timestamp_utc"].replace("Z", "+00:00"))
age_min = int((datetime.now(timezone.utc) - ts).total_seconds() / 60)
daily_span = (
    f"{daily.index.min().date()} → {daily.index.max().date()}"
    if not daily.empty else "no data"
)

st.markdown(
    f"""
    <div class="hero">
      <h1>📈 MSTR Sell-High Signal Dashboard</h1>
      <div class="sub">
        Eight playbook signals from the deep-research report on MSTR cycle tops (Feb-2021 + Nov-2024).
        &nbsp;·&nbsp; Refreshed every 30 min via GitHub Actions.<br>
        Last update: <b>{ts.strftime('%Y-%m-%d %H:%M UTC')}</b> ({age_min} min ago)
        &nbsp;·&nbsp; Daily history: <b>{daily_span}</b> ({len(daily):,} rows)
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── mNAV hero + key metrics ─────────────────────────────────
left, right = st.columns([1, 2], gap="large")

with left:
    color = latest["mnav_color"]
    st.markdown(
        f"""
        <div class="mnav-card" style="border-color:{color}66; box-shadow:0 0 60px {color}22;">
          <div class="metric-label">P1 · Current mNAV</div>
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
        ("MSTR Price",      f"${latest['mstr_price']:,.2f}",       f"Diluted shares: {latest['diluted_shares']:,}"),
        ("BTC Spot",        f"${latest['btc_price']:,.0f}",        f"Avg cost ${latest['btc_avg_cost']:,.0f} · {latest['btc_holdings']:,} BTC"),
        ("Market Cap",      f"${latest['market_cap_b']:.2f} B",    f"BTC NAV ${latest['btc_nav_b']:.2f} B"),
        ("Implied Vol 30d", (f"{latest['implied_vol_30d_pct']:.0f}%" if latest.get('implied_vol_30d_pct') else "n/a"), "ATM call IV"),
        ("Realized Vol 30d", f"{latest['realized_vol_30d_pct']:.0f}%", f"90d: {latest['realized_vol_90d_pct']:.0f}%"),
        ("BTC Funding (ann.)", (f"{latest['btc_funding']['annualized_pct']:+.1f}%" if latest['btc_funding'].get('annualized_pct') is not None else "n/a"),
                              f"7d avg per 8h: {latest['btc_funding'].get('latest_pct_per_8h', 'n/a')}%"),
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

# ── PLAYBOOK SIGNAL PANELS (card + chart per signal) ────────
st.markdown(
    '<div class="section-title">🚦 Quantifiable Playbook Signals '
    '<small>· 10y trends with threshold zones</small></div>',
    unsafe_allow_html=True,
)

# Compute per-signal status from latest snapshot
mnav = latest["mnav"]
p1_color, p1_label = grade(mnav, [
    (0,    "FIRE SALE",  "green"),
    (1.5,  "ACCUMULATE", "green"),
    (2.0,  "FAIR",       "yellow"),
    (2.5,  "TRIM",       "orange"),
    (3.0,  "SELL",       "red"),
])

ratio_dd = latest["ratio_dd_from_90d_peak_pct"]
btc_dd = latest["btc_dd_from_ath_pct"]
if latest["lead_lag_divergence_flag"]:
    p2_color, p2_label = "red", "DIVERGING"
elif ratio_dd > -3 and btc_dd > -5:
    p2_color, p2_label = "orange", "BOTH HOT"
elif btc_dd > -10:
    p2_color, p2_label = "yellow", "WATCH"
else:
    p2_color, p2_label = "green", "DORMANT"

g30_ann = latest["shares_30d_annualized_pct"]
g90_ann = latest["shares_90d_annualized_pct"]
accel_flag = latest["shares_acceleration_flag"]
if accel_flag and (g30_ann or 0) > 30 and mnav > 2.0:
    p3_color, p3_label = "red", "ACCEL + HOT"
elif accel_flag:
    p3_color, p3_label = "orange", "ACCELERATING"
elif g30_ann is None:
    p3_color, p3_label = "grey", "N/A"
elif g30_ann > 25:
    p3_color, p3_label = "yellow", "ELEVATED"
else:
    p3_color, p3_label = "green", "NORMAL"

rv30 = latest["realized_vol_30d_pct"]
if latest["gamma_squeeze_armed"]:
    p5_color, p5_label = "red", "ARMED"
elif latest["gamma_squeeze_elevated"]:
    p5_color, p5_label = "orange", "ELEVATED"
elif rv30 > 60:
    p5_color, p5_label = "yellow", "VOL HIGH"
else:
    p5_color, p5_label = "green", "QUIET"

mult = latest["ratio_multiple_off_2y_low"]
p6_color, p6_label = grade(mult, [
    (0,   "EARLY CYCLE", "green"),
    (1.5, "MID CYCLE",   "yellow"),
    (2.5, "LATE CYCLE",  "orange"),
    (3.5, "EXTREME",     "red"),
])

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

fund_ann = latest["btc_funding"].get("annualized_pct")
p8_color, p8_label = grade(fund_ann, [
    (-1e9, "NEGATIVE",  "green"),
    (5,    "NORMAL",    "green"),
    (15,   "ELEVATED",  "yellow"),
    (30,   "HOT",       "orange"),
    (60,   "EUPHORIC",  "red"),
])

WHY = {
    "P1": "Both prior tops printed here — Feb 2021 ≈ 2.7x, Nov 2024 ≈ 3.1x. Premium is the first thing to evaporate.",
    "P2": "In 2021 MSTR topped 9 months before BTC. Divergence (ratio rolling over while BTC pinned to ATH) is the cleanest in-real-time tell.",
    "P3": "Accelerating ATM dilution at high mNAV = Saylor confirming the premium is rich. Verify via SEC 8-K filings.",
    "P5": "Blow-off vol with rich premium = options-driven top — the classic Nov-2024 setup. When IV crushes after this fires, gamma reverses violently.",
    "P6": "Crowding indicator. Ratio at 3–5x off cycle low = leverage trade is full; mean-reverts hard.",
    "P7": "MSTR's capital structure depends on rolling cheap converts/preferreds. Widening spreads = funding stress before forced selling.",
    "P8": "Persistently positive funding = leveraged longs paying through the nose. Sustained > 0.10%/8h (~110%/yr) historically marks BTC tops within weeks.",
}


def render_signal(p_num: str, name: str, reading: str, status_color: str,
                  status_label: str, meta: str, chart_fn, why_key: str):
    st.markdown(
        card_html(p_num, name, reading, status_color, status_label, meta, WHY[why_key]),
        unsafe_allow_html=True,
    )
    fig = chart_fn(daily)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown(card_footer(WHY[why_key]), unsafe_allow_html=True)


col1, col2 = st.columns(2, gap="medium")

with col1:
    render_signal(
        "#1", "mNAV — premium to BTC NAV",
        f"{mnav:.2f}x", p1_color, p1_label,
        "Trim ≥ 2.0x &nbsp;·&nbsp; Sell ≥ 2.5x &nbsp;·&nbsp; Max sell ≥ 3.0x",
        chart_p1_mnav, "P1",
    )
    render_signal(
        "#3", "Accelerated ATM equity issuance",
        (f"{g30_ann:+.0f}% (30d ann.)" if g30_ann is not None else "—"),
        p3_color, p3_label,
        f"30d ann. share growth: <b>{g30_ann if g30_ann is not None else 'n/a'}%</b> · "
        f"90d ann.: <b>{g90_ann if g90_ann is not None else 'n/a'}%</b> · "
        f"Acceleration: 30d &gt; 90d × 1.2",
        chart_p3_atm, "P3",
    )
    render_signal(
        "#6", "MSTR/BTC ratio — multiple off cycle low",
        f"{mult:.2f}x", p6_color, p6_label,
        f"Trailing-2y ratio low: <b>{latest['ratio_2y_low']:.6f}</b> · "
        "Late cycle ≥ 2.5x · Extreme ≥ 3.5x",
        chart_p6_off_cycle, "P6",
    )
    render_signal(
        "#8", "BTC perp funding — leverage saturation",
        (f"{fund_ann:+.1f}% (ann.)" if fund_ann is not None else "n/a"),
        p8_color, p8_label,
        f"Latest 8h: <b>{latest['btc_funding'].get('latest_pct_per_8h', 'n/a')}%</b> · "
        "Hot ≥ 30%/yr · Euphoric ≥ 60%/yr · Source: OKX BTC-USDT-SWAP",
        chart_p8_funding, "P8",
    )

with col2:
    render_signal(
        "#2", "MSTR tops BEFORE BTC — lead-lag divergence",
        f"{ratio_dd:+.1f}% / {btc_dd:+.1f}%",
        p2_color, p2_label,
        f"Ratio off 90d peak: <b>{ratio_dd:+.2f}%</b> · BTC off ATH: <b>{btc_dd:+.2f}%</b><br>"
        "Trigger: ratio ≤ −10% AND BTC ≥ −5%",
        chart_p2_lead_lag, "P2",
    )
    render_signal(
        "#5", "Gamma-squeeze blow-off (compound)",
        f"RV30 {rv30:.0f}% · mNAV {mnav:.2f}x",
        p5_color, p5_label,
        "<b>ARMED</b>: RV30 &gt; 100% AND mNAV &gt; 2.5x · "
        "<b>ELEVATED</b>: RV30 &gt; 80% AND mNAV &gt; 2.0x",
        chart_p5_gamma, "P5",
    )
    render_signal(
        "#7", "STRC/STRF/STRK/STRD credit-spread stress",
        (f"max yield {max_yld:.1f}%" if max_yld is not None else "n/a"),
        p7_color, p7_label,
        f"Worst preferred 30d drawdown: <b>{worst_dd if worst_dd is not None else 'n/a'}%</b> · "
        "Stress: max yield &gt; 12% or 30d DD &lt; −8%",
        chart_p7_credit, "P7",
    )

    # P4 panel — qualitative-only, no chart
    st.markdown(
        f"""
        <div class="signal-card" style="border-left-color:#94a3b8;">
          <div class="pnum">Playbook Signal #4</div>
          <div class="name">Catalyst calendar exhausted &nbsp;<span class="pill pill-grey">MANUAL</span></div>
          <div class="meta" style="margin-top:10px;">
            The KEY non-quantifiable playbook signal. Track in the manual checklist below.
            Are major catalysts (ETF launches, options launches, index inclusion, splits, big announcements)
            all behind us with nothing big in the queue? In Nov-2024, the queue emptied right at the top.
          </div>
        </div>
        <div style="background: rgba(255,255,255,0.025);
                    border: 1px solid rgba(255,255,255,0.07);
                    border-top: none;
                    border-radius: 0 0 12px 12px;
                    padding: 0 18px 14px 18px; margin-top: -14px; margin-bottom: 14px;
                    color:#cbd5e1; font-size:0.83rem; line-height:1.5;">
          See ✅ checklist at bottom of page.
        </div>
        """,
        unsafe_allow_html=True,
    )

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
                  <div class="metric-sub">Price {px_str} · 30d DD {dd_str}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

# ── Spot price reference ────────────────────────────────────
if not daily.empty:
    st.markdown('<div class="section-title">📈 MSTR vs BTC — spot reference</div>', unsafe_allow_html=True)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily.index, y=daily["mstr"], mode="lines",
                              line=dict(color="#a5b4fc", width=2), name="MSTR ($)",
                              hovertemplate="%{x|%Y-%m-%d}: $%{y:,.2f}<extra></extra>",
                              yaxis="y1"))
    fig.add_trace(go.Scatter(x=daily.index, y=daily["btc"], mode="lines",
                              line=dict(color="#fbbf24", width=2), name="BTC ($)",
                              hovertemplate="%{x|%Y-%m-%d}: $%{y:,.0f}<extra></extra>",
                              yaxis="y2"))
    layout = base_layout(height=320)
    layout["yaxis"] = dict(title="MSTR ($)", gridcolor="rgba(255,255,255,0.05)")
    layout["yaxis2"] = dict(title="BTC ($)", overlaying="y", side="right", showgrid=False)
    layout["legend"] = dict(orientation="h", yanchor="bottom", y=1.02,
                             xanchor="right", x=1, font=dict(size=10))
    layout["showlegend"] = True
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# ── P4 + qualitative manual checklist ───────────────────────
st.markdown(
    '<div class="section-title">📝 P4 + Qualitative Signals '
    '<small>· not API-able; track manually</small></div>',
    unsafe_allow_html=True,
)

manual = [
    ("P4 · Catalyst calendar exhausted",
     "The KEY MANUAL signal from the playbook. Are the major structural catalysts (ETF launches, options launches, index inclusion, splits, big announcements) all behind us with nothing big in the queue? In Nov-2024 after IBIT options + Nasdaq-100 inclusion talks, the queue emptied and the market topped within weeks."),
    ("Saylor media tour intensity",
     "Is Saylor on every podcast / CNBC / X-spaces in a single week? Peak media presence clusters within ~30 days of the cycle top."),
    ("Retail mania / social sentiment",
     "MSTR memes on r/wallstreetbets, FinTwit, TikTok daily? Random people asking you about MSTR? Confirms crowding."),
    ("Index inclusion narrative peaking",
     "S&P 500 / Nasdaq-100 inclusion stories peak right at the top — the news IS the catalyst, post-inclusion is usually a fade."),
    ("Macro liquidity turning hawkish",
     "Fed pivot to hawkish, DXY ripping, real yields rising — risk-asset top conditions."),
    ("Copycat treasuries proliferating",
     "10+ new public-co BTC treasuries announced per month? Late-cycle behavior — peak supply of leveraged BTC vehicles."),
    ("Insider Form 4 selling",
     "Unusual sales by Saylor or other officers (SEC EDGAR Form 4 filings)."),
    ("Convertible refinancing deteriorating",
     "Stalled converts, wider conversion premiums, higher coupons. Complementary to P7 — preferred yields catch the same stress."),
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
      Data: yfinance (MSTR, BTC-USD, preferreds) · OKX (BTC perp funding) · bitcointreasuries.net (live BTC count) ·
      hardcoded milestones for historical BTC holdings + share count (sourced from public 8-K filings, interpolated daily).<br>
      Signal calibration anchored to Feb-2021 (~2.7x mNAV) and Nov-2024 (~3.1x mNAV) tops. Future cycles may differ.<br>
      <b>Not financial advice.</b> Snapshots refresh every 30 min via GitHub Actions; Streamlit Cloud auto-redeploys on commit.
    </div>
    """,
    unsafe_allow_html=True,
)
