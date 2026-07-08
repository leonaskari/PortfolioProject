"""
Entry criteria for the trend-following strategy.

Each rule is an independent, testable function that takes a DataFrame of
OHLCV data and config, and returns a dict with:
    - passed: bool
    - confidence: float (0.0 to 1.0)
    - details: str (plain-English explanation)

The composite function `evaluate_entry` combines all rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from config import ENTRY
from strategy.indicators import (
    atr,
    average_volume,
    breakout_detected,
    golden_cross_aligned,
    price_above_sma,
    relative_strength,
    sma,
    volatility_contraction,
    volume_surge,
)


@dataclass
class RuleResult:
    """Result of evaluating a single entry rule."""
    rule_name: str
    passed: bool
    confidence: float  # 0.0 - 1.0
    details: str


def rule_trend_long(df: pd.DataFrame, cfg=ENTRY) -> RuleResult:
    """Rule 1: Current price > SMA200 (long-term uptrend)."""
    sma200 = sma(df["Close"], cfg.sma_long_period)
    current_price = df["Close"].iloc[-1]
    current_sma200 = sma200.iloc[-1]

    if pd.isna(current_sma200):
        return RuleResult("Trend (Long)", False, 0.0,
                          f"Insufficient data for SMA{cfg.sma_long_period}")

    passed = current_price > current_sma200
    pct_above = (current_price / current_sma200 - 1) * 100
    confidence = min(1.0, max(0.0, pct_above / 10)) if passed else 0.0

    return RuleResult(
        f"Trend (Long) — Price > SMA{cfg.sma_long_period}",
        passed,
        confidence,
        f"Price ${current_price:.2f} is {pct_above:+.1f}% vs SMA{cfg.sma_long_period} ${current_sma200:.2f}. "
        f"{'PASS' if passed else 'FAIL'}: {'Above' if passed else 'Below'} long-term trend.",
    )


def rule_trend_medium(df: pd.DataFrame, cfg=ENTRY) -> RuleResult:
    """Rule 2: SMA50 > SMA200 (golden-cross alignment)."""
    sma50 = sma(df["Close"], cfg.sma_short_period)
    sma200 = sma(df["Close"], cfg.sma_long_period)

    current_sma50 = sma50.iloc[-1]
    current_sma200 = sma200.iloc[-1]

    if pd.isna(current_sma50) or pd.isna(current_sma200):
        return RuleResult("Trend (Medium)", False, 0.0,
                          f"Insufficient data for SMA{cfg.sma_short_period} / SMA{cfg.sma_long_period}")

    passed = current_sma50 > current_sma200
    gap_pct = (current_sma50 / current_sma200 - 1) * 100
    confidence = min(1.0, max(0.0, gap_pct / 5)) if passed else 0.0

    return RuleResult(
        f"Trend (Medium) — SMA{cfg.sma_short_period} > SMA{cfg.sma_long_period}",
        passed,
        confidence,
        f"SMA{cfg.sma_short_period} ${current_sma50:.2f} vs SMA{cfg.sma_long_period} ${current_sma200:.2f} "
        f"(gap {gap_pct:+.1f}%). {'PASS' if passed else 'FAIL'}: "
        f"{'Golden-cross aligned' if passed else 'Not aligned'}.",
    )


def rule_consolidation(df: pd.DataFrame, cfg=ENTRY) -> RuleResult:
    """Rule 3: Volatility contraction — recent ATR < ratio * prior ATR."""
    atr_series = atr(df, cfg.atr_period)

    recent_atr = atr_series.rolling(window=cfg.consolidation_lookback).mean().iloc[-1]
    prior_atr = atr_series.rolling(window=cfg.prior_lookback).mean().iloc[-1]

    if pd.isna(recent_atr) or pd.isna(prior_atr) or prior_atr == 0:
        return RuleResult("Consolidation (Volatility Contraction)", False, 0.0,
                          f"Insufficient ATR data.")

    passed = recent_atr < cfg.atr_contraction_ratio * prior_atr
    ratio = recent_atr / prior_atr if prior_atr > 0 else 999
    contraction_pct = (1 - ratio) * 100 if ratio < 1 else (1 - ratio) * 100

    confidence = min(1.0, max(0.0, (cfg.atr_contraction_ratio - ratio) * 3)) if passed else 0.0

    return RuleResult(
        f"Consolidation — ATR Contraction (ATR{cfg.consolidation_lookback} / ATR{cfg.prior_lookback})",
        passed,
        confidence,
        f"Recent ATR ${recent_atr:.2f} vs Prior ATR ${prior_atr:.2f} (ratio {ratio:.2f}, "
        f"threshold {cfg.atr_contraction_ratio}). "
        f"{'PASS' if passed else 'FAIL'}: "
        f"{'Stock is consolidating (tight range)' if passed else 'No significant contraction'}.",
    )


def rule_resistance_breakout(df: pd.DataFrame, cfg=ENTRY) -> RuleResult:
    """Rule 4: Price closes above the highest high of the consolidation window."""
    if len(df) < cfg.breakout_lookback + 5:
        return RuleResult("Resistance Breakout", False, 0.0,
                          f"Insufficient data for {cfg.breakout_lookback}-day lookback.")

    breakout = breakout_detected(df, cfg.breakout_lookback)
    current_breakout = breakout.iloc[-1]

    highest_high_val = df["High"].shift(1).rolling(window=cfg.breakout_lookback).max().iloc[-1]
    current_close = df["Close"].iloc[-1]

    passed = bool(current_breakout) if not pd.isna(current_breakout) else False

    if passed:
        pct_above = (current_close / highest_high_val - 1) * 100 if highest_high_val > 0 else 0
        confidence = min(1.0, max(0.1, pct_above / 3))
    else:
        pct_above = ((current_close / highest_high_val) - 1) * 100 if highest_high_val and highest_high_val > 0 else 0
        confidence = 0.0

    return RuleResult(
        f"Resistance Breakout — Close > {cfg.breakout_lookback}-day High",
        passed,
        confidence,
        f"Close ${current_close:.2f} vs {cfg.breakout_lookback}-day high ${highest_high_val:.2f} "
        f"({pct_above:+.1f}%). {'PASS' if passed else 'FAIL'}: "
        f"{'Breakout confirmed' if passed else 'Below resistance'}.",
    )


def rule_volume_confirmation(df: pd.DataFrame, cfg=ENTRY) -> RuleResult:
    """Rule 5: Breakout day volume > multiplier × average volume."""
    avg_vol = average_volume(df["Volume"], cfg.vol_avg_period)
    current_vol = df["Volume"].iloc[-1]
    current_avg_vol = avg_vol.iloc[-1]

    if pd.isna(current_avg_vol) or current_avg_vol == 0:
        return RuleResult("Volume Confirmation", False, 0.0,
                          f"Insufficient volume data.")

    passed = current_vol > cfg.vol_multiplier * current_avg_vol
    vol_ratio = current_vol / current_avg_vol if current_avg_vol > 0 else 0
    confidence = min(1.0, max(0.0, (vol_ratio - cfg.vol_multiplier) / 2)) if passed else 0.0

    return RuleResult(
        f"Volume Confirmation — Vol > {cfg.vol_multiplier}x Avg({cfg.vol_avg_period})",
        passed,
        confidence,
        f"Volume {current_vol:,.0f} vs Avg {current_avg_vol:,.0f} (ratio {vol_ratio:.1f}x, "
        f"threshold {cfg.vol_multiplier}x). "
        f"{'PASS' if passed else 'FAIL'}: "
        f"{'Volume surge confirmed' if passed else 'Volume too low'}.",
    )


def rule_relative_strength(
    df: pd.DataFrame,
    bench_df: pd.DataFrame,
    cfg=ENTRY,
) -> RuleResult:
    """
    Rule 6: Relative strength vs benchmark (optional — ranking, not hard gate).

    Returns PASS with a confidence score based on RS ratio.
    Always passes by default; the confidence is used for ranking.
    """
    rs_score = relative_strength(
        df["Close"], bench_df["Close"], lookback=cfg.rs_lookback_months * 21
    )

    # Normalise confidence: RS > 1.3 -> 1.0, RS < 0.7 -> 0.0
    confidence = max(0.0, min(1.0, (rs_score - 0.7) / 0.6))

    return RuleResult(
        f"Relative Strength vs {cfg.rs_benchmark} ({cfg.rs_lookback_months}mo)",
        True,  # always passes — used for ranking, not gating
        confidence,
        f"RS ratio {rs_score:.2f}x vs {cfg.rs_benchmark}. "
        f"Confidence: {confidence:.0%}. "
        "This is a ranking signal, not a hard gate.",
    )


@dataclass
class CompositeEntryResult:
    """Result of evaluating all entry rules."""
    passed: bool                          # True only if ALL hard rules pass
    confidence: float                     # weighted composite confidence (0-1)
    rule_results: list[RuleResult] = field(default_factory=list)
    summary: str = ""


HARD_RULES = [rule_trend_long, rule_trend_medium, rule_consolidation,
              rule_resistance_breakout, rule_volume_confirmation]
SOFT_RULES = [rule_relative_strength]


def evaluate_entry(
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None = None,
    cfg=ENTRY,
) -> CompositeEntryResult:
    """
    Run all entry rules against a stock's OHLCV DataFrame.

    Args:
        df: OHLCV DataFrame with at least cfg.sma_long_period + buffer rows.
        bench_df: Optional benchmark OHLCV for relative strength.
        cfg: EntryConfig dataclass.

    Returns:
        CompositeEntryResult with pass/fail, confidence, and per-rule details.
    """
    results: list[RuleResult] = []

    for rule_fn in HARD_RULES:
        result = rule_fn(df, cfg)
        results.append(result)

    if bench_df is not None:
        rs_result = rule_relative_strength(df, bench_df, cfg)
        results.append(rs_result)

    hard_passed = all(r.passed for r in results if r.rule_name != "Relative Strength")
    hard_confidences = [r.confidence for r in results if r.rule_name != "Relative Strength"]

    # Soft rules add to confidence but don't gate
    soft_confidences = [r.confidence for r in results if r.rule_name == "Relative Strength"]

    # Composite confidence: weighted average of hard rules (80%) + soft (20%)
    if hard_confidences:
        hard_avg = np.mean(hard_confidences)
    else:
        hard_avg = 0.0

    if soft_confidences:
        soft_avg = np.mean(soft_confidences)
    else:
        soft_avg = 0.0

    composite_confidence = 0.8 * hard_avg + 0.2 * soft_avg

    # Build summary
    passed_count = sum(1 for r in results if r.passed)
    total_rules = len(results)
    summary = (
        f"Rules passed: {passed_count}/{total_rules} | "
        f"Confidence: {composite_confidence:.0%} | "
        f"{'ENTRY SIGNAL' if hard_passed else 'No entry — hard rules not met'}"
    )

    return CompositeEntryResult(
        passed=hard_passed,
        confidence=composite_confidence,
        rule_results=results,
        summary=summary,
    )