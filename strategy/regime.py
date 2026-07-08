"""
Market regime filter — protects the bot from trading in unfavorable conditions.

The single most important filter: trend-following systems get destroyed in
choppy/declining markets. A simple gate checking whether the S&P 500 is
above its own 200-day MA eliminates most of the worst drawdown periods.

Usage:
    from strategy.regime import check_market_regime, is_healthy_trend
    regime = check_market_regime(bench_df)
    if regime.is_favorable:
        # proceed with screening
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import REGIME
from strategy.indicators import sma

logger = logging.getLogger(__name__)


@dataclass
class RegimeResult:
    """Result of market regime analysis."""
    is_favorable: bool
    regime: str  # "bull_trend", "correction", "bear_market", "choppy"
    sp500_vs_sma200_pct: float  # how far S&P is from its 200-day MA
    sp500_current: float
    sp500_sma200: float
    vix_level: float | None = None
    vix_sma50: float | None = None
    summary: str = ""


def check_market_regime(
    bench_df: pd.DataFrame | None,
    vix_df: pd.DataFrame | None = None,
    cfg: Any = None,
) -> RegimeResult:
    """
    Evaluate the current market regime based on S&P 500 position vs 200-MA.

    Args:
        bench_df: OHLCV DataFrame for ^GSPC (S&P 500).
        vix_df: Optional OHLCV DataFrame for ^VIX (volatility index).
        cfg: RegimeConfig (falls back to config.REGIME).

    Returns:
        RegimeResult with is_favorable (True = safe to trade), regime label,
        and detailed metrics.
    """
    if cfg is None:
        cfg = REGIME

    # Default: assume unfavorable until proven otherwise
    result = RegimeResult(
        is_favorable=False,
        regime="unknown",
        sp500_vs_sma200_pct=0.0,
        sp500_current=0.0,
        sp500_sma200=0.0,
        summary="No benchmark data available — defaulting to cautious.",
    )

    if bench_df is None or bench_df.empty:
        logger.warning("No benchmark data for regime filter — defaulting to cautious")
        return result

    close = bench_df["Close"]
    if len(close) < cfg.sma_period + 10:
        logger.warning("Insufficient benchmark data for regime filter")
        return result

    current_price = float(close.iloc[-1])
    sma200 = sma(close, cfg.sma_period)
    current_sma200 = float(sma200.iloc[-1])

    if pd.isna(current_sma200) or current_sma200 == 0:
        return result

    # How far is S&P from its 200-day MA?
    pct_vs_sma = (current_price / current_sma200 - 1) * 100

    # Determine regime
    if pct_vs_sma > cfg.bull_threshold:
        regime = "bull_trend"
        is_favorable = True
    elif pct_vs_sma > cfg.correction_threshold:
        regime = "correction"
        is_favorable = cfg.allow_correction
    else:
        regime = "bear_market"
        is_favorable = False

    # VIX filter (if available) — high VIX means fear/choppiness
    vix_level = None
    vix_sma50 = None
    if vix_df is not None and not vix_df.empty:
        vix_close = vix_df["Close"]
        vix_level = float(vix_close.iloc[-1])
        if len(vix_close) > 50:
            vix_sma50 = float(sma(vix_close, 50).iloc[-1])
            if vix_level > cfg.vix_threshold:
                is_favorable = False
                regime = "choppy"

    parts = [
        f"S&P 500: {current_price:,.0f} vs SMA{cfg.sma_period}: {current_sma200:,.0f} "
        f"({pct_vs_sma:+.1f}%)",
    ]
    if vix_level is not None and vix_sma50 is not None:
        parts.append(f"VIX: {vix_level:.1f} (SMA50: {vix_sma50:.1f})")
    parts.append(f"Regime: {regime}")
    parts.append("✅ Favorable for trend-following" if is_favorable else "❌ Unfavorable — trading disabled")

    return RegimeResult(
        is_favorable=is_favorable,
        regime=regime,
        sp500_vs_sma200_pct=pct_vs_sma,
        sp500_current=current_price,
        sp500_sma200=current_sma200,
        vix_level=vix_level,
        vix_sma50=vix_sma50,
        summary=" | ".join(parts),
    )


def is_healthy_trend(
    bench_df: pd.DataFrame | None,
    vix_df: pd.DataFrame | None = None,
    cfg: Any = None,
) -> bool:
    """
    Quick gate: is the market in a healthy trend for this strategy?

    Returns True only if the S&P 500 is above its 200-day MA by at least
    the minimum threshold (default 0% = simply above). Use this as a
    pre-check before running any screens.
    """
    result = check_market_regime(bench_df, vix_df, cfg)
    return result.is_favorable