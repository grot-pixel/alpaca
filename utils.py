import pandas as pd
import numpy as np


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    high = data["high"]
    low = data["low"]
    close = data["close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(data: pd.DataFrame, period: int = 14):
    """
    Wilder-style ADX plus directional indicators.

    Returns:
        adx
        plus_di
        minus_di
    """

    high = data["high"]
    low = data["low"]
    close = data["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0),
        0.0,
    )

    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0),
        0.0,
    )

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    plus_dm_smoothed = plus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    minus_dm_smoothed = minus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    plus_di = (
        100
        * plus_dm_smoothed
        / atr_value.replace(0, np.nan)
    )

    minus_di = (
        100
        * minus_dm_smoothed
        / atr_value.replace(0, np.nan)
    )

    di_sum = (plus_di + minus_di).replace(0, np.nan)

    dx = (
        100
        * (plus_di - minus_di).abs()
        / di_sum
    )

    adx_value = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return adx_value, plus_di, minus_di


def vwap(data: pd.DataFrame) -> pd.Series:
    """
    True session VWAP.

    VWAP resets at the beginning of every trading session.
    """

    df = data.copy()

    typical_price = (
        df["high"] +
        df["low"] +
        df["close"]
    ) / 3.0

    volume_price = typical_price * df["volume"]

    # Convert timestamps to session dates.
    if isinstance(df.index, pd.DatetimeIndex):
        session = df.index.tz_convert("America/New_York").date
    else:
        session = pd.Series(
            pd.to_datetime(df.index).date,
            index=df.index,
        )

    cumulative_pv = volume_price.groupby(session).cumsum()
    cumulative_volume = df["volume"].groupby(session).cumsum()

    return cumulative_pv / cumulative_volume.replace(0, np.nan)


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
):
    fast_ema = series.ewm(
        span=fast,
        adjust=False,
        min_periods=fast,
    ).mean()

    slow_ema = series.ewm(
        span=slow,
        adjust=False,
        min_periods=slow,
    ).mean()

    macd_line = fast_ema - slow_ema

    signal_line = macd_line.ewm(
        span=signal,
        adjust=False,
        min_periods=signal,
    ).mean()

    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def generate_signals(
    data: pd.DataFrame,
    config: dict,
    return_stats: bool = False,
):
    """
    V3 trend-pullback strategy.

    LONG ENTRY:

        1. ADX says the market is actually trending.
        2. +DI > -DI confirms bullish directional pressure.
        3. Fast SMA > slow SMA confirms trend.
        4. Price > VWAP confirms intraday strength.
        5. RSI recently pulled back.
        6. RSI is now recovering.
        7. MACD histogram is positive and improving.
        8. ATR is within acceptable volatility bounds.

    EXIT:

        Trend / momentum reversal.

    The strategy remains long-only.
    """

    df = data.copy()

    # -------------------------
    # Indicators
    # -------------------------

    df["sma_fast"] = df["close"].rolling(
        config["sma_fast"]
    ).mean()

    df["sma_slow"] = df["close"].rolling(
        config["sma_slow"]
    ).mean()

    df["rsi"] = rsi(
        df["close"],
        config["rsi_period"],
    )

    df["vwap"] = vwap(df)

    df["atr"] = atr(
        df,
        config["atr_period"],
    )

    (
        df["adx"],
        df["plus_di"],
        df["minus_di"],
    ) = adx(
        df,
        config["adx_period"],
    )

    (
        df["macd"],
        df["macd_signal"],
        df["macd_hist"],
    ) = macd(
        df["close"],
        config["macd_fast"],
        config["macd_slow"],
        config["macd_signal"],
    )

    # -------------------------
    # Latest / previous bars
    # -------------------------

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    price = float(latest["close"])

    sma_fast = float(latest["sma_fast"])
    sma_slow = float(latest["sma_slow"])

    rsi_now = float(latest["rsi"])
    rsi_prev = float(previous["rsi"])

    vwap_now = float(latest["vwap"])

    atr_now = float(latest["atr"])

    adx_now = float(latest["adx"])
    plus_di = float(latest["plus_di"])
    minus_di = float(latest["minus_di"])

    macd_hist = float(latest["macd_hist"])
    macd_hist_prev = float(previous["macd_hist"])

    # -------------------------
    # NaN guard
    # -------------------------

    values = [
        sma_fast,
        sma_slow,
        rsi_now,
        rsi_prev,
        vwap_now,
        atr_now,
        adx_now,
        plus_di,
        minus_di,
        macd_hist,
        macd_hist_prev,
    ]

    if any(pd.isna(x) for x in values):

        stats = {
            "sma_f": sma_fast,
            "sma_s": sma_slow,
            "rsi": rsi_now,
            "vwap": vwap_now,
            "atr": atr_now,
            "adx": adx_now,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "macd_hist": macd_hist,
            "atr_pct": np.nan,
        }

        return (None, stats) if return_stats else None

    # -------------------------
    # Trend / regime
    # -------------------------

    trending = adx_now >= config["adx_min"]

    bullish_direction = (
        plus_di > minus_di
        and (plus_di - minus_di)
        >= config["min_di_spread"]
    )

    bearish_direction = (
        minus_di > plus_di
        and (minus_di - plus_di)
        >= config["min_di_spread"]
    )

    uptrend = sma_fast > sma_slow
    downtrend = sma_fast < sma_slow

    above_vwap = price > vwap_now
    below_vwap = price < vwap_now

    # -------------------------
    # RSI pullback
    # -------------------------

    rsi_was_low = (
        rsi_prev <= config["rsi_pullback_max"]
    )

    rsi_recovering = (
        rsi_now > rsi_prev
        and rsi_now >= config["rsi_recovery_min"]
    )

    bullish_rsi = (
        rsi_was_low
        and rsi_recovering
    )

    bearish_rsi = (
        rsi_prev >= config["rsi_sell_min"]
        and rsi_now < rsi_prev
    )

    # -------------------------
    # MACD momentum
    # -------------------------

    bullish_macd = (
        macd_hist > 0
        and macd_hist > macd_hist_prev
    )

    bearish_macd = (
        macd_hist < 0
        and macd_hist < macd_hist_prev
    )

    # -------------------------
    # Volatility filter
    # -------------------------

    atr_pct = atr_now / price

    volatility_ok = (
        config["min_atr_pct"]
        <= atr_pct
        <= config["max_atr_pct"]
    )

    # -------------------------
    # Signals
    # -------------------------

    buy_conditions = [
        trending,
        bullish_direction,
        uptrend,
        above_vwap,
        bullish_rsi,
        bullish_macd,
        volatility_ok,
    ]

    sell_conditions = [
        downtrend,
        bearish_direction,
        below_vwap,
        bearish_rsi,
        bearish_macd,
    ]

    bullish_score = sum(bool(x) for x in buy_conditions)
    bearish_score = sum(bool(x) for x in sell_conditions)

    signal = None

    if bullish_score >= config["buy_signal_threshold"]:
        signal = "buy"

    elif bearish_score >= config["sell_signal_threshold"]:
        signal = "sell"

    stats = {
        "sma_f": sma_fast,
        "sma_s": sma_slow,
        "rsi": rsi_now,
        "vwap": vwap_now,
        "atr": atr_now,
        "atr_pct": atr_pct,
        "adx": adx_now,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "macd_hist": macd_hist,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
    }

    return (signal, stats) if return_stats else signal
