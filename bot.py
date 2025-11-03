"""
Balanced Swing Alpaca Multi-Account Bot (single-cycle)

- Uses hourly bars (TimeFrame.Hour) for balanced swing trading.
- Signals: SMA crossover (fast/slow) + RSI confirmation + trend filter (ATR-based).
- Position sizing: ATR-based risk-per-trade with caps from config.
- Execution: parent market order + bracket children (stop_loss + take_profit).
- Logging: trade_log.csv
- Sends email alerts on orders if SMTP env vars are set and email_alerts.send_alert is available.

Run once and exit (safe for GitHub Actions schedule).
"""
import os
import json
import csv
import math
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame, APIError

# Local helper for notifications
try:
    from email_alerts import send_alert
except Exception:
    # fallback if module missing
    def send_alert(subject, body):
        print("[Alert disabled] ", subject)
        return

# ---------- CONFIG ----------
CONFIG_FILE = "config.json"
with open(CONFIG_FILE) as f:
    cfg = json.load(f)

TRADE_LOG = "trade_log.csv"

# ---------- INDICATORS ----------
def sma(series: pd.Series, window: int):
    return series.rolling(window).mean()

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.rolling(period).mean()
    ma_down = down.rolling(period).mean()
    rs = ma_up / (ma_down.replace(0, 1e-9))
    return 100 - (100 / (1 + rs))

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

# ---------- UTIL ----------

def log_trade(record: dict):
    header = [
        "timestamp_utc", "account", "symbol", "side", "qty",
        "intended_value", "avg_fill_price", "stop_price", "take_profit_price",
        "notes"
    ]
    exists = os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if not exists:
            writer.writeheader()
        writer.writerow(record)

def is_regular_hours():
    # 09:30-16:00 Eastern -> UTC 14:30-21:00; approximate check uses hour bounds
    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and 14 <= now.hour < 21

