import os
import json
import pandas as pd
from datetime import datetime, timezone
from alpaca_trade_api.rest import REST, TimeFrame
from utils import generate_signal

CONFIG_FILE = "config.json"

# --- Load config ---
with open(CONFIG_FILE) as f:
    cfg = json.load(f)
print("Loaded config:", cfg)

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# --- One-shot trading cycle ---
def trade_account(account_info):
    name = account_info["name"]
    api = account_info["api"]
    symbols = account_info["symbols"]
    print(f"\n=== Trading for {name} ===")

    # account stats
    try:
        account = api.get_account()
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"[{name}] Error getting account info: {e}")
        return

    # current positions
    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception:
        positions = {}

    for sym in symbols:
        try:
            bars = api.get_bars(sym, TimeFrame.Minute, limit=max(cfg["sma_slow"], cfg["rsi_period"]) + 2).df
            if bars.empty:
                print(f"[{sym}] No data, skipping.")
                continue

            signal = generate_signal(bars, cfg)
            if not signal:
                print(f"[{sym}] No signal.")
                continue

            price = float(bars["close"].iloc[-1])
            position_qty = positions.get(sym, 0.0)

            # Determine trade size dynamically
            target_value = equity * cfg["max_trade_pct"]
            qty = max(int(target_value / price), 1)

            # Execute logic
            if signal == "buy" and position_qty == 0:
                print(f"[{sym}] BUY {qty} @ ${price:.2f}")
                api.submit_order(
                    symbol=sym,
                    qty=qty,
                    side="buy",
                    type="market",
                    time_in_force="day",
                    order_class="bracket",
                    take_profit={"limit_price": round(price * (1 + cfg["take_profit_pct"]), 2)},
                    stop_loss={"stop_price": round(price * (1 - cfg["stop_loss_pct"]), 2)}
                )
                log_trade(name, sym, "BUY", qty, price)

            elif signal == "sell" and position_qty > 0:
                print(f"[{sym}] SELL {position_qty} @ ${price:.2f}")
                api.submit_order(
                    symbol=sym,
                    qty=position_qty,
                    side="sell",
                    type="market",
                    time_in_force="day"
                )
                log_trade(name, sym, "SELL", position_qty, price)
            else:
                print(f"[{sym}] No action needed.")
        except Exception as e:
            print(f"[{sym}] Error: {e}")


def log_trade(account_name, symbol, side, qty, price):
    row = {
        "time": utc_now(),
        "account": account_name,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
    }
    df = pd.DataFrame([row])
    if os.path.exists("trade_log.csv"):
        df.to_csv("trade_log.csv", mode="a", header=False, index=False)
    else:
        df.to_csv("trade_log.csv", index=False)
    print(f"Logged: {row}")


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
        accounts.append({"name": f"PaperAccount{i}", "api": api, "symbols": cfg["symbols"]})
        print(f"Connected Account {i}")
    except Exception as e:
        print(f"Account {i} failed: {e}")

# --- Run single cycle ---
for acct in accounts:
    trade_account(acct)

print("\nCycle complete. Exiting cleanly.")
