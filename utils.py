import numpy as np
import pandas as pd

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def generate_signals(data, config):
    """Return 'buy', 'sell', or None based on SMA + RSI."""
    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])

    latest = data.iloc[-1]

    if (
        latest["sma_fast"] > latest["sma_slow"]
        and latest["rsi"] < config["rsi_oversold"]
    ):
        return "buy"
    elif (
        latest["sma_fast"] < latest["sma_slow"]
        and latest["rsi"] > config["rsi_overbought"]
    ):
        return "sell"
    else:
        return None
