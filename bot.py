import os
import json
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from datetime import datetime, timezone
from utils import generate_signals

def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)

def is_regular_market_open():
    now = datetime.now(timezone.utc)
    # Regular hours: 14:30 - 21:00 UTC (9:30 AM - 4:00 PM ET)
    return now.hour >= 14 and (now.hour < 21 or (now.hour == 21 and now.minute == 0))

def trade_account(account_info, config):
    api = account_info['api']
    print(f"\n--- Trading for {account_info['name']} ---")

    try:
        account = api.get_account()
        equity = float(account.equity)
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception as e:
        print(f"Account Error: {e}"); return

    for symbol in config['symbols']:
        try:
            # Fetch bars (Limit 100 to cover SMA 30 + buffer)
            bars = api.get_bars(symbol, TimeFrame.Minute, limit=100).df
            if bars.empty: continue

            signal = generate_signals(bars, config)
            price = bars['close'].iloc[-1]

            if signal == 'buy':
                # Calculate Scalable Quantity
                # 1. Buy Size (15% of total equity)
                target_buy_dollars = equity * config['max_trade_pct']
                # 2. Max Position Size (20% of total equity)
                max_pos_dollars = equity * config['max_position_pct']
                current_pos_dollars = positions.get(symbol, 0) * price

                # Only buy if we aren't already at our position cap
                if current_pos_dollars < max_pos_dollars:
                    available_to_buy = min(target_buy_dollars, max_pos_dollars - current_pos_dollars)
                    qty = int(available_to_buy / price)

                    if qty > 0 and is_regular_market_open():
                        print(f"🚀 [{symbol}] BUY {qty} shares @ ${price}")
                        api.submit_order(symbol, str(qty), 'buy', 'market', 'day')
                    else:
                        print(f"⏸ [{symbol}] Buy signal, but market closed or qty 0.")

            elif signal == 'sell' and symbol in positions:
                qty = int(positions[symbol])
                if is_regular_market_open():
                    print(f"🔥 [{symbol}] SELL {qty} shares @ ${price}")
                    api.submit_order(symbol, str(qty), 'sell', 'market', 'day')

        except Exception as e:
            print(f"Error on {symbol}: {e}")

# Main execution
if __name__ == "__main__":
    cfg = load_config()
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        if key and sec:
            api = REST(key, sec, url)
            trade_account({"name": f"Account{i}", "api": api}, cfg)
