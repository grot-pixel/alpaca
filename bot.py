@@ -3,7 +3,6 @@
from datetime import datetime, timezone
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame
from alpaca.common.exceptions import APIError

# --- Load config ---
CONFIG_FILE = "config.json"
@@ -14,7 +13,6 @@
# --- Example signal logic ---
def generate_signal(symbol, api, cfg):
    try:
        # Fetch recent bars
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=max(cfg["sma_slow"], cfg["rsi_period"])+1).df
        if bars.empty or len(bars) < max(cfg["sma_slow"], cfg["rsi_period"]):
            return None, "Not enough data"
@@ -52,11 +50,8 @@ def trade_account(account_info):
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Account Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except APIError as e:
        print(f"API error fetching account info for {name}: {e}")
        return
    except Exception as e:
        print(f"Unexpected error fetching account info for {name}: {e}")
        print(f"Error fetching account info for {name}: {e}")
        return

    try:
@@ -86,7 +81,6 @@ def trade_account(account_info):
                    api.submit_order(sym, qty, 'sell', 'market', 'day')
                    print(f"[{sym}] Order submitted: SELL {qty} shares")
            else:
                # Extended hours: log only
                print(f"[{sym}] Signal: {signal.upper()} ({reason}) [Extended Hours, NOT placing order]")
        except Exception as e:
            print(f"[{sym}] Error processing symbol: {e}")
@@ -104,7 +98,7 @@ def trade_account(account_info):

    try:
        api = REST(key, secret, base)
        api.get_account()  # test connection
        api.get_account()
        print(f"Account {i} connected successfully")
        accounts.append({
            "name": f"PaperAccount{i}",
