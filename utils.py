"""
Utilities: signal generation and liquidity/volatility checks.
SMA fast/slow + RSI with volatility filter produces clean scalp signals.
"""
import pandas as pd
import numpy as np

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / (loss.replace(0, 1e-9))
    return 100 - (100 / (1 + rs))

def generate_signal(bars: pd.DataFrame, cfg: dict):
    """
    bars: DataFrame with at least 'close' and 'volume' columns (index ascending).
    returns: (signal, reason)
    """
    df = bars.copy()
    fast = cfg.get("sma_fast", 5)
    slow = cfg.get("sma_slow", 13)
    rsi_period = cfg.get("rsi_period", 9)

    df["sma_fast"] = df["close"].rolling(fast).mean()
    df["sma_slow"] = df["close"].rolling(slow).mean()
    df["rsi"] = rsi(df["close"], rsi_period)

    if len(df) < slow + 2:
        return None, "insufficient data"

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # volatility filter: require recent std dev of returns > threshold
    vol_lookback = cfg.get("volatility_lookback", 30)
    ret_std = df["close"].pct_change().iloc[-vol_lookback:].std()
    if ret_std is None or ret_std < cfg.get("min_volatility", 0.0012):
        return None, f"low volatility ({ret_std})"

    # Signal: fast crosses above slow + RSI < oversold -> buy
    if (last["sma_fast"] > last["sma_slow"] and prev["sma_fast"] <= prev["sma_slow"]
        and last["rsi"] < cfg.get("rsi_oversold", 35)):
        return "buy", f"sma cross up; rsi {last['rsi']:.1f}"

    # Sell: fast cross below slow OR RSI > overbought
    if (last["sma_fast"] < last["sma_slow"] and prev["sma_fast"] >= prev["sma_slow"]) or last["rsi"] > cfg.get("rsi_overbought", 70):
        return "sell", f"sma cross down or rsi {last['rsi']:.1f}"

    return None, "no signal"

def avg_volume_threshold(bars: pd.DataFrame, cfg: dict) -> bool:
    """
    Quick liquidity check 
