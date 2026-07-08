"""
Technical indicator library for the trend-following screener.

All functions operate on pandas DataFrames with columns:
    'Open', 'High', 'Low', 'Close', 'Volume'

Each function is pure (no side effects) and returns a Series or scalar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (Wilder smoothing).

    Returns a Series of ATR values aligned to the input DataFrame index.
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"].shift(1)

    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothed ATR
    atr_series = true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr_series


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series


def average_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """Simple moving average of volume."""
    return volume.rolling(window=period).mean()


def highest_high(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Rolling highest high over the given period."""
    return df["High"].rolling(window=period).max()


def lowest_low(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Rolling lowest low over the given period."""
    return df["Low"].rolling(window=period).min()


def volatility_contraction(
    df: pd.DataFrame,
    atr_series: pd.Series,
    consolidation_lookback: int = 20,
    prior_lookback: int = 50,
    contraction_ratio: float = 0.7,
) -> pd.Series:
    """
    Returns a boolean Series: True where the recent ATR is a contraction
    relative to the prior period's ATR.

    ATR(consolidation_lookback) < contraction_ratio * ATR(prior_lookback)
    """
    recent_atr = atr_series.rolling(window=consolidation_lookback).mean()
    prior_atr = atr_series.rolling(window=prior_lookback).mean()
    return recent_atr < contraction_ratio * prior_atr


def relative_strength(
    price_series: pd.Series,
    benchmark_series: pd.Series,
    lookback: int = 126,  # ~6 trading months
) -> float:
    """
    Compute a simple relative strength score: the ratio of total return
    of the stock to total return of the benchmark over the lookback period.

    Returns a float where > 1.0 means the stock outperformed the benchmark.
    """
    if len(price_series) < lookback or len(benchmark_series) < lookback:
        return 1.0

    stock_return = price_series.iloc[-1] / price_series.iloc[-lookback] - 1
    bench_return = benchmark_series.iloc[-1] / benchmark_series.iloc[-lookback] - 1

    if bench_return == 0:
        return 1.0

    return stock_return / bench_return


def price_above_sma(price: pd.Series, sma_series: pd.Series) -> pd.Series:
    """Boolean Series: True where price > SMA."""
    return price > sma_series


def golden_cross_aligned(
    sma_short: pd.Series, sma_long: pd.Series
) -> pd.Series:
    """Boolean Series: True where SMA_short > SMA_long (golden-cross alignment)."""
    return sma_short > sma_long


def breakout_detected(
    df: pd.DataFrame,
    breakout_lookback: int = 20,
) -> pd.Series:
    """
    Boolean Series: True where Close > highest high of the last N days
    (excluding today for the lookback).
    """
    highest = df["High"].shift(1).rolling(window=breakout_lookback).max()
    return df["Close"] > highest


def volume_surge(
    volume: pd.Series,
    avg_volume: pd.Series,
    multiplier: float = 1.5,
) -> pd.Series:
    """Boolean Series: True where volume > multiplier * average volume."""
    return volume > multiplier * avg_volume