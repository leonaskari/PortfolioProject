"""
Exit criteria for the trend-following strategy.

Each rule is an independent, testable function that takes position context
and current market data, and returns a dict with:
    - triggered: bool
    - action: "HOLD" | "TRIM" | "SELL"
    - reason: str (which rule triggered and why)

The composite function `evaluate_exit` checks all rules and returns the
most severe action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from config import EXIT, RISK
from strategy.indicators import atr, sma, relative_strength


@dataclass
class ExitRuleResult:
    """Result of evaluating a single exit rule."""
    rule_name: str
    triggered: bool
    action: Literal["HOLD", "TRIM", "SELL"]
    details: str


@dataclass
class PositionContext:
    """Context about an open position needed for exit evaluation."""
    ticker: str
    entry_price: float
    quantity: int
    current_price: float
    breakout_level: float | None = None  # the resistance level that was broken
    entry_atr: float | None = None       # ATR at time of entry


def rule_support_break(
    df: pd.DataFrame,
    pos: PositionContext,
    cfg=EXIT,
) -> ExitRuleResult:
    """
    Exit Rule 1: Price closes below support level.

    Support is either the breakout level or SMA50, configured via
    EXIT.support_type.
    """
    current_price = df["Close"].iloc[-1]

    if cfg.support_type == "breakout_level" and pos.breakout_level is not None:
        support = pos.breakout_level
        support_desc = f"breakout level (${support:.2f})"
    else:
        sma50 = sma(df["Close"], cfg.sma_short_period)
        support = sma50.iloc[-1]
        support_desc = f"SMA{cfg.sma_short_period} (${support:.2f})"

    if pd.isna(support) or support == 0:
        return ExitRuleResult("Support Break", False, "HOLD",
                              f"Cannot evaluate — support level unavailable.")

    triggered = current_price < support
    pct_below = (current_price / support - 1) * 100

    if triggered:
        return ExitRuleResult(
            "Support Break",
            True,
            "SELL",
            f"Price ${current_price:.2f} closed {pct_below:.1f}% below {support_desc}. "
            f"Trend-failure exit triggered.",
        )

    return ExitRuleResult(
        "Support Break", False, "HOLD",
        f"Price ${current_price:.2f} is {abs(pct_below):.1f}% above {support_desc}. Support holds.",
    )


def rule_stop_loss(
    df: pd.DataFrame,
    pos: PositionContext,
    cfg=EXIT,
    risk_cfg=RISK,
) -> ExitRuleResult:
    """
    Exit Rule 2: Price hits the stop-loss level calculated at entry.

    Stop-loss = entry_price - stop_atr_mult * entry_atr
    """
    if pos.entry_atr is None or pos.entry_atr == 0:
        # Fallback: use current ATR
        current_atr = atr(df, 14).iloc[-1]
        stop_price = pos.entry_price - risk_cfg.stop_atr_mult * current_atr
        stop_desc = f"dynamic ATR-based (${stop_price:.2f})"
    else:
        stop_price = pos.entry_price - risk_cfg.stop_atr_mult * pos.entry_atr
        stop_desc = f"entry ATR-based (${stop_price:.2f})"

    current_price = df["Close"].iloc[-1]
    triggered = current_price <= stop_price
    pct_loss = (current_price / pos.entry_price - 1) * 100

    if triggered:
        return ExitRuleResult(
            "Stop-Loss Hit",
            True,
            "SELL",
            f"Price ${current_price:.2f} hit stop-loss ${stop_price:.2f} "
            f"(entry ${pos.entry_price:.2f}, loss {pct_loss:.1f}%). "
            f"Stop-loss exit triggered.",
        )

    return ExitRuleResult(
        "Stop-Loss Hit", False, "HOLD",
        f"Price ${current_price:.2f} above stop ${stop_price:.2f}. Stop holds.",
    )


def rule_profit_target(
    df: pd.DataFrame,
    pos: PositionContext,
    cfg=EXIT,
    risk_cfg=RISK,
) -> ExitRuleResult:
    """
    Exit Rule 3: Price hits profit target (≥ reward_risk_min:1 R:R).

    If price ≥ entry + reward_risk_min * (entry - stop), recommend TRIM.
    """
    if pos.entry_atr is None or pos.entry_atr == 0:
        current_atr = atr(df, 14).iloc[-1]
        risk_amount = risk_cfg.stop_atr_mult * current_atr
    else:
        risk_amount = risk_cfg.stop_atr_mult * pos.entry_atr

    target_price = pos.entry_price + risk_cfg.min_reward_risk * risk_amount
    current_price = df["Close"].iloc[-1]
    triggered = current_price >= target_price

    if triggered:
        r_multiple = (current_price - pos.entry_price) / risk_amount if risk_amount > 0 else 0
        return ExitRuleResult(
            "Profit Target Hit",
            True,
            "TRIM",
            f"Price ${current_price:.2f} hit target ${target_price:.2f} "
            f"(R multiple: {r_multiple:.1f}). "
            f"Recommend trimming {cfg.trim_fraction:.0%} of position.",
        )

    return ExitRuleResult(
        "Profit Target", False, "HOLD",
        f"Price ${current_price:.2f} below target ${target_price:.2f}. "
        f"Letting it run.",
    )


def rule_trend_deterioration(
    df: pd.DataFrame,
    pos: PositionContext,
    cfg=EXIT,
) -> ExitRuleResult:
    """
    Exit Rule 4: SMA50 crosses back below SMA200 (trend deterioration).

    Broader exit signal even without a stop being hit.
    """
    sma50 = sma(df["Close"], cfg.sma_short_period)
    sma200 = sma(df["Close"], cfg.sma_long_period)

    current_sma50 = sma50.iloc[-1]
    current_sma200 = sma200.iloc[-1]

    if pd.isna(current_sma50) or pd.isna(current_sma200):
        return ExitRuleResult("Trend Deterioration", False, "HOLD",
                              "Insufficient data for SMA comparison.")

    triggered = current_sma50 < current_sma200
    gap_pct = (current_sma50 / current_sma200 - 1) * 100

    if triggered:
        return ExitRuleResult(
            "Trend Deterioration",
            True,
            "SELL",
            f"SMA{cfg.sma_short_period} (${current_sma50:.2f}) crossed below "
            f"SMA{cfg.sma_long_period} (${current_sma200:.2f}, gap {gap_pct:.1f}%). "
            f"Trend deterioration — exit signal.",
        )

    return ExitRuleResult(
        "Trend Deterioration", False, "HOLD",
        f"SMA{cfg.sma_short_period} (${current_sma50:.2f}) above "
        f"SMA{cfg.sma_long_period} (${current_sma200:.2f}, gap {gap_pct:+.1f}%). "
        f"Trend intact.",
    )


def rule_momentum_decay(
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None,
    pos: PositionContext,
    cfg=EXIT,
) -> ExitRuleResult:
    """
    Exit Rule 5 (optional): Relative strength vs benchmark deteriorates sharply.

    If the stock's RS ratio drops below 0.8 (underperforming benchmark),
    flag as momentum decay.
    """
    if bench_df is None:
        return ExitRuleResult("Momentum Decay", False, "HOLD",
                              "No benchmark data provided — skipping.")

    rs_score = relative_strength(
        df["Close"], bench_df["Close"],
        lookback=cfg.rs_monitor_lookback_months * 21,
    )

    triggered = rs_score < 0.8

    if triggered:
        return ExitRuleResult(
            "Momentum Decay",
            True,
            "TRIM",
            f"RS ratio {rs_score:.2f}x vs benchmark — stock is underperforming. "
            f"Momentum decay detected. Consider reducing position.",
        )

    return ExitRuleResult(
        "Momentum Decay", False, "HOLD",
        f"RS ratio {rs_score:.2f}x vs benchmark — momentum intact.",
    )


@dataclass
class CompositeExitResult:
    """Result of evaluating all exit rules for a position."""
    action: Literal["HOLD", "TRIM", "SELL"] = "HOLD"
    triggered_rules: list[ExitRuleResult] = field(default_factory=list)
    summary: str = ""


EXIT_RULES = [
    rule_support_break,
    rule_stop_loss,
    rule_profit_target,
    rule_trend_deterioration,
    rule_momentum_decay,
]

# Priority order for actions (higher index = more severe)
ACTION_PRIORITY: dict[str, int] = {"HOLD": 0, "TRIM": 1, "SELL": 2}


def evaluate_exit(
    df: pd.DataFrame,
    pos: PositionContext,
    bench_df: pd.DataFrame | None = None,
    cfg=EXIT,
) -> CompositeExitResult:
    """
    Run all exit rules against an open position.

    Args:
        df: Current OHLCV data for the position's ticker.
        pos: PositionContext with entry details.
        bench_df: Optional benchmark OHLCV for momentum decay check.
        cfg: ExitConfig.

    Returns:
        CompositeExitResult with the most severe action triggered.
    """
    results: list[ExitRuleResult] = []
    most_severe_action: Literal["HOLD", "TRIM", "SELL"] = "HOLD"

    for rule_fn in EXIT_RULES:
        result = rule_fn(df, pos, cfg)
        results.append(result)

        if ACTION_PRIORITY.get(result.action, 0) > ACTION_PRIORITY.get(most_severe_action, 0):
            most_severe_action = result.action

    triggered = [r for r in results if r.triggered]
    if triggered:
        summary = f"EXIT SIGNAL: {most_severe_action} | Rules triggered: {', '.join(r.rule_name for r in triggered)}"
    else:
        summary = "HOLD — no exit rules triggered."

    return CompositeExitResult(
        action=most_severe_action,
        triggered_rules=results,
        summary=summary,
    )