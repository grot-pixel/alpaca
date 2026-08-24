from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# RSI
# ============================================================

def rsi(
    series: pd.Series,
    period: int = 14,
) -> pd.Series:
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = (
        avg_gain
        / avg_loss.replace(0, np.nan)
    )

    result = 100 - (
        100 / (1 + rs)
    )

    # A continuously rising series has no losses,
    # therefore RSI should be 100 rather than NaN.
    result = result.where(
        ~(
            (avg_loss == 0)
            & (avg_gain > 0)
        ),
        100.0,
    )

    return result


# ============================================================
# ATR
# ============================================================

def atr(
    data: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    high = data["high"]
    low = data["low"]
    close = data["close"]

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


# ============================================================
# ADX / DI
# ============================================================

def adx(
    data: pd.DataFrame,
    period: int = 14,
):
    high = data["high"]
    low = data["low"]
    close = data["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where(
        (up_move > down_move)
        & (up_move > 0),
        0.0,
    )

    minus_dm = down_move.where(
        (down_move > up_move)
        & (down_move > 0),
        0.0,
    )

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_value = true_range.ewm(
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

    di_sum = (
        plus_di + minus_di
    ).replace(0, np.nan)

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

    return (
        adx_value,
        plus_di,
        minus_di,
    )


# ============================================================
# SESSION VWAP
# ============================================================

def vwap(
    data: pd.DataFrame,
) -> pd.Series:
    """
    Session VWAP.

    VWAP resets at each New York calendar session.
    """

    df = data.copy()

    typical_price = (
        df["high"]
        + df["low"]
        + df["close"]
    ) / 3.0

    volume_price = (
        typical_price
        * df["volume"]
    )

    if isinstance(
        df.index,
        pd.DatetimeIndex,
    ):
        if df.index.tz is None:
            index = df.index.tz_localize(
                "UTC"
            )
        else:
            index = df.index

        session = pd.Series(
            index.tz_convert(
                "America/New_York"
            ).date,
            index=df.index,
        )
    else:
        session = pd.Series(
            pd.to_datetime(
                df.index,
                utc=True,
            )
            .tz_convert(
                "America/New_York"
            )
            .date,
            index=df.index,
        )

    cumulative_pv = (
        volume_price
        .groupby(session)
        .cumsum()
    )

    cumulative_volume = (
        df["volume"]
        .groupby(session)
        .cumsum()
    )

    return (
        cumulative_pv
        / cumulative_volume.replace(
            0,
            np.nan,
        )
    )


# ============================================================
# MACD
# ============================================================

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

    macd_line = (
        fast_ema - slow_ema
    )

    signal_line = macd_line.ewm(
        span=signal,
        adjust=False,
        min_periods=signal,
    ).mean()

    histogram = (
        macd_line - signal_line
    )

    return (
        macd_line,
        signal_line,
        histogram,
    )


# ============================================================
# SIGNAL ENGINE
# ============================================================

def generate_signals(
    data: pd.DataFrame,
    config: dict,
    return_stats: bool = False,
):
    """
    V4 trend-pullback strategy.

    BUY conditions:
        1. ADX trend confirmation
        2. +DI directional confirmation
        3. SMA trend
        4. Price above VWAP
        5. RSI pullback/recovery
        6. MACD positive and improving
        7. ATR volatility filter

    SELL conditions:
        1. SMA downtrend
        2. -DI directional confirmation
        3. Price below VWAP
        4. RSI rolling over
        5. MACD negative and deteriorating

    This remains long-only.
    """

    if data is None or data.empty:
        raise ValueError(
            "No market data supplied"
        )

    if len(data) < 3:
        raise ValueError(
            "At least 3 bars are required"
        )

    df = data.copy()

    required_columns = {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    missing = (
        required_columns
        - set(df.columns)
    )

    if missing:
        raise ValueError(
            "Missing market-data columns: "
            + ", ".join(sorted(missing))
        )

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

    df["sma_fast"] = (
        df["close"]
        .rolling(
            config["sma_fast"]
        )
        .mean()
    )

    df["sma_slow"] = (
        df["close"]
        .rolling(
            config["sma_slow"]
        )
        .mean()
    )

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

    # --------------------------------------------------------
    # LATEST DATA
    # --------------------------------------------------------

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    price = float(
        latest["close"]
    )

    sma_fast = float(
        latest["sma_fast"]
    )

    sma_slow = float(
        latest["sma_slow"]
    )

    rsi_now = float(
        latest["rsi"]
    )

    rsi_prev = float(
        previous["rsi"]
    )

    vwap_now = float(
        latest["vwap"]
    )

    atr_now = float(
        latest["atr"]
    )

    adx_now = float(
        latest["adx"]
    )

    plus_di = float(
        latest["plus_di"]
    )

    minus_di = float(
        latest["minus_di"]
    )

    macd_hist = float(
        latest["macd_hist"]
    )

    macd_hist_prev = float(
        previous["macd_hist"]
    )

    # --------------------------------------------------------
    # NAN / INVALID DATA GUARD
    # --------------------------------------------------------

    values = [
        price,
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

    if any(
        pd.isna(value)
        or not np.isfinite(value)
        for value in values
    ):
        stats = {
            "price": price,
            "sma_f": sma_fast,
            "sma_s": sma_slow,
            "rsi": rsi_now,
            "vwap": vwap_now,
            "atr": atr_now,
            "atr_pct": np.nan,
            "adx": adx_now,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "macd_hist": macd_hist,
            "bullish_score": 0,
            "bearish_score": 0,
            "buy_threshold": config[
                "buy_signal_threshold"
            ],
            "sell_threshold": config[
                "sell_signal_threshold"
            ],
            "data_ready": False,
        }

        return (
            (None, stats)
            if return_stats
            else None
        )

    # --------------------------------------------------------
    # TREND / REGIME
    # --------------------------------------------------------

    trending = (
        adx_now
        >= config["adx_min"]
    )

    bullish_direction = (
        plus_di > minus_di
        and (
            plus_di - minus_di
        ) >= config["min_di_spread"]
    )

    bearish_direction = (
        minus_di > plus_di
        and (
            minus_di - plus_di
        ) >= config["min_di_spread"]
    )

    uptrend = (
        sma_fast > sma_slow
    )

    downtrend = (
        sma_fast < sma_slow
    )

    above_vwap = (
        price > vwap_now
    )

    below_vwap = (
        price < vwap_now
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_was_low = (
        rsi_prev
        <= config["rsi_pullback_max"]
    )

    rsi_recovering = (
        rsi_now > rsi_prev
        and rsi_now
        >= config["rsi_recovery_min"]
    )

    bullish_rsi = (
        rsi_was_low
        and rsi_recovering
    )

    bearish_rsi = (
        rsi_prev
        >= config["rsi_sell_min"]
        and rsi_now < rsi_prev
    )

    # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    bullish_macd = (
        macd_hist > 0
        and macd_hist
        > macd_hist_prev
    )

    bearish_macd = (
        macd_hist < 0
        and macd_hist
        < macd_hist_prev
    )

    # --------------------------------------------------------
    # VOLATILITY
    # --------------------------------------------------------

    atr_pct = (
        atr_now / price
    )

    volatility_ok = (
        config["min_atr_pct"]
        <= atr_pct
        <= config["max_atr_pct"]
    )

    # --------------------------------------------------------
    # SCORES
    # --------------------------------------------------------

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

    bullish_score = sum(
        bool(value)
        for value in buy_conditions
    )

    bearish_score = sum(
        bool(value)
        for value in sell_conditions
    )

    signal = None

    if (
        bullish_score
        >= config["buy_signal_threshold"]
    ):
        signal = "buy"

    elif (
        bearish_score
        >= config["sell_signal_threshold"]
    ):
        signal = "sell"

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    stats = {
        "price": price,
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
        "buy_threshold": config[
            "buy_signal_threshold"
        ],
        "sell_threshold": config[
            "sell_signal_threshold"
        ],
        "data_ready": True,
        "conditions": {
            "trending": trending,
            "bullish_direction": bullish_direction,
            "uptrend": uptrend,
            "above_vwap": above_vwap,
            "bullish_rsi": bullish_rsi,
            "bullish_macd": bullish_macd,
            "volatility_ok": volatility_ok,
            "downtrend": downtrend,
            "bearish_direction": bearish_direction,
            "below_vwap": below_vwap,
            "bearish_rsi": bearish_rsi,
            "bearish_macd": bearish_macd,
        },
    }

    return (
        (signal, stats)
        if return_stats
        else signal
    )
