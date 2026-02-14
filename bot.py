import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd

def generate_signal(symbol, api):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=2).df
        if len(bars) < 2: return None, None
        last_close = bars['close'].iloc[-1]
        prev_close = bars['close'].iloc[-2]
        if last_close > prev_close: return 'buy', last_close
        if last_close < prev_close: return 'sell', last_close
        return None, None
    except: return None, None

def trade_account(acc):
    api = acc['api']
    print(f"\n=== DEPLOYING 50% AGGRESSIVE POOL: {acc['name']} ===")
    
    # Unlock capital and shares immediately
    api.cancel_all_orders()

    try:
        account = api.get_account()
        # Scale with 50% of available buying power
        allocation_pool = float(account.buying_power) * 0.5
    except: return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except:
        positions = {}

    for symbol in acc['symbols']:
        try:
            signal, price = generate_signal(symbol, api)
            if not signal: continue

            if signal == 'buy' and allocation_pool > price:
                qty = int(allocation_pool / price)
                if qty > 0:
                    print(f"🚀 [BUY] {symbol}: {qty} shares @ ${price}")
                    api.submit_order(symbol, qty, 'buy', 'limit', 'day', price, True)
                    allocation_pool -= (qty * price)
            
            elif signal == 'sell' and symbol in positions:
                qty = positions[symbol]
                print(f"🔥 [SELL] {symbol}: {qty} shares @ ${price}")
                api.submit_order(symbol, qty, 'sell', 'limit', 'day', price, True)
        except Exception as e:
            print(f"Error on {symbol}: {e}")

# --- Initialize and Run ---
for i in [1, 2]:
    key, secret = os.getenv(f"APCA_API_KEY_{i}"), os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
    if key and secret:
        api = REST(key, secret, base)
        trade_account({"name": f"Account{i}", "api": api, "symbols": ['TQQQ','SOXL','AAPL','TSLA','AMD','NVDA']})
