"""
Historical backtest engine for the trend-following strategy.

This is the single highest-leverage feature. It replays the 6-criteria entry
logic and exit rules over historical data so you can see how the strategy
would have performed before risking real money.

Usage:
    from strategy.backtest import backtest_symbol, run_backtest
    result = backtest_symbol("AAPL", start_date="2020-01-01")
    print(result.summary)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import numpy as np
import pandas as pd

from config import ENTRY, EXIT, RISK
from data.market_data import fetch_ohlcv, fetch_benchmark
from strategy.entry_rules import evaluate_entry, CompositeEntryResult
from strategy.exit_rules import (
    evaluate_exit,
    PositionContext,
    CompositeExitResult,
    rule_support_break,
    rule_stop_loss,
    rule_profit_target,
    rule_trend_deterioration,
)
from strategy.indicators import atr
from strategy.risk import (
    size_position,
    compute_stop_loss,
    compute_target_price,
    compute_position_size,
    PositionSizingResult,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A single simulated trade from the backtest."""
    entry_date: datetime
    exit_date: datetime | None = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: int = 0
    stop_loss: float = 0.0
    target_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    entry_confidence: float = 0.0
    entry_rationale: str = ""


@dataclass
class BacktestResult:
    """Complete backtest result for a single symbol."""
    ticker: str
    trades: list[BacktestTrade] = field(default_factory=list)
    start_date: datetime | None = None
    end_date: datetime | None = None
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    avg_bars_held: float = 0.0
    summary: str = ""


def backtest_symbol(
    ticker: str,
    start_date: str | None = None,
    end_date: str | None = None,
    initial_equity: float = 100_000.0,
    risk_per_trade: float = 0.01,
    max_concurrent_positions: int = 5,
    entry_cfg: Any = None,
    exit_cfg: Any = None,
    risk_cfg: Any = None,
) -> BacktestResult:
    """
    Run a historical backtest for a single symbol.

    The backtest walks through the OHLCV data day by day, evaluating entry
    and exit rules as if trading in real time. It simulates:
      - Entry when all 5 hard rules pass
      - Position sizing based on ATR and risk per trade
      - Exit on stop-loss, profit target, support break, or trend deterioration
      - Only one position at a time per symbol

    Args:
        ticker: Stock ticker symbol.
        start_date: Start date string (e.g. "2020-01-01"). Defaults to 3 years ago.
        end_date: End date string. Defaults to today.
        initial_equity: Starting account equity for position sizing.
        risk_per_trade: Fraction of equity to risk per trade.
        max_concurrent_positions: Max positions held simultaneously (per symbol = 1).
        entry_cfg: EntryConfig override.
        exit_cfg: ExitConfig override.
        risk_cfg: RiskConfig override.

    Returns:
        BacktestResult with all trades and performance metrics.
    """
    if entry_cfg is None:
        entry_cfg = ENTRY
    if exit_cfg is None:
        exit_cfg = EXIT
    if risk_cfg is None:
        risk_cfg = RISK

    # Parse dates
    if end_date is None:
        end_dt = datetime.now(timezone.utc)
    else:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if start_date is None:
        start_dt = end_dt - timedelta(days=3 * 365)  # ~3 years
    else:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # Fetch data (need extra buffer for SMA200)
    buffer_days = entry_cfg.sma_long_period + 50
    fetch_start = start_dt - timedelta(days=buffer_days)
    period_str = f"{(end_dt - fetch_start).days}d"

    df = fetch_ohlcv(ticker, period=period_str)
    if df.empty or len(df) < entry_cfg.sma_long_period + 20:
        return BacktestResult(
            ticker=ticker,
            summary=f"Insufficient data for {ticker} backtest.",
        )

    # Filter to date range
    df = df[df.index >= pd.Timestamp(start_dt)]
    df = df[df.index <= pd.Timestamp(end_dt)]

    if df.empty:
        return BacktestResult(
            ticker=ticker,
            summary=f"No data in date range for {ticker}.",
        )

    # Fetch benchmark for RS calculation
    bench_df = fetch_benchmark()
    if not bench_df.empty:
        bench_df = bench_df[bench_df.index >= pd.Timestamp(fetch_start)]

    # Run the backtest
    trades: list[BacktestTrade] = []
    current_position: BacktestTrade | None = None
    equity_curve: list[float] = [initial_equity]
    peak_equity = initial_equity
    max_dd = 0.0

    account_equity = initial_equity

    for i in range(len(df)):
        # Slice data up to current bar (looks like real-time)
        current_df = df.iloc[: i + 1]
        current_date = df.index[i]
        current_price = float(df["Close"].iloc[i])

        # --- EXIT CHECK (if in a position) ---
        if current_position is not None:
            pos_ctx = PositionContext(
                ticker=ticker,
                entry_price=current_position.entry_price,
                quantity=current_position.quantity,
                current_price=current_price,
                breakout_level=current_position.target_price,
                entry_atr=atr(current_df, entry_cfg.atr_period).iloc[-1]
                if len(current_df) > entry_cfg.atr_period
                else None,
            )

            exit_result = evaluate_exit(current_df, pos_ctx, bench_df, exit_cfg)

            if exit_result.action in ("SELL", "TRIM"):
                exit_price = current_price
                pnl = (exit_price - current_position.entry_price) * current_position.quantity
                pnl_pct = (exit_price / current_position.entry_price - 1) * 100
                bars_held = i - (df.index.get_loc(current_date) - bars_held_offset)

                current_position.exit_date = current_date.to_pydatetime()
                current_position.exit_price = exit_price
                current_position.pnl = pnl
                current_position.pnl_pct = pnl_pct
                current_position.exit_reason = exit_result.summary
                current_position.bars_held = bars_held
                trades.append(current_position)

                # Update equity
                account_equity += pnl
                equity_curve.append(account_equity)
                if account_equity > peak_equity:
                    peak_equity = account_equity
                dd = (peak_equity - account_equity) / peak_equity * 100
                max_dd = max(max_dd, dd)

                current_position = None
                continue

        # --- ENTRY CHECK (if not in a position) ---
        if current_position is None:
            # Need enough data for indicators
            if len(current_df) < entry_cfg.sma_long_period + 10:
                continue

            entry_result = evaluate_entry(current_df, bench_df, entry_cfg)

            if entry_result.passed:
                # Size the position
                atr_val = atr(current_df, entry_cfg.atr_period).iloc[-1]
                stop_loss = compute_stop_loss(current_price, atr_val, risk_cfg.stop_atr_mult)
                target = compute_target_price(current_price, stop_loss, risk_cfg.min_reward_risk)
                shares, total_risk = compute_position_size(
                    account_equity, current_price, stop_loss,
                    risk_per_trade, risk_cfg.max_position_value,
                )

                if shares > 0:
                    current_position = BacktestTrade(
                        entry_date=current_date.to_pydatetime(),
                        entry_price=current_price,
                        quantity=shares,
                        stop_loss=stop_loss,
                        target_price=target,
                        entry_confidence=entry_result.confidence,
                        entry_rationale=entry_result.summary,
                    )
                    bars_held_offset = i

    # Close any open position at the end
    if current_position is not None:
        final_price = float(df["Close"].iloc[-1])
        pnl = (final_price - current_position.entry_price) * current_position.quantity
        pnl_pct = (final_price / current_position.entry_price - 1) * 100

        current_position.exit_date = df.index[-1].to_pydatetime()
        current_position.exit_price = final_price
        current_position.pnl = pnl
        current_position.pnl_pct = pnl_pct
        current_position.exit_reason = "End of backtest period"
        trades.append(current_position)

    # Compute performance metrics
    total_trades = len(trades)
    if total_trades == 0:
        return BacktestResult(
            ticker=ticker,
            start_date=start_dt,
            end_date=end_dt,
            summary=f"No trades generated for {ticker} in this period.",
        )

    winning = [t for t in trades if t.pnl > 0]
    losing = [t for t in trades if t.pnl <= 0]
    win_rate = len(winning) / total_trades * 100 if total_trades > 0 else 0
    total_pnl = sum(t.pnl for t in trades)
    total_pnl_pct = (account_equity / initial_equity - 1) * 100
    avg_win = np.mean([t.pnl for t in winning]) if winning else 0
    avg_loss = np.mean([t.pnl for t in losing]) if losing else 0
    gross_profit = sum(t.pnl for t in winning) if winning else 0
    gross_loss = abs(sum(t.pnl for t in losing)) if losing else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_bars_held = np.mean([t.bars_held for t in trades]) if trades else 0

    summary = (
        f"📊 Backtest: {ticker} | "
        f"Trades: {total_trades} | "
        f"Win Rate: {win_rate:.1f}% | "
        f"Total P&L: ${total_pnl:+,.2f} ({total_pnl_pct:+.1f}%) | "
        f"Profit Factor: {profit_factor:.2f} | "
        f"Max DD: {max_dd:.1f}% | "
        f"Avg Win: ${avg_win:+,.2f} | "
        f"Avg Loss: ${avg_loss:+,.2f} | "
        f"Avg Hold: {avg_bars_held:.0f} days"
    )

    return BacktestResult(
        ticker=ticker,
        trades=trades,
        start_date=start_dt,
        end_date=end_dt,
        total_trades=total_trades,
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        avg_win=avg_win,
        avg_loss=avg_loss,
        max_drawdown=max_dd,
        profit_factor=profit_factor,
        avg_bars_held=avg_bars_held,
        summary=summary,
    )


