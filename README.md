# MSTR Sell-High Signal Dashboard

A live web dashboard that tracks every quantifiable signal historically associated with MSTR (Strategy) cycle tops — calibrated against the **Feb 2021 (~2.7x mNAV)** and **Nov 2024 (~3.1x mNAV)** peaks — and surfaces non-quantifiable signals as a manual checklist.

Data refreshes every 30 minutes via GitHub Actions; the Streamlit app reads the committed snapshots, so the page is always current.

## Quantifiable signals tracked

| Signal | Why it matters |
| --- | --- |
| **mNAV** (market cap ÷ BTC NAV) | The single most important signal. Both prior tops printed ≥2.7x. |
| **MSTR/BTC ratio + 365d z-score** | Crowding indicator — extreme outperformance mean-reverts hard. |
| **MSTR – BTC return, 30/90/180d** | Premium-expansion proxy. |
| **30d realized volatility** | Blow-off vol + high mNAV = gamma-squeeze top. |
| **30d ATM implied volatility** | Expensive options + IV crush marks tops. |
| **BTC drawdown from ATH / days since ATH** | MSTR tends to top *before* BTC. |
| **Diluted market cap, BTC NAV, holdings** | Underlying inputs surfaced for inspection. |

## Manual signals (dashboard prompts you to check)

- Catalyst calendar exhaustion (ETFs, options, index inclusion already shipped)
- Saylor media tour intensity
- Retail mania / social sentiment
- Accelerating ATM equity issuance (SEC 8-K filings)
- Convertible refinancing health, STRC/STRF/STRK/STRD credit spreads
- Index inclusion narrative peaking
- Macro liquidity turning
- Copycat BTC treasuries proliferating
- Insider selling (Form 4)
- BTC perp funding extremely positive

## Architecture

```
.github/workflows/update_data.yml   ← cron every 30 min
scripts/fetch_signals.py            ← pulls prices + holdings, computes signals
data/latest.json                    ← most recent snapshot
data/history.csv                    ← rolling time-series of every signal
data/price_history.csv              ← 2y daily MSTR + BTC closes
app.py                              ← Streamlit dashboard
.streamlit/config.toml              ← dark theme
```

The GitHub Action commits updated `data/*` files back to the repo. Streamlit Cloud auto-redeploys on commit.

## Local dev

```bash
pip install -r requirements.txt
python scripts/fetch_signals.py    # seed data/
streamlit run app.py
```

## Deployment

1. Push the repo to GitHub.
2. **GitHub Actions** — the schedule starts running automatically once the workflow is on the default branch. (First scheduled run can take a few minutes; trigger manually via the Actions tab to verify.)
3. **Streamlit Community Cloud** — at <https://share.streamlit.io> click *New app*, point at this repo, set main file to `app.py`. Deploys in ~1 min.

## Calibration

The mNAV zone thresholds are anchored to the prior two cycle tops:

| Zone | mNAV | Action |
| --- | --- | --- |
| Fire Sale | < 1.0 | Aggressive accumulate |
| Accumulate | 1.0–1.5 | Buy |
| Fair | 1.5–2.0 | HODL |
| Trim | 2.0–2.5 | Start scaling out (25%) |
| Sell | 2.5–3.0 | Heavy trim (50–75%) |
| Max Sell | > 3.0 | Dump aggressively |

These are based on a sample size of two cycles. Future cycles may run hotter or cooler — the manual checklist exists to catch regime changes the numbers will lag.

## Caveats

- **BTC holdings** are scraped from bitcointreasuries.net with a hardcoded fallback. Update the fallback in `scripts/fetch_signals.py` after every Saylor purchase tweet (or when the scraper drifts).
- **Diluted share count** uses `yfinance.fast_info["shares"]`, which can lag — sanity-check against the latest 10-Q.
- **Implied vol** is a single ATM-call read — fine as a directional gauge, not for precision.
- **Not financial advice.** This is a personal monitoring tool.
