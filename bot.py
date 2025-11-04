# trading_bot_enhanced.py
import os
import json
import time
import math
import csv
from datetime import datetime, timezone, time as dt_time
from zoneinfo import ZoneInfo
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

# === Load config ===
with open("config.json") as f:
    cfg = json.load(f)
print("Loaded config:", cfg)

ET_ZONE = ZoneInfo("US/Eastern")
TRADE_LOG_FILE = "trade_log.csv"

# === Helpers ===
def parse_hhmm_to_time(hhmm: str):
    h, m = [int(x) for x in hhmm.split(":")]
    return dt_time(h, m)

def in_trade_window(cfg):
    now_et = datetime.now(timezone.utc).astimezone(ET_ZONE)
    start = parse_hhmm_to_time(cfg.get("trade_start_time_et", "09:40"))
    end = parse_hhmm_to_time(cfg.get("trade_end_time_et", "15:55"))
    return start <= now_et.time() <= end and now_et.weekday() < 5

def sma_signal_from_bars(bars_df, cfg):
    if bars_df.empty or len(bars_df) < max(cfg["sma_slow"], cfg["rsi_period"]):
        return None, "Not enough data"
    bars_df = bars_df.copy()
    bars_df["sma_fast"] = bars_df["close"].rolling(cfg["sma_fast"]).mean()
    bars_df["sma_slow"] = bars_df["close"].rolling(cfg["sma_slow"]).mean()
    last_fast = bars_df["sma_fast"].iloc[-1]
    last_slow = bars_df["sma_slow"].iloc[-1]
    if pd.isna(last_fast) or pd.isna(last_slow):
        return None, "NaN in SMA"
    if last_fast > last_slow:
        return "buy", f"SMA fast {last_fast:.2f} > SMA slow {last_slow:.2f}"
    elif last_fast < last_slow:
        return "sell", f"SMA fast {last_fast:.2f} < SMA slow {last_slow:.2f}"
    return None, "No crossover"

def get_last_bar(api, symbol):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=2).df
        if bars.empty:
            return None
        return bars.iloc[-1]
    except Exception as e:
        print(f"[{symbol}] Error fetching bars: {e}")
        return None

def get_quote_safe(api, symbol):
    try:
        q = api.get_latest_quote(symbol)
        bid = getattr(q, "bidprice", None) or getattr(q, "bid", None)
        ask = getattr(q, "askprice", None) or getattr(q, "ask", None)
        bid = float(getattr(bid, "price", bid) or bid)
        ask = float(getattr(ask, "price", ask) or ask)
        return bid, ask
    except Exception:
        return None, None

def min_volume_for_symbol(sym, cfg):
    min_vol_cfg = cfg.get("min_volume", {})
    if isinstance(min_vol_cfg, dict):
        high_list = {"AAPL", "TSLA", "AMD", "NVDA", "TQQQ", "SOXL"}
        if sym in high_list:
            return min_vol_cfg.get("high_liquidity", 20000)
        else:
            return min_vol_cfg.get("low_liquidity", 1000)
    else:
        try:
            return int(min_vol_cfg)
        except Exception:
            return 1000

def log_trade(account_name, symbol, side, qty, price, order_type, status, reason="", tp=None, sl=None):
    headers = ["timestamp", "account", "symbol", "side", "qty", "price", "order_type", "status", "reason", "take_profit", "stop_loss"]
    file_exists = os.path.isfile(TRADE_LOG_FILE)
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "account": account_name,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "order_type": order_type,
            "status": status,
            "reason": reason,
            "take_profit": tp,
            "stop_loss": sl
        })

# === Order placement with retry & fallback ===
def place_order_with_retry(api, symbol, side, qty=None, notional=None,
                           type_="limit", limit_price=None, order_class=None,
                           take_profit=None, stop_loss=None,
                           retry_after_sec=30, max_attempts=3, allow_market_fallback=True):
    attempt = 0
    wait = retry_after_sec
    while attempt < max_attempts:
        attempt += 1
        try:
            kwargs = {
                "symbol": symbol,
                "side": side,
                "type": type_,
                "time_in_force": "day"
            }
            if qty is not None: kwargs["qty"] = qty
            if notional is not None: kwargs["notional"] = notional
            if limit_price is not None and type_ == "limit": kwargs["limit_price"] = float(limit_price)
            if order_class: kwargs["order_class"] = order_class
            if take_profit: kwargs["take_profit"] = take_profit
            if stop_loss: kwargs["stop_loss"] = stop_loss

            order = api.submit_order(**kwargs)
            oid = getattr(order, "id", None) or getattr(order, "order_id", None) or None
            print(f"[{symbol}] Submitted order attempt {attempt}: {kwargs}, order_id={oid}")

            if not oid:
                return order

            start = time.time()
            while time.time() - start < wait:
                try:
                    o = api.get_order(oid)
                except Exception:
                    time.sleep(0.5)
                    continue
                status = getattr(o, "status", None)
                filled_qty = float(getattr(o, "filled_qty", 0) or 0)
                if status in ("filled", "partially_filled") and filled_qty > 0:
                    return o
                time.sleep(1)

            try:
                api.cancel_order(oid)
                print(f"[{symbol}] Order {oid} canceled after {wait}s")
            except Exception as e:
                print(f"[{symbol}] Failed to cancel order {oid}: {e}")
        except Exception as e:
            print(f"[{symbol}] submit_order exception attempt {attempt}: {e}")

        wait = int(wait * 1.5)

    if allow_market_fallback:
        try:
            print(f"[{symbol}] Falling back to MARKET order")
            kwargs["type"] = "market"
            order = api.submit_order(**kwargs)
            return order
        except Exception as e:
            raise RuntimeError(f"All order attempts failed for {symbol}: {e}")
    else:
        raise RuntimeError(f"All order attempts failed for {symbol}")

