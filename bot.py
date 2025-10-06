import os
import json
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from datetime import datetime, timezone

# --- Load Config ---
CONFIG_FILE = "config.json"
try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    print(f"Loaded config: {CONFIG_FILE}")
except Exception as e:
    print(f"Error loading {CONFIG_FILE}: {e}")
    config = {}

# --- Signal Generation Logic (RSI + SMA crossover example) ---
def generate_signal(symbol, api):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=config.get("sma_slow", 30) + 1).df
        if len(bars) < config.get("sma_slow", 30):
            print(f"Not enough data for {symbol}, skipping signal.")
            return None

        bars["sma_fast"] = bars["close"].rolling(config.get("sma_fast", 10)).mean()
        bars["sma_slow"] = bars["close"].rolling(config.get("sma_slow", 30)).mean()

        # RSI calculation
        delta = bars["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(config.get("rsi_period", 14)).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(config.get("rsi_period", 14)).mean()
        rs = gain / loss
        bars["rsi"] = 100 - (100 / (1 + rs))

        latest = bars.iloc[-1]
        prev = bars.iloc[-2]

        signal = None

        # Buy signal: fast SMA crosses above slow SMA and RSI < oversold
        if (
            prev["sma_fast"] < prev["sma_slow"]
            and latest["sma_fast"] > latest["sma_slow"]
            and latest["rsi"] < config.get("rsi_oversold", 30)
        ):
            signal = "buy"

        # Sell signal: fast SMA crosses below slow SMA or RSI > overbought
        elif (
            prev["sma_fast"] > prev["sma_slow"]
            and latest["sma_fast"] < latest["sma_slow"]
        ) or latest["rsi"] > config.get("rsi_overbought", 70):
            signal = "sell"

        return signal
    except Exception as e:
        print(f"Error generating signal for {symbol}: {e}")
        return None

# --- Determine if market is open (regular hours only) ---
def is_regular_market_open():
    now = datetime.now(timezone.utc)
    # Regular hours: 9:30–16:00 ET = 14:30–21:00 UTC
    return 14 <= now.hour < 21 or (now.hour == 21 and now.minute == 0)

# --- Trade function for one account ---
def trade_account(account_info):
    name = account_info["name"]
    api = account_info["api"]
    symbols = config.get("symbols", [])

    print(f"\n=== Trading for {name} ===")

    try:
        account = api.get_account()
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Account Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"Error fetching account info for {name}: {e}")
        return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
        print(f"Current Positions: {positions}")
    except Exception as e:
        print(f"Error fetching positions for {name}: {e}")
        positions = {}

    regular_hours = is_regular_market_open()

    for symbol in symbols:
        try:
            signal = generate_signal(symbol, api)
            if not signal:
                print(f"[{symbol}] No signal, skipping.")
                continue

            if regular_hours:
                if signal == "buy":
                    print(f"[{symbol}] BUY signal (regular hours)")
                    api.submit_order(symbol, 1, "buy", "market", "day")
                elif signal == "sell" and positions.get(symbol, 0) > 0:
                    qty = positions[symbol]
                    print(f"[{symbol}] SELL signal (regular hours) - closing {qty} shares")
                    api.submit_order(symbol, qty, "sell", "market", "day")
            else:
                print(f"[{symbol}] Signal: {signal.upper()} (extended hours, not placing order)")

        except Exception as e:
            print(f"[{symbol}] Error processing symbol: {e}")

# --- Initialize accounts ---
accounts = []
for i in [1, 2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if not key or not secret:
        print(f"API key/secret missing for account {i}, skipping.")
        continue

    try:
        api = REST(key, secret, base)
        api.get_account()
        print(f"Account {i} connected successfully.")
        accounts.append({
            "name": f"PaperAccount{i}",
            "api": api,
        })
    except Exception as e:
        print(f"Error initializing account {i}: {e}")

# --- Run trading loop ---
for account_info in accounts:
    trade_account(account_info)
