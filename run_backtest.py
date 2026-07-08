#!/usr/bin/env python3
"""
CLI entry point for the historical backtest engine.

Replays the 6-criteria entry logic and exit rules over historical data
so you can see how the strategy would have performed before risking money.

Usage:
    # Single symbol
    python run_backtest.py --ticker AAPL --start 2022-01-01 --end 2025-01-01

    # Multiple symbols
    python run_backtest.py --ticker AAPL MSFT NVDA --start 2022-01-01

    # All S&P 500 (will take a while)
    python run_backtest.py --all-sp500 --start 2023-01-01

    # Quick test with defaults (3 year backtest on major tech)
    python run_backtest.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategy.backtest import backtest_symbol, run_backtest, print_backtest_summary
from data.market_data import SP500_TICKERS, NASDAQ100_TICKERS, MAJOR_ETFS


def main():
    parser = argparse.ArgumentParser(
        description="Backtest the trend-following strategy on historical data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--ticker", "-t", nargs="+",
        default=["AAPL", "MSFT", "NVDA"],
        help="Ticker(s) to backtest (space-separated).",
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Start date (YYYY-MM-DD). Default: 3 years ago.",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="End date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--equity", type=float, default=100_000.0,
        help="Starting account equity (default: $100,000).",
    )
    parser.add_argument(
        "--risk", type=float, default=0.01,
        help="Risk per trade fraction (default: 0.01 = 1%%).",
    )
    parser.add_argument(
        "--all-sp500", action="store_true",
        help="Backtest all S&P 500 stocks (overrides --ticker).",
    )
    parser.add_argument(
        "--all-nasdaq100", action="store_true",
        help="Backtest all Nasdaq 100 stocks (overrides --ticker).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed per-trade output.",
    )

    args = parser.parse_args()

    # Determine tickers
    if args.all_sp500:
        tickers = SP500_TICKERS
    elif args.all_nasdaq100:
        tickers = NASDAQ100_TICKERS
    else:
        tickers = args.ticker

    print("=" * 70)
    print("  HISTORICAL BACKTEST ENGINE")
    print(f"  Tickers: {len(tickers)}")
    print(f"  Period: {args.start or '3 years ago'} → {args.end or 'today'}")
    print(f"  Equity: ${args.equity:,.0f} | Risk: {args.risk:.1%}/trade")
    print("=" * 70)

    if args.verbose:
        for ticker in tickers:
            result = backtest_symbol(
                ticker,
                start_date=args.start,
                end_date=args.end,
                initial_equity=args.equity,
                risk_per_trade=args.risk,
            )
            print(f"\n--- {ticker} ---")
            print(f"  {result.summary}")
            if result.trades:
                print(f"  Trades:")
                for t in result.trades[:10]:  # Show first 10 trades
                    print(f"    {t.entry_date.date()} → {t.exit_date.date() if t.exit_date else 'open'}: "
                          f"{'✅' if t.pnl > 0 else '❌'} ${t.pnl:+,.2f} ({t.pnl_pct:+.1f}%) "
                          f"| {t.exit_reason[:60]}")
                if len(result.trades) > 10:
                    print(f"    ... and {len(result.trades) - 10} more trades")
    else:
        results = run_backtest(
            tickers,
            start_date=args.start,
            end_date=args.end,
            initial_equity=args.equity,
            risk_per_trade=args.risk,
            verbose=True,
        )
        if len(results) > 1:
            print_backtest_summary(results)


if __name__ == "__main__":
    main()