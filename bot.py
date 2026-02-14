import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd

def generate_signal(symbol, api):
    try:
        # Aggressive 2-bar trigger: Up = Buy, Down = Sell
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=2).df
        if len(bars) < 2: return None, None
        
        last_close = bars['close'].iloc[-1]
        prev_close = bars['close'].iloc[-2]
        
        if last_close > prev_close: return 'buy', last_close
        if last_close < prev_close: return 'sell', last_close
        return None, None
    except:
        return None, None

def trade_account(acc):
    api = acc['api']
    print(f"\n=== DEPLOYING 50% AGGRESSIVE POOL: {acc['name']} ===")

    # NUKE: Cancel all pending orders to unlock buying power immediately
    api.cancel_all_orders()

    try:
        account = api.get_account()
        # Scale with 50% of current available firepower
        allocation_pool = float(account.buying_power) * 0.5
        print(f"Firepower Available: ${allocation_pool:.2f}")
    except:
        return

    try:
        positions = {p.symbol: int(float(p.qty)) for p in api.list_positions()}
    except:
        positions = {}

    for symbol in acc['symbols']:
        try:
            signal, price = generate_signal(symbol, api)
            if not signal: continue

            if signal == 'buy' and allocation_pool > price:
                # Calculate qty as an integer to avoid format errors
                qty = int(allocation_pool / price)
                if qty > 0:
                    # AGGRESSIVE: Chase price by bidding $0.05 higher
                    limit_p = round(float(price) + 0.05, 2) 
                    print(f"🚀 [BUY] {symbol}: {qty} shares (Limit: ${limit_p})")
                    api.submit_order(
                        symbol=symbol,
                        qty=str(qty), # Stringified integer for API safety
                        side='buy',
                        type='limit',
                        time_in_force='day',
                        limit_price=str(limit_p),
                        extended_hours=True
                    )
                    allocation_pool -= (qty * price)
            
            elif signal == 'sell' and symbol in positions:
                qty = positions[symbol]
                # AGGRESSIVE: Chase price by asking $0.05 lower
                limit_p = round(float(price) - 0.05, 2)
                print(f"🔥 [SELL] {symbol}: {qty} shares (Limit: ${limit_p})")
                api.submit_order(
                    symbol=symbol,
                    qty=str(qty),
                    side='sell',
                    type='limit',
                    time_in_force='day',
                    limit_price=str(limit_p),
                    extended_hours=True
                )

        except Exception as e:
            print(f"[{symbol}] Error: {e}")

# --- Initialize and Run ---
for i in [1, 2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if key and secret:
        try:
            api = REST(key, secret, base)
            trade_account({
                "name": f"Account{i}", 
                "api": api, 
                "symbols": ['TQQQ','SOXL','AAPL','TSLA','AMD','NVDA']
            })
        except: continue
