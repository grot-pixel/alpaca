import os
import json
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from alpaca_trade_api.rest import REST, TimeFrame

# --- Load config ---
with open("config.json") as f:
    config = json.load(f)
print("Loaded config:", config)

# --- Signal logic ---
def generate_signal(df, cfg):
    if len(df) < max(cfg["sma_slow"], cfg["rsi_period"]):
        return None, "Not enough data"

    df["sma_fast"] = df["close"].rolling(cfg["sma_fast"]).mean()
    df["sma_slow"] = df["close"].rolling(cfg["sma_slow"]).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(cfg["rsi_period"]).mean()
    avg_loss = loss.rolling(cfg["rsi_period"]).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    sma_fast = df["sma_fast"].iloc[-1]
    sma_slow = df["sma_slow"].iloc[-1]
    rsi = df["rsi"].iloc[-1]

    reason = f"sma_fast={sma_fast:.2f}, sma_slow={sma_slow:.2f}, rsi={rsi:.2f}"
    if sma_fast > sma_slow and rsi < cfg["rsi_overbought"]:
        return "BUY", reason
    elif sma_fast < sma_slow and rsi > cfg["rsi_oversold"]:
        return "SELL", reason
    else:
        return None, reason

# --- Market hours check (regular US market) ---
def is_regular_hours():
    now = datetime.now(timezone.utc)
    return (now.hour > 14 or (now.hour == 14 and now.minute >= 30)) and now.hour < 21

# --- Trade one account ---
def trade_account(account_info):
    name = account_info["name"]
    api = account_info["api"]
    symbols = account_info["symbols"]

    print(f"\n=== Trading for {name} ===")
    try:
        account = api.get_account()
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Account Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"Error fetching account info: {e}")
        return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
        print(f"Current Positions: {positions}")
    except Exception as e:
        print(f"Error fetching positions: {e}")
        positions = {}

    regular_hours = is_regular_hours()

    for sym in symbols:
        try:
            bars = api.get_bars(sym, TimeFrame.Minute, limit=50).df
            bars = bars[bars["exchange"] == "NASDAQ"]  # filter if needed
            if len(bars) < 20:
                print(f"[{sym}] Not enough data, skipping")
                continue

            signal, reason = generate_signal(bars, config)
            if signal is None:
                print(f"[{sym}] No signal. ({reason})")
                continue

            if regular_hours:
                qty = 1 if signal == "BUY" else positions.get(sym, 0)
                side = signal.lower()
                if qty > 0:
                    api.submit_order(sym, qty, side, "market", "day")
                    print(f"[{sym}] {signal} order submitted ({qty} shares). Reason: {reason}")
            else:
                print(f"[{sym}] Signal={signal} (extended hours, not placing). Reason: {reason}")

        except Exception as e:
            print(f"[{sym}] Error processing: {e}")

# --- Initialize accounts ---
accounts = []
for i in [1, 2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if not key or not secret:
        print(f"Account {i} missing API key/secret, skipping")
        continue

    try:
        api = REST(key, secret, base)
        api.get_account()
        print(f"Account {i} connected successfully")
        accounts.append({
            "name": f"PaperAccount{i}",
            "api": api,
            "symbols": config["symbols"]
        })
    except Exception as e:
        print(f"Error initializing account {i}: {e}")

# --- Run bot ---
for acc in accounts:
    trade_account(acc)
