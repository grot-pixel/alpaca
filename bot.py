import os
import json
from datetime import datetime, timedelta, timezone

from alpaca_trade_api.rest import REST, TimeFrame

from utils import generate_signals


def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)


def get_equity_snapshot(api):
    account = api.get_account()
    return float(account.equity), float(account.last_equity)


def cancel_pending_orders_for_symbol(api, symbol):
    """Idempotency: kill any open orders for this symbol before placing new ones."""
    try:
        open_orders = api.list_orders(status='open', symbols=[symbol])
        for o in open_orders:
            api.cancel_order(o.id)
            print(f"   ↩︎  Canceled stale {o.side} order {o.id[:8]} on {symbol}")
    except Exception as e:
        print(f"   ⚠️  Could not cancel pending orders for {symbol}: {e}")


def has_open_order(api, symbol):
    try:
        return len(api.list_orders(status='open', symbols=[symbol])) > 0
    except Exception:
        return False


def trade_account(account_info, config):
    api = account_info['api']
    print(f"\n--- 🧠 Trading: {account_info['name']} ---")

    try:
        equity, last_equity = get_equity_snapshot(api)
        daily_pnl_pct = (equity - last_equity) / last_equity if last_equity else 0.0
        print(f"  Equity: ${equity:,.2f} | Day P&L: {daily_pnl_pct*100:.2f}%")

        # === CIRCUIT BREAKERS ===
        if daily_pnl_pct >= config['daily_profit_target_pct']:
            print(f"  🏆 Daily profit target hit ({daily_pnl_pct*100:.2f}%). Flat & stop.")
            api.cancel_all_orders()
            api.close_all_positions()
            return
        if daily_pnl_pct <= -config['daily_loss_limit_pct']:
            print(f"  🚨 Daily loss limit hit ({daily_pnl_pct*100:.2f}%). Flat & stop.")
            api.cancel_all_orders()
            api.close_all_positions()
            return

        raw_positions = api.list_positions()
        positions = {
            p.symbol: {
                'qty': int(float(p.qty)),
                'avg_cost': float(p.avg_entry_price),
                'price': float(p.current_price),
            }
            for p in raw_positions
        }
    except Exception as e:
        print(f"Account Error: {e}")
        return

    # === SIGNAL LOOP ===
    for symbol in config['symbols']:
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=5)
            bars = api.get_bars(
                symbol,
                TimeFrame.Minute,
                start=start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                end=end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                limit=500,
                adjustment='raw',
                feed='iex',
            ).df

            min_bars = max(config['sma_slow'] + 5, config['adx_period'] * 2 + 5, 50)
            if bars.empty or len(bars) < min_bars:
                print(f"[{symbol}] Not enough data ({len(bars)} bars), skipping.")
                continue

            bars = bars.tail(200)
            signal, stats = generate_signals(bars, config, return_stats=True)
            price = float(bars['close'].iloc[-1])
            atr_val = stats['atr']

            print(
                f"[{symbol}] ${price:.2f} | "
                f"ADX:{stats['adx']:.1f} RSI:{stats['rsi']:.1f} "
                f"SMA:{stats['sma_f']:.2f}/{stats['sma_s']:.2f} "
                f"VWAP:{stats['vwap']:.2f} ATR:${atr_val:.2f} | "
                f"→ {signal or 'no signal'}"
            )

            current_pos = positions.get(symbol)

            # === EXIT on trend reversal (bracket TP/SL handles the normal exits) ===
            if signal == 'sell' and current_pos:
                cancel_pending_orders_for_symbol(api, symbol)
                qty = current_pos['qty']
                print(f"  🔥 EXIT {qty} x {symbol} (trend reversed)")
                api.submit_order(
                    symbol=symbol, qty=str(qty), side='sell',
                    type='market', time_in_force='day',
                )
                continue

            # === ENTRY ===
            if signal == 'buy' and not current_pos and not has_open_order(api, symbol):
                # Vol-targeted sizing: each trade risks risk_per_trade_pct of equity
                stop_distance = atr_val * config['atr_stop_mult']
                tp_distance = atr_val * config['atr_take_profit_mult']
                if stop_distance <= 0:
                    print(f"  ⏸ Bad ATR, skipping")
                    continue

                risk_dollars = equity * config['risk_per_trade_pct']
                qty_by_risk = int(risk_dollars / stop_distance)

                # Cap by max position size
                max_pos_dollars = equity * config['max_position_pct']
                qty_by_pos = int(max_pos_dollars / price)
                qty = min(qty_by_risk, qty_by_pos)

                if qty < 1:
                    print(f"  ⏸ Sizing produced qty=0 (risk=${risk_dollars:.0f}, "
                          f"stop_dist=${stop_distance:.2f})")
                    continue

                limit_price = round(price * 1.001, 2)
                tp_price = round(price + tp_distance, 2)
                sl_price = round(price - stop_distance, 2)

                cancel_pending_orders_for_symbol(api, symbol)
                print(
                    f"  ✅ BUY {qty} x {symbol} @ limit ${limit_price} "
                    f"(TP ${tp_price} / SL ${sl_price}, "
                    f"risk ${qty*stop_distance:.0f})"
                )
                api.submit_order(
                    symbol=symbol,
                    qty=str(qty),
                    side='buy',
                    type='limit',
                    limit_price=str(limit_price),
                    time_in_force='day',
                    order_class='bracket',
                    take_profit={'limit_price': str(tp_price)},
                    stop_loss={'stop_price': str(sl_price)},
                )

        except Exception as e:
            print(f"  ❌ Error on {symbol}: {e}")


if __name__ == "__main__":
    cfg = load_config()
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        if key and sec:
            api = REST(key, sec, url)
            trade_account({"name": f"Account{i}", "api": api}, cfg)
