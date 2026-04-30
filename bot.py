import os
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from utils import generate_signals


def load_config():
    with open('config.json', 'r') as f:
        return json.load(f)


def get_equity_snapshot(trading_client):
    account = trading_client.get_account()
    return float(account.equity), float(account.last_equity)


def list_open_orders(trading_client, symbol):
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
    return trading_client.get_orders(filter=req)


def cancel_pending_orders_for_symbol(trading_client, symbol):
    """Idempotency: kill any open orders for this symbol before placing new ones."""
    try:
        for o in list_open_orders(trading_client, symbol):
            trading_client.cancel_order_by_id(o.id)
            print(f"   ↩︎  Canceled stale {o.side} order {str(o.id)[:8]} on {symbol}")
    except Exception as e:
        print(f"   ⚠️  Could not cancel pending orders for {symbol}: {e}")


def has_open_order(trading_client, symbol):
    try:
        return len(list_open_orders(trading_client, symbol)) > 0
    except Exception:
        return False


def get_recent_bars(data_client, symbol):
    """Fetch the last few days of minute bars and return as a flat df indexed by timestamp."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=5)
    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=500,
        feed=DataFeed.IEX,  # Free tier. Switch to DataFeed.SIP with a paid data plan.
    )
    bars = data_client.get_stock_bars(req).df
    if bars is None or bars.empty:
        return pd.DataFrame()
    # alpaca-py returns a MultiIndex (symbol, timestamp) — flatten to just timestamp.
    if isinstance(bars.index, pd.MultiIndex):
        bars = bars.xs(symbol, level=0)
    return bars


def trade_account(account_info, config):
    trading_client = account_info['trading_client']
    data_client = account_info['data_client']
    print(f"\n--- 🧠 Trading: {account_info['name']} ---")

    try:
        equity, last_equity = get_equity_snapshot(trading_client)
        daily_pnl_pct = (equity - last_equity) / last_equity if last_equity else 0.0
        print(f"  Equity: ${equity:,.2f} | Day P&L: {daily_pnl_pct*100:.2f}%")

        # === CIRCUIT BREAKERS ===
        if daily_pnl_pct >= config['daily_profit_target_pct']:
            print(f"  🏆 Daily profit target hit ({daily_pnl_pct*100:.2f}%). Flat & stop.")
            trading_client.cancel_orders()
            trading_client.close_all_positions(cancel_orders=True)
            return
        if daily_pnl_pct <= -config['daily_loss_limit_pct']:
            print(f"  🚨 Daily loss limit hit ({daily_pnl_pct*100:.2f}%). Flat & stop.")
            trading_client.cancel_orders()
            trading_client.close_all_positions(cancel_orders=True)
            return

        raw_positions = trading_client.get_all_positions()
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
            bars = get_recent_bars(data_client, symbol)

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

            # === EXIT on trend reversal (bracket TP/SL handles normal exits at the broker) ===
            if signal == 'sell' and current_pos:
                cancel_pending_orders_for_symbol(trading_client, symbol)
                qty = current_pos['qty']
                print(f"  🔥 EXIT {qty} x {symbol} (trend reversed)")
                exit_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
                trading_client.submit_order(order_data=exit_req)
                continue

            # === ENTRY ===
            if signal == 'buy' and not current_pos and not has_open_order(trading_client, symbol):
                # Vol-targeted sizing: each trade risks risk_per_trade_pct of equity
                stop_distance = atr_val * config['atr_stop_mult']
                tp_distance = atr_val * config['atr_take_profit_mult']
                if stop_distance <= 0 or pd.isna(stop_distance):
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

                cancel_pending_orders_for_symbol(trading_client, symbol)
                print(
                    f"  ✅ BUY {qty} x {symbol} @ limit ${limit_price} "
                    f"(TP ${tp_price} / SL ${sl_price}, "
                    f"risk ${qty*stop_distance:.0f})"
                )
                bracket_req = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tp_price),
                    stop_loss=StopLossRequest(stop_price=sl_price),
                )
                trading_client.submit_order(order_data=bracket_req)

        except Exception as e:
            print(f"  ❌ Error on {symbol}: {e}")


if __name__ == "__main__":
    cfg = load_config()
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        if key and sec:
            paper = "paper" in url.lower()
            trading_client = TradingClient(api_key=key, secret_key=sec, paper=paper)
            data_client = StockHistoricalDataClient(api_key=key, secret_key=sec)
            trade_account(
                {
                    "name": f"Account{i}",
                    "trading_client": trading_client,
                    "data_client": data_client,
                },
                cfg,
            )