# === Signal wrapper ===
def generate_signal(symbol, api, cfg):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=max(cfg["sma_slow"], cfg["rsi_period"]) + 1).df
        return sma_signal_from_bars(bars, cfg)
    except Exception as e:
        return None, f"Error generating signal: {e}"

# === Core trading logic ===
REENTRY_COOLDOWN = {}  # symbol -> last closed timestamp
TRAILING_STOPS = {}    # symbol -> current stop price

def trade_account(account_info):
    name = account_info["name"]
    api = account_info["api"]
    symbols = account_info["symbols"]

    print(f"\n=== Trading for {name} ===")
    try:
        account = api.get_account()
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"Error fetching account info for {name}: {e}")
        return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
    except Exception as e:
        print(f"Error fetching positions: {e}")
        positions = {}

    if not in_trade_window(cfg):
        print("Outside trade window, skipping.")
        return

    for sym in symbols:
        try:
            # Re-entry cooldown: 5 min after closing
            last_closed = REENTRY_COOLDOWN.get(sym)
            if last_closed and (datetime.now(timezone.utc) - last_closed).total_seconds() < 300:
                print(f"[{sym}] Cooldown active, skipping signal")
                continue

            signal, reason = generate_signal(sym, api, cfg)
            if not signal:
                print(f"[{sym}] No signal ({reason})")
                continue

            last_bar = get_last_bar(api, sym)
            if last_bar is None:
                continue
            last_price = float(last_bar.close)
            last_volume = int(last_bar.volume)
            bid_p, ask_p = get_quote_safe(api, sym)
            spread_pct = ((ask_p - bid_p)/ask_p if bid_p and ask_p else None)

            min_vol = min_volume_for_symbol(sym, cfg)
            if last_volume < min_vol:
                continue
            max_spread = cfg.get("max_spread_pct", 0.01)
            if spread_pct is not None and spread_pct > max_spread:
                continue

            current_qty = positions.get(sym, 0)
            current_value = current_qty * last_price
            max_position_value = equity * cfg["max_position_pct"]
            max_trade_value = cash * cfg["max_trade_pct"]
            remaining_value = max_position_value - current_value
            trade_value = min(max_trade_value, remaining_value)
            if trade_value <= 0 and signal == "buy":
                continue

            # Trailing stop
            trailing_stop = TRAILING_STOPS.get(sym)

            # Execute
            if signal == "buy":
                qty = int(math.floor(trade_value / last_price))
                if qty < 1 and cfg.get("allow_fractional", True):
                    notional = trade_value
                    qty = None
                else:
                    notional = None

                tp_price = round(last_price*(1+cfg["take_profit_pct"]),4)
                sl_price = round(last_price*(1-cfg["stop_loss_pct"]),4)

                order_res = place_order_with_retry(
                    api, sym, "buy", qty=qty, notional=notional,
                    type_=cfg.get("order_type","limit"),
                    limit_price=last_price,
                    order_class="bracket",
                    take_profit={"limit_price": tp_price},
                    stop_loss={"stop_price": sl_price},
                    retry_after_sec=cfg.get("retry_unfilled_after_sec", 30)
                )
                print(f"[{sym}] BUY placed: qty={qty}, price={last_price}, TP={tp_price}, SL={sl_price}")
                log_trade(name, sym, "buy", qty or notional, last_price, cfg.get("order_type","limit"), "submitted", reason, tp_price, sl_price)
                TRAILING_STOPS[sym] = sl_price

            elif signal == "sell" and current_qty > 0:
                order_res = place_order_with_retry(
                    api, sym, "sell", qty=current_qty, type_=cfg.get("order_type","limit"),
                    limit_price=last_price, retry_after_sec=cfg.get("retry_unfilled_after_sec",30)
                )
                print(f"[{sym}] SELL placed: qty={current_qty}, price={last_price}")
                log_trade(name, sym, "sell", current_qty, last_price, cfg.get("order_type","limit"), "submitted", reason)
                REENTRY_COOLDOWN[sym] = datetime.now(timezone.utc)
                if sym in TRAILING_STOPS:
                    del TRAILING_STOPS[sym]

        except Exception as e:
            print(f"[{sym}] Exception: {e}")

# === Initialize accounts ===
accounts = []
for i in [1,2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
    if not key or not secret:
        continue
    try:
        api = REST(key, secret, base)
        api.get_account()
        accounts.append({"name": f"PaperAccount{i}", "api": api, "symbols": cfg["symbols"]})
        print(f"Account {i} connected successfully")
    except Exception as e:
        print(f"Error initializing account {i}: {e}")

# === Run once ===
for acc in accounts:
    trade_account(acc)
