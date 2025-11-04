# trading_bot.py
import os
import json
import time
import math
from datetime import datetime, timezone, time as dt_time
from zoneinfo import ZoneInfo
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

# === Load config ===
with open("config.json") as f:
    cfg = json.load(f)
print("Loaded config:", cfg)

# === Constants / Timezone ===
ET_ZONE = ZoneInfo("US/Eastern")

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
        # Try common attribute names
        ask = getattr(q, "askprice", None) or getattr(q, "ask", None)
        bid = getattr(q, "bidprice", None) or getattr(q, "bid", None)
        # Normalize nested structures
        def extract_price(x):
            if x is None:
                return None
            if hasattr(x, "price"):
                return float(x.price)
            try:
                return float(x)
            except Exception:
                return None
        return extract_price(bid), extract_price(ask)
    except Exception:
        return None, None

# === Adaptive min volume decision ===
def min_volume_for_symbol(sym, cfg):
    min_vol_cfg = cfg.get("min_volume", {})
    if isinstance(min_vol_cfg, dict):
        high_list = {"AAPL", "TSLA", "AMD", "NVDA", "TQQQ", "SOXL"}
        if sym in high_list:
            return min_vol_cfg.get("high_liquidity", 20000)
        else:
            return min_vol_cfg.get("low_liquidity", 1000)
    else:
        # single numeric
        try:
            return int(min_vol_cfg)
        except Exception:
            return 1000

# === Place single order and optionally wait for fill with retries ===
def place_order_with_retry(api, symbol, side, qty=None, notional=None,
                           type_="limit", limit_price=None, order_class=None,
                           take_profit=None, stop_loss=None,
                           retry_after_sec=30, max_attempts=3, allow_market_fallback=True):
    """
    Submit order and wait up to retry_after_sec for fill. If not filled, cancel and optionally retry
    with increased slippage (calling code should compute new limit_price) or fallback to market.
    Returns order result dict or raises.
    """
    attempt = 0
    wait = retry_after_sec
    while attempt < max_attempts:
        attempt += 1
        try:
            # build kwargs for submit_order
            kwargs = {
                "symbol": symbol,
                "side": side,
                "type": type_,
                "time_in_force": "day"
            }
            if qty is not None:
                kwargs["qty"] = qty
            if notional is not None:
                kwargs["notional"] = notional
            if limit_price is not None and type_.lower() == "limit":
                kwargs["limit_price"] = float(limit_price)
            if order_class:
                kwargs["order_class"] = order_class
            if take_profit:
                kwargs["take_profit"] = take_profit
            if stop_loss:
                kwargs["stop_loss"] = stop_loss

            order = api.submit_order(**kwargs)
            oid = getattr(order, "id", None) or getattr(order, "order_id", None) or None
            print(f"[{symbol}] Submitted order attempt {attempt}: {kwargs}. order_id={oid}")

            # If no order id (sdk differences), return directly
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
                    print(f"[{symbol}] Order {oid} filled (status={status}, filled_qty={filled_qty})")
                    return o
                # not filled yet
                time.sleep(1)
            # Timeout waiting for fill -> cancel and retry strategy
            try:
                api.cancel_order(oid)
                print(f"[{symbol}] Order {oid} canceled after timeout waiting {wait}s (attempt {attempt}).")
            except Exception as e:
                print(f"[{symbol}] Failed to cancel order {oid}: {e}")
        except Exception as e:
            print(f"[{symbol}] submit_order exception on attempt {attempt}: {e}")

        # prepare for next attempt: widen wait slightly
        wait = int(wait * 1.5)
        attempt += 0

    # if reached here, all attempts failed
    if allow_market_fallback:
        try:
            print(f"[{symbol}] All limit attempts failed. Falling back to MARKET order as last resort.")
            kwargs = {
                "symbol": symbol,
                "side": side,
                "type": "market",
                "time_in_force": "day"
            }
            if qty is not None:
                kwargs["qty"] = qty
            if notional is not None:
                kwargs["notional"] = notional
            if order_class:
                kwargs["order_class"] = order_class
            if take_profit:
                kwargs["take_profit"] = take_profit
            if stop_loss:
                kwargs["stop_loss"] = stop_loss
            order = api.submit_order(**kwargs)
            print(f"[{symbol}] MARKET fallback submitted: {kwargs}")
            return order
        except Exception as e:
            print(f"[{symbol}] MARKET fallback failed: {e}")
            raise RuntimeError(f"All order attempts failed for {symbol}: {e}")
    else:
        raise RuntimeError(f"All order attempts failed for {symbol}")

# === Signal generator wrapper ===
def generate_signal(symbol, api, cfg):
    try:
        bars = api.get_bars(symbol, TimeFrame.Minute, limit=max(cfg["sma_slow"], cfg["rsi_period"]) + 1).df
        return sma_signal_from_bars(bars, cfg)
    except Exception as e:
        return None, f"Error generating signal: {e}"

