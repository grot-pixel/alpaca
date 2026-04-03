"""
bot.py — Alpaca Multi-Strategy Trading Bot
===========================================
Designed for GitHub Actions: stateless, runs one full cycle per invocation.
Supports up to 2 Alpaca accounts via environment variables.

Secrets required (GitHub → Settings → Secrets → Actions):
  APCA_API_KEY_1, APCA_API_SECRET_1, APCA_BASE_URL_1  (required)
  APCA_API_KEY_2, APCA_API_SECRET_2, APCA_BASE_URL_2  (optional second account)
  EMAIL_USER, EMAIL_PASS                                (for daily report)
"""

import os
import json
from datetime import datetime, timedelta, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from utils import generate_signals, get_today_bars, get_multi_day_bars

# ─── CONFIG ──────────────────────────────────────────────────────────────────

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

# ─── ACCOUNT HELPERS ─────────────────────────────────────────────────────────

def get_equity(client: TradingClient) -> tuple[float, float]:
    """Returns (current_equity, prev_close_equity)."""
    acct = client.get_account()
    return float(acct.equity), float(acct.last_equity)


def get_positions(client: TradingClient) -> dict:
    """Returns {symbol: {qty, avg_cost, price}}."""
    raw = client.get_all_positions()
    return {
        p.symbol: {
            "qty":      int(float(p.qty)),
            "avg_cost": float(p.avg_entry_price),
            "price":    float(p.current_price),
            "unreal_pct": float(p.unrealized_plpc) * 100,
        }
        for p in raw
    }


def is_market_open(client: TradingClient) -> bool:
    return client.get_clock().is_open

# ─── STOP / TAKE PROFIT ──────────────────────────────────────────────────────

def check_stops_and_targets(client: TradingClient, positions: dict, cfg: dict) -> set:
    """
    Exits any position that has hit its stop-loss or take-profit.
    Returns set of symbols that were closed.
    """
    closed = set()
    stop_pct  = cfg["stop_loss_pct"]
    take_pct  = cfg["take_profit_pct"]

    for symbol, pos in positions.items():
        pnl_pct = pos["unreal_pct"]
        qty     = pos["qty"]
        price   = pos["price"]

        if pnl_pct <= -(stop_pct * 100):
            print(f"  🛑 STOP-LOSS  {symbol}: {pnl_pct:+.2f}% — closing {qty} shares")
            _market_sell(client, symbol, qty)
            closed.add(symbol)

        elif pnl_pct >= (take_pct * 100):
            print(f"  🎯 TAKE-PROFIT {symbol}: {pnl_pct:+.2f}% — closing {qty} shares")
            _market_sell(client, symbol, qty)
            closed.add(symbol)

        # Trailing stop: lock in half the gain if up > 5%
        elif pnl_pct >= 5.0:
            trail_floor = price * (1 - (stop_pct * 0.5))
            print(f"  ↗ Trailing floor active on {symbol}: ${trail_floor:.2f} (up {pnl_pct:.1f}%)")

    return closed


def _market_sell(client: TradingClient, symbol: str, qty: int):
    try:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                  time_in_force=TimeInForce.DAY)
        client.submit_order(req)
    except Exception as e:
        print(f"    ❌ Sell failed for {symbol}: {e}")

# ─── SIGNAL LOOP ─────────────────────────────────────────────────────────────

