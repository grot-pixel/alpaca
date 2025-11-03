import numpy as np
import pandas as pd

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def detect_trend(series: pd.Series, window: int = 50):
    """Return True if upward trend, False otherwise."""
    if len(series) < window:
        return False
    slope = np.polyfit(range(window), series[-window:], 1)[0]
    return slope > 0

def generate_signals(data: pd.DataFrame, config: dict):
    """Return ('buy' | 'sell' | None, reason) based on SMA+RSI+Trend."""
    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])

    latest = data.iloc[-1]
    trend_up = detect_trend(data["close"], config["trend_window"])

    if (
        latest["sma_fast"] > latest["sma_slow"]
        and latest["rsi"] < config["rsi_oversold"]
        and trend_up
    ):
        return "buy", f"Uptrend confirmed, RSI {latest['rsi']:.1f}, SMA fast>slow"
    elif (
        latest["sma_fast"] < latest["sma_slow"]
        or latest["rsi"] > config["rsi_overbought"]
    ):
        return "sell", f"RSI {latest['rsi']:.1f} or SMA fast<slow"
    else:
        return None, "No signal"