# === Core trading logic per account ===
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

    # trade window
    if not in_trade_window(cfg):
        print("Outside trade window or weekend — skipping.")
        return

    for sym in symbols:
        try:
            signal, reason = generate_signal(sym, api, cfg)
            if not signal:
                print(f"[{sym}] No signal ({reason})")
                continue

            last_bar = get_last_bar(api, sym)
            if last_bar is None:
                print(f"[{sym}] No bar data, skipping.")
                continue

            last_price = float(last_bar.close)
            last_volume = int(last_bar.volume)
            bid_p, ask_p = get_quote_safe(api, sym)
            spread_pct = None
            if bid_p and ask_p and ask_p > 0:
                spread_pct = (ask_p - bid_p) / ask_p

            # Adaptive min volume threshold
            min_vol = min_volume_for_symbol(sym, cfg)
            if last_volume < min_vol:
                print(f"[{sym}] Skipping: minute volume {last_volume} < threshold {min_vol}")
                continue

            max_spread = cfg.get("max_spread_pct", 0.01)
            if spread_pct is not None and spread_pct > max_spread:
                print(f"[{sym}] Skipping: spread {spread_pct:.4f} > max_spread {max_spread}")
                continue

            print(f"[{sym}] Signal: {signal.upper()} ({reason}) price ${last_price:.2f}, vol {last_volume}, spread {spread_pct}")

            # position sizing
            current_qty = positions.get(sym, 0)
            current_value = current_qty * last_price
            max_position_value = equity * cfg["max_position_pct"]
            max_trade_value = cash * cfg["max_trade_pct"]
            remaining_value = max_position_value - current_value
            trade_value = min(max_trade_value, remaining_value)

            if signal == "buy":
                if trade_value <= 0:
                    print(f"[{sym}] No allocation left (already near max position), skipping buy.")
                    continue

                slices = max(1, int(cfg.get("order_slices", 1)))
                slippage_base = cfg.get("max_slippage_pct", 0.0025)
                allow_fractional = cfg.get("allow_fractional", True)
                order_type_cfg = cfg.get("order_type", "limit").lower()
                retry_after = int(cfg.get("retry_unfilled_after_sec", 30))

                slice_value = trade_value / slices
                executed_any = False

                for i in range(slices):
                    # progressive slippage step
                    step = (i / max(1, slices - 1)) if slices > 1 else 0
                    slippage = slippage_base * (1 + step)
                    # Compute desired limit price allowing slippage above mid/last
                    limit_price = round(last_price * (1 + slippage), 4)

                    # compute qty for slice (integer shares)
                    qty = int(math.floor(slice_value / limit_price))
                    notional = None
                    if qty < 1 and allow_fractional:
                        # use notional order if fractional allowed
                        notional = slice_value
                    elif qty < 1:
                        print(f"[{sym}] Slice {i+1}: not enough to buy 1 share at ${limit_price:.2f}, skipping slice.")
                        continue

                    # bracket TP/SL prices
                    tp_price = round(last_price * (1 + cfg["take_profit_pct"]), 4)
                    sl_price = round(last_price * (1 - cfg["stop_loss_pct"]), 4)

                    if order_type_cfg == "market":
                        # submit market bracket order (fast)
                        try:
                            res = place_order_with_retry(
                                api, sym, "buy", qty=qty if qty >= 1 else None,
                                notional=notional, type_="market",
                                order_class="bracket",
                                take_profit={"limit_price": tp_price},
                                stop_loss={"stop_price": sl_price},
                                retry_after_sec=retry_after,
                                max_attempts=1,  # market should fill quickly
                                allow_market_fallback=False
                            )
                            print(f"[{sym}] MARKET buy slice {i+1} submitted (notional={notional}, qty={qty})")
                            executed_any = True
                        except Exception as e:
                            print(f"[{sym}] MARKET buy slice {i+1} failed: {e}")
                    else:
                        # limit (preferred): try limit bracket first, then escalate if not filled
                        try:
                            res = place_order_with_retry(
                                api, sym, "buy", qty=qty if qty >= 1 else None,
                                notional=notional, type_="limit",
                                limit_price=limit_price,
                                order_class="bracket",
                                take_profit={"limit_price": tp_price},
                                stop_loss={"stop_price": sl_price},
                                retry_after_sec=retry_after,
                                max_attempts=2,
                                allow_market_fallback=True
                            )
                            print(f"[{sym}] LIMIT buy slice {i+1} (limit={limit_price}) result: {getattr(res,'status', res)}")
                            executed_any = True
                        except Exception as e:
                            print(f"[{sym}] LIMIT buy slice {i+1} failed fully: {e}")

                if not executed_any:
                    print(f"[{sym}] No buy slices executed for {sym} (all slices skipped/failed).")

            elif signal == "sell":
                if current_qty <= 0:
                    print(f"[{sym}] Sell signal but no current position, skipping.")
                    continue

                # try limit sell slightly below current to get filled quickly but not too poor price
                slippage = cfg.get("max_slippage_pct", 0.0025)
                order_type_cfg = cfg.get("order_type", "limit").lower()
                retry_after = int(cfg.get("retry_unfilled_after_sec", 30))
                target_limit = round(last_price * (1 - slippage), 4)

                try:
                    if order_type_cfg == "market":
                        res = place_order_with_retry(
                            api, sym, "sell", qty=int(math.floor(current_qty)),
                            type_="market",
                            retry_after_sec=retry_after,
                            max_attempts=1,
                            allow_market_fallback=False
                        )
                        print(f"[{sym}] MARKET SELL executed for {current_qty}")
                    else:
                        res = place_order_with_retry(
                            api, sym, "sell", qty=int(math.floor(current_qty)),
                            type_="limit",
                            limit_price=target_limit,
                            retry_after_sec=retry_after,
                            max_attempts=2,
                            allow_market_fallback=True
                        )
                        print(f"[{sym}] LIMIT SELL submitted for {current_qty} @ {target_limit}")
                except Exception as e:
                    print(f"[{sym}] Sell attempts failed: {e}")

        except Exception as e:
            print(f"[{sym}] Unexpected error in symbol loop: {e}")

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
