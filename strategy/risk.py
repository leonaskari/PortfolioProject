"""
Risk management module for the trend-following screener.

Handles position sizing, stop-loss placement, reward:risk filtering,
and portfolio-level risk caps. Applies to every recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config import RISK
from strategy.indicators import atr


@dataclass
class PositionSizingResult:
    """Result of position sizing calculation."""
    recommended_shares: int
    stop_loss_price: float
    target_price: float | None
    risk_amount: float        # $ at risk per share
    total_risk_dollars: float # total $ at risk for this position
    reward_risk_ratio: float
    passes_rr_filter: bool
    details: str


def compute_stop_loss(
    entry_price: float,
    atr_value: float,
    atr_mult: float = 1.5,
) -> float:
    """
    Compute stop-loss price as entry_price - atr_mult * ATR.

    ATR-based stops adapt to volatility; tighter multipliers (1.0-1.5)
    work well for breakouts, wider (2.0-3.0) for longer trends.
    """
    return entry_price - atr_mult * atr_value


def compute_target_price(
    entry_price: float,
    stop_price: float,
    reward_risk_ratio: float = 2.0,
) -> float:
    """
    Compute profit target price.

    target = entry_price + (entry_price - stop_price) * reward_risk_ratio
    """
    risk_per_share = entry_price - stop_price
    return entry_price + risk_per_share * reward_risk_ratio


def compute_position_size(
    account_equity: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float = 0.01,
    max_position_value: float = 0.0,
) -> tuple[int, float]:
    """
    Compute number of shares to buy based on risk.

    Formula:
        risk_amount_per_share = entry_price - stop_price
        total_risk = account_equity * risk_per_trade
        shares = total_risk / risk_amount_per_share  (rounded down)

    Also capped by max_position_value if set (> 0).

    Returns:
        (shares, total_risk_dollars)
    """
    risk_per_share = entry_price - stop_price

    if risk_per_share <= 0:
        return 0, 0.0

    total_risk = account_equity * risk_per_trade
    raw_shares = total_risk / risk_per_share

    # Cap by max position value if configured
    if max_position_value > 0:
        max_shares_by_value = max_position_value / entry_price
        raw_shares = min(raw_shares, max_shares_by_value)

    shares = max(1, int(raw_shares))  # at least 1 share, round down
    actual_risk = shares * risk_per_share
    return shares, actual_risk


def compute_reward_risk(
    entry_price: float,
    stop_price: float,
    target_price: float | None = None,
) -> tuple[float, float | None]:
    """
    Compute reward:risk ratio.

    R:R = (target - entry) / (entry - stop)

    Returns:
        (risk_per_share, reward_risk_ratio)
    """
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return 0.0, None

    if target_price is not None:
        reward = target_price - entry_price
        rr = reward / risk_per_share if risk_per_share > 0 else 0.0
    else:
        rr = None

    return risk_per_share, rr


def size_position(
    ticker: str,
    entry_price: float,
    account_equity: float,
    df: pd.DataFrame | None = None,
    atr_value: float | None = None,
    cfg=RISK,
) -> PositionSizingResult:
    """
    Full position sizing pipeline for a candidate entry.

    Computes stop-loss, target price, position size, and R:R filter.

    Args:
        ticker: Stock ticker.
        entry_price: Proposed entry price (typically current close).
        account_equity: Total account equity (cash + invested).
        df: Optional OHLCV DataFrame (used to compute ATR if atr_value is None).
        atr_value: Pre-computed ATR value.
        cfg: RiskConfig.

    Returns:
        PositionSizingResult containing all sizing details.
    """
    # 1. Get ATR
    if atr_value is None and df is not None:
        atr_series = atr(df, cfg.stop_atr_mult)
        atr_value = atr_series.iloc[-1]
    elif atr_value is None:
        return PositionSizingResult(
            recommended_shares=0, stop_loss_price=0, target_price=None,
            risk_amount=0, total_risk_dollars=0, reward_risk_ratio=0,
            passes_rr_filter=False,
            details="Cannot size position: no ATR data available.",
        )

    # 2. Stop-loss
    stop_loss = compute_stop_loss(entry_price, atr_value, cfg.stop_atr_mult)

    # 3. Target price
    target = compute_target_price(entry_price, stop_loss, cfg.min_reward_risk)

    # 4. R:R
    risk_per_share, rr = compute_reward_risk(entry_price, stop_loss, target)

    # 5. Check R:R filter
    passes_rr = rr is not None and rr >= cfg.min_reward_risk

    if not passes_rr:
        return PositionSizingResult(
            recommended_shares=0, stop_loss_price=stop_loss,
            target_price=target, risk_amount=risk_per_share,
            total_risk_dollars=0, reward_risk_ratio=rr or 0,
            passes_rr_filter=False,
            details=f"R:R ratio {rr:.1f}:1 below minimum {cfg.min_reward_risk}:1. "
                    f"Setup rejected by risk filter.",
        )

    # 6. Position size
    shares, total_risk = compute_position_size(
        account_equity, entry_price, stop_loss,
        cfg.risk_per_trade, cfg.max_position_value,
    )

    return PositionSizingResult(
        recommended_shares=shares,
        stop_loss_price=stop_loss,
        target_price=target,
        risk_amount=risk_per_share,
        total_risk_dollars=total_risk,
        reward_risk_ratio=rr or 0,
        passes_rr_filter=True,
        details=(
            f"Entry ${entry_price:.2f} | Stop ${stop_loss:.2f} "
            f"({'-${:.2f}'.format(risk_per_share)}/share) | "
            f"Target ${target:.2f} | "
            f"R:R {rr:.1f}:1 | "
            f"Shares: {shares} (risk ${total_risk:.2f} = {cfg.risk_per_trade:.1%} of ${account_equity:,.2f})"
        ),
    )


@dataclass
class PortfolioRiskCheck:
    """Result of portfolio-level risk checks."""
    total_risk_dollars: float = 0.0
    total_risk_pct: float = 0.0
    within_total_cap: bool = True
    sector_exposures: dict[str, float] = field(default_factory=dict)
    within_sector_caps: dict[str, bool] = field(default_factory=dict)
    details: str = ""


def check_portfolio_risk(
    account_equity: float,
    existing_positions: list[dict],
    candidate_ticker: str | None = None,
    candidate_risk: float = 0.0,
    candidate_sector: str | None = None,
    cfg=RISK,
) -> PortfolioRiskCheck:
    """
    Check portfolio-level risk constraints.

    Args:
        account_equity: Total account equity.
        existing_positions: List of position dicts with at minimum
                           {'ticker': str, 'quantity': int, 'unrealized': float,
                            'sector': str (optional)}.
        candidate_ticker: Proposed new position ticker.
        candidate_risk: Dollar risk of proposed position.
        candidate_sector: Sector of proposed position (optional).
        cfg: RiskConfig.

    Returns:
        PortfolioRiskCheck with risk analysis.
    """
    # Calculate total risk from existing positions
    total_risk = 0.0
    sector_values: dict[str, float] = {}

    for pos in existing_positions:
        pos_ticker = pos.get("ticker", "")
        pos_qty = pos.get("quantity", 0)
        pos_value = abs(pos.get("unrealized", 0))

        sector = pos.get("sector", "Unknown")
        sector_values[sector] = sector_values.get(sector, 0) + pos_value

        # Estimate risk as position value * risk_per_trade (simplified)
        estimated_risk = pos_value * cfg.risk_per_trade
        total_risk += estimated_risk

    # Add candidate risk
    if candidate_ticker and candidate_risk > 0:
        total_risk += candidate_risk
        if candidate_sector:
            sector_values[candidate_sector] = sector_values.get(candidate_sector, 0) + candidate_risk

    total_risk_pct = total_risk / account_equity if account_equity > 0 else 0
    within_cap = total_risk_pct <= cfg.max_total_risk

    # Check sector caps
    sector_checks: dict[str, bool] = {}
    for sector, value in sector_values.items():
        sector_pct = value / account_equity if account_equity > 0 else 0
        sector_checks[sector] = sector_pct <= cfg.max_sector_exposure

    if within_cap and all(sector_checks.values()):
        detail = f"Portfolio risk {total_risk_pct:.1%} (cap {cfg.max_total_risk:.0%}). All sectors within limits."
    else:
        violations = []
        if not within_cap:
            violations.append(f"total risk {total_risk_pct:.1%} exceeds {cfg.max_total_risk:.0%} cap")
        for sector, ok in sector_checks.items():
            if not ok:
                sector_pct = sector_values[sector] / account_equity * 100
                violations.append(f"sector {sector} at {sector_pct:.1f}% exceeds {cfg.max_sector_exposure:.0%} cap")
        detail = f"⚠️ Risk limit violations: {'; '.join(violations)}"

    return PortfolioRiskCheck(
        total_risk_dollars=total_risk,
        total_risk_pct=total_risk_pct,
        within_total_cap=within_cap,
        sector_exposures=sector_values,
        within_sector_caps=sector_checks,
        details=detail,
    )