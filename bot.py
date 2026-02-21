import os
import json
from alpaca_trade_api.rest import REST, TimeFrame
import pandas as pd
from utils import generate_signals


def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)


def get_equity_snapshot(api):
    """Returns current equity and starting equity for the day."""
    account = api.get_account()
    equity = float(account.equity)
    last_equity = float(account.last_equity)  # Alpaca provides prior close equity
    return equity, last_equity


def check_open_stop_take(api, positions, config):
    """
    For each open position, check if stop-loss or take-profit has been hit.
    Submits market sell if so. Returns set of symbols that were force-closed.
    """
    closed = set()
    for symbol, pos in positions.items():
        qty = pos['qty']
        avg_cost = pos['avg_cost']
        current_price = pos['price']

        pct_change = (current_price - avg_cost) / avg_cost

        if pct_change <= -config['stop_loss_pct']:
            print(f"   🛑 STOP-LOSS triggered on {symbol}: {pct_change*100:.2f}% loss — selling {qty} shares")
            try:
                api.submit_order(
                    symbol=symbol,
                    qty=str(qty),
                    side='sell',
                    type='market',
                    time_in_force='day',
                )
                closed.add(symbol)
            except Exception as e:
                print(f"   ❌ Stop-loss order failed for {symbol}: {e}")

        elif pct_change >= config['take_profit_pct']:
            print(f"   💰 TAKE-PROFIT triggered on {symbol}: +{pct_change*100:.2f}% gain — selling {qty} shares")
            try:
                api.submit_order(
                    symbol=symbol,
                    qty=str(qty),
                    side='sell',
                    type='market',
                    time_in_force='day',
                )
                closed.add(symbol)
            except Exception as e:
                print(f"   ❌ Take-profit order failed for {symbol}: {e}")

    return closed


def trade_account(account_info, config):
    api = account_info['api']
    print(f"\n--- 🧠 Trading: {account_info['name']} ---")

    try:
        equity, last_equity = get_equity_snapshot(api)
        daily_pnl_pct = (equity - last_equity) / last_equity
        print(f"   Equity: ${equity:,.2f} | Day P&L: {daily_pnl_pct*100:.2f}%")

        # === CIRCUIT BREAKERS ===
        if daily_pnl_pct >= config['daily_profit_target_pct']:
            print(f"   🏆 Daily profit target hit ({daily_pnl_pct*100:.2f}%). Closing all & stopping.")
            api.close_all_positions()
            return

        if daily_pnl_pct <= -config['daily_loss_limit_pct']:
            print(f"   🚨 Daily loss limit hit ({daily_pnl_pct*100:.2f}%). Closing all & stopping.")
            api.close_all_positions()
            return

        raw_positions = api.list_positions()
        positions = {}
        for p in raw_positions:
            positions[p.symbol] = {
                'qty': int(float(p.qty)),
                'avg_cost': float(p.avg_entry_price),
                'price': float(p.current_price),
            }

    except Exception as e:
        print(f"Account Error: {e}")
        return

    # === STOP-LOSS / TAKE-PROFIT CHECK (runs every cycle) ===
    force_closed = check_open_stop_take(api, positions, config)

    # Refresh positions after any force-closes
    if force_closed:
        try:
            raw_positions = api.list_positions()
            positions = {p.symbol: {
                'qty': int(float(p.qty)),
                'avg_cost': float(p.avg_entry_price),
                'price': float(p.current_price),
            } for p in raw_positions}
        except Exception as e:
            print(f"Position refresh error: {e}")

    # === SIGNAL LOOP ===
    for symbol in config['symbols']:
        if symbol in force_closed:
            continue  # Don't re-enter a symbol just stopped out

        try:
            bars = api.get_bars(symbol, TimeFrame.Minute, limit=200).df
            if bars.empty or len(bars) < config['sma_slow'] + 5:
                print(f"[{symbol}] Not enough data, skipping.")
                continue

            signal, stats = generate_signals(bars, config, return_stats=True)
            price = bars['close'].iloc[-1]

            print(
                f"[{symbol}] ${price:.2f} | "
                f"SMA({config['sma_fast']}/{config['sma_slow']}): {stats['sma_f']:.2f}/{stats['sma_s']:.2f} | "
                f"RSI: {stats['rsi']:.1f} | "
                f"VWAP: {stats['vwap']:.2f} | "
                f"BuyConf: {stats['buy_conf']}/3 | SellConf: {stats['sell_conf']}/3"
            )

            if signal == 'buy':
                target_buy_dollars = equity * config['max_trade_pct']
                max_pos_dollars = equity * config['max_position_pct']
                current_pos = positions.get(symbol, {})
                current_pos_dollars = current_pos.get('qty', 0) * price

                if current_pos_dollars < max_pos_dollars:
                    available_to_buy = min(target_buy_dollars, max_pos_dollars - current_pos_dollars)
                    qty = int(available_to_buy / price)

                    if qty > 0:
                        # Slight slippage buffer: buy slightly above last price to increase fill chance
                        limit_price = round(price * 1.001, 2)
                        print(f"   ✅ BUY {qty} x {symbol} @ limit ${limit_price:.2f}")
                        api.submit_order(
                            symbol=symbol,
                            qty=str(qty),
                            side='buy',
                            type='limit',
                            time_in_force='day',
                            limit_price=str(limit_price),
                            extended_hours=True,
                        )
                    else:
                        print(f"   ⏸ BUY signal but qty=0 (position may be full)")

            elif signal == 'sell' and symbol in positions:
                qty = positions[symbol]['qty']
                limit_price = round(price * 0.999, 2)
                print(f"   🔥 SELL {qty} x {symbol} @ limit ${limit_price:.2f}")
                api.submit_order(
                    symbol=symbol,
                    qty=str(qty),
                    side='sell',
                    type='limit',
                    time_in_force='day',
                    limit_price=str(limit_price),
                    extended_hours=True,
                )

            else:
                print(f"   ⏳ No signal")

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
