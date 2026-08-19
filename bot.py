import os
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
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
        print(f"⚠️ Could not retrieve open orders: {e}")
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
            "market_value": abs(float(p.market_value)),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
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

    # Enough history for SMA / MACD / ATR / ADX
    start = end - timedelta(
        days=config["data_lookback_days"]
    )

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=config["data_max_bars"],
        feed=DataFeed.IEX,
    )

    bars = data_client.get_stock_bars(
        request
    ).df

    if bars is None or bars.empty:
        return pd.DataFrame()

    if isinstance(
        bars.index,
        pd.MultiIndex,
    ):
        bars = bars.xs(
            symbol,
            level=0,
        )

    bars = bars.sort_index()

    return bars


# ============================================================
# TIME FILTER
# ============================================================

def is_trade_time(config):

    now = datetime.now(
        timezone.utc
    )

    eastern = now.astimezone(
        __import__("zoneinfo")
        .zoneinfo.ZoneInfo(
            "America/New_York"
        )
    )

    minutes = (
        eastern.hour * 60
        + eastern.minute
    )

    start = (
        config["trade_start_hour"] * 60
        + config["trade_start_minute"]
    )

    end = (
        config["trade_end_hour"] * 60
        + config["trade_end_minute"]
    )

    return start <= minutes <= end


# ============================================================
# PORTFOLIO RISK
# ============================================================

def calculate_open_risk(
    positions,
    equity,
    config,
):

    total_risk = 0.0

    for symbol, position in positions.items():

        atr = position.get(
            "atr",
            0
        )

        if atr <= 0:
            continue

        stop_distance = (
            atr
            * config["atr_stop_mult"]
        )

        total_risk += (
            position["qty"]
            * stop_distance
        )

    if equity <= 0:
        return 0.0

    return total_risk / equity


def correlation_group(
    symbol,
    config,
):

    for group, symbols in config[
        "correlation_groups"
    ].items():

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

    symbols = config[
        "correlation_groups"
    ].get(group, [])

    exposure = sum(
        position["market_value"]
        for symbol, position
        in positions.items()
        if symbol in symbols
    )

    return exposure / equity


# ============================================================
# DAILY CIRCUIT BREAKER
# ============================================================

def circuit_breaker_hit(
    snapshot,
    config,
):

    equity = snapshot["equity"]
    last_equity = snapshot["last_equity"]

    if not last_equity:
        return False

    daily_return = (
        equity - last_equity
    ) / last_equity

    print(
        f"  Daily P&L: "
        f"{daily_return * 100:+.2f}%"
    )

    if daily_return <= -config[
        "daily_loss_limit_pct"
    ]:

        print(
            "🚨 DAILY LOSS LIMIT HIT"
        )

        return True

    return False


def flatten_account(
    trading_client
):

    print(
        "🛑 Flattening account..."
    )

    try:
        trading_client.cancel_orders()
    except Exception:
        pass

    try:
        trading_client.close_all_positions(
            cancel_orders=True
        )
    except Exception as e:

        print(
            f"⚠️ Flatten failed: {e}"
        )


# ============================================================
# POSITION SIZE
# ============================================================

def calculate_position_size(
    equity,
    price,
    atr_value,
    config,
):

    if (
        equity <= 0
        or price <= 0
        or atr_value <= 0
    ):
        return 0.0

    stop_distance = (
        atr_value
        * config["atr_stop_mult"]
    )

    risk_dollars = (
        equity
        * config["risk_per_trade_pct"]
    )

    qty_by_risk = (
        risk_dollars
        / stop_distance
    )

    max_position_dollars = (
        equity
        * config["max_position_pct"]
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
        max(qty, 0),
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
        * config["atr_stop_mult"]
    )

    target_distance = (
        atr_value
        * config["atr_take_profit_mult"]
    )

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

    if actual_rr < config[
        "minimum_reward_risk"
    ]:

        print(
            f"  ⏸ {symbol}: "
            f"R/R too low "
            f"({actual_rr:.2f})"
        )

        return None

    print(
        f"  🟢 BUY {qty:.4f} {symbol} "
        f"@ ${price:.2f}"
    )

    print(
        f"     SL: ${stop_price:.2f} "
        f"TP: ${target_price:.2f} "
        f"R/R: {actual_rr:.2f}"
    )

    # Use market entry rather than a limit
    # 0.1% ABOVE the market.
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

    return trading_client.submit_order(
        order_data=request
    )


# ============================================================
# EXIT
# ============================================================

