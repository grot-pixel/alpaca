import os
import json
from datetime import datetime, timezone, time as dt_time
from zoneinfo import ZoneInfo
import math
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

# === Load config ===
CONFIG_FILE = "config.json"
with open(CONFIG_FILE) as f:
    cfg = json.load(f)
print("Loaded config:", cfg)

# === Helpers ===
ET_ZONE = ZoneInfo("US/Eastern")

def parse_hhmm_to_time(hhmm: str):
    h, m = [int(x) for x in hhmm.split(":")]
    return dt_time(h, m)

def in_trade_window(cfg):
    now_et = datetime.now(timezone.utc).astimezone(ET_ZONE)
    start = parse_hhmm_to_time(cfg.get("trade_start_time_et", "09:40"))
    end = parse_hhmm_to_time(cfg.get("trade_end_time_et", "15:55"))
    return start <= now_et.time() <= end and now_et.weekday() < 5

def sma_signal_from_bars(bars_df, cfg):
    # assumes bars_df has 'close'
    if bars_df.empty or len(bars_df) < max(cfg["sma_slow"], cfg["rsi_period"]):
        return None, "Not enough data"
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
    bars = api.get_bars(symbol, TimeFrame.Minute, limit=2).df
    if bars.empty:
        return None
    return bars.iloc[-1]

def get_quote_safe(api, symbol):
    try:
        q = api.get_latest_quote(symbol)
        # object may have .askprice/.bidprice or .ask.price etc depending on sdk
        ask = getattr(q, "askprice", None) or getattr(q, "ask", None)
        bid = getattr(q, "bidprice", None) or getattr(q, "bid", None)
        # Some wrappers use nested fields:
        if hasattr(ask, "price"):
            ask_p = float(ask.price)
        else:
            try:
                ask_p = float(ask)
            except Exception:
                ask_p = None
        if hasattr(bid, "price"):
            bid_p = float(bid.price)
        else:
            try:
                bid_p = float(bid)
            except Exception:
                bid_p = None
        return bid_p, ask_p
    except Exception:
        return None, None

# === Signal generator wrapper ===
def generate_signal(symbol, api, cfg):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=max(cfg["sma_slow"], cfg["rsi_period"]) + 1).df
        return sma_signal_from_bars(bars, cfg)
    except Exception as e:
        return None, f"Error generating signal: {e}"

