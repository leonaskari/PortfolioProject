"""
Earnings blackout filter — prevents entering positions around earnings.

Earnings announcements create gap risk that makes ATR-based stops meaningless.
A stock can gap 10-20% overnight, blowing through any reasonable stop.

This filter checks if a stock is within N days of its next earnings report
and blocks entry if so.

Data source: yfinance provides earnings dates via Ticker.calendar or
Ticker.earnings_dates. We cache results to avoid excessive API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from config import EARNINGS_BLACKOUT

logger = logging.getLogger(__name__)

# Simple in-memory cache for earnings dates
_earnings_cache: dict[str, list[datetime]] = {}


def fetch_earnings_dates(ticker: str) -> list[datetime]:
    """
    Fetch upcoming earnings dates for a ticker using yfinance.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        List of upcoming earnings report dates (as datetime objects).
        Empty list if data is unavailable.
    """
    # Check cache first
    if ticker in _earnings_cache:
        return _earnings_cache[ticker]

    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        # Try calendar first (most reliable for next earnings)
        cal = stock.calendar
        if cal is not None and not cal.empty:
            earnings_date = cal.get("Earnings Date", cal.get("Earnings Date", None))
            if earnings_date is not None:
                if isinstance(earnings_date, pd.Timestamp):
                    dates = [earnings_date.to_pydatetime()]
                elif isinstance(earnings_date, (list, pd.Index)):
                    dates = [d.to_pydatetime() if isinstance(d, pd.Timestamp) else d for d in earnings_date]
                else:
                    dates = []
                _earnings_cache[ticker] = dates
                return dates

        # Fallback: try earnings_dates
        earnings = stock.earnings_dates
        if earnings is not None and not earnings.empty:
            dates = []
            for idx in earnings.index:
                if isinstance(idx, pd.Timestamp):
                    dates.append(idx.to_pydatetime())
            _earnings_cache[ticker] = dates
            return dates

    except Exception as e:
        logger.debug("Failed to fetch earnings for %s: %s", ticker, e)

    _earnings_cache[ticker] = []
    return []


@dataclass
class EarningsCheckResult:
    """Result of earnings blackout check."""
    in_blackout: bool
    next_earnings_date: datetime | None
    days_until_earnings: int | None
    blackout_reason: str = ""


def check_earnings_blackout(
    ticker: str,
    cfg: Any = None,
) -> EarningsCheckResult:
    """
    Check if a ticker is in an earnings blackout period.

    Args:
        ticker: Stock ticker symbol.
        cfg: EarningsBlackoutConfig (falls back to config.EARNINGS_BLACKOUT).

    Returns:
        EarningsCheckResult with in_blackout flag and details.
    """
    if cfg is None:
        cfg = EARNINGS_BLACKOUT

    if not cfg.enabled:
        return EarningsCheckResult(
            in_blackout=False,
            next_earnings_date=None,
            days_until_earnings=None,
            blackout_reason="Earnings blackout disabled in config.",
        )

    dates = fetch_earnings_dates(ticker)
    if not dates:
        # No data available — assume it's safe (can't block everything)
        return EarningsCheckResult(
            in_blackout=False,
            next_earnings_date=None,
            days_until_earnings=None,
            blackout_reason="No earnings data available — assuming safe.",
        )

    now = datetime.now(timezone.utc)

    for earnings_date in dates:
        # Ensure timezone-aware
        if earnings_date.tzinfo is None:
            earnings_date = earnings_date.replace(tzinfo=timezone.utc)

        days_until = (earnings_date - now).days

        # Check if we're in the blackout window
        if -cfg.days_after <= days_until <= cfg.days_before:
            if days_until >= 0:
                reason = (
                    f"Earnings in {days_until} day(s) on {earnings_date.strftime('%Y-%m-%d')}. "
                    f"Blackout: {cfg.days_before}d before / {cfg.days_after}d after."
                )
            else:
                reason = (
                    f"Earnings was {abs(days_until)} day(s) ago on {earnings_date.strftime('%Y-%m-%d')}. "
                    f"Blackout: {cfg.days_after}d after."
                )
            return EarningsCheckResult(
                in_blackout=True,
                next_earnings_date=earnings_date,
                days_until_earnings=days_until,
                blackout_reason=reason,
            )

        # If earnings is far in the future, no need to check further dates
        if days_until > cfg.days_before:
            return EarningsCheckResult(
                in_blackout=False,
                next_earnings_date=earnings_date,
                days_until_earnings=days_until,
                blackout_reason=(
                    f"Next earnings in {days_until} days on {earnings_date.strftime('%Y-%m-%d')}. "
                    f"Outside blackout window ({cfg.days_before}d before / {cfg.days_after}d after)."
                ),
            )

    return EarningsCheckResult(
        in_blackout=False,
        next_earnings_date=None,
        days_until_earnings=None,
        blackout_reason="No upcoming earnings found.",
    )


def clear_earnings_cache():
    """Clear the earnings date cache (useful for testing)."""
    _earnings_cache.clear()