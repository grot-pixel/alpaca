from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from utils import generate_signals


CONFIG_FILE = "config.json"
EASTERN = ZoneInfo("America/New_York")


# ============================================================
# CONFIGURATION
# ============================================================

def load_config() -> dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    required = {
        "symbols",
        "max_open_positions",
        "max_position_pct",
        "risk_per_trade_pct",
        "max_portfolio_risk",
        "max_correlation_exposure",
        "buy_signal_threshold",
        "sell_signal_threshold",
        "sma_fast",
        "sma_slow",
        "rsi_period",
        "rsi_pullback_max",
        "rsi_recovery_min",
        "rsi_sell_min",
        "adx_period",
        "adx_min",
        "min_di_spread",
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "atr_period",
        "atr_stop_mult",
        "atr_take_profit_mult",
        "minimum_reward_risk",
        "min_atr_pct",
        "max_atr_pct",
        "daily_loss_limit_pct",
        "trade_start_hour",
        "trade_start_minute",
        "trade_end_hour",
        "trade_end_minute",
        "data_lookback_days",
        "data_max_bars",
        "data_feed",
        "force_flat_at_close",
        "flatten_minutes_before_close",
    }

    missing = sorted(required - set(config))
    if missing:
        raise ValueError(
            "config.json is missing required keys: "
            + ", ".join(missing)
        )

    if not config["symbols"]:
        raise ValueError("symbols cannot be empty")

    if config["buy_signal_threshold"] < 1:
        raise ValueError("buy_signal_threshold must be >= 1")

    if config["sell_signal_threshold"] < 1:
        raise ValueError("sell_signal_threshold must be >= 1")

    if not 0 < config["risk_per_trade_pct"] <= 0.02:
        raise ValueError("risk_per_trade_pct must be between 0 and 0.02")

    if not 0 < config["max_portfolio_risk"] <= 1:
        raise ValueError("max_portfolio_risk must be > 0 and <= 1")

    if not 0 < config["max_position_pct"] <= 1:
        raise ValueError("max_position_pct must be > 0 and <= 1")

    if not 0 < config["max_correlation_exposure"] <= 1:
        raise ValueError("max_correlation_exposure must be > 0 and <= 1")

    if config["atr_stop_mult"] <= 0:
        raise ValueError("atr_stop_mult must be > 0")

    if config["atr_take_profit_mult"] <= 0:
        raise ValueError("atr_take_profit_mult must be > 0")

    rr = (
        config["atr_take_profit_mult"]
        / config["atr_stop_mult"]
    )

    if rr < config["minimum_reward_risk"]:
        raise ValueError(
            f"Configured R/R {rr:.2f} is below "
            f"minimum_reward_risk {config['minimum_reward_risk']:.2f}"
        )

    if config["min_atr_pct"] >= config["max_atr_pct"]:
        raise ValueError("min_atr_pct must be below max_atr_pct")

    if config["data_feed"].lower() not in {
        "iex",
        "sip",
        "delayed_sip",
    }:
        raise ValueError(
            "data_feed must be one of: iex, sip, delayed_sip"
        )


# ============================================================
# ACCOUNT
# ============================================================

@dataclass
class AccountContext:
    name: str
    trading_client: TradingClient
    data_client: StockHistoricalDataClient


def get_account_snapshot(
    trading_client: TradingClient,
) -> dict[str, float]:
    account = trading_client.get_account()

    return {
        "equity": float(account.equity),
        "last_equity": float(account.last_equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
    }


# ============================================================
# MARKET CLOCK / CALENDAR
# ============================================================

def get_market_clock(trading_client: TradingClient):
    return trading_client.get_clock()


def trading_window_status(
    config: dict[str, Any],
) -> tuple[bool, datetime]:
    now_utc = datetime.now(timezone.utc)
    eastern = now_utc.astimezone(EASTERN)

    current_minutes = eastern.hour * 60 + eastern.minute

    start_minutes = (
        config["trade_start_hour"] * 60
        + config["trade_start_minute"]
    )

    end_minutes = (
        config["trade_end_hour"] * 60
        + config["trade_end_minute"]
    )

    return (
        start_minutes <= current_minutes <= end_minutes,
        eastern,
    )


def can_open_new_positions(
    trading_client: TradingClient,
    config: dict[str, Any],
) -> tuple[bool, str]:
    clock = get_market_clock(trading_client)

    if not clock.is_open:
        return False, "market is closed"

    in_window, eastern = trading_window_status(config)

    if not in_window:
        return (
            False,
            f"outside strategy window "
            f"({eastern.strftime('%H:%M:%S %Z')})",
        )

    return True, "entry window open"


def should_flatten_for_close(
    trading_client: TradingClient,
    config: dict[str, Any],
) -> bool:
    if not config["force_flat_at_close"]:
        return False

    clock = get_market_clock(trading_client)

    if not clock.is_open:
        return False

    eastern = datetime.now(timezone.utc).astimezone(EASTERN)

    configured_end = (
        eastern.replace(
            hour=config["trade_end_hour"],
            minute=config["trade_end_minute"],
            second=0,
            microsecond=0,
        )
    )

    # We use the actual exchange close supplied by Alpaca.
    next_close = clock.next_close.astimezone(EASTERN)

    flatten_at = (
        next_close
        - timedelta(
            minutes=config["flatten_minutes_before_close"]
        )
    )

    return eastern >= flatten_at


# ============================================================
# ORDERS
# ============================================================

def get_open_orders(
    trading_client: TradingClient,
    symbol: str | None = None,
):
    request_kwargs: dict[str, Any] = {
        "status": QueryOrderStatus.OPEN,
    }

    if symbol:
        request_kwargs["symbols"] = [symbol]

    request = GetOrdersRequest(**request_kwargs)

    return trading_client.get_orders(filter=request)


def has_open_order(
    trading_client: TradingClient,
    symbol: str,
) -> bool:
    return bool(get_open_orders(trading_client, symbol))


def cancel_pending_orders_for_symbol(
    trading_client: TradingClient,
    symbol: str,
) -> None:
    orders = get_open_orders(trading_client, symbol)

    for order in orders:
        try:
            trading_client.cancel_order_by_id(order.id)
            print(
                f"   ↩️ Cancelled {order.side.value} "
                f"order {str(order.id)[:8]} for {symbol}"
            )
        except Exception as exc:
            print(
                f"   ⚠️ Could not cancel "
                f"{symbol} order {order.id}: {exc}"
            )


# ============================================================
# POSITIONS
# ============================================================

def get_positions(
    trading_client: TradingClient,
) -> dict[str, dict[str, float]]:
    positions: dict[str, dict[str, float]] = {}

    for position in trading_client.get_all_positions():
        qty = float(position.qty)

        positions[position.symbol] = {
            "qty": qty,
            "avg_cost": float(position.avg_entry_price),
            "price": float(position.current_price),
            "market_value": abs(float(position.market_value)),
            "unrealized_pl": float(position.unrealized_pl),
            "unrealized_plpc": float(position.unrealized_plpc),
        }

    return positions


# ============================================================
# MARKET DATA
# ============================================================

def resolve_data_feed(config: dict[str, Any]) -> DataFeed:
    value = config["data_feed"].lower()

    mapping = {
        "iex": DataFeed.IEX,
        "sip": DataFeed.SIP,
        "delayed_sip": DataFeed.DELAYED_SIP,
    }

    return mapping[value]


def get_recent_bars(
    data_client: StockHistoricalDataClient,
    symbol: str,
    config: dict[str, Any],
) -> pd.DataFrame:
    end = datetime.now(timezone.utc)

    start = end - timedelta(
        days=config["data_lookback_days"]
    )

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        limit=config["data_max_bars"],
        feed=resolve_data_feed(config),
    )

    bars = data_client.get_stock_bars(request).df

    if bars is None or bars.empty:
        return pd.DataFrame()

    if isinstance(bars.index, pd.MultiIndex):
        try:
            bars = bars.xs(symbol, level=0)
        except KeyError:
            return pd.DataFrame()

    bars = bars.sort_index()

    # Ensure timestamps are timezone-aware.
    if isinstance(bars.index, pd.DatetimeIndex):
        if bars.index.tz is None:
            bars.index = bars.index.tz_localize("UTC")
        else:
            bars.index = bars.index.tz_convert("UTC")

    return bars


# ============================================================
# RISK
# ============================================================

def correlation_group(
    symbol: str,
    config: dict[str, Any],
) -> str:
    for group, symbols in config["correlation_groups"].items():
        if symbol in symbols:
            return group

    return symbol


def group_exposure(
    positions: dict[str, dict[str, float]],
    equity: float,
    group: str,
    config: dict[str, Any],
) -> float:
    if equity <= 0:
        return 0.0

    group_symbols = set(
        config["correlation_groups"].get(group, [])
    )

    exposure = sum(
        position["market_value"]
        for symbol, position in positions.items()
        if symbol in group_symbols
    )

    return exposure / equity


def get_stop_orders(
    trading_client: TradingClient,
) -> dict[str, float]:
    """
    Extract currently-open stop prices.

    Bracket child orders are exposed as regular open orders.
    We use the lowest protective sell stop per symbol.
    """
    result: dict[str, float] = {}

    orders = get_open_orders(trading_client)

    for order in orders:
        try:
            if order.side != OrderSide.SELL:
                continue

            stop_price = getattr(order, "stop_price", None)

            if stop_price is None:
                continue

            price = float(stop_price)

            current = result.get(order.symbol)

            if current is None or price < current:
                result[order.symbol] = price

        except Exception:
            continue

    return result


def calculate_actual_open_risk(
    trading_client: TradingClient,
    positions: dict[str, dict[str, float]],
    equity: float,
) -> float:
    """
    Estimate downside risk using actual broker-side stop orders.

    If a position has no visible stop order, we conservatively
    assign zero measurable risk here and report the missing stop.
    The caller separately prevents new entries when configured
    protection is required.
    """
    if equity <= 0:
        return 0.0

    stops = get_stop_orders(trading_client)

    risk_dollars = 0.0

    for symbol, position in positions.items():
        stop = stops.get(symbol)

        if stop is None:
            print(
                f"  ⚠️ {symbol}: no visible protective stop; "
                "risk cannot be measured from broker orders"
            )
            continue

        current = position["price"]
        qty = position["qty"]

        if stop >= current:
            continue

        risk_dollars += (
            current - stop
        ) * qty

    return risk_dollars / equity


# ============================================================
# CIRCUIT BREAKER
# ============================================================

def daily_return(snapshot: dict[str, float]) -> float:
    if snapshot["last_equity"] <= 0:
        return 0.0

    return (
        snapshot["equity"]
        - snapshot["last_equity"]
    ) / snapshot["last_equity"]


def circuit_breaker_hit(
    snapshot: dict[str, float],
    config: dict[str, Any],
) -> bool:
    value = daily_return(snapshot)

    print(
        f"  Daily P&L: {value * 100:+.2f}%"
    )

    limit = config["daily_loss_limit_pct"]

    if value <= -limit:
        print(
            f"🚨 DAILY LOSS LIMIT HIT "
            f"({value * 100:.2f}% <= {-limit * 100:.2f}%)"
        )
        return True

    return False


def flatten_account(
    trading_client: TradingClient,
) -> None:
    print("🛑 Flattening account...")

    try:
        trading_client.cancel_orders()
    except Exception as exc:
        print(f"⚠️ Could not cancel orders: {exc}")

    try:
        trading_client.close_all_positions(
            cancel_orders=True
        )
        print("✅ Flatten request submitted.")
    except Exception as exc:
        print(f"❌ Flatten failed: {exc}")
        raise


# ============================================================
# POSITION SIZING
# ============================================================

def floor_fractional_qty(
    qty: float,
    decimals: int = 6,
) -> float:
    if qty <= 0:
        return 0.0

    quantum = Decimal("1").scaleb(-decimals)

    value = (
        Decimal(str(qty))
        .quantize(
            quantum,
            rounding=ROUND_DOWN,
        )
    )

    return float(value)


def calculate_position_size(
    equity: float,
    price: float,
    atr_value: float,
    config: dict[str, Any],
) -> float:
    if equity <= 0 or price <= 0 or atr_value <= 0:
        return 0.0

    stop_distance = (
        atr_value
        * config["atr_stop_mult"]
    )

    if stop_distance <= 0:
        return 0.0

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

    return floor_fractional_qty(qty)


# ============================================================
# ORDER SUBMISSION
# ============================================================

def submit_entry(
    trading_client,
    symbol,
    qty,
    price,
    atr_value,
    config,
):
    """
    Submit a fractional/simple market entry.

    Alpaca does not allow fractional quantities inside
    bracket orders, so entry and exits are handled separately.
    """

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
        f"{qty:.6f} {symbol} "
        f"@ ~${price:.2f}"
    )

    print(
        f"     Planned SL ${stop_price:.2f} "
        f"TP ${target_price:.2f} "
        f"R/R {actual_rr:.2f}"
    )

    # IMPORTANT:
    # Fractional shares must use a SIMPLE order.
    request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )

    try:

        order = trading_client.submit_order(
            order_data=request
        )

        print(
            f"  ✅ Entry submitted: "
            f"{order.id}"
        )

        return {
            "order": order,
            "stop_price": stop_price,
            "target_price": target_price,
            "qty": qty,
        }

    except Exception as e:

        print(
            f"  ❌ Entry failed for "
            f"{symbol}: {e}"
        )

        return None

def wait_for_order_fill(
    trading_client,
    order_id,
    timeout_seconds=30,
):
    """
    Wait for an entry order to fill.
    """

    import time

    start = time.time()

    while (
        time.time() - start
        < timeout_seconds
    ):

        try:

            order = (
                trading_client.get_order_by_id(
                    order_id
                )
            )

            status = str(
                order.status
            ).lower()

            if status == "filled":

                print(
                    f"  ✅ Entry filled: "
                    f"{order.filled_qty} shares "
                    f"@ ${order.filled_avg_price}"
                )

                return order

            if status in (
                "canceled",
                "expired",
                "rejected",
            ):

                print(
                    f"  ❌ Entry "
                    f"{status}"
                )

                return None

        except Exception as e:

            print(
                f"  ⚠️ Error checking "
                f"fill: {e}"
            )

        time.sleep(1)

    print(
        "  ⚠️ Entry did not fill "
        "within timeout"
    )

    return None


def exit_position(
    trading_client: TradingClient,
    symbol: str,
    position: dict[str, float],
):
    qty = position["qty"]

    if qty <= 0:
        return None

    print(
        f"  🔴 EXIT {qty:.6f} {symbol}"
    )

    cancel_pending_orders_for_symbol(
        trading_client,
        symbol,
    )

    request = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )

    order = trading_client.submit_order(
        order_data=request
    )

    print(
        f"  ✅ Exit submitted: {order.id}"
    )

    return order


# ============================================================
# SIGNAL DISPLAY
# ============================================================

def print_signal(
    symbol: str,
    stats: dict[str, Any],
    signal: str | None,
) -> None:
    print(
        f"[{symbol}] "
        f"${stats['price']:.2f} | "
        f"ADX {stats['adx']:.1f} | "
        f"DI {stats['plus_di']:.1f}/"
        f"{stats['minus_di']:.1f} | "
        f"RSI {stats['rsi']:.1f} | "
        f"ATR {stats['atr_pct'] * 100:.2f}% | "
        f"MACD {stats['macd_hist']:.4f} | "
        f"BUY {stats['bullish_score']}/"
        f"{stats['buy_threshold']} | "
        f"SELL {stats['bearish_score']}/"
        f"{stats['sell_threshold']} | "
        f"→ {signal or '-'}"
    )


# ============================================================
# ACCOUNT TRADING
# ============================================================

def trade_account(
    account: AccountContext,
    config: dict[str, Any],
) -> int:
    """
    Returns number of errors encountered.
    """
    errors = 0

    trading_client = account.trading_client
    data_client = account.data_client

    print()
    print("=" * 60)
    print(f"🧠 Trading {account.name}")
    print("=" * 60)

    # --------------------------------------------------------
    # ACCOUNT
    # --------------------------------------------------------

    try:
        snapshot = get_account_snapshot(
            trading_client
        )
    except Exception as exc:
        print(
            f"❌ Account snapshot failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    equity = snapshot["equity"]

    print(f"Equity: ${equity:,.2f}")
    print(f"Cash: ${snapshot['cash']:,.2f}")
    print(
        f"Buying Power: "
        f"${snapshot['buying_power']:,.2f}"
    )

    # --------------------------------------------------------
    # DAILY LOSS CIRCUIT BREAKER
    # --------------------------------------------------------

    if circuit_breaker_hit(
        snapshot,
        config,
    ):
        flatten_account(
            trading_client
        )
        return 0

    # --------------------------------------------------------
    # POSITIONS
    # --------------------------------------------------------

    try:
        positions = get_positions(
            trading_client
        )
    except Exception as exc:
        print(
            f"❌ Position retrieval failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    print(
        f"Open positions: {len(positions)}"
    )

    # --------------------------------------------------------
    # EOD FLATTEN
    # --------------------------------------------------------

    try:
        if should_flatten_for_close(
            trading_client,
            config,
        ):
            print(
                "⏰ End-of-day flatten window reached."
            )
            flatten_account(
                trading_client
            )
            return 0
    except Exception as exc:
        print(
            f"❌ EOD check failed: "
            f"{type(exc).__name__}: {exc}"
        )
        errors += 1

    # --------------------------------------------------------
    # ACTUAL OPEN RISK
    # --------------------------------------------------------

    try:
        existing_risk = calculate_actual_open_risk(
            trading_client,
            positions,
            equity,
        )

        print(
            f"Measured open stop risk: "
            f"{existing_risk * 100:.2f}%"
        )
    except Exception as exc:
        print(
            f"❌ Risk calculation failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return errors + 1

    # --------------------------------------------------------
    # ENTRY WINDOW
    # --------------------------------------------------------

    try:
        entry_allowed, reason = can_open_new_positions(
            trading_client,
            config,
        )
    except Exception as exc:
        print(
            f"❌ Market clock failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return errors + 1

    print(
        f"  Entry status: "
        f"{'OPEN' if entry_allowed else 'CLOSED'} "
        f"({reason})"
    )

    # Existing positions can still be exited outside
    # the entry window if the market is open.
    market_clock = trading_client.get_clock()

    if not market_clock.is_open:
        print(
            "⏸ Market closed. Nothing to manage."
        )
        return errors

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    symbols = config["symbols"]

    minimum_bars = max(
        config["sma_slow"] + 20,
        config["macd_slow"]
        + config["macd_signal"]
        + 20,
        config["adx_period"] * 3,
        config["atr_period"] * 3,
        100,
    )

    for symbol in symbols:
        try:
            bars = get_recent_bars(
                data_client,
                symbol,
                config,
            )

            if bars.empty:
                print(
                    f"[{symbol}] "
                    "No market data returned."
                )
                errors += 1
                continue

            if len(bars) < minimum_bars:
                print(
                    f"[{symbol}] "
                    f"Not enough data: "
                    f"{len(bars)}/{minimum_bars}"
                )
                continue

            signal, stats = generate_signals(
                bars,
                config,
                return_stats=True,
            )

            print_signal(
                symbol,
                stats,
                signal,
            )

            current_position = positions.get(
                symbol
            )

            # ------------------------------------------------
            # SELL
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

                positions.pop(symbol, None)
                continue

            # ------------------------------------------------
            # NO BUY
            # ------------------------------------------------

            if signal != "buy":
                continue

            # ------------------------------------------------
            # ENTRY WINDOW
            # ------------------------------------------------

            if not entry_allowed:
                print(
                    f"  ⏸ {symbol}: "
                    "buy signal ignored outside "
                    "entry window"
                )
                continue

            # ------------------------------------------------
            # ALREADY LONG
            # ------------------------------------------------

            if current_position:
                print(
                    f"  ⏸ {symbol}: already holding"
                )
                continue

            # ------------------------------------------------
            # PENDING ORDER
            # ------------------------------------------------

            if has_open_order(
                trading_client,
                symbol,
            ):
                print(
                    f"  ⏸ {symbol}: "
                    "open order already exists"
                )
                continue

            # ------------------------------------------------
            # MAX POSITIONS
            # ------------------------------------------------

            if (
                len(positions)
                >= config["max_open_positions"]
            ):
                print(
                    f"  ⏸ {symbol}: "
                    "max positions reached"
                )
                continue

            # ------------------------------------------------
            # CORRELATION CAP
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

            max_group_exp = (
                config["max_correlation_exposure"]
            )

            if group_exp >= max_group_exp:
                print(
                    f"  ⏸ {symbol}: "
                    f"{group} exposure already "
                    f"{group_exp * 100:.1f}%"
                )
                continue

            # ------------------------------------------------
            # POSITION SIZE
            # ------------------------------------------------

            price = stats["price"]

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

            # ------------------------------------------------
            # CORRELATION POSITION CAP
            # ------------------------------------------------

            position_dollars = qty * price

            new_group_exposure = (
                position_dollars / equity
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

                qty = floor_fractional_qty(
                    min(
                        qty,
                        allowed_dollars / price,
                    )
                )

            if qty <= 0:
                print(
                    f"  ⏸ {symbol}: "
                    "correlation cap"
                )
                continue

            # ------------------------------------------------
            # ACTUAL TRADE RISK
            # ------------------------------------------------

            stop_distance = (
                stats["atr"]
                * config["atr_stop_mult"]
            )

            new_trade_risk = (
                qty
                * stop_distance
                / equity
            )

            max_portfolio_risk = (
                config["max_portfolio_risk"]
            )

            if (
                existing_risk
                + new_trade_risk
                > max_portfolio_risk
            ):
                allowed_risk_dollars = max(
                    0.0,
                    equity
                    * (
                        max_portfolio_risk
                        - existing_risk
                    ),
                )

                qty_by_remaining_risk = (
                    allowed_risk_dollars
                    / stop_distance
                )

                qty = floor_fractional_qty(
                    min(
                        qty,
                        qty_by_remaining_risk,
                    )
                )

                if qty <= 0:
                    print(
                        f"  ⏸ {symbol}: "
                        "portfolio risk cap"
                    )
                    continue

                new_trade_risk = (
                    qty
                    * stop_distance
                    / equity
                )

            # ------------------------------------------------
            # FINAL BUYING POWER CHECK
            # ------------------------------------------------

            estimated_position_value = (
                qty * price
            )

            if (
                estimated_position_value
                > snapshot["buying_power"]
            ):
                qty = floor_fractional_qty(
                    snapshot["buying_power"]
                    / price
                )

            if qty <= 0:
                print(
                    f"  ⏸ {symbol}: "
                    "insufficient buying power"
                )
                continue

            # ------------------------------------------------
            # SUBMIT
            # ------------------------------------------------

            order = submit_entry(
                trading_client,
                symbol,
                qty,
                price,
                stats["atr"],
                config,
            )

            if order:
                positions[symbol] = {
                    "qty": qty,
                    "avg_cost": price,
                    "price": price,
                    "market_value": qty * price,
                    "unrealized_pl": 0.0,
                    "unrealized_plpc": 0.0,
                }

                existing_risk += new_trade_risk

        except Exception as exc:
            errors += 1

            print(
                f"  ❌ {symbol}: "
                f"{type(exc).__name__}: {exc}"
            )

    print()
    if errors:
        print(
            f"⚠️ Finished {account.name} "
            f"with {errors} error(s)."
        )
    else:
        print(
            f"✅ Finished {account.name} "
            "with no errors."
        )

    return errors


# ============================================================
# MAIN
# ============================================================

def build_account(
    index: int,
) -> AccountContext | None:
    key = os.getenv(
        f"APCA_API_KEY_{index}"
    )

    secret = os.getenv(
        f"APCA_API_SECRET_{index}"
    )

    url = os.getenv(
        f"APCA_BASE_URL_{index}",
        "https://paper-api.alpaca.markets",
    )

    if not key or not secret:
        return None

    paper = "paper" in url.lower()

    trading_client = TradingClient(
        api_key=key,
        secret_key=secret,
        paper=paper,
    )

    data_client = StockHistoricalDataClient(
        api_key=key,
        secret_key=secret,
    )

    return AccountContext(
        name=f"Account{index}",
        trading_client=trading_client,
        data_client=data_client,
    )


def main() -> int:
    config = load_config()

    print()
    print("=" * 60)
    print("🚀 ALPACA TRADING BOT V4")
    print("=" * 60)

    print(
        f"Symbols: {', '.join(config['symbols'])}"
    )

    print(
        f"Buy threshold: "
        f"{config['buy_signal_threshold']}/7"
    )

    print(
        f"Sell threshold: "
        f"{config['sell_signal_threshold']}/5"
    )

    print(
        f"Risk/trade: "
        f"{config['risk_per_trade_pct'] * 100:.2f}%"
    )

    print(
        f"Max portfolio risk: "
        f"{config['max_portfolio_risk'] * 100:.2f}%"
    )

    print(
        f"Max position: "
        f"{config['max_position_pct'] * 100:.1f}%"
    )

    print(
        f"Max positions: "
        f"{config['max_open_positions']}"
    )

    print(
        f"Data feed: "
        f"{config['data_feed'].upper()}"
    )

    print("=" * 60)

    total_errors = 0
    account_count = 0

    for index in (1, 2):
        account = build_account(index)

        if account is None:
            print(
                f"ℹ️ Account{index} credentials "
                "not configured."
            )
            continue

        account_count += 1

        paper = (
            "paper"
            in os.getenv(
                f"APCA_BASE_URL_{index}",
                "https://paper-api.alpaca.markets",
            ).lower()
        )

        print()
        print(
            f"Connecting {account.name} "
            f"({'PAPER' if paper else 'LIVE'})..."
        )

        try:
            total_errors += trade_account(
                account,
                config,
            )
        except Exception as exc:
            total_errors += 1
            print(
                f"❌ {account.name} failed: "
                f"{type(exc).__name__}: {exc}"
            )

    if account_count == 0:
        print(
            "❌ No Alpaca accounts configured."
        )
        return 1

    print()
    print("=" * 60)

    if total_errors:
        print(
            f"🚨 BOT RUN COMPLETE WITH "
            f"{total_errors} ERROR(S)"
        )
        print("=" * 60)
        return 1

    print("🏁 BOT RUN COMPLETE — NO ERRORS")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