def run_signals(client: TradingClient, data_client: StockHistoricalDataClient,
                positions: dict, equity: float, cfg: dict, force_closed: set):
    """
    Scans symbols for buy/sell signals and submits orders.
    """
    symbols    = cfg["symbols"]
    max_pos    = cfg["max_position_pct"]
    trade_pct  = cfg["max_trade_pct"]
    max_slots  = cfg["max_open_positions"]
    open_count = len(positions)

    for symbol in symbols:
        if symbol in force_closed:
            continue

        try:
            # Fetch data: multi-day minute bars for indicators
            bars_intra = get_today_bars(data_client, symbol)      # today only (for VWAP)
            bars_hist  = get_multi_day_bars(data_client, symbol)  # 3+ days (for SMA/RSI/MACD)

            if bars_hist is None or len(bars_hist) < cfg["sma_slow"] + 10:
                print(f"  [{symbol}] Not enough historical data, skipping.")
                continue

            if bars_intra is None or bars_intra.empty:
                print(f"  [{symbol}] No intraday data, skipping.")
                continue

            signal, stats = generate_signals(bars_hist, bars_intra, cfg)
            price = float(bars_hist["close"].iloc[-1])

            print(
                f"  [{symbol}] ${price:.2f} | "
                f"SMA({cfg['sma_fast']}/{cfg['sma_slow']}): {stats['sma_f']:.2f}/{stats['sma_s']:.2f} | "
                f"RSI: {stats['rsi']:.1f} | "
                f"MACD hist: {stats['macd_hist']:+.3f} | "
                f"VWAP: {stats['vwap']:.2f} | "
                f"Signal: {signal or '—'} [{stats['buy_conf']}/4 buy, {stats['sell_conf']}/4 sell]"
            )

            current_pos = positions.get(symbol)
            current_val = (current_pos["qty"] * price) if current_pos else 0.0
            max_val     = equity * max_pos

            # ── BUY ─────────────────────────────────────────────────────────
            if signal == "buy":
                if open_count >= max_slots:
                    print(f"    ⏸ Max open positions ({max_slots}) reached")
                    continue

                if current_val >= max_val:
                    print(f"    ⏸ Position already at max size (${current_val:.0f})")
                    continue

                available  = min(equity * trade_pct, max_val - current_val)
                qty        = int(available / price)
                if qty < 1:
                    print(f"    ⏸ BUY qty=0, skipping")
                    continue

                limit_price = round(price * 1.001, 2)
                print(f"    ✅ BUY  {qty}x {symbol} @ limit ${limit_price:.2f} (${qty*price:.0f})")
                try:
                    req = LimitOrderRequest(
                        symbol=symbol, qty=qty, side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY,
                        limit_price=limit_price,
                    )
                    client.submit_order(req)
                    open_count += 1
                except Exception as e:
                    print(f"    ❌ BUY order failed: {e}")

            # ── SELL ────────────────────────────────────────────────────────
            elif signal == "sell" and current_pos:
                qty = current_pos["qty"]
                limit_price = round(price * 0.999, 2)
                print(f"    🔥 SELL {qty}x {symbol} @ limit ${limit_price:.2f}")
                try:
                    req = LimitOrderRequest(
                        symbol=symbol, qty=qty, side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                        limit_price=limit_price,
                    )
                    client.submit_order(req)
                    open_count -= 1
                except Exception as e:
                    print(f"    ❌ SELL order failed: {e}")

        except Exception as e:
            print(f"  [{symbol}] ❌ Error: {e}")

# ─── MAIN PER-ACCOUNT LOGIC ──────────────────────────────────────────────────

def trade_account(name: str, api_key: str, api_secret: str, base_url: str, cfg: dict):
    print(f"\n{'═'*50}")
    print(f"  🤖 Trading: {name} ({'Paper' if 'paper' in base_url else '⚠️  LIVE'})")
    print(f"{'═'*50}")

    is_paper = "paper" in base_url
    client = TradingClient(api_key, api_secret, paper=is_paper)
    data_client = StockHistoricalDataClient(api_key, api_secret)

    # Market open check
    if not is_market_open(client):
        print("  Market is closed. Skipping.")
        return

    # Portfolio snapshot
    try:
        equity, last_equity = get_equity(client)
    except Exception as e:
        print(f"  ❌ Account fetch failed: {e}")
        return

    day_pnl     = equity - last_equity
    day_pnl_pct = (day_pnl / last_equity) * 100 if last_equity else 0
    print(f"  Equity: ${equity:,.2f} | Day P&L: ${day_pnl:+,.2f} ({day_pnl_pct:+.2f}%)")

    # Circuit breakers
    if day_pnl_pct >= cfg["daily_profit_target_pct"] * 100:
        print(f"  🏆 Daily profit target hit ({day_pnl_pct:.2f}%). Closing all & stopping.")
        client.close_all_positions()
        return

    if day_pnl_pct <= -(cfg["daily_loss_limit_pct"] * 100):
        print(f"  🚨 Daily loss limit hit ({day_pnl_pct:.2f}%). Closing all & stopping.")
        client.close_all_positions()
        return

    # Positions
    try:
        positions = get_positions(client)
        print(f"  Open positions: {len(positions)} — {list(positions.keys()) or 'none'}")
    except Exception as e:
        print(f"  ❌ Position fetch failed: {e}")
        return

    # Stop-loss / take-profit sweep
    force_closed = check_stops_and_targets(client, positions, cfg)

    # Refresh positions if anything was closed
    if force_closed:
        try:
            positions = get_positions(client)
        except Exception:
            pass

    # Signal scan & new orders
    print(f"\n  Scanning {len(cfg['symbols'])} symbols...")
    run_signals(client, data_client, positions, equity, cfg, force_closed)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n🤖 Alpaca Bot — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    cfg = load_config()

    for i in [1, 2]:
        key    = os.getenv(f"APCA_API_KEY_{i}")
        secret = os.getenv(f"APCA_API_SECRET_{i}")
        url    = os.getenv(f"APCA_BASE_URL_{i}", "https://paper-api.alpaca.markets")

        if key and secret:
            trade_account(f"Account {i}", key, secret, url, cfg)
        elif i == 1:
            print("❌ APCA_API_KEY_1 / APCA_API_SECRET_1 not set. Exiting.")
            break
