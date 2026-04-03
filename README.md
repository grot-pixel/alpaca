# 🤖 Alpaca Multi-Strategy Trading Bot

Automated intraday trading bot for [Alpaca Markets](https://alpaca.markets), running serverlessly via **GitHub Actions** (no VPS needed). Supports up to 2 accounts and sends a daily email report at market close.

> ⚠️ **Beta software. Paper trade only until you have validated at least 30–60 days of performance.**

---

## What's New (v2)

| | v1 (old) | v2 (this) |
|---|---|---|
| Library | `alpaca-trade-api` (deprecated) | `alpaca-py` ✅ |
| VWAP | Cumulative from 5-day fetch ❌ | Today's bars only ✅ |
| Risk/Reward | Stop 2%, Take 1.5% (negative R/R!) ❌ | Stop 3%, Take 6% (1:2 R/R) ✅ |
| Strategies | SMA + RSI + VWAP | SMA + RSI + **MACD** + VWAP ✅ |
| Max position | 40% per symbol ❌ | 20% per symbol ✅ |
| Max open | unlimited | 5 simultaneous ✅ |
| Trailing stop | ✗ | ✅ (locks in half the gain when up >5%) |

---

## How It Works

Each GitHub Actions run (every 5 min during market hours):

1. Checks if the market is open — skips if not
2. Reads account equity and applies **circuit breakers** (daily profit/loss limits)
3. Sweeps open positions for stop-loss and take-profit exits
4. Scans all symbols for buy/sell signals using **4 indicators**
5. Submits limit orders for the best signals

### Signal Logic

A **BUY** fires when **2 of 4** indicators are bullish:

| # | Indicator | Bullish condition |
|---|---|---|
| 1 | **SMA crossover** | Fast SMA (8) above Slow SMA (21), or recent cross |
| 2 | **RSI** | Between 38–68 (healthy momentum, not overbought) |
| 3 | **MACD histogram** | Positive and expanding (momentum building) |
| 4 | **VWAP** | Price above today's VWAP |

A **SELL** fires on 2-of-4 bearish confirmation (same indicators inverted).

---

## Setup

### 1. Clone and push to your repo

```bash
git clone https://github.com/YOUR_USERNAME/alpaca.git
cd alpaca
# Replace all files with this new version, then:
git add -A
git commit -m "v2: upgrade to alpaca-py, fix VWAP, fix R/R, add MACD"
git push
```

### 2. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `APCA_API_KEY_1` | Your Alpaca API key |
| `APCA_API_SECRET_1` | Your Alpaca API secret |
| `APCA_BASE_URL_1` | `https://paper-api.alpaca.markets` (or live URL) |
| `APCA_API_KEY_2` | *(optional)* Second account key |
| `APCA_API_SECRET_2` | *(optional)* Second account secret |
| `APCA_BASE_URL_2` | *(optional)* Second account URL |
| `EMAIL_USER` | Gmail address for reports |
| `EMAIL_PASS` | Gmail [App Password](https://support.google.com/accounts/answer/185833) |

### 3. Enable GitHub Actions

Go to **Actions** tab → enable workflows if prompted.

The bot runs automatically every 5 minutes on market days. You can also trigger it manually from the Actions tab.

---

## Configuration

Edit `config.json` to customize behavior:

```jsonc
{
  "symbols": ["TQQQ", "SOXL", "NVDA", ...],   // what to trade

  "max_open_positions": 5,     // max simultaneous positions
  "max_position_pct": 0.20,    // max 20% of equity per symbol
  "max_trade_pct": 0.10,       // deploy 10% of equity per signal

  "stop_loss_pct": 0.03,       // 3% stop-loss (always < take_profit!)
  "take_profit_pct": 0.06,     // 6% take-profit = 1:2 risk/reward

  "daily_profit_target_pct": 0.04,   // halt + close all if up 4% today
  "daily_loss_limit_pct": 0.025,     // halt + close all if down 2.5% today

  "signal_threshold": 2,   // indicators needed for a signal (2, 3, or 4)
                            // 2 = more trades, 3 = higher conviction only

  "sma_fast": 8,
  "sma_slow": 21,
  "rsi_period": 10,
  "rsi_overbought": 68,
  "rsi_sell_min": 58,
  "rsi_buy_min": 38
}
```

**Tuning tips:**
- Raise `signal_threshold` to 3 for fewer, higher-conviction trades
- Tighten `stop_loss_pct` to 0.02 in low-volatility markets
- Remove leveraged ETFs (TQQQ, SOXL) for lower volatility
- Add more blue chips (AMZN, GOOGL) for steadier signals

---

## Running Locally

```bash
pip install -r requirements.txt

# Set env vars
export APCA_API_KEY_1="your_key"
export APCA_API_SECRET_1="your_secret"
export APCA_BASE_URL_1="https://paper-api.alpaca.markets"

# Run the bot
python bot.py

# Run the report
python report.py
```

---

## Risk Management Summary

- **Stop-loss**: 3% below entry — hard rule, no exceptions
- **Take-profit**: 6% above entry (2× the stop)
- **Trailing stop**: activates when up >5%, locks in ~2.5% minimum
- **Max 5 positions** open at once — forces diversification
- **Daily circuit breakers**: closes everything if up 4% or down 2.5%
- **Market-open check**: never trades outside market hours

---

## Why the R/R Ratio Matters

The old config had stop=2%, take=1.5%. That means you risk $2 to make $1.50. At a 50% win rate, you **lose money** long-term:

```
50 wins × $1.50 = $75 gain
50 losses × $2.00 = $100 loss
Net: -$25
```

With stop=3%, take=6% (this config) at 50% win rate:
```
50 wins × $6 = $300 gain
50 losses × $3 = $150 loss
Net: +$150  ← positive even at 50% win rate
```

You only need to be right ~34% of the time to break even with a 1:2 R/R ratio.

---

## ⚠️ Disclaimer

This software is for educational and research purposes only.

- **Experimental**: Beta quality — use paper trading until performance is validated
- **No guarantees**: Past signals do not guarantee future profits
- **Real money risk**: Automated bots can lose capital rapidly
- **Not financial advice**: The authors are not financial advisors
- **Your responsibility**: You are solely responsible for all trades executed

See [LICENSE](./LICENSE) for full terms.