# === Core trading logic for each account ===
def trade_account(account_info):
    name = account_info["name"]
    api = account_info["api"]
    symbols = account_info["symbols"]

    print(f"\n=== Trading for {name} ===")

    try:
        account = api.get_account()
        cash = float(account.cash)
        equity = float(account.equity)
        print(f"Account Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    except Exception as e:
        print(f"Error fetching account info for {name}: {e}")
        return

    try:
        positions = {p.symbol: float(p.qty) for p in api.list_positions()}
        print(f"Current Positions: {positions}")
    except Exception as e:
        print(f"Error fetching positions for {name}: {e}")
        positions = {}

    # Check trade window
    if not in_trade_window(cfg):
        print("Outside configured trade window or weekend — skipping trading for now.")
        return

    for sym in symbols:
        try:
            # generate signal
            signal, reason = generate_signal(sym, api, cfg)
            if not signal:
                print(f"[{sym}] No signal ({reason})")
                continue

            # fetch price info and volume
            last_bar = get_last_bar(api, sym)
            if last_bar is None:
                print(f"[{sym}] No bar data, skipping.")
                continue
            last_price = float(last_bar.close)
            last_volume = int(last_bar.volume)

            # Quote check for spread
            bid_p, ask_p = get_quote_safe(api, sym)
            spread_pct = None
            if bid_p and ask_p and ask_p > 0:
                spread_pct = (ask_p - bid_p) / ask_p

            # Volume & spread filters
            if last_volume < cfg.get("min_volume", 0):
                print(f"[{sym}] Skipping: minute volume {last_volume} < min_volume {cfg.get('min_volume')}")
                continue
            max_spread = cfg.get("max_spread_pct", 0.01)
            if spread_pct is not None and spread_pct > max_spread:
                print(f"[{sym}] Skipping: spread {spread_pct:.4f} > max_spread {max_spread}")
                continue

            print(f"[{sym}] Signal: {signal.upper()} ({reason}) price ${last_price:.2f}, vol {last_volume}, spread {spread_pct}")

            # === BUY logic with limit orders and slices to reduce slippage ===
            if signal == "buy":
                current_qty = positions.get(sym, 0)
                current_value = current_qty * last_price

                max_position_value = equity * cfg["max_position_pct"]
                max_trade_value = cash * cfg["max_trade_pct"]
                remaining_value = max_position_value - current_value
                trade_value = min(max_trade_value, remaining_value)

                if trade_value <= 0:
                    print(f"[{sym}] Already at max position or no cash allocated, skipping.")
                    continue

                # slicing
                slices = max(1, int(cfg.get("order_slices", 1)))
                slice_value = trade_value / slices
                slippage = cfg.get("max_slippage_pct", 0.0025)

                executed_any = False
                for i in range(slices):
                    target_slice_value = slice_value
                    # price step: slightly more aggressive for later slices (if not filled)
                    step = (i / max(1, slices - 1)) if slices > 1 else 0
                    limit_price = round(last_price * (1 + slippage * step), 2)

                    # Calculate qty for this slice (integer shares)
                    qty = int(target_slice_value // limit_price)
                    if qty < 1:
                        # if fractional allowed, use notional market fallback (risk slippage)
                        if cfg.get("allow_fractional", True):
                            notional = target_slice_value
                            try:
                                api.submit_order(
                                    symbol=sym,
                                    notional=notional,
                                    side="buy",
                                    type="market",
                                    time_in_force="day",
                                    order_class="bracket",
                                    take_profit={"limit_price": round(last_price * (1 + cfg["take_profit_pct"]), 2)},
                                    stop_loss={"stop_price": round(last_price * (1 - cfg["stop_loss_pct"]), 2)}
                                )
                                print(f"[{sym}] Fractional MARKET notional ${notional:.2f} submitted (fallback).")
                                executed_any = True
                            except Exception as e:
                                print(f"[{sym}] Fractional market order failed: {e}")
                        else:
                            print(f"[{sym}] Slice {i+1}: Not enough for 1 share at ${limit_price:.2f}, skipping slice.")
                        continue

                    # Place limit bracket order (reduces slippage)
                    tp = round(limit_price * (1 + cfg["take_profit_pct"]), 2)
                    sl = round(limit_price * (1 - cfg["stop_loss_pct"]), 2)
                    try:
                        api.submit_order(
                            symbol=sym,
                            qty=qty,
                            side="buy",
                            type="limit",
                            time_in_force="day",
                            limit_price=limit_price,
                            order_class="bracket",
                            take_profit={"limit_price": tp},
                            stop_loss={"stop_price": sl}
                        )
                        print(f"[{sym}] Slice {i+1}: LIMIT BUY {qty} @ ${limit_price:.2f} → TP {tp} / SL {sl}")
                        executed_any = True
                    except Exception as e:
                        # If bracket limit not supported/fails, fallback to limit without bracket, then to market bracket
                        print(f"[{sym}] Slice {i+1}: limit bracket failed: {e}. Trying limit w/o bracket.")
                        try:
                            api.submit_order(
                                symbol=sym,
                                qty=qty,
                                side="buy",
                                type="limit",
                                time_in_force="day",
                                limit_price=limit_price
                            )
                            print(f"[{sym}] Slice {i+1}: LIMIT BUY {qty} @ ${limit_price:.2f} (no bracket).")
                            executed_any = True
                        except Exception as e2:
                            print(f"[{sym}] Slice {i+1}: limit order failed: {e2}. Trying market bracket as last resort.")
                            try:
                                api.submit_order(
                                    symbol=sym,
                                    qty=qty,
                                    side="buy",
                                    type="market",
                                    time_in_force="day",
                                    order_class="bracket",
                                    take_profit={"limit_price": tp},
                                    stop_loss={"stop_price": sl}
                                )
                                print(f"[{sym}] Slice {i+1}: MARKET BUY fallback executed for {qty} shares.")
                                executed_any = True
                            except Exception as e3:
                                print(f"[{sym}] Slice {i+1}: All order attempts failed: {e3}")

                if not executed_any:
                    print(f"[{sym}] No slices executed for buy (insufficient funds, failures, or filtered).")

            # === SELL logic: close positions when signal says sell ===
            elif signal == "sell" and positions.get(sym, 0) > 0:
                qty = int(positions[sym])
                # To reduce slippage on exit, use limit sell slightly below current price by slippage config
                slippage = cfg.get("max_slippage_pct", 0.0025)
                limit_price = round(last_price * (1 - slippage), 2)
                try:
                    api.submit_order(
                        symbol=sym,
                        qty=qty,
                        side="sell",
                        type="limit",
                        time_in_force="day",
                        limit_price=limit_price
                    )
                    print(f"[{sym}] LIMIT SELL {qty} @ ${limit_price:.2f} (signal sell).")
                except Exception as e:
                    print(f"[{sym}] Limit sell failed: {e}. Falling back to market sell.")
                    try:
                        api.submit_order(
                            symbol=sym,
                            qty=qty,
                            side="sell",
                            type="market",
                            time_in_force="day"
                        )
                        print(f"[{sym}] MARKET SELL {qty} executed.")
                    except Exception as e2:
                        print(f"[{sym}] Market sell also failed: {e2}")

        except Exception as e:
            print(f"[{sym}] Unexpected error: {e}")

# === Initialize accounts ===
accounts = []
for i in [1, 2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if not key or not secret:
        print(f"API key/secret missing for account {i}, skipping.")
        continue

    try:
        api = REST(key, secret, base)
        api.get_account()
        print(f"Account {i} connected successfully.")
        accounts.append({
            "name": f"PaperAccount{i}",
            "api": api,
            "symbols": cfg["symbols"]
        })
    except Exception as e:
        print(f"Error initializing account {i}: {e}")

# === Run trading loop ===
for account_info in accounts:
    trade_account(account_info)
