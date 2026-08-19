import os
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderClass,
    QueryOrderStatus,
)

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

from utils import generate_signals


CONFIG_FILE = "config.json"


# ============================================================
# CONFIG
# ============================================================

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


# ============================================================
# ACCOUNT
# ============================================================

def get_account_snapshot(trading_client):
    account = trading_client.get_account()

    return {
        "equity": float(account.equity),
        "last_equity": float(account.last_equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
    }


# ============================================================
# ORDERS
# ============================================================

def get_open_orders(trading_client, symbol=None):
    try:
        if symbol:
            request = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[symbol],
            )
        else:
            request = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
            )

        return trading_client.get_orders(
            filter=request
        )

    except Exception as e:
        print(
            f"⚠️ Could not retrieve open orders: {e}"
        )
        return []


def has_open_order(trading_client, symbol):
    return len(
        get_open_orders(
            trading_client,
            symbol,
        )
    ) > 0


def cancel_pending_orders_for_symbol(
    trading_client,
    symbol,
):
    orders = get_open_orders(
        trading_client,
        symbol,
    )

    for order in orders:
        try:
            trading_client.cancel_order_by_id(
                order.id
            )

            print(
                f"   ↩️ Cancelled "
                f"{order.side} order "
                f"{str(order.id)[:8]} "
                f"for {symbol}"
            )

        except Exception as e:
            print(
                f"   ⚠️ Could not cancel "
                f"{symbol} order: {e}"
            )


# ============================================================
# POSITIONS
# ============================================================

def get_positions(trading_client):
    positions = {}

    for p in trading_client.get_all_positions():

        qty = float(p.qty)

        positions[p.symbol] = {
            "qty": qty,
            "avg_cost": float(p.avg_entry_price),
            "price": float(p.current_price),
            "market_value": abs(
                float(p.market_value)
            ),
            "unrealized_pl": float(
                p.unrealized_pl
            ),
            "unrealized_plpc": float(
                p.unrealized_plpc
            ),
        }

    return positions


# ============================================================
# MARKET DATA
# ============================================================

def get_recent_bars(
    data_client,
    symbol,
    config,
):
    end = datetime.now(timezone.utc)

    start = end - timedelta(
        days=config.get(
            "data_lookback_days",
            10,
        )
    )

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=config.get(
            "data_max_bars",
            3000,
        ),
        feed=DataFeed.IEX,
    )

    bars = data_client.get_stock_bars(
        request
    ).df

    if bars is None or bars.empty:
        return pd.DataFrame()

    # Alpaca may return a MultiIndex:
    #
    # symbol / timestamp
    #
    if isinstance(
        bars.index,
        pd.MultiIndex,
    ):
        try:
            bars = bars.xs(
                symbol,
                level=0,
            )
        except KeyError:
            return pd.DataFrame()

    bars = bars.sort_index()

    return bars


# ============================================================
# MARKET TIME
# ============================================================

def is_trade_time(config):
    """
    Determine whether the current time is inside
    the configured trading window.

    Uses America/New_York so DST is handled automatically.
    """

    now = datetime.now(timezone.utc)

    eastern = now.astimezone(
        ZoneInfo("America/New_York")
    )

    minutes = (
        eastern.hour * 60
        + eastern.minute
    )

    start = (
        config.get("trade_start_hour", 9) * 60
        + config.get("trade_start_minute", 45)
    )

    end = (
        config.get("trade_end_hour", 15) * 60
        + config.get("trade_end_minute", 30)
    )

    in_window = (
        start <= minutes <= end
    )

    print(
        f"  🕐 Market time: "
        f"{eastern.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
        f"Trading window: "
        f"{config.get('trade_start_hour', 9):02d}:"
        f"{config.get('trade_start_minute', 45):02d}–"
        f"{config.get('trade_end_hour', 15):02d}:"
        f"{config.get('trade_end_minute', 30):02d} | "
        f"{'OPEN' if in_window else 'CLOSED'}"
    )

    return in_window


# ============================================================
# PORTFOLIO RISK
# ============================================================

def correlation_group(
    symbol,
    config,
):
    groups = config.get(
        "correlation_groups",
        {},
    )

    for group, symbols in groups.items():
        if symbol in symbols:
            return group

    return symbol


def group_exposure(
    positions,
    equity,
    group,
    config,
):
    if equity <= 0:
        return 0.0

    symbols = config.get(
        "correlation_groups",
        {},
    ).get(
        group,
        [],
    )

    exposure = sum(
        position["market_value"]
        for symbol, position
        in positions.items()
        if symbol in symbols
    )

    return exposure / equity


def calculate_existing_risk(
    positions,
    equity,
    config,
):
    """
    Conservative estimate of existing portfolio
    stop risk.

    We use configured risk_per_trade_pct for each
    existing position because the broker does not
    expose our original strategy stop directly.
    """

    if equity <= 0:
        return 0.0

    risk_per_trade = config.get(
        "risk_per_trade_pct",
        0.005,
    )

    return (
        len(positions)
        * risk_per_trade
    )


# ============================================================
# DAILY CIRCUIT BREAKER
# ============================================================

def circuit_breaker_hit(
    snapshot,
    config,
):
    equity = snapshot["equity"]
    last_equity = snapshot["last_equity"]

    if last_equity <= 0:
        return False

    daily_return = (
        equity - last_equity
    ) / last_equity

    print(
        f"  Daily P&L: "
        f"{daily_return * 100:+.2f}%"
    )

    daily_limit = config.get(
        "daily_loss_limit_pct",
        0.02,
    )

    if daily_return <= -daily_limit:

        print(
            "🚨 DAILY LOSS LIMIT HIT"
        )

        return True

    return False


def flatten_account(
    trading_client,
):
    print(
        "🛑 Flattening account..."
    )

    try:
        trading_client.cancel_orders()
    except Exception as e:
        print(
            f"⚠️ Could not cancel orders: {e}"
        )

    try:
        trading_client.close_all_positions(
            cancel_orders=True
        )
    except Exception as e:
        print(
            f"⚠️ Flatten failed: {e}"
        )


# ============================================================
# POSITION SIZING
# ============================================================

def calculate_position_size(
    equity,
    price,
    atr_value,
    config,
):
    """
    Position size based on:

        risk dollars
        --------------------
        distance to stop

    Then capped by max_position_pct.
    """

    if (
        equity <= 0
        or price <= 0
        or atr_value <= 0
    ):
        return 0.0

    stop_distance = (
        atr_value
        * config.get(
            "atr_stop_mult",
            2.0,
        )
    )

    if stop_distance <= 0:
        return 0.0

    risk_dollars = (
        equity
        * config.get(
            "risk_per_trade_pct",
            0.005,
        )
    )

    qty_by_risk = (
        risk_dollars
        / stop_distance
    )

    max_position_dollars = (
        equity
        * config.get(
            "max_position_pct",
            0.15,
        )
    )

    qty_by_position = (
        max_position_dollars
        / price
    )

    qty = min(
        qty_by_risk,
        qty_by_position,
    )

    return round(
        max(qty, 0.0),
        6,
    )


# ============================================================
# ENTRY
# ============================================================

