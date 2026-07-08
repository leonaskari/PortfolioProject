#!/usr/bin/env python3
"""
CLI entry point for the MetaTrader 5 Trend-Following Stock Screener.

Runs a one-off scan of a watchlist and outputs results to the console.
Can be scheduled via cron, launchd, or APScheduler for daily runs.

Usage:
    # Basic scan (signals only)
    python run_screener.py --watchlist AAPL MSFT NVDA

    # With MetaTrader 5 account integration
    python run_screener.py --watchlist AAPL MSFT NVDA --mt5

    # Auto-execute on demo (paper trading)
    python run_screener.py --watchlist AAPL MSFT NVDA --mt5 --auto-execute

    # Live trading (REAL MONEY — only after thorough testing)
    python run_screener.py --watchlist AAPL MSFT NVDA --mt5 --auto-execute --live

    # Scheduled daily scan (via cron):
    # 0 9 * * 1-5 cd /path/to/project && python run_screener.py --watchlist AAPL MSFT NVDA --mt5
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from mt5_bot import run_screener, compute_calibration, print_calibration
from data.mt5_adapter import MetaTrader5Client
from config import MT5_HOST, MT5_PORT, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER

logger = logging.getLogger("run_screener")


def main():
    parser = argparse.ArgumentParser(
        description="MetaTrader 5 Trend-Following Stock Screener",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--watchlist", "-w", nargs="+",
        default=["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"],
        help="Tickers to screen (space-separated). Default: major tech stocks.",
    )
    parser.add_argument(
        "--mt5", action="store_true",
        help="Connect to MetaTrader 5 API for account/portfolio data.",
    )
    parser.add_argument(
        "--auto-execute", action="store_true",
        help="Enable auto-order placement (requires --mt5 and EXECUTION_MODE=auto_execute in config).",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Use live (real-money) MetaTrader 5 environment. DANGER: only after thorough testing.",
    )
    parser.add_argument(
        "--equity", type=float, default=None,
        help="Account equity override (USD). If not set and --mt5 is used, pulls from API.",
    )
    parser.add_argument(
        "--no-notify", action="store_true",
        help="Disable all notifications (Telegram/Discord/Email).",
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="Disable database logging.",
    )
    parser.add_argument(
        "--calibrate", action="store_true",
        help="Show confidence calibration from historical signal decay data.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging.",
    )

    args = parser.parse_args()

    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled.")

    # Show calibration if requested
    if args.calibrate:
        print_calibration()
        return

    # Print header
    print("=" * 70)
    print("  METATRADER 5 TREND-FOLLOWING STOCK SCREENER")
    print(f"  Watchlist: {', '.join(args.watchlist)}")
    print(f"  Mode: {'LIVE' if args.live else 'DEMO (paper)'}")
    print(f"  Execution: {'AUTO' if args.auto_execute else 'SIGNALS ONLY'}")
    print("=" * 70)

    # Initialise MT5 client if requested
    mt5_client = None
    if args.mt5:
        try:
            mt5_client = MetaTrader5Client(
                host=MT5_HOST,
                port=MT5_PORT,
                login=MT5_LOGIN,
                password=MT5_PASSWORD,
                server=MT5_SERVER,
            )
            if mt5_client.connect():
                logger.info("Connected to MetaTrader 5 (%s mode).",
                            "LIVE" if args.live else "DEMO")
                print(f"✅ Connected to MetaTrader 5 at {MT5_HOST}:{MT5_PORT}")
            else:
                print("⚠️  Could not connect to MT5. Continuing without account data.")
                mt5_client = None
        except Exception as e:
            logger.error("Failed to initialise MetaTrader 5 client: %s", e)
            print(f"⚠️  Failed to connect to MetaTrader 5: {e}")
            print("   Continuing without account integration.")
            mt5_client = None

    # Run the screener
    results = run_screener(
        watchlist=args.watchlist,
        mt5_client=mt5_client,
        live_mode=args.live,
        auto_execute=args.auto_execute,
        account_equity=args.equity,
        send_notifications=not args.no_notify,
        log_to_db=not args.no_db,
    )

    if mt5_client:
        mt5_client.disconnect()

    # Print summary
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)

    buy_signals = [r for r in results if r.action == "BUY"]
    sell_signals = [r for r in results if r.action in ("SELL", "TRIM")]
    hold_signals = [r for r in results if r.action == "HOLD"]
    capped_signals = [r for r in results if "HOLD_" in r.action]
    errors = [r for r in results if r.current_price == 0]

    print(f"  BUY:  {len(buy_signals)}")
    print(f"  SELL/TRIM: {len(sell_signals)}")
    print(f"  HOLD: {len(hold_signals)}")
    print(f"  CAPPED/BLOCKED: {len(capped_signals)}")
    if errors:
        print(f"  ERRORS: {len(errors)}")

    if buy_signals:
        print("\n  --- BUY SIGNALS ---")
        for s in buy_signals:
            sizing_str = (
                f"(conf: {s.confidence:.0%}, "
                f"shares: {s.sizing.recommended_shares if s.sizing else 'N/A'}, "
                f"R:R: {s.sizing.reward_risk_ratio:.1f}:1)" if s.sizing else ""
            )
            print(f"  🟢 {s.ticker}: ${s.current_price:.2f} {sizing_str}")

    if sell_signals:
        print("\n  --- SELL/TRIM SIGNALS ---")
        for s in sell_signals:
            print(f"  🔴 {s.ticker}: {s.action} at ${s.current_price:.2f}")

    if capped_signals:
        print("\n  --- CAPPED/BLOCKED ---")
        for s in capped_signals:
            reason = s.action.replace("HOLD_", "").title()
            print(f"  🟡 {s.ticker}: {reason} (conf: {s.confidence:.0%})")

    print("\n" + "=" * 70)
    print("  Run complete. See logs/ and db/ for details.")
    print("=" * 70)


if __name__ == "__main__":
    main()