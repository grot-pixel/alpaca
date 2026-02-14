import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd

def generate_signal(symbol, api):
    try:
        # Aggressive 2-bar trigger
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=2).df
        if len(bars) < 2: return None, None
        
        last_close = bars['close'].iloc[-1]
        prev_close = bars['close'].iloc[-2]
        
        if last_close > prev_close: return 'buy', last_close
        if last_close < prev_close: return 'sell', last_close
        return None, None
    except Exception as e:
        print(f"Signal Error for {symbol}: {e}")
        return None, None

def trade_account(acc):
    api = acc['api']
    print(f"\n=== DEPLOYING 50% AGGRESSIVE POOL: {acc['name']} ===")

    # NUKE: Cancel all pending orders to unlock buying power and shares immediately
    api.cancel_all_orders()

    try:
        account = api.get_account()
        buying_power = float(account.buying_power)
        # Target 50% of current available firepower
        allocation_pool = buying_power * 0.5
        print(f"Buying Power: ${buying_power:.2f} | 50% Target: ${allocation_pool:.2f}")
    except Exception as e:
        print(f"Account Error: {e}")
        return

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
                    print(f"🚀 [BUY] {symbol}: {qty} shares @ ${price} (Stacking Enabled)")
                    api.submit_order(
                        symbol=symbol, qty=qty, side='buy',
                        type='limit', time_in_force='day',
                        limit_price=price, extended_hours=True
                    )
                    # Update pool if buying multiple symbols in one run
                    allocation_pool -= (qty * price)
            
            elif signal == 'sell' and symbol in positions:
                qty = positions[symbol]
                print(f"🔥 [SELL] {symbol}: {qty} shares @ ${price} (Liquidating Stack)")
                api.submit_order(
                    symbol=symbol, qty=qty, side='sell',
                    type='limit', time_in_force='day',
                    limit_price=price, extended_hours=True
                )

        except Exception as e:
            print(f"[{symbol}] Trade Error: {e}")

# --- Initialize and Run ---
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
        except: continue

for acc in accounts:
    trade_account(acc)
