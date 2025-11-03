import numpy as np
import pandas as pd

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(span=period).mean()
    roll_down = down.ewm(span=period).mean()
    rs = roll_up / roll_down
    return 100 - (100 / (1 + rs))

def generate_signal(data, cfg):
    """Generate clean momentum signal with volatility filter."""
    data["sma_fast"] = data["close"].rolling(cfg["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(cfg["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], cfg["rsi_period"])

    # volatility filter
    data["volatility"] = data["close"].pct_change().rolling(cfg["vol_window"]).std()
    vol_ok = data["volatility"].iloc[-1] > cfg["min_volatility"]

    latest = data.iloc[-1]
    if (
        vol_ok
        and latest["sma_fast"] > latest["sma_slow"]
        and latest["rsi"] < cfg["rsi_oversold"]
    ):
        return "buy"
    elif (
        vol_ok
        and latest["sma_fast"] < latest["sma_slow"]
        and latest["rsi"] > cfg["rsi_overbought"]
    ):
        return "sell"
    else:
        return None
