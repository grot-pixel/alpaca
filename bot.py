import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from datetime import datetime
import pytz

# --- Example signal logic ---
def generate_signal(symbol, api):
    try:
        # Fetch last 2 minute bars (regular + extended hours)
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=2).df
        if len(bars) < 2:
            print(f"Not enough data for {symbol}, skipping signal.")
            return None
        last_close = bars['close'][-1]
        prev_close = bars['close'][-2]
        if last_close > prev_close:
            return 'buy'
        elif last_close < prev_close:
            return 'sell'
        else:
            return None
    except Exception as e:
        print(f"Error generating signal for {symbol}: {e}")
        return None

# --- Check if market is open ---
def is_market_open():
    est = pytz.timezone('US/Eastern')
    now = datetime.now(est)
    # Market hours: 9:30–16:00 EST
    if now.weekday() < 5 and ((now.hour == 9 and now.minute >= 30) or (9 < now.hour < 16)):
        return True
    return False

# --- Trade function for one account ---
def trade_account(account_info):
    name = account_info['name']
    api = account_info['api']
    symbols = account_info['symbols']

    print(f"\n=== Trading for {name} ===")

    # Get account info
    try:
        account = api.get_account()
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Account Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"Error fetching account info for {name}: {e}")
        return

    # Get current positions
    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
        print(f"Current Positions: {positions}")
    except Exception as e:
        print(f"Error fetching positions for {name}: {e}")
        positions = {}

    # Loop through symbols
    for symbol in symbols:
        signal = generate_signal(symbol, api)
        if signal:
            print(f"[{symbol}] Signal generated: {signal.upper()}")
        else:
            print(f"[{symbol}] No signal.")

        # Only place orders during market hours
        if is_market_open() and signal:
            try:
                if signal == 'buy':
                    print(f"[{symbol}] Market open, submitting BUY order")
                    api.submit_order(symbol, 1, 'buy', 'market', 'day')
                    print(f"[{symbol}] BUY order submitted: 1 share")
                elif signal == 'sell' and positions.get(symbol, 0) > 0:
                    qty = positions[symbol]
                    print(f"[{symbol}] Market open, submitting SELL order")
                    api.submit_order(symbol, qty, 'sell', 'market', 'day')
                    print(f"[{symbol}] SELL order submitted: {qty} shares")
            except Exception as e:
                print(f"[{symbol}] Error submitting order: {e}")
        else:
            if signal:
                print(f"[{symbol}] Market closed, order skipped.")

# --- Load secrets and initialize accounts ---
accounts = []
for i in [1, 2]:  # support multiple accounts
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if not key or not secret:
        print(f"API key/secret missing for account {i}, skipping.")
        continue

    try:
        api = REST(key, secret, base)
        api.get_account()  # test connection
        print(f"Account {i} connected successfully.")
        accounts.append({
            "name": f"PaperAccount{i}",
            "api": api,
            "symbols": ['TQQQ','SOXL','AAPL','TSLA','AMD','NVDA']  # your watchlist
        })
    except Exception as e:
        print(f"Error initializing account {i}: {e}")

# --- Run trading loop ---
for account_info in accounts:
    trade_account(account_info)