def exit_position(
    trading_client,
    symbol,
    position,
):

    cancel_pending_orders_for_symbol(
        trading_client,
        symbol,
    )

    qty = position["qty"]

    if qty <= 0:
        return

    print(
        f"  🔴 EXIT "
        f"{qty:.4f} {symbol}"
    )

    request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )

    trading_client.submit_order(
        order_data=request
    )


# ============================================================
# MAIN ACCOUNT LOOP
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
    print(
        "=" * 60
    )

    print(
        f"🧠 Trading {account_info['name']}"
    )

    # --------------------------------------------------------
    # Account
    # --------------------------------------------------------

    try:

        snapshot = get_account_snapshot(
            trading_client
        )

        equity = snapshot[
            "equity"
        ]

        print(
            f"Equity: "
            f"${equity:,.2f}"
        )

    except Exception as e:

        print(
            f"❌ Account error: {e}"
        )

        return

    # --------------------------------------------------------
    # Daily loss circuit breaker
    # --------------------------------------------------------

    if circuit_breaker_hit(
        snapshot,
        config,
    ):

        flatten_account(
            trading_client
        )

        return

    # --------------------------------------------------------
    # Time filter
    # --------------------------------------------------------

    if not is_trade_time(config):

        print(
            "⏸ Outside strategy "
            "trading window."
        )

        return

    # --------------------------------------------------------
    # Existing positions
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Scan symbols
    # --------------------------------------------------------

    for symbol in config[
        "symbols"
    ]:

        try:

            bars = get_recent_bars(
                data_client,
                symbol,
                config,
            )

            minimum_bars = max(
                config["sma_slow"] + 20,
                config["macd_slow"]
                + config["macd_signal"]
                + 20,
                config["adx_period"] * 3,
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
                f"ATR {stats['atr_pct'] * 100:.2f}% | "
                f"MACD {stats['macd_hist']:.4f} | "
                f"Score "
                f"{stats['bullish_score']} | "
                f"→ {signal or '-'}"
            )

            current_position = positions.get(
                symbol
            )

            # ------------------------------------------------
            # EXIT
            # ------------------------------------------------

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

            # ------------------------------------------------
            # ENTRY
            # ------------------------------------------------

            if signal != "buy":
                continue

            if current_position:
                continue

            if has_open_order(
                trading_client,
                symbol,
            ):
                continue

            # ------------------------------------------------
            # Max positions
            # ------------------------------------------------

            if len(positions) >= config[
                "max_open_positions"
            ]:

                print(
                    f"  ⏸ {symbol}: "
                    "max positions reached"
                )

                continue

            # ------------------------------------------------
            # Correlation exposure
            # ------------------------------------------------

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

            if group_exp >= config[
                "max_correlation_exposure"
            ]:

                print(
                    f"  ⏸ {symbol}: "
                    f"{group} exposure "
                    f"{group_exp * 100:.1f}%"
                )

                continue

            # ------------------------------------------------
            # Position sizing
            # ------------------------------------------------

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

            position_dollars = (
                qty * price
            )

            # ------------------------------------------------
            # Correlation cap
            # ------------------------------------------------

            if (
                group_exp
                + position_dollars / equity
                > config[
                    "max_correlation_exposure"
                ]
            ):

                allowed_dollars = max(
                    0,
                    equity
                    * (
                        config[
                            "max_correlation_exposure"
                        ]
                        - group_exp
                    ),
                )

                qty = min(
                    qty,
                    allowed_dollars / price,
                )

                qty = round(
                    max(qty, 0),
                    6,
                )

            if qty <= 0:
                continue

            # ------------------------------------------------
            # Portfolio risk cap
            # ------------------------------------------------

            stop_distance = (
                stats["atr"]
                * config[
                    "atr_stop_mult"
                ]
            )

            new_trade_risk = (
                qty
                * stop_distance
                / equity
            )

            existing_risk = 0.0

            for p_symbol, p in positions.items():

                # Conservative estimate:
                # use configured per-trade risk.
                existing_risk += config[
                    "risk_per_trade_pct"
                ]

            if (
                existing_risk
                + new_trade_risk
                > config[
                    "max_portfolio_risk"
                ]
            ):

                print(
                    f"  ⏸ {symbol}: "
                    "portfolio risk cap"
                )

                continue

            # ------------------------------------------------
            # Submit
            # ------------------------------------------------

            submit_entry(
                trading_client,
                symbol,
                qty,
                price,
                stats["atr"],
                config,
            )

        except Exception as e:

            print(
                f"  ❌ {symbol}: {e}"
            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    config = load_config()

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
            continue

        paper = (
            "paper"
            in url.lower()
        )

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
