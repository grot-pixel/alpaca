import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from datetime import datetime, timezone

# --- Signal logic (Fixed for Pandas deprecation) ---
def generate_signal(symbol, api):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=5).df
        if len(bars) < 4:
            return None, None
        
        # FIXED: Using .iloc to avoid FutureWarnings
        last_close = bars['close'].iloc[-1]
        prev_close = bars['close'].iloc[-2]
        
        signal = None
        if last_close > prev_close:
            signal = 'buy'
        elif last_close < prev_close:
            signal = 'sell'
            
        return signal, last_close
    except Exception as e:
        print(f"Error generating signal for {symbol}: {e}")
        return None, None

def trade_account(account_info):
    name = account_info['name']
    api = account_info['api']
    symbols = account_info['symbols']

    print(f"\n=== Trading for {name} ===")

    try:
        account = api.get_account()
        buying_power = float(account.buying_power)
    except Exception as e:
        print(f"Error fetching account: {e}")
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

            # Check if we already own the stock before buying more
            if signal == 'buy' and symbol not in positions:
                allocation = buying_power / len(symbols)
                qty = int(allocation / price)
                
                if qty > 0:
                    print(f"[{symbol}] BUY Signal: {qty} shares @ ${price}")
                    api.submit_order(
                        symbol=symbol, qty=qty, side='buy',
                        type='limit', time_in_force='day',
                        limit_price=price, extended_hours=True
                    )
            
            elif signal == 'sell' and symbol in positions:
                qty = positions[symbol]
                print(f"[{symbol}] SELL Signal: {qty} shares @ ${price}")
                api.submit_order(
                    symbol=symbol, qty=qty, side='sell',
                    type='limit', time_in_force='day',
                    limit_price=price, extended_hours=True
                )

        except Exception as e:
            print(f"[{symbol}] Error: {e}")

# ... rest of your initialization code ...
