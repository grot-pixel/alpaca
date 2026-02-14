import pandas as pd

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def generate_signals(data, config):
    """Return 'buy', 'sell', or None based on SMA + RSI."""
    # Ensure we have enough data
    if len(data) < config["sma_slow"]:
        return None

    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])

    latest = data.iloc[-1]

    # Original Condition: Fast SMA > Slow SMA AND Oversold RSI
    if latest["sma_fast"] > latest["sma_slow"] and latest["rsi"] < config["rsi_oversold"]:
        return "buy"
    # Original Condition: Fast SMA < Slow SMA AND Overbought RSI
    elif latest["sma_fast"] < latest["sma_slow"] and latest["rsi"] > config["rsi_overbought"]:
        return "sell"
    
    return None
