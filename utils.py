import pandas as pd

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def generate_signals(data, config, return_stats=False):
    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])

    latest = data.iloc[-1]
    
    stats = {
        "sma_f": latest["sma_fast"],
        "sma_s": latest["sma_slow"],
        "rsi": latest["rsi"]
    }

    signal = None
    if latest["sma_fast"] > latest["sma_slow"] and latest["rsi"] < config["rsi_oversold"]:
        signal = "buy"
    elif latest["sma_fast"] < latest["sma_slow"] and latest["rsi"] > config["rsi_overbought"]:
        signal = "sell"

    return (signal, stats) if return_stats else signal
