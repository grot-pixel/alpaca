import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from datetime import datetime, timezone

def generate_signal(symbol, api):
    try:
        # Core 4-bar logic with .iloc fix for stability
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=5).df
        if len(bars) < 4:
            return None, None
        
        last_close = bars['close'].iloc[-1]
        prev_close = bars['close'].iloc[-2]
        
        signal = None
        if last_close > prev_close:
            signal = 'buy'
        elif last_close < prev_close:
            signal = 'sell'
            
        return signal, last_close
    except Exception as e:
        print(f"Error for {symbol}: {e}")
        return None, None

def trade_account(account_info):
    name = account_info['name']
    api = account_info['api']
    symbols = account_info['symbols']

    print(f"\n=== EXTREME RISK TRADING: {name} ===")

    try:
        account = api.get_account()
        # Use total available buying power
        buying_power = float(account.buying_power)
        print(f"Total Firepower: ${buying_power:.2f}")
    except Exception as e:
        print(f"Error: {e}")
        return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception as e:
        positions = {}

    for symbol in symbols:
        try:
            signal, price = generate_signal(symbol, api)
            if not signal:
                continue

            if signal == 'buy':
                # EXTREME: Allocate 50% of REMAINING buying power to this one signal
                # This allows stacking even if you already own it.
                allocation = buying_power * 0.5 
                qty = int(allocation / price)
                
                if qty > 0:
                    print(f"[{symbol}] STACKING BUY: {qty} shares @ ${price}")
                    api.submit_order(
                        symbol=symbol, qty=qty, side='buy',
                        type='limit', time_in_force='day',
                        limit_price=price, extended_hours=True # Extended hours enabled
                    )
            
            elif signal == 'sell' and symbol in positions:
                # Liquidate the entire stacked position
                qty = positions[symbol]
                print(f"[{symbol}] TOTAL LIQUIDATION: {qty} shares @ ${price}")
                api.submit_order(
                    symbol=symbol, qty=qty, side='sell',
                    type='limit', time_in_force='day',
                    limit_price=price, extended_hours=True
                )

        except Exception as e:
            print(f"[{symbol}] Error: {e}")

# --- Init and Run ---
accounts = []
for i in [1, 2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if key and secret:
        try:
            api = REST(key, secret, base)
            accounts.append({
                "name": f"Account{i}",
                "api": api,
                "symbols": ['TQQQ','SOXL','AAPL','TSLA','AMD','NVDA']
            })
        except Exception as e:
            print(f"Conn Error {i}: {e}")

for account_info in accounts:
    trade_account(account_info)
