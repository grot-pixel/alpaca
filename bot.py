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
    return now.hour >= 14 and (now.hour < 21 or (now.hour == 21 and now.minute == 0))

def trade_account(account_info, config):
    api = account_info['api']
    print(f"\n--- 🧠 Logic Check for {account_info['name']} ---")

    try:
        account = api.get_account()
        equity = float(account.equity)
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception as e:
        print(f"Account Error: {e}"); return

    for symbol in config['symbols']:
        try:
            bars = api.get_bars(symbol, TimeFrame.Minute, limit=100).df
            if bars.empty: continue

            # Generate signals and get the data used for the decision
            signal, stats = generate_signals(bars, config, return_stats=True)
            price = bars['close'].iloc[-1]

            # --- LOGIC PRINTOUT ---
            print(f"[{symbol}] Price: ${price:.2f} | SMA({config['sma_fast']}/{config['sma_slow']}): {stats['sma_f']:.2f}/{stats['sma_s']:.2f} | RSI: {stats['rsi']:.1f}")

            if signal == 'buy':
                target_buy_dollars = equity * config['max_trade_pct']
                max_pos_dollars = equity * config['max_position_pct']
                current_pos_dollars = positions.get(symbol, 0) * price

                if current_pos_dollars < max_pos_dollars:
                    qty = int(min(target_buy_dollars, max_pos_dollars - current_pos_dollars) / price)
                    if qty > 0 and is_regular_market_open():
                        print(f"   ✅ SIGNAL: BUY {qty} shares (Momentum + Oversold)")
                        api.submit_order(symbol, str(qty), 'buy', 'market', 'day')
                    else:
                        print(f"   ⏸ SIGNAL: BUY (Market closed or position full)")
            
            elif signal == 'sell' and symbol in positions:
                qty = int(positions[symbol])
                if is_regular_market_open():
                    print(f"   🔥 SIGNAL: SELL {qty} shares (Trend reversal + Overbought)")
                    api.submit_order(symbol, str(qty), 'sell', 'market', 'day')

        except Exception as e:
            print(f"   ❌ Error on {symbol}: {e}")

if __name__ == "__main__":
    cfg = load_config()
    for i in [1, 2]:
        key, sec = os.getenv(f"APCA_API_KEY_{i}"), os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        if key and sec:
            api = REST(key, sec, url)
            trade_account({"name": f"Account{i}", "api": api}, cfg)
