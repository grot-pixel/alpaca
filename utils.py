import pandas as pd
import numpy as np


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def vwap(data):
    """Volume-weighted average price (intraday anchor)."""
    typical = (data["high"] + data["low"] + data["close"]) / 3
    return (typical * data["volume"]).cumsum() / data["volume"].cumsum()


def atr(data, period=14):
    """Average True Range — used for vol-targeted sizing and ATR-multiple stops."""
    high, low, close = data["high"], data["low"], data["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def adx(data, period=14):
    """
    Average Directional Index — regime filter.
    ADX > 20-25 = trending market (favorable for trend strategies).
    ADX < 20    = chop (avoid trend-following entries).
    """
    high, low, close = data["high"], data["low"], data["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_val = tr.rolling(window=period).mean()

    plus_di = 100 * plus_dm.rolling(window=period).mean() / atr_val.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(window=period).mean() / atr_val.replace(0, np.nan)
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return dx.rolling(window=period).mean()


def generate_signals(data, config, return_stats=False):
    """
    Trend-pullback strategy:
      Gate 1 — Regime: ADX > adx_min (only trade in trending markets)
      Gate 2 — Trend:  SMA fast vs SMA slow (which direction)
      Gate 3 — Timing: RSI pullback (don't buy tops / sell bottoms)
      Gate 4 — Confirm: VWAP aligned with trend

    All four must agree. Unlike a 2-of-3 vote on correlated indicators,
    this requires *independent* conditions — regime, direction, timing, confirmation.
    """
    data = data.copy()
    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])
    data["vwap"] = vwap(data)
    data["atr"] = atr(data, config["atr_period"])
    data["adx"] = adx(data, config["adx_period"])

    latest = data.iloc[-1]
    sma_f = latest["sma_fast"]
    sma_s = latest["sma_slow"]
    rsi_val = latest["rsi"]
    price = latest["close"]
    vwap_val = latest["vwap"]
    atr_val = latest["atr"]
    adx_val = latest["adx"]

    # NaN guard — early bars before indicators warm up
    if any(pd.isna(x) for x in (sma_f, sma_s, rsi_val, vwap_val, atr_val, adx_val)):
        signal = None
    else:
        trending = adx_val > config["adx_min"]
        uptrend = sma_f > sma_s
        downtrend = sma_f < sma_s
        rsi_dipped = rsi_val < config["rsi_buy_max"]   # bought into a pullback
        rsi_popped = rsi_val > config["rsi_sell_min"]  # sold into a bounce
        above_vwap = price > vwap_val
        below_vwap = price < vwap_val

        signal = None
        if trending and uptrend and rsi_dipped and above_vwap:
            signal = "buy"
        elif trending and downtrend and rsi_popped and below_vwap:
            signal = "sell"  # exit long position; bot is long-only

    stats = {
        "sma_f": sma_f, "sma_s": sma_s,
        "rsi": rsi_val, "vwap": vwap_val,
        "atr": atr_val, "adx": adx_val,
    }
    return (signal, stats) if return_stats else signal
