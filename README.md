# MSTR Sell-High Signal Dashboard

A live web dashboard tracking the **8 sell-high playbook signals** identified in the deep-research report on MSTR (Strategy) cycle tops. Calibrated against the **Feb 2021 (~2.7x mNAV)** and **Nov 2024 (~3.1x mNAV)** peaks.

Data refreshes every 30 minutes via GitHub Actions; the Streamlit app reads the committed snapshots, so the page is always current.

## Playbook signals → dashboard mapping

| # | Playbook signal | Source | Dashboard implementation |
| - | --- | :-: | --- |
| **P1** | mNAV ≥ 2.5–3.0x = euphoria zone | auto | mNAV with color-coded zones (Fire Sale → Max Sell at 0.5x intervals from 1.0 to 3.0x) |
| **P2** | MSTR tops *before* BTC | auto | Lead-lag divergence: MSTR/BTC ratio drawdown from 90d peak vs BTC drawdown from ATH; flag fires when ratio ≤ −10% off peak while BTC ≥ −5% off ATH |
| **P3** | Accelerated ATM equity issuance | auto | Daily diluted-shares history from `yfinance.get_shares_full`, forward-filled to a daily grid; 30d annualized growth vs 90d annualized; acceleration flag when 30d > 90d × 1.2 |
| **P4** | Catalyst calendar exhausted | manual | Top item in qualitative checklist — inherently subjective |
| **P5** | Gamma-squeeze blow-off | auto | Compound trigger: `RV30 > 100% AND mNAV > 2.5x` (ARMED) or `RV30 > 80% AND mNAV > 2.0x` (ELEVATED) |
| **P6** | MSTR/BTC ratio at multi-year extreme | auto | Current ratio ÷ trailing-2y ratio low (matches "3–5x off cycle low" rule from playbook) |
| **P7** | STRC/STRF/STRK/STRD credit-spread stress | auto | Pull preferreds via yfinance; compute current yield + 30d drawdown per ticker; stress flag when any yield > 12% or any 30d drawdown < −8% |
| **P8** | BTC perp funding extremely positive | auto | OKX BTC-USDT-SWAP funding history; annualized 7d-avg with thresholds (Normal < 5%, Hot ≥ 30%, Euphoric ≥ 60%) |

## Manual checklist (P4 + qualitative)

These need human judgement or sources without reliable APIs:

- **P4 — Catalyst calendar exhausted** (the key playbook manual signal)
- Saylor media tour intensity
- Retail mania / social sentiment (r/wallstreetbets, FinTwit, TikTok)
- Index inclusion narrative peaking
- Macro liquidity turning hawkish
- Copycat BTC treasuries proliferating
- Insider Form 4 selling
- Convertible refinancing deteriorating

## Architecture

```
.github/workflows/update_data.yml   ← cron */30 * * * *
scripts/fetch_signals.py            ← 8 playbook signal computations
data/latest.json                    ← most recent snapshot
data/history.csv                    ← rolling time-series of every signal
data/price_history.csv              ← 2y daily MSTR + BTC closes
app.py                              ← Streamlit dashboard
.streamlit/config.toml              ← dark theme
```

The GitHub Action commits updated `data/*` files back to the repo. Streamlit Cloud auto-redeploys on every commit.

## Local dev

```bash
pip install -r requirements.txt
python scripts/fetch_signals.py    # seed data/
streamlit run app.py
```

## Deployment

1. Push the repo to GitHub (private is fine — Streamlit Cloud connects via OAuth).
2. **GitHub Actions** — the schedule starts running automatically once the workflow is on the default branch. Trigger manually via the Actions tab to verify the first run.
3. **Streamlit Community Cloud** — at <https://share.streamlit.io> click *New app*, point at this repo, set main file to `app.py`. Deploys in ~1 min.

## mNAV zone calibration

| Zone | mNAV | Action |
| --- | --- | --- |
| Fire Sale | < 1.0 | Aggressive accumulate |
| Accumulate | 1.0–1.5 | Buy |
| Fair | 1.5–2.0 | HODL |
| Trim | 2.0–2.5 | Start scaling out (25%) |
| Sell | 2.5–3.0 | Heavy trim (50–75%) |
| Max Sell | > 3.0 | Dump aggressively |

Anchored to the prior two cycle tops (sample size: 2). The manual checklist exists to catch regime changes the numbers will lag.

## Caveats

- **BTC holdings** are scraped from bitcointreasuries.net with a hardcoded fallback. Update `FALLBACK_BTC_HOLDINGS` in `scripts/fetch_signals.py` after major Saylor purchases.
- **Diluted share count** uses `yfinance.fast_info["shares"]`, which can lag — sanity-check against the latest 10-Q.
- **ATM-pace signal** is a proxy; verify against SEC 8-K filings (item 8.01) at `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001050446&type=8-K`.
- **Implied vol** is an averaged 3-strike ATM read with a 30%-IV floor to filter junk yfinance values.
- **BTC funding** uses OKX since Binance and Bybit are geo-blocked from US/CloudFront. Different exchanges report slightly different funding rates.
- **Not financial advice.** Personal monitoring tool.