def submit_entry(
    trading_client,
    symbol,
    qty,
    price,
    atr_value,
    config,
):
    stop_distance = (
        atr_value
        * config.get(
            "atr_stop_mult",
            2.0,
        )
    )

    target_distance = (
        atr_value
        * config.get(
            "atr_take_profit_mult",
            4.0,
        )
    )

    if stop_distance <= 0:
        print(
            f"  ⏸ {symbol}: "
            "invalid stop distance"
        )
        return None

    stop_price = round(
        price - stop_distance,
        2,
    )

    target_price = round(
        price + target_distance,
        2,
    )

    actual_rr = (
        target_distance
        / stop_distance
    )

    minimum_rr = config.get(
        "minimum_reward_risk",
        1.75,
    )

    if actual_rr < minimum_rr:

        print(
            f"  ⏸ {symbol}: "
            f"R/R too low "
            f"({actual_rr:.2f})"
        )

        return None

    print(
        f"  🟢 BUY "
        f"{qty:.4f} {symbol} "
        f"@ ${price:.2f}"
    )

    print(
        f"     SL: ${stop_price:.2f} "
        f"TP: ${target_price:.2f} "
        f"R/R: {actual_rr:.2f}"
    )

    request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(
            limit_price=target_price
        ),
        stop_loss=StopLossRequest(
            stop_price=stop_price
        ),
    )

    try:

        order = trading_client.submit_order(
            order_data=request
        )

        print(
            f"  ✅ Order submitted: "
            f"{order.id}"
        )

        return order

    except Exception as e:

        print(
            f"  ❌ Order failed for "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# EXIT
# ============================================================

def exit_position(
    trading_client,
    symbol,
    position,
):
    print(
        f"  🔴 EXIT "
        f"{position['qty']:.4f} "
        f"{symbol}"
    )

    # Cancel bracket/other pending orders first.
    cancel_pending_orders_for_symbol(
        trading_client,
        symbol,
    )

    qty = position["qty"]

    if qty <= 0:
        return None

    request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )

    try:

        order = trading_client.submit_order(
            order_data=request
        )

        print(
            f"  ✅ Exit submitted: "
            f"{order.id}"
        )

        return order

    except Exception as e:

        print(
            f"  ❌ Exit failed for "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# ACCOUNT TRADING
# ============================================================

def trade_account(
    account_info,
    config,
):
    trading_client = (
        account_info["trading_client"]
    )

    data_client = (
        account_info["data_client"]
    )

    print()
    print("=" * 60)

    print(
        f"🧠 Trading "
        f"{account_info['name']}"
    )

    # ========================================================
    # ACCOUNT SNAPSHOT
    # ========================================================

    try:

        snapshot = get_account_snapshot(
            trading_client
        )

        equity = snapshot["equity"]

        print(
            f"Equity: "
            f"${equity:,.2f}"
        )

        print(
            f"Cash: "
            f"${snapshot['cash']:,.2f}"
        )

        print(
            f"Buying Power: "
            f"${snapshot['buying_power']:,.2f}"
        )

    except Exception as e:

        print(
            f"❌ Account error: {e}"
        )

        return

    # ========================================================
    # DAILY LOSS CIRCUIT BREAKER
    # ========================================================

    if circuit_breaker_hit(
        snapshot,
        config,
    ):

        flatten_account(
            trading_client
        )

        return

    # ========================================================
    # TRADING WINDOW
    # ========================================================

    if not is_trade_time(config):

        print(
            "⏸ Outside strategy "
            "trading window."
        )

        return

    # ========================================================
    # EXISTING POSITIONS
    # ========================================================

    try:

        positions = get_positions(
            trading_client
        )

    except Exception as e:

        print(
            f"❌ Position error: {e}"
        )

        return

    print(
        f"Open positions: "
        f"{len(positions)}"
    )

    # ========================================================
    # EXISTING PORTFOLIO RISK
    # ========================================================

    existing_risk = (
        calculate_existing_risk(
            positions,
            equity,
            config,
        )
    )

    print(
        f"Estimated open risk: "
        f"{existing_risk * 100:.2f}%"
    )

    # ========================================================
    # SCAN SYMBOLS
    # ========================================================

    symbols = config.get(
        "symbols",
        [],
    )

    for symbol in symbols:

        try:

            # ------------------------------------------------
            # DATA
            # ------------------------------------------------

            bars = get_recent_bars(
                data_client,
                symbol,
                config,
            )

            minimum_bars = max(
                config.get(
                    "sma_slow",
                    21,
                ) + 20,

                config.get(
                    "macd_slow",
                    26,
                )
                + config.get(
                    "macd_signal",
                    9,
                )
                + 20,

                config.get(
                    "adx_period",
                    14,
                ) * 3,

                100,
            )

            if (
                bars.empty
                or len(bars)
                < minimum_bars
            ):

                print(
                    f"[{symbol}] "
                    f"Not enough data: "
                    f"{len(bars)}"
                )

                continue

            # ------------------------------------------------
            # SIGNAL
            # ------------------------------------------------

            signal, stats = (
                generate_signals(
                    bars,
                    config,
                    return_stats=True,
                )
            )

            price = float(
                bars["close"].iloc[-1]
            )

            print(
                f"[{symbol}] "
                f"${price:.2f} | "
                f"ADX {stats['adx']:.1f} | "
                f"DI "
                f"{stats['plus_di']:.1f}/"
                f"{stats['minus_di']:.1f} | "
                f"RSI {stats['rsi']:.1f} | "
                f"ATR "
                f"{stats['atr_pct'] * 100:.2f}% | "
                f"MACD "
                f"{stats['macd_hist']:.4f} | "
                f"Score "
                f"{stats['bullish_score']} | "
                f"→ {signal or '-'}"
            )

            # ------------------------------------------------
            # CURRENT POSITION
            # ------------------------------------------------

            current_position = (
                positions.get(symbol)
            )

            # =================================================
            # EXIT
            # =================================================

            if (
                signal == "sell"
                and current_position
            ):

                exit_position(
                    trading_client,
                    symbol,
                    current_position,
                )

                continue

            # =================================================
            # NO ENTRY SIGNAL
            # =================================================

            if signal != "buy":
                continue

            # =================================================
            # ALREADY LONG
            # =================================================

            if current_position:

                print(
                    f"  ⏸ {symbol}: "
                    "already holding"
                )

                continue

            # =================================================
            # PENDING ORDER
            # =================================================

            if has_open_order(
                trading_client,
                symbol,
            ):

                print(
                    f"  ⏸ {symbol}: "
                    "open order already exists"
                )

                continue

            # =================================================
            # MAX POSITIONS
            # =================================================

            max_positions = config.get(
                "max_open_positions",
                4,
            )

            if (
                len(positions)
                >= max_positions
            ):

                print(
                    f"  ⏸ {symbol}: "
                    "max positions reached"
                )

                continue

            # =================================================
            # CORRELATION GROUP
            # =================================================

            group = correlation_group(
                symbol,
                config,
            )

            group_exp = group_exposure(
                positions,
                equity,
                group,
                config,
            )

            max_group_exp = config.get(
                "max_correlation_exposure",
                0.35,
            )

            if (
                group_exp
                >= max_group_exp
            ):

                print(
                    f"  ⏸ {symbol}: "
                    f"{group} exposure "
                    f"{group_exp * 100:.1f}%"
                )

                continue

            # =================================================
            # POSITION SIZE
            # =================================================

            qty = calculate_position_size(
                equity=equity,
                price=price,
                atr_value=stats["atr"],
                config=config,
            )

            if qty <= 0:

                print(
                    f"  ⏸ {symbol}: "
                    "position size = 0"
                )

                continue

            # =================================================
            # CORRELATION EXPOSURE CAP
            # =================================================

            position_dollars = (
                qty * price
            )

            new_group_exposure = (
                position_dollars
                / equity
            )

            if (
                group_exp
                + new_group_exposure
                > max_group_exp
            ):

                allowed_dollars = max(
                    0.0,
                    equity
                    * (
                        max_group_exp
                        - group_exp
                    ),
                )

                qty = min(
                    qty,
                    allowed_dollars / price,
                )

                qty = round(
                    max(qty, 0.0),
                    6,
                )

            if qty <= 0:

                print(
                    f"  ⏸ {symbol}: "
                    "correlation cap"
                )

                continue

            # =================================================
            # PORTFOLIO RISK CAP
            # =================================================

            stop_distance = (
                stats["atr"]
                * config.get(
                    "atr_stop_mult",
                    2.0,
                )
            )

            new_trade_risk = (
                qty
                * stop_distance
                / equity
            )

            max_portfolio_risk = (
                config.get(
                    "max_portfolio_risk",
                    0.02,
                )
            )

            if (
                existing_risk
                + new_trade_risk
                > max_portfolio_risk
            ):

                print(
                    f"  ⏸ {symbol}: "
                    f"portfolio risk cap "
                    f"("
                    f"{(existing_risk + new_trade_risk) * 100:.2f}%"
                    f" > "
                    f"{max_portfolio_risk * 100:.2f}%"
                    f")"
                )

                continue

            # =================================================
            # SUBMIT ENTRY
            # =================================================

            order = submit_entry(
                trading_client,
                symbol,
                qty,
                price,
                stats["atr"],
                config,
            )

            if order:

                # Keep our local position count
                # conservative during this scan.
                positions[symbol] = {
                    "qty": qty,
                    "avg_cost": price,
                    "price": price,
                    "market_value": qty * price,
                    "unrealized_pl": 0.0,
                    "unrealized_plpc": 0.0,
                }

                existing_risk += (
                    new_trade_risk
                )

        except Exception as e:

            print(
                f"  ❌ {symbol}: "
                f"{type(e).__name__}: {e}"
            )

    print()
    print(
        f"✅ Finished "
        f"{account_info['name']}"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    config = load_config()

    print()
    print("=" * 60)
    print("🚀 ALPACA TRADING BOT")
    print("=" * 60)

    print(
        f"Symbols: "
        f"{', '.join(config.get('symbols', []))}"
    )

    print(
        f"Risk/trade: "
        f"{config.get('risk_per_trade_pct', 0.005) * 100:.2f}%"
    )

    print(
        f"Max portfolio risk: "
        f"{config.get('max_portfolio_risk', 0.02) * 100:.2f}%"
    )

    print(
        f"Max position: "
        f"{config.get('max_position_pct', 0.15) * 100:.1f}%"
    )

    print(
        f"Max positions: "
        f"{config.get('max_open_positions', 4)}"
    )

    print("=" * 60)

    # ========================================================
    # MULTIPLE ACCOUNTS
    # ========================================================

    for i in [1, 2]:

        key = os.getenv(
            f"APCA_API_KEY_{i}"
        )

        secret = os.getenv(
            f"APCA_API_SECRET_{i}"
        )

        url = os.getenv(
            f"APCA_BASE_URL_{i}",
            "https://paper-api.alpaca.markets",
        )

        if not key or not secret:

            print(
                f"ℹ️ Account{i} "
                "credentials not configured."
            )

            continue

        paper = (
            "paper"
            in url.lower()
        )

        print()
        print(
            f"Connecting Account{i} "
            f"({'PAPER' if paper else 'LIVE'})..."
        )

        try:

            trading_client = TradingClient(
                api_key=key,
                secret_key=secret,
                paper=paper,
            )

            data_client = (
                StockHistoricalDataClient(
                    api_key=key,
                    secret_key=secret,
                )
            )

            trade_account(
                {
                    "name": f"Account{i}",
                    "trading_client": trading_client,
                    "data_client": data_client,
                },
                config,
            )

        except Exception as e:

            print(
                f"❌ Account{i} failed: "
                f"{type(e).__name__}: {e}"
            )

    print()
    print("=" * 60)
    print("🏁 BOT RUN COMPLETE")
    print("=" * 60)
