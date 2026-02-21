# 🤖 Alpaca Multi-Account Trading Bot

An automated intraday trading bot for [Alpaca Markets](https://alpaca.markets) using SMA crossover, RSI, and VWAP signals. Supports multiple accounts and runs on a GitHub Actions schedule.

> ⚠️ **This project is in active beta. Use on paper trading accounts only until you have validated performance.**

---

## Features

- Multi-account support (up to 2 Alpaca accounts)
- Signal generation using SMA crossover + RSI + VWAP (2-of-3 confirmation)
- Automatic stop-loss (2%) and take-profit (1.5%) per position
- Daily profit target (+3%) and loss circuit breaker (-2%)
- Runs automatically via GitHub Actions every 5 minutes during market hours
- Daily performance report delivered by email

---

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/grot-pixel/alpaca.git
cd alpaca
pip install -r requirements.txt
```

### 2. Configure GitHub Secrets
Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `APCA_API_KEY_1` | Alpaca API key for account 1 |
| `APCA_API_SECRET_1` | Alpaca API secret for account 1 |
| `APCA_BASE_URL_1` | `https://paper-api.alpaca.markets` (or live URL) |
| `APCA_API_KEY_2` | Alpaca API key for account 2 (optional) |
| `APCA_API_SECRET_2` | Alpaca API secret for account 2 (optional) |
| `APCA_BASE_URL_2` | Base URL for account 2 |
| `EMAIL_USER` | Gmail address for daily reports |
| `EMAIL_PASS` | Gmail [App Password](https://support.google.com/accounts/answer/185833) |

### 3. Configure symbols and strategy
Edit `config.json` to set your symbols, position sizing, and signal thresholds.

### 4. Run manually
```bash
python bot.py      # Run the trading bot once
python report.py   # Generate and email the daily report
```

The GitHub Actions workflow runs the bot automatically every 5 minutes from **7:00 AM – 7:55 PM CST**, Monday–Friday.

---

## How It Works

Each run fetches the last 200 minutes of bar data per symbol and scores each on 3 signals:

- **SMA trend**: fast SMA (8) vs slow SMA (21)
- **RSI momentum**: between 38–65 for buys, above 58 for sells
- **VWAP position**: price above/below intraday VWAP

A **buy** fires on 2-of-3 bullish confirmations. A **sell** fires on 2-of-3 bearish confirmations. Stop-loss and take-profit are checked every run against open positions.

---

## ⚠️ Disclaimer

This software is for educational and testing purposes only.

- **Experimental:** This project is in beta. No guarantees of performance, stability, or profitability.
- **Paper Trading Recommended:** Test exclusively on Alpaca Paper Trading accounts before risking real capital.
- **Financial Risk:** Automated trading bots can execute rapidly and may cause substantial losses due to bugs, latency, or market conditions.
- **Not Financial Advice:** The author is not a financial advisor. Nothing here constitutes investment, legal, or tax advice.
- **Your Responsibility:** You are solely responsible for any trades executed. The author assumes no liability for financial losses.

See [LICENSE](./LICENSE) for full terms.