def run_backtest(
    tickers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    initial_equity: float = 100_000.0,
    risk_per_trade: float = 0.01,
    verbose: bool = True,
) -> dict[str, BacktestResult]:
    """
    Run backtest on multiple symbols.

    Args:
        tickers: List of ticker symbols to backtest.
        start_date: Start date string.
        end_date: End date string.
        initial_equity: Starting equity for each symbol.
        risk_per_trade: Risk per trade fraction.
        verbose: If True, print results as they complete.

    Returns:
        Dict mapping ticker -> BacktestResult.
    """
    results: dict[str, BacktestResult] = {}
    for ticker in tickers:
        result = backtest_symbol(
            ticker, start_date, end_date,
            initial_equity, risk_per_trade,
        )
        results[ticker] = result
        if verbose:
            print(result.summary)
    return results


def print_backtest_summary(results: dict[str, BacktestResult]):
    """Print a consolidated summary of multi-symbol backtest results."""
    if not results:
        print("No backtest results.")
        return

    print("\n" + "=" * 80)
    print("  BACKTEST SUMMARY")
    print("=" * 80)

    all_trades = []
    total_pnl = 0.0
    total_initial = 0.0

    for ticker, result in results.items():
        all_trades.extend(result.trades)
        total_pnl += result.total_pnl
        print(f"  {ticker}: {result.total_trades} trades, "
              f"P&L ${result.total_pnl:+,.2f} ({result.total_pnl_pct:+.1f}%), "
              f"Win {result.win_rate:.0f}%, PF {result.profit_factor:.2f}")

    if all_trades:
        winning = [t for t in all_trades if t.pnl > 0]
        losing = [t for t in all_trades if t.pnl <= 0]
        win_rate = len(winning) / len(all_trades) * 100
        gross_profit = sum(t.pnl for t in winning)
        gross_loss = abs(sum(t.pnl for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        print("-" * 80)
        print(f"  TOTAL: {len(all_trades)} trades, "
              f"P&L ${total_pnl:+,.2f}, "
              f"Win {win_rate:.0f}%, "
              f"PF {profit_factor:.2f}")
    print("=" * 80)