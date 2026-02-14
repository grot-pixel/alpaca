import os
import json
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from utils import generate_signals

def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)

def trade_account(account_info, config):
    api = account_info['api']
    print(f"\n--- 🧠 Logic Check for {account_info['name']} ---")

    try:
        account = api.get_account()
        equity = float(account.equity)
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception as e:
        print(f"Account Error: {e}")
        return

    for symbol in config['symbols']:
        try:
            # Extended hours data is included by default in minute bars
            bars = api.get_bars(symbol, TimeFrame.Minute, limit=100).df
            if bars.empty: 
                continue

            signal, stats = generate_signals(bars, config, return_stats=True)
            price = bars['close'].iloc[-1]

            print(f"[{symbol}] Price: ${price:.2f} | SMA({config['sma_fast']}/{config['sma_slow']}): {stats['sma_f']:.2f}/{stats['sma_s']:.2f} | RSI: {stats['rsi']:.1f}")

            if signal == 'buy':
                # Full Scaling Logic Restored
                target_buy_dollars = equity * config['max_trade_pct']
                max_pos_dollars = equity * config['max_position_pct']
                current_pos_dollars = positions.get(symbol, 0) * price

                if current_pos_dollars < max_pos_dollars:
                    available_to_buy = min(target_buy_dollars, max_pos_dollars - current_pos_dollars)
                    qty = int(available_to_buy / price)
                    
                    if qty > 0:
                        print(f"   ✅ SIGNAL: BUY {qty} shares (Limit Order @ ${price:.2f})")
                        # Upgraded to Limit Order + Extended Hours
                        api.submit_order(
                            symbol=symbol, 
                            qty=str(qty), 
                            side='buy', 
                            type='limit', 
                            time_in_force='day',
                            limit_price=price,
                            extended_hours=True
                        )
                    else:
                        print(f"   ⏸ SIGNAL: BUY (Position full or qty 0)")
            
            elif signal == 'sell' and symbol in positions:
                qty = int(positions[symbol])
                print(f"   🔥 SIGNAL: SELL {qty} shares (Limit Order @ ${price:.2f})")
                api.submit_order(
                    symbol=symbol, 
                    qty=str(qty), 
                    side='sell', 
                    type='limit', 
                    time_in_force='day',
                    limit_price=price,
                    extended_hours=True
                )

        except Exception as e:
            print(f"   ❌ Error on {symbol}: {e}")

if __name__ == "__main__":
    cfg = load_config()
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        if key and sec:
            api = REST(key, sec, url)
            trade_account({"name": f"Account{i}", "api": api}, cfg)
