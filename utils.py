import pandas as pd


def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def vwap(data):
    """Volume Weighted Average Price — intraday momentum anchor."""
    typical = (data["high"] + data["low"] + data["close"]) / 3
    return (typical * data["volume"]).cumsum() / data["volume"].cumsum()


def generate_signals(data, config, return_stats=False):
    data = data.copy()
    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])
    data["vwap"] = vwap(data)

    latest = data.iloc[-1]
    prev = data.iloc[-2]

    sma_f = latest["sma_fast"]
    sma_s = latest["sma_slow"]
    rsi_val = latest["rsi"]
    price = latest["close"]
    vwap_val = latest["vwap"]

    # Trend: fast SMA crossed above slow SMA recently (last 3 bars)
    recent = data.tail(3)
    bullish_cross = any(
        recent["sma_fast"].iloc[i] > recent["sma_slow"].iloc[i] and
        recent["sma_fast"].iloc[i - 1] <= recent["sma_slow"].iloc[i - 1]
        for i in range(1, len(recent))
    )
    bearish_cross = any(
        recent["sma_fast"].iloc[i] < recent["sma_slow"].iloc[i] and
        recent["sma_fast"].iloc[i - 1] >= recent["sma_slow"].iloc[i - 1]
        for i in range(1, len(recent))
    )

    sma_bullish = sma_f > sma_s
    sma_bearish = sma_f < sma_s
    price_above_vwap = price > vwap_val
    price_below_vwap = price < vwap_val

    signal = None

    # BUY conditions (need 2 of 3 confirmations for more frequent signals):
    # 1. SMA trend bullish
    # 2. RSI not overbought (< rsi_overbought threshold) and recovering (> 40)
    # 3. Price above VWAP (momentum confirmation)
    buy_confirmations = sum([
        sma_bullish or bullish_cross,
        config["rsi_buy_min"] < rsi_val < config["rsi_overbought"],
        price_above_vwap,
    ])

    # SELL conditions
    sell_confirmations = sum([
        sma_bearish or bearish_cross,
        rsi_val > config["rsi_sell_min"],
        price_below_vwap,
    ])

    if buy_confirmations >= 2:
        signal = "buy"
    elif sell_confirmations >= 2:
        signal = "sell"

    stats = {
        "sma_f": sma_f,
        "sma_s": sma_s,
        "rsi": rsi_val,
        "vwap": vwap_val,
        "buy_conf": buy_confirmations,
        "sell_conf": sell_confirmations,
    }

    return (signal, stats) if return_stats else signal
