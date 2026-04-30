"""Quick offline validation that all chart builders return valid Plotly figs."""
import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Re-define helpers locally (mirrors app.py)
def _runs(mask: pd.Series) -> list[tuple]:
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

def base_layout(title=None, height=230):
    return dict(
        title=dict(text=title, font=dict(color="#cbd5e1", size=12)) if title else None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cbd5e1", size=11),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        height=height,
        margin=dict(l=10, r=10, t=24 if title else 8, b=8),
        hovermode="x unified",
        showlegend=False,
    )

# Pull the chart functions from app.py via exec
ns = {"pd": pd, "go": go, "_runs": _runs, "base_layout": base_layout}
src = (ROOT / "app.py").read_text()
chart_section = src.split("# ─────────────────── per-signal chart builders ───────────────")[1]
chart_section = chart_section.split("def _runs(")[0]  # stop before _runs
exec(chart_section, ns)

daily = pd.read_csv(ROOT / "data" / "daily_history.csv", parse_dates=["date"]).set_index("date")
print(f"daily rows: {len(daily)}")

for name, fn_name in [
    ("P1 mNAV",          "chart_p1_mnav"),
    ("P2 lead-lag",      "chart_p2_lead_lag"),
    ("P3 ATM",           "chart_p3_atm"),
    ("P5 gamma",         "chart_p5_gamma"),
    ("P6 off cycle low", "chart_p6_off_cycle"),
    ("P7 credit",        "chart_p7_credit"),
    ("P8 funding",       "chart_p8_funding"),
]:
    fn = ns[fn_name]
    fig = fn(daily)
    assert isinstance(fig, go.Figure), f"{name}: not a Figure"
    print(f"  {name}: {len(fig.data)} traces, {len(fig.layout.shapes or [])} shapes — OK")

print("\nall chart builders OK")
