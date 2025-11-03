import os
import json
import time
from datetime import datetime, timezone, timedelta
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from utils import generate_signals

CONFIG_FILE = "config.json"
with open(CONFIG_FILE) as f:
    cfg = json.load(f)

def is_regular_hours():
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and 14 <= now.hour < 21  # 9:30–16:00 ET

def get_bars(api, symbol, lookback=100):
    bars = api.get_bars(symbol, TimeFrame.Minute, limit=lookback).df
    return bars if not bars.empty else None

def position_exists(api, symbol):
    try:
        pos = api.get_position(symbol)
        return float(pos.qty)
    except:
        return 0

def submit_order(api, symbol, qty, side):
    try:
        api.submit_order(symbol, qty, side, "market", "day")
        print(f"→ {side.upper()} {qty} {symbol}")
    except Exception as e:
        print(f"[{symbol}] Order failed: {e}")

def trade_account(account_info):
    name, api = account_info["name"], account_info["api"]
    symbols = cfg["symbols"]

    print(f"\n=== Trading for {name} === {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    account = api.get_account()
    equity = float(account.equity)
    cash = float(account.cash)
    positions = {p.symbol: float(p.qty) for p in api.list_positions()}

    print(f"Equity: ${equity:.2f}, Cash: ${cash:.2f}")
    regular_hours = is_regular_hours()

    for sym in symbols:
        bars = get_bars(api, sym, lookback=max(cfg["sma_slow"], cfg["trend_window"]) + 5)
        if bars is None or len(bars) < cfg["sma_slow"]:
            print(f"[{sym}] Not enough data")
            continue

        signal, reason = generate_signals(bars, cfg)
        qty_held = positions.get(sym, 0)
        last_price = bars["close"].iloc[-1]

        # Stop/take-profit checks
        if qty_held > 0:
            position = api.get_position(sym)
            avg_entry = float(position.avg_entry_price)
            change = (last_price - avg_entry) / avg_entry
            if change <= -cfg["stop_loss_pct"]:
                print(f"[{sym}] Stop-loss triggered ({change:.2%}) → SELL")
                submit_order(api, sym, qty_held, "sell")
                continue
            if change >= cfg["take_profit_pct"]:
                print(f"[{sym}] Take-profit triggered ({change:.2%}) → SELL")
                submit_order(api, sym, qty_held, "sell")
                continue

        if not regular_hours:
            print(f"[{sym}] {signal or 'no signal'} ({reason}) — Market closed")
            continue

        if signal == "buy" and qty_held == 0:
            max_alloc = equity * cfg["max_position_pct"]
            trade_size = equity * cfg["max_trade_pct"]
            qty = int(trade_size / last_price)
            if qty < 1:
                print(f"[{sym}] Trade too small, skipping.")
                continue
            if trade_size > cash:
                print(f"[{sym}] Not enough cash to buy.")
                continue
            print(f"[{sym}] BUY signal ({reason}) — {qty} shares")
            submit_order(api, sym, qty, "buy")

        elif signal == "sell" and qty_held > 0:
            print(f"[{sym}] SELL signal ({reason}) — {qty_held} shares")
            submit_order(api, sym, qty_held, "sell")

        else:
            print(f"[{sym}] Hold — {reason}")

def main():
    accounts = []
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        secret = os.getenv(f"APCA_API_SECRET_{i}")
        base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

        if not key or not secret:
            print(f"Missing credentials for account {i}")
            continue

        try:
            api = REST(key, secret, base)
            api.get_account()
            print(f"✅ Connected: Account {i}")
            accounts.append({"name": f"PaperAccount{i}", "api": api})
        except Exception as e:
            print(f"Error initializing account {i}: {e}")

    cooldown = timedelta(minutes=cfg.get("cooldown_minutes", 10))
    last_run = datetime.min

    while True:
        if datetime.now() - last_run > cooldown:
            for acc in accounts:
                trade_account(acc)
            last_run = datetime.now()
        else:
            remaining = cooldown - (datetime.now() - last_run)
            print(f"Sleeping {remaining.seconds // 60}m...")
        time.sleep(60)

if __name__ == "__main__":
    main()
