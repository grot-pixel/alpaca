import os
import json
from datetime import datetime, timezone
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

# === Load config ===
CONFIG_FILE = "config.json"
with open(CONFIG_FILE) as f:
    cfg = json.load(f)
print("Loaded config:", cfg)

# === Example signal logic (simple SMA crossover) ===
def generate_signal(symbol, api, cfg):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=max(cfg["sma_slow"], cfg["rsi_period"]) + 1).df
        if bars.empty or len(bars) < max(cfg["sma_slow"], cfg["rsi_period"]):
            return None, "Not enough data"

        bars["sma_fast"] = bars["close"].rolling(cfg["sma_fast"]).mean()
        bars["sma_slow"] = bars["close"].rolling(cfg["sma_slow"]).mean()

        last_fast = bars["sma_fast"].iloc[-1]
        last_slow = bars["sma_slow"].iloc[-1]

        if last_fast > last_slow:
            return "buy", f"SMA fast {last_fast:.2f} > SMA slow {last_slow:.2f}"
        elif last_fast < last_slow:
            return "sell", f"SMA fast {last_fast:.2f} < SMA slow {last_slow:.2f}"
        else:
            return None, "No crossover"
    except Exception as e:
        return None, f"Error generating signal: {e}"

# === Regular market hours check (9:30 AM–4:00 PM ET) ===
def is_regular_hours():
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and 14 <= now.hour < 21  # 9:30–16:00 ET

# === Core trading logic for each account ===
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
        print(f"Error fetching account info for {name}: {e}")
        return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
        print(f"Current Positions: {positions}")
    except Exception as e:
        print(f"Error fetching positions for {name}: {e}")
        positions = {}

    regular_hours = is_regular_hours()

    for sym in symbols:
        try:
            signal, reason = generate_signal(sym, api, cfg)
            if not signal:
                print(f"[{sym}] No signal, skipping ({reason})")
                continue

            if not regular_hours:
                print(f"[{sym}] Signal: {signal.upper()} ({reason}) [Extended Hours, NOT placing order]")
                continue

            # === BUY logic ===
            if signal == "buy":
                quote = api.get_latest_trade(sym)
                price = float(quote.price)
                if price <= 0:
                    print(f"[{sym}] Invalid price, skipping.")
                    continue

                current_qty = positions.get(sym, 0)
                current_value = current_qty * price

                max_position_value = equity * cfg["max_position_pct"]
                max_trade_value = cash * cfg["max_trade_pct"]

                remaining_value = max_position_value - current_value
                trade_value = min(max_trade_value, remaining_value)

                if trade_value <= 0:
                    print(f"[{sym}] Position already at or above max size, skipping.")
                    continue

                try:
                    # Prefer fractional notional order
                    api.submit_order(
                        symbol=sym,
                        notional=trade_value,
                        side="buy",
                        type="market",
                        time_in_force="day"
                    )
                    print(f"[{sym}] Order submitted: BUY ${trade_value:.2f} notional (~${price:.2f})")
                except Exception:
                    # Fallback for non-fractional accounts
                    qty = int(trade_value // price)
                    if qty < 1:
                        print(f"[{sym}] Not enough cash to buy even 1 share (${price:.2f}).")
                        continue
                    api.submit_order(
                        symbol=sym,
                        qty=qty,
                        side="buy",
                        type="market",
                        time_in_force="day"
                    )
                    print(f"[{sym}] Order submitted: BUY {qty} shares (~${price:.2f})")

            # === SELL logic ===
            elif signal == "sell" and positions.get(sym, 0) > 0:
                qty = positions[sym]
                print(f"[{sym}] Signal: SELL {qty} shares ({reason})")
                api.submit_order(
                    symbol=sym,
                    qty=qty,
                    side="sell",
                    type="market",
                    time_in_force="day"
                )
                print(f"[{sym}] Order submitted: SELL {qty} shares at market")

        except Exception as e:
            print(f"[{sym}] Error processing symbol: {e}")

# === Initialize accounts ===
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
            "symbols": cfg["symbols"]
        })
    except Exception as e:
        print(f"Error initializing account {i}: {e}")

# === Run trading loop ===
for account_info in accounts:
    trade_account(account_info)
