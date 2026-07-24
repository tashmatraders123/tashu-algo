"""
strategy.py
1-minute scalping strategy:

  - EMA(fast) / EMA(slow) crossover decides trade direction
  - RSI filter avoids chasing overbought/oversold extremes
  - ATR sets the stop-loss / take-profit distance so exits scale with
    current volatility instead of being a fixed number of points

This is a simple, well-known style of scalping logic (trend + momentum
filter + volatility-based exits). It is a starting point, not a proven
profitable system -- backtest and paper-trade before risking real money.
"""
import pandas as pd
import numpy as np

import config


def candles_to_df(candles):
    df = pd.DataFrame(candles)
    if df.empty:
        return df
    df = df.rename(columns={"time": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.set_index("timestamp").sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def atr(df, period):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df):
    df = df.copy()
    df["ema_fast"] = ema(df["close"], config.EMA_FAST)
    df["ema_slow"] = ema(df["close"], config.EMA_SLOW)
    df["rsi"] = rsi(df["close"], config.RSI_PERIOD)
    df["atr"] = atr(df, config.ATR_PERIOD)
    return df


def generate_signal(df):
    """
    Two modes, controlled by config.STRATEGY_MODE:

    "trend" (default) -- fires on EVERY closed candle where price and both
        EMAs are aligned with a trend and RSI confirms momentum. This trades
        much more often than a pure crossover, following the trend for as
        long as it holds (a new entry follows shortly after each exit if
        the trend is still intact).

    "crossover" -- only fires the moment the fast EMA freshly crosses the
        slow EMA. Fewer, more selective signals.

    Returns one of: "long", "short", None, plus the ATR value at the
    signal candle (used for SL/TP sizing).
    """
    if len(df) < max(config.EMA_SLOW, config.RSI_PERIOD, config.ATR_PERIOD) + 2:
        return None, None

    last = df.iloc[-1]

    if config.STRATEGY_MODE == "crossover":
        prev = df.iloc[-2]
        crossed_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
        crossed_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]

        if crossed_up and config.RSI_LONG_MIN <= last["rsi"] <= config.RSI_LONG_MAX:
            return "long", last["atr"]
        if crossed_down and config.RSI_SHORT_MIN <= last["rsi"] <= config.RSI_SHORT_MAX:
            return "short", last["atr"]
        return None, None

    # --- "trend" mode (default) ---
    uptrend = last["close"] > last["ema_fast"] > last["ema_slow"]
    downtrend = last["close"] < last["ema_fast"] < last["ema_slow"]

    if uptrend and config.RSI_LONG_MIN <= last["rsi"] <= config.RSI_LONG_MAX:
        return "long", last["atr"]
    if downtrend and config.RSI_SHORT_MIN <= last["rsi"] <= config.RSI_SHORT_MAX:
        return "short", last["atr"]

    return None, None


def compute_sl_tp(entry_price, direction, atr_value):
    sl_dist = atr_value * config.SL_ATR_MULT
    tp_dist = atr_value * config.TP_ATR_MULT
    if direction == "long":
        return entry_price - sl_dist, entry_price + tp_dist
    else:
        return entry_price + sl_dist, entry_price - tp_dist
