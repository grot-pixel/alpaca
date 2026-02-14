import os
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from datetime import datetime, timezone

# --- Signal logic (Keeping your core 4-bar logic) ---
def generate_signal(symbol, api):
    try:
        # Get enough bars to also get the latest price for the limit order
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=5).df
        if len(bars) < 4:
            return None, None
        
        last_close = bars['close'][-1]
        prev_close = bars['close'][-2]
        
        signal = None
        if last_close > prev_close:
            signal = 'buy'
        elif last_close < prev_close:
            signal = 'sell'
            
        return signal, last_close
    except Exception as e:
        print(f"Error generating signal for {symbol}: {e}")
        return None, None

# --- Trade function for one account ---
def trade_account(account_info):
    name = account_info['name']
    api = account_info['api']
    symbols = account_info['symbols']

    print(f"\n=== Trading for {name} ===")

    try:
        account = api.get_account()
        # Using 'buying_power' for maximum leverage/risk as requested
        buying_power = float(account.buying_power)
        equity = float(account.equity)
        print(f"Buying Power: ${buying_power:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"Error fetching account info: {e}")
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
                # RISK: Dividing total buying power by number of symbols to spread risk 
                # but using 100% of allocated power per trade.
                allocation = buying_power / len(symbols)
                qty = int(allocation / price)
                
                if qty > 0:
                    print(f"[{symbol}] BUY Signal: {qty} shares @ ${price}")
                    api.submit_order(
                        symbol=symbol,
                        qty=qty,
                        side='buy',
                        type='limit', # Required for extended hours
                        time_in_force='day',
                        limit_price=price,
                        extended_hours=True # Enable Pre/Post market
                    )
            
            elif signal == 'sell' and symbol in positions:
                qty = positions[symbol]
                print(f"[{symbol}] SELL Signal: {qty} shares @ ${price}")
                api.submit_order(
                    symbol=symbol,
                    qty=qty,
                    side='sell',
                    type='limit',
                    time_in_force='day',
                    limit_price=price,
                    extended_hours=True
                )

        except Exception as e:
            print(f"[{symbol}] Error: {e}")

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
        except Exception as e:
            print(f"Error connecting Account {i}: {e}")

for account_info in accounts:
    trade_account(account_info)