def compute_qty_atr(equity, price, stop_price, cfg, cash_available, current_position_value):
    """
    Position sizing:
      - risk_per_trade = equity * risk_per_trade_pct
      - per_share_risk = price - stop_price
      - qty = floor(risk_per_trade / per_share_risk)
      - apply hard caps: max_position_pct, max_trade_pct, affordability
    """
    if price <= 0 or stop_price >= price:
        return 0

    risk_per_trade = equity * float(cfg.get("risk_per_trade_pct", 0.01))
    per_share_risk = price - stop_price
    if per_share_risk <= 0:
        return 0

    raw_qty = int(risk_per_trade // per_share_risk)
    if raw_qty <= 0:
        return 0

    max_position_value = equity * float(cfg.get("max_position_pct", 0.25))
    max_trade_value = equity * float(cfg.get("max_trade_pct", 0.20))

    max_allowed_qty_by_position = int(max(0, (max_position_value - current_position_value) // price))
    max_allowed_qty_by_trade = int(max(0, max_trade_value // price))
    max_allowed_qty_by_cash = int(max(0, cash_available // price))

    qty = min(raw_qty, max_allowed_qty_by_position, max_allowed_qty_by_trade, max_allowed_qty_by_cash)
    return max(0, qty)

# ---------- SIGNALS ----------
def generate_signal_from_bars(bars: pd.DataFrame, cfg: dict):
    """
    Returns (signal: 'buy'|'sell'|None, reason: str)
    Uses SMA cross (fast/slow), RSI confirmation, and trend filter via slope of slow SMA.
    """
    if bars is None or bars.empty:
        return None, "no bars"

    bars = bars.copy()
    bars["sma_fast"] = sma(bars["close"], cfg["sma_fast"])
    bars["sma_slow"] = sma(bars["close"], cfg["sma_slow"])
    bars["rsi"] = rsi(bars["close"], cfg["rsi_period"])

    if len(bars) < max(cfg["sma_slow"], cfg["rsi_period"]) + 2:
        return None, "insufficient data"

    last = bars.iloc[-1]
    prev = bars.iloc[-2]

    # Trend filter: slope of sma_slow over trend_window
    tw = cfg.get("trend_window", 50)
    if len(bars) >= tw:
        y = bars["sma_slow"].dropna().values[-tw:]
        if len(y) == tw:
            slope = np_polyfit_slope(y)
        else:
            slope = 0.0
    else:
        slope = 0.0

    # Determine signals
    # Buy: fast crosses above slow (prev <=), RSI below oversold, slope positive
    if (last["sma_fast"] > last["sma_slow"] and prev["sma_fast"] <= prev["sma_slow"]
        and last["rsi"] < cfg["rsi_oversold"] and slope > 0):
        return "buy", f"SMA cross up; RSI {last['rsi']:.1f}; slope {slope:.6f}"

    # Sell: fast crosses below slow (prev >=) OR RSI above overbought
    if (last["sma_fast"] < last["sma_slow"] and prev["sma_fast"] >= prev["sma_slow"]) or last["rsi"] > cfg["rsi_overbought"]:
        return "sell", f"SMA cross down or RSI high ({last['rsi']:.1f})"

    return None, "no signal"

def np_polyfit_slope(arr):
    """Return slope of linear fit against index"""
    # small helper to avoid importing numpy at top-level if not needed heavily
    try:
        import numpy as _np
        x = _np.arange(len(arr))
        slope = _np.polyfit(x, arr, 1)[0]
        return float(slope)
    except Exception:
        return 0.0

# ---------- MAIN TRADE LOOP (single-cycle) ----------
def trade_account(account_info):
    name = account_info["name"]
    api: REST = account_info["api"]
    symbols = account_info["symbols"]

    print(f"\n=== Trading for {name} === {datetime.now(timezone.utc).isoformat()}")
    try:
        account = api.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        print(f"Equity: ${equity:.2f}  Cash: ${cash:.2f}")
    except Exception as e:
        print(f"[{name}] Could not fetch account: {e}")
        return

    # fetch existing positions
    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception:
        positions = {}

    regular_hours = is_regular_hours()

    for sym in symbols:
        try:
            # fetch recent hourly bars
            lookback = max(cfg.get("sma_slow", 13), cfg.get("trend_window", 50), cfg.get("rsi_period", 9)) + 10
            try:
                bars = api.get_bars(sym, TimeFrame.Hour, limit=lookback).df
            except Exception as e:
                print(f"[{sym}] Error fetching bars: {e}")
                bars = None

            signal, reason = generate_signal_from_bars(bars, cfg)
            qty_held = int(positions.get(sym, 0))
            last_price = None
            if bars is not None and not bars.empty:
                last_price = float(bars["close"].iloc[-1])
            else:
                # fallback to last trade
                try:
                    last_trade = api.get_latest_trade(sym)
                    last_price = float(last_trade.price)
                except Exception:
                    print(f"[{sym}] No price data available, skipping")
                    continue

            # If we have a position, check stop/take based on entry and config
            if qty_held > 0:
                try:
                    pos = api.get_position(sym)
                    avg_entry = float(pos.avg_entry_price)
                    pnl_pct = (last_price - avg_entry) / avg_entry
                    # stop or take
                    if pnl_pct <= -cfg["stop_loss_pct"]:
                        print(f"[{sym}] Stop-loss ({pnl_pct:.2%}) -> SELL {qty_held}")
                        order = api.submit_order(sym, qty_held, "sell", "market", "day")
                        log_and_alert(name, sym, "sell", qty_held, last_price, None, None, "stop_loss")
                        positions[sym] = 0
                        continue
                    if pnl_pct >= cfg["take_profit_pct"]:
                        print(f"[{sym}] Take-profit ({pnl_pct:.2%}) -> SELL {qty_held}")
                        order = api.submit_order(sym, qty_held, "sell", "market", "day")
                        log_and_alert(name, sym, "sell", qty_held, last_price, None, None, "take_profit")
                        positions[sym] = 0
                        continue
                except APIError as e:
                    print(f"[{sym}] Error checking position: {e}")
                except Exception:
                    pass

            # Do not place new trades outside regular hours
            if not regular_hours:
                print(f"[{sym}] Market closed or outside regular hours — skipping new entries (signal={signal})")
                continue

            if signal == "buy" and qty_held == 0:
                # ATR-based stop
                try:
                    current_atr = atr(bars["high"], bars["low"], bars["close"], period=cfg.get("atr_period", 14))
                except Exception:
                    current_atr = None

                if current_atr is None or current_atr <= 0:
                    print(f"[{sym}] ATR not available, skipping")
                    continue

                atr_multiplier = float(cfg.get("atr_multiplier", 1.5))
                stop_price = round(last_price - (current_atr * atr_multiplier), 4)
                if stop_price <= 0 or stop_price >= last_price:
                    print(f"[{sym}] Computed invalid stop {stop_price} (price {last_price}) — skipping")
                    continue

                current_position_value = qty_held * last_price
                qty = compute_qty_atr(equity, last_price, stop_price, cfg, cash, current_position_value)
                if qty <= 0:
                    print(f"[{sym}] Computed qty 0 (maybe insufficient cash or caps).")
                    continue

                tp_price = round(last_price * (1 + cfg.get("take_profit_pct", 0.08)), 4)

                # Place bracket order (market parent + stop_loss + take_profit)
                try:
                    order = api.submit_order(
                        symbol=sym,
                        qty=qty,
                        side="buy",
                        type="market",
                        time_in_force="day",
                        order_class="bracket",
                        take_profit={"limit_price": str(tp_price)},
                        stop_loss={"stop_price": str(stop_price)}
                    )
                    print(f"[{sym}] Placed BUY {qty} @ approx {last_price:.2f}. TP {tp_price}, SL {stop_price}")
                    log_and_alert(name, sym, "buy", qty, last_price, stop_price, tp_price, "placed_bracket")
                    # optimistic local update
                    positions[sym] = positions.get(sym, 0) + qty
                    cash -= qty * last_price
                except Exception as e:
                    print(f"[{sym}] Order submit failed: {e}")
                    log_and_alert(name, sym, "buy", qty, last_price, stop_price, tp_price, f"order_failed:{e}")

            elif signal == "sell" and qty_held > 0:
                try:
                    order = api.submit_order(sym, qty_held, "sell", "market", "day")
                    print(f"[{sym}] SELL signal executed for {qty_held}")
                    log_and_alert(name, sym, "sell", qty_held, last_price, None, None, "signal_sell")
                    positions[sym] = 0
                except Exception as e:
                    print(f"[{sym}] Sell order failed: {e}")
                    log_and_alert(name, sym, "sell", qty_held, last_price, None, None, f"sell_failed:{e}")
            else:
                print(f"[{sym}] No action ({reason})")

        except Exception as e:
            print(f"[{sym}] Unexpected error: {e}")

def log_and_alert(account_name, symbol, side, qty, avg_fill_price, stop_price, take_profit_price, notes):
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "account": account_name,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "intended_value": round((avg_fill_price or 0) * (qty or 0), 2),
        "avg_fill_price": avg_fill_price,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "notes": notes
    }
    try:
        log_trade(row)
    except Exception:
        pass

    # send email alert if configured
    try:
        subject = f"[Bot] {side.upper()} {symbol} ({qty}) - {notes}"
        body = f"{row}\n\nTime: {datetime.now(timezone.utc).isoformat()}"
        send_alert(subject, body)
    except Exception:
        pass

# ---------- START ----------
def main():
    accounts = []
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        secret = os.getenv(f"APCA_API_SECRET_{i}")
        base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        if not key or not secret:
            print(f"Missing credentials for account {i}, skipping.")
            continue
        try:
            api = REST(key, secret, base)
            api.get_account()
            accounts.append({"name": f"PaperAccount{i}", "api": api, "symbols": cfg["symbols"]})
            print(f"Connected account {i}")
        except Exception as e:
            print(f"Failed to init account {i}: {e}")

    if not accounts:
        print("No accounts available. Exiting.")
        return

    for acc in accounts:
        trade_account(acc)

    print("All accounts processed. Exiting.")

if __name__ == "__main__":
    main()
