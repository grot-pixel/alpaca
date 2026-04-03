"""
utils.py — Signal generation & data helpers
============================================
Strategies (need 2-of-4 for a buy, 2-of-4 for a sell):
  1. SMA trend       — fast SMA vs slow SMA crossover
  2. RSI momentum    — not overbought/oversold with direction filter
  3. MACD crossover  — histogram expanding in signal direction
  4. VWAP position   — price above/below intraday VWAP (today only)

Key fix from v1: VWAP now uses TODAY's bars only (proper intraday anchor).
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ─── DATA FETCHERS ───────────────────────────────────────────────────────────

def get_today_bars(data_client: StockHistoricalDataClient, symbol: str) -> pd.DataFrame | None:
    """Fetch today's 1-minute bars (for VWAP). Returns None on failure."""
    try:
        now   = datetime.now(timezone.utc)
        start = now.replace(hour=13, minute=25, second=0, microsecond=0)  # 9:25 AM ET
        if now < start:
            return None

        req  = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                                start=start, end=now, feed="iex")
        bars = data_client.get_stock_bars(req)
        df   = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        return df.sort_index() if not df.empty else None
    except Exception:
        return None


def get_multi_day_bars(data_client: StockHistoricalDataClient, symbol: str,
                       days: int = 6) -> pd.DataFrame | None:
    """
    Fetch minute bars for the past N calendar days.
    Used for SMA, RSI, and MACD which need historical context.
    """
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        req   = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute,
                                 start=start, end=end, limit=1500, feed="iex")
        bars  = data_client.get_stock_bars(req)
        df    = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)
        df = df.sort_index()
        return df if not df.empty else None
    except Exception:
        return None

# ─── INDICATORS ──────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast    = series.ewm(span=fast, adjust=False).mean()
    ema_slow    = series.ewm(span=slow, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def vwap_today(today_bars: pd.DataFrame) -> float | None:
    """VWAP calculated from today's bars only — correct intraday anchor."""
    if today_bars is None or today_bars.empty:
        return None
    typical = (today_bars["high"] + today_bars["low"] + today_bars["close"]) / 3
    cum_tp_vol = (typical * today_bars["volume"]).cumsum()
    cum_vol    = today_bars["volume"].cumsum()
    vwap_series = cum_tp_vol / cum_vol.replace(0, np.nan)
    return float(vwap_series.iloc[-1])

# ─── SIGNAL GENERATOR ────────────────────────────────────────────────────────

def generate_signals(
    bars_hist: pd.DataFrame,    # multi-day minute bars (for SMA/RSI/MACD)
    bars_today: pd.DataFrame,   # today's minute bars (for VWAP)
    cfg: dict,
) -> tuple[str | None, dict]:
    """
    Returns (signal, stats) where signal is 'buy', 'sell', or None.
    Requires 2-of-4 confirmations in the same direction.
    """
    close  = bars_hist["close"]
    sma_fast_period = cfg["sma_fast"]
    sma_slow_period = cfg["sma_slow"]
    rsi_period      = cfg.get("rsi_period", 10)

    # ── Compute indicators ───────────────────────────────────────────────────
    sma_f_series = close.rolling(sma_fast_period).mean()
    sma_s_series = close.rolling(sma_slow_period).mean()
    rsi_series   = rsi(close, rsi_period)
    macd_line, sig_line, hist_series = macd(close)
    vwap_val     = vwap_today(bars_today)

    # Latest values
    sma_f      = float(sma_f_series.iloc[-1])
    sma_s      = float(sma_s_series.iloc[-1])
    rsi_val    = float(rsi_series.iloc[-1])
    macd_hist  = float(hist_series.iloc[-1])
    macd_hist_prev = float(hist_series.iloc[-2]) if len(hist_series) > 1 else 0.0
    price      = float(close.iloc[-1])

    # ── 1. SMA crossover (check last 3 bars for recent cross) ───────────────
    recent_fast = sma_f_series.iloc[-3:]
    recent_slow = sma_s_series.iloc[-3:]
    bullish_cross = any(
        recent_fast.iloc[i] > recent_slow.iloc[i] and
        recent_fast.iloc[i-1] <= recent_slow.iloc[i-1]
        for i in range(1, len(recent_fast))
    )
    bearish_cross = any(
        recent_fast.iloc[i] < recent_slow.iloc[i] and
        recent_fast.iloc[i-1] >= recent_slow.iloc[i-1]
        for i in range(1, len(recent_fast))
    )
    sma_bullish = (sma_f > sma_s) or bullish_cross
    sma_bearish = (sma_f < sma_s) or bearish_cross

    # ── 2. RSI ───────────────────────────────────────────────────────────────
    rsi_buy_min  = cfg.get("rsi_buy_min", 38)
    rsi_overbought = cfg.get("rsi_overbought", 68)
    rsi_sell_min = cfg.get("rsi_sell_min", 58)
    # Buy: RSI in healthy momentum zone (not overbought, not extreme oversold)
    rsi_bullish  = rsi_buy_min < rsi_val < rsi_overbought
    # Sell: RSI elevated and price weakening
    rsi_bearish  = rsi_val > rsi_sell_min

    # ── 3. MACD histogram ────────────────────────────────────────────────────
    # Bullish: histogram positive and expanding
    macd_bullish = macd_hist > 0 and macd_hist > macd_hist_prev
    # Bearish: histogram negative and expanding (more negative)
    macd_bearish = macd_hist < 0 and macd_hist < macd_hist_prev

    # ── 4. VWAP ──────────────────────────────────────────────────────────────
    if vwap_val:
        vwap_bullish = price > vwap_val
        vwap_bearish = price < vwap_val
    else:
        # No intraday data yet — treat as neutral (don't count either way)
        vwap_bullish = False
        vwap_bearish = False

    # ── Confirmation counts ──────────────────────────────────────────────────
    buy_conf  = sum([sma_bullish, rsi_bullish, macd_bullish, vwap_bullish])
    sell_conf = sum([sma_bearish, rsi_bearish, macd_bearish, vwap_bearish])

    signal = None
    threshold = cfg.get("signal_threshold", 2)
    if buy_conf >= threshold:
        signal = "buy"
    elif sell_conf >= threshold:
        signal = "sell"

    stats = {
        "sma_f":     sma_f,
        "sma_s":     sma_s,
        "rsi":       rsi_val,
        "macd_hist": macd_hist,
        "vwap":      vwap_val or 0.0,
        "buy_conf":  buy_conf,
        "sell_conf": sell_conf,
        "sma_bull":  sma_bullish,
        "rsi_bull":  rsi_bullish,
        "macd_bull": macd_bullish,
        "vwap_bull": vwap_bullish,
    }
    return signal, stats
