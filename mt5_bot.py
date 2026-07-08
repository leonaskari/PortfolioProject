"""
MetaTrader 5 Trend-Following Stock Screener & Alert Bot — Main Orchestrator.

This is the primary module that ties together:
  1. Market data fetching (yfinance)
  2. Market regime filter (S&P 200-MA gate)
  3. Technical indicator computation
  4. Entry rule evaluation (6 criteria)
  5. Earnings blackout filter
  6. Risk management & position sizing (sector-aware)
  7. Exit rule evaluation for open positions (with trailing stop)
  8. MetaTrader 5 account/portfolio integration
  9. Signal logging and notification
 10. Signal decay tracking

Usage:
    from mt5_bot import run_screener, analyze_symbol
    result = analyze_symbol("AAPL", account_equity=10000)
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

# Add project root to path if needed
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    DB_PATH,
    ENTRY,
    EXIT,
    EXECUTION_MODE,
    HISTORY_DAYS,
    LOG_DIR,
    LOG_LEVEL,
    RISK,
    REGIME,
    EARNINGS_BLACKOUT,
    LIVE_TRADING,
    PAPER_TRADING,
    MT5_HOST,
    MT5_PORT,
    MT5_TIMEOUT,
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_SERVER,
    BROKER,
    BLACKLIST_FILE,
    WATCHLIST_FILE,
    DAILY_LOSS_KILL_SWITCH_PCT,
    KILL_SWITCH_FILE,
)
from data.market_data import (
    fetch_ohlcv,
    fetch_benchmark,
    fetch_multiple,
    get_ticker_info,
    filter_universe,
)
from data.mt5_adapter import MetaTrader5Client, build_ticker_map
from notifications.notifier import notify_all, format_signal_message
from strategy.entry_rules import evaluate_entry, CompositeEntryResult
from strategy.exit_rules import evaluate_exit, PositionContext, CompositeExitResult
from strategy.indicators import atr, sma
from strategy.risk import (
    size_position,
    check_portfolio_risk,
    PositionSizingResult,
    PortfolioRiskCheck,
)
from strategy.regime import check_market_regime, RegimeResult
from strategy.earnings_blackout import check_earnings_blackout, EarningsCheckResult
from strategy.signal_decay import (
    record_entry_signal,
    record_exit_outcome,
    compute_calibration,
    print_calibration,
    ConfidenceCalibration,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "trading_bot.log")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("mt5_bot")

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _init_db():
    """Initialise the SQLite database and create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker TEXT NOT NULL,
            action TEXT NOT NULL,
            price REAL,
            confidence REAL,
            entry_rules_passed INTEGER,
            reason TEXT,
            rationale TEXT,
            suggested_shares INTEGER,
            stop_loss REAL,
            target_price REAL,
            reward_risk REAL,
            account_equity REAL,
            context_notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            stop_loss REAL,
            target_price REAL,
            entry_atr REAL,
            breakout_level REAL,
            status TEXT DEFAULT 'open',
            exit_date TEXT,
            exit_price REAL,
            pnl REAL,
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            action TEXT NOT NULL,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            rules_summary TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_pnl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            starting_equity REAL NOT NULL,
            current_equity REAL NOT NULL,
            pnl REAL NOT NULL,
            pnl_pct REAL NOT NULL
        )
    """)

    conn.commit()
    conn.close()


_init_db()

# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class SignalResult:
    """Full analysis result for a single ticker."""
    ticker: str
    current_price: float
    last_close: float
    entry_result: CompositeEntryResult
    sizing: PositionSizingResult | None = None
    exit_result: CompositeExitResult | None = None
    portfolio_check: PortfolioRiskCheck | None = None
    regime_check: RegimeResult | None = None
    earnings_check: EarningsCheckResult | None = None
    context_notes: str = ""
    info: dict[str, Any] = field(default_factory=dict)
    signal_id: str = ""  # For signal decay tracking

    @property
    def action(self) -> str:
        if self.exit_result and self.exit_result.action in ("TRIM", "SELL"):
            return self.exit_result.action
        if not self.entry_result.passed:
            return "HOLD"
        if self.regime_check and not self.regime_check.is_favorable:
            return "HOLD_REGIME"
        if self.earnings_check and self.earnings_check.in_blackout:
            return "HOLD_EARNINGS"
        if self.sizing and self.sizing.passes_rr_filter:
            if self.portfolio_check and not self.portfolio_check.within_total_cap:
                return "HOLD_CAPPED"
            if self.portfolio_check and not all(self.portfolio_check.within_sector_caps.values()):
                return "HOLD_SECTOR"
            return "BUY"
        return "HOLD"

    @property
    def confidence(self) -> float:
        return self.entry_result.confidence

    @property
    def rationale(self) -> str:
        parts = [self.entry_result.summary]
        if self.exit_result and self.exit_result.action != "HOLD":
            parts.append(self.exit_result.summary)
        if self.sizing:
            parts.append(self.sizing.details)
        if self.portfolio_check:
            parts.append(self.portfolio_check.details)
        if self.regime_check:
            parts.append(self.regime_check.summary)
        if self.earnings_check:
            parts.append(self.earnings_check.blackout_reason)
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Utility: load blacklist / watchlist
# ---------------------------------------------------------------------------


def load_blacklist() -> set[str]:
    """Load blacklisted tickers from the blacklist file."""
    blacklist_path = Path(BLACKLIST_FILE)
    if not blacklist_path.exists():
        return set()
    tickers = set()
    with open(blacklist_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tickers.add(line.upper())
    return tickers


def load_watchlist() -> list[str] | None:
    """Load watchlist from file. Returns None if file doesn't exist."""
    watchlist_path = Path(WATCHLIST_FILE)
    if not watchlist_path.exists():
        return None
    tickers = []
    with open(watchlist_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tickers.append(line.upper())
    return tickers if tickers else None


# ---------------------------------------------------------------------------
# Daily P&L kill-switch
# ---------------------------------------------------------------------------


def check_daily_pnl_kill_switch() -> tuple[bool, str]:
    """
    Check if the daily loss kill-switch has been triggered.

    Reads the latest daily P&L from the database. If current day's loss
    exceeds the configured threshold, return (True, reason).

    Returns:
        (triggered: bool, reason: str)
    """
    if not Path(KILL_SWITCH_FILE).exists():
        return False, "No kill-switch file."

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT date, pnl_pct FROM daily_pnl ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return False, "No daily P&L data yet."

    date_str, pnl_pct = row
    if pnl_pct < DAILY_LOSS_KILL_SWITCH_PCT:
        return True, (
            f"Daily loss {pnl_pct:.1f}% exceeds kill-switch threshold "
            f"{DAILY_LOSS_KILL_SWITCH_PCT:.1f}% on {date_str}. "
            f"Trading halted."
        )

    return False, f"Daily P&L {pnl_pct:.1f}% within limits."


def record_daily_pnl(starting_equity: float, current_equity: float):
    """Record the day's P&L to the database."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pnl = current_equity - starting_equity
    pnl_pct = (current_equity / starting_equity - 1) * 100 if starting_equity > 0 else 0

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO daily_pnl 
           (date, starting_equity, current_equity, pnl, pnl_pct)
           VALUES (?, ?, ?, ?, ?)""",
        (today, starting_equity, current_equity, pnl, pnl_pct),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------


def analyze_symbol(
    ticker: str,
    account_equity: float | None = None,
    existing_positions: list[dict] | None = None,
    bench_df: pd.DataFrame | None = None,
    vix_df: pd.DataFrame | None = None,
) -> SignalResult:
    """
    Run the full analysis pipeline for a single ticker.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').
        account_equity: Total account equity for position sizing.
                        If None, sizing is skipped.
        existing_positions: List of current open positions for portfolio checks.
        bench_df: Pre-fetched benchmark DataFrame.
        vix_df: Pre-fetched VIX DataFrame for regime filter.

    Returns:
        SignalResult with entry/exit analysis, sizing, and context.
    """
    logger.info("Analyzing %s...", ticker)

    # 1. Fetch data
    df = fetch_ohlcv(ticker)
    if df.empty:
        return SignalResult(
            ticker=ticker, current_price=0, last_close=0,
            entry_result=CompositeEntryResult(
                passed=False, confidence=0, summary="No data available"
            ),
        )

    current_price = float(df["Close"].iloc[-1])
    last_close = current_price

    # 2. Fetch benchmark if not provided
    if bench_df is None:
        bench_df = fetch_benchmark()

    # 3. Market regime check
    regime_check = check_market_regime(bench_df, vix_df)

    # 4. Evaluate entry rules
    entry_result = evaluate_entry(df, bench_df)

    # 5. Earnings blackout check (only if entry passed)
    earnings_check = check_earnings_blackout(ticker)

    # 6. Check if this ticker is already held
    is_held = False
    held_position = None
    if existing_positions:
        for pos in existing_positions:
            if pos.get("ticker", "").upper() == ticker.upper():
                is_held = True
                held_position = pos
                break

    # 7. Evaluate exit rules if position is held
    sizing = None
    exit_result = None
    portfolio_check = None

    if is_held and held_position:
        pos_ctx = PositionContext(
            ticker=held_position.get("ticker", ticker),
            entry_price=float(held_position.get("avgPrice", held_position.get("entry_price", 0))),
            quantity=int(held_position.get("quantity", 0)),
            current_price=current_price,
            breakout_level=held_position.get("breakout_level"),
            entry_atr=held_position.get("entry_atr"),
        )
        exit_result = evaluate_exit(df, pos_ctx, bench_df)

    # 8. Position sizing if entry qualifies and regime is favorable
    if entry_result.passed and not is_held and account_equity and account_equity > 0:
        atr_val = atr(df, ENTRY.atr_period).iloc[-1] if len(df) > ENTRY.atr_period else None
        sizing = size_position(ticker, current_price, account_equity, df, atr_val)

        # 9. Portfolio-level risk check (includes sector caps)
        if existing_positions is not None and sizing and sizing.passes_rr_filter:
            portfolio_check = check_portfolio_risk(
                account_equity, existing_positions,
                candidate_ticker=ticker, candidate_risk=sizing.total_risk_dollars,
                candidate_sector=entry_result.rule_results[0].details if entry_result.rule_results else None,
            )

    # 10. Record signal for decay tracking (only for BUY/SELL signals)
    signal_id = ""
    if entry_result.passed:
        signal_id = record_entry_signal(
            ticker=ticker,
            confidence=entry_result.confidence,
            entry_price=current_price,
            action="BUY",
            regime=regime_check.regime if regime_check else "",
        )

    # 11. Build context notes
    info = get_ticker_info(ticker)
    context_parts = []

    # Market-wide context via regime
    if regime_check:
        context_parts.append(regime_check.summary)

    # Sector context
    sector = info.get("sector", "")
    if sector:
        context_parts.append(f"Sector: {sector}")

    # Earnings note
    if earnings_check:
        context_parts.append(earnings_check.blackout_reason)

    # Existing exposure
    if is_held:
        context_parts.append("Already held — exit rules evaluated")

    context_notes = " | ".join(context_parts)

    return SignalResult(
        ticker=ticker.upper(),
        current_price=current_price,
        last_close=last_close,
        entry_result=entry_result,
        sizing=sizing,
        exit_result=exit_result,
        portfolio_check=portfolio_check,
        regime_check=regime_check,
        earnings_check=earnings_check,
        context_notes=context_notes,
        info=info,
        signal_id=signal_id,
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log_signal(signal: SignalResult):
    """Log a signal result to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO signals
            (timestamp, ticker, action, price, confidence, entry_rules_passed,
             reason, rationale, suggested_shares, stop_loss, target_price,
             reward_risk, account_equity, context_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            signal.ticker,
            signal.action,
            signal.current_price,
            signal.confidence,
            1 if signal.entry_result.passed else 0,
            signal.rationale[:500] if signal.rationale else "",
            signal.entry_result.summary,
            signal.sizing.recommended_shares if signal.sizing else None,
            signal.sizing.stop_loss_price if signal.sizing else None,
            signal.sizing.target_price if signal.sizing else None,
            signal.sizing.reward_risk_ratio if signal.sizing else None,
            None,
            signal.context_notes,
        ),
    )

    conn.commit()
    conn.close()
    logger.info("Logged %s signal for %s", signal.action, signal.ticker)


def log_csv(signals: list[SignalResult], filepath: str | None = None):
    """Append signals to a CSV file for easy review in Excel/Google Sheets."""
    if filepath is None:
        filepath = os.path.join(LOG_DIR, f"signals_{datetime.now().strftime('%Y%m%d')}.csv")

    file_exists = os.path.isfile(filepath)
    with open(filepath, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp", "ticker", "action", "price", "confidence",
                "entry_passed", "regime", "earnings_ok", "shares", "stop_loss",
                "target", "rr", "rationale",
            ])
        for s in signals:
            writer.writerow([
                datetime.now(timezone.utc).isoformat(),
                s.ticker, s.action, s.current_price, round(s.confidence, 3),
                1 if s.entry_result.passed else 0,
                s.regime_check.regime if s.regime_check else "",
                0 if (s.earnings_check and s.earnings_check.in_blackout) else 1,
                s.sizing.recommended_shares if s.sizing else "",
                round(s.sizing.stop_loss_price, 2) if s.sizing else "",
                round(s.sizing.target_price, 2) if s.sizing else "",
                round(s.sizing.reward_risk_ratio, 1) if s.sizing else "",
                s.rationale[:200],
            ])

    logger.info("Appended %d signals to %s", len(signals), filepath)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def notify_signal(signal: SignalResult):
    """Send a notification for a BUY/SELL/TRIM signal."""
    if signal.action in ("HOLD", "HOLD_CAPPED", "HOLD_REGIME", "HOLD_EARNINGS", "HOLD_SECTOR"):
        return

    # Build per-rule breakdown for notification
    rule_details = ""
    if signal.entry_result.rule_results:
        rule_lines = []
        for r in signal.entry_result.rule_results:
            icon = "✅" if r.passed else "❌"
            rule_lines.append(f"{icon} {r.rule_name}: {r.details[:80]}")
        rule_details = "\n".join(rule_lines)

    msg = format_signal_message(
        ticker=signal.ticker,
        action=signal.action,
        price=signal.current_price,
        shares=signal.sizing.recommended_shares if signal.sizing else None,
        stop_loss=signal.sizing.stop_loss_price if signal.sizing else None,
        target=signal.sizing.target_price if signal.sizing else None,
        rr=signal.sizing.reward_risk_ratio if signal.sizing else None,
        confidence=signal.confidence,
        rationale=signal.entry_result.summary,
        context_notes=signal.context_notes,
    )
    notify_all(msg, subject=f"{signal.action} Signal: {signal.ticker}")


# ---------------------------------------------------------------------------
# Full screener run
# ---------------------------------------------------------------------------


def run_screener(
    watchlist: list[str],
    mt5_client: MetaTrader5Client | None = None,
    live_mode: bool = False,
    auto_execute: bool = False,
    account_equity: float | None = None,
    send_notifications: bool = True,
    log_to_db: bool = True,
) -> list[SignalResult]:
    """
    Run the full screening pipeline against a watchlist using MetaTrader 5.

    Args:
        watchlist: List of ticker symbols to screen.
        mt5_client: Optional MetaTrader5Client for pulling portfolio data.
        live_mode: If True, use real account data.
        auto_execute: If True, attempt to place orders for BUY signals.
        account_equity: Override account equity. If None and mt5_client
                        is given, pulls from API.
        send_notifications: If True, send alerts via configured channels.
        log_to_db: If True, log signals to database.

    Returns:
        List of SignalResult for each ticker analysed.
    """
    logger.info("Starting MT5 screener run for %d tickers...", len(watchlist))

    # 0. Check kill switch
    kill_switch_triggered, kill_reason = check_daily_pnl_kill_switch()
    if kill_switch_triggered:
        logger.warning("Daily loss kill-switch triggered: %s", kill_reason)
        notify_all(kill_reason, subject="⚠️ Kill-Switch Triggered")
        return []

    # 1. Fetch benchmark and VIX data once
    bench_df = fetch_benchmark()
    vix_df = fetch_ohlcv("^VIX")

    # 2. Get account context from MT5 if available
    existing_positions: list[dict] = []
    if mt5_client:
        try:
            existing_positions = mt5_client.get_portfolio()
            cash_data = mt5_client.get_cash()
            free_cash = float(cash_data.get("free", 0))
            invested = float(cash_data.get("invested", 0))
            if account_equity is None:
                account_equity = free_cash + invested
            logger.info("Account equity: $%.2f (free: $%.2f, invested: $%.2f)",
                        account_equity, free_cash, invested)
        except Exception as e:
            logger.warning("Failed to fetch MT5 account data: %s", e)

    if account_equity is None:
        account_equity = 10_000.0  # fallback

    # 3. Record daily P&L
    if mt5_client:
        try:
            cash_data = mt5_client.get_cash()
            total_value = float(cash_data.get("total", account_equity))
            record_daily_pnl(account_equity, total_value)
        except Exception:
            pass

    # 4. Check market regime
    regime = check_market_regime(bench_df, vix_df)
    logger.info("Market regime: %s (favorable=%s)", regime.regime, regime.is_favorable)

    # 5. Load blacklist
    blacklist = load_blacklist()
    if blacklist:
        logger.info("Loaded %d blacklisted tickers", len(blacklist))

    # 6. Analyse each ticker
    results: list[SignalResult] = []
    for ticker in watchlist:
        if ticker.upper() in blacklist:
            logger.info("Skipping blacklisted %s", ticker)
            continue

        try:
            signal = analyze_symbol(
                ticker, account_equity, existing_positions,
                bench_df, vix_df,
            )
            results.append(signal)

            if log_to_db:
                log_signal(signal)

            if send_notifications:
                notify_signal(signal)

            logger.info(
                "%s: %s (conf=%.0f%%, regime=%s)",
                ticker, signal.action, signal.confidence * 100,
                regime.regime if regime else "unknown",
            )
        except Exception as e:
            logger.error("Failed to analyse %s: %s", ticker, e, exc_info=True)
            results.append(SignalResult(
                ticker=ticker, current_price=0, last_close=0,
                entry_result=CompositeEntryResult(
                    passed=False, confidence=0, summary=f"Error: {e}"
                ),
            ))

    # 7. Log CSV
    log_csv(results)

    # 8. Optionally auto-execute BUY signals
    if auto_execute and mt5_client and EXECUTION_MODE == "auto_execute":
        for signal in results:
            if signal.action == "BUY" and signal.sizing and signal.sizing.recommended_shares > 0:
                try:
                    # Convert shares to MT5 volume (standard lot = 100 shares)
                    volume = signal.sizing.recommended_shares / 100.0
                    order = mt5_client.place_market_order(
                        symbol=signal.ticker,
                        volume=max(volume, 0.01),  # minimum lot size
                        side="BUY",
                        stop_loss=signal.sizing.stop_loss_price,
                        take_profit=signal.sizing.target_price,
                        comment=f"Bot BUY {signal.ticker}",
                    )
                    logger.info("Order placed for %s: %s", signal.ticker, order)
                except Exception as e:
                    logger.error("Order failed for %s: %s", signal.ticker, e)

    logger.info("Screener run complete: %d/%d signals generated",
                sum(1 for r in results if r.action == "BUY"), len(results))
    return results


def get_mt5_client() -> MetaTrader5Client:
    """
    Create and connect a MetaTrader 5 client using config settings.

    Returns:
        Connected MetaTrader5Client instance.

    Raises:
        RuntimeError: If connection fails.
    """
    client = MetaTrader5Client(
        host=MT5_HOST,
        port=MT5_PORT,
        timeout=MT5_TIMEOUT,
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    )

    if not client.connect():
        raise RuntimeError(
            f"Cannot connect to MetaTrader 5 at {MT5_HOST}:{MT5_PORT}. "
            "Ensure MT5 is running and the mt5linux bridge is active."
        )

    return client


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MetaTrader 5 Trend Screener Bot")
    parser.add_argument(
        "--watchlist", nargs="+",
        default=["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"],
        help="Tickers to screen (space-separated)",
    )
    parser.add_argument("--auto-execute", action="store_true",
                        help="Enable auto-order placement")
    parser.add_argument("--live", action="store_true",
                        help="Use live trading environment")
    parser.add_argument("--equity", type=float, default=None,
                        help="Account equity override (USD)")
    parser.add_argument("--no-notify", action="store_true",
                        help="Disable notifications")
    parser.add_argument("--no-db", action="store_true",
                        help="Disable database logging")
    parser.add_argument("--host", default=MT5_HOST,
                        help=f"MT5 bridge host (default: {MT5_HOST})")
    parser.add_argument("--port", type=int, default=MT5_PORT,
                        help=f"MT5 bridge port (default: {MT5_PORT})")

    args = parser.parse_args()

    # Connect to MT5
    print("=" * 70)
    print("  METATRADER 5 TREND-FOLLOWING STOCK SCREENER")
    print(f"  Watchlist: {', '.join(args.watchlist)}")
    print(f"  MT5 Bridge: {args.host}:{args.port}")
    print(f"  Mode: {'LIVE' if args.live else 'DEMO (paper)'}")
    print(f"  Execution: {'AUTO' if args.auto_execute else 'SIGNALS ONLY'}")
    print("=" * 70)

    mt5 = None
    try:
        mt5 = MetaTrader5Client(
            host=args.host,
            port=args.port,
            login=MT5_LOGIN,
            password=MT5_PASSWORD,
            server=MT5_SERVER,
        )
        if mt5.connect():
            print(f"✅ Connected to MetaTrader 5 at {args.host}:{args.port}")
        else:
            print("⚠️  Could not connect to MT5. Continuing without account data.")
            mt5 = None
    except Exception as e:
        print(f"⚠️  MT5 connection failed: {e}")
        mt5 = None

    results = run_screener(
        watchlist=args.watchlist,
        mt5_client=mt5,
        live_mode=args.live,
        auto_execute=args.auto_execute,
        account_equity=args.equity,
        send_notifications=not args.no_notify,
        log_to_db=not args.no_db,
    )

    if mt5:
        mt5.disconnect()

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
                f"volume: {s.sizing.recommended_shares / 100:.2f} lots, "
                f"R:R: {s.sizing.reward_risk_ratio:.1f}:1)"
                if s.sizing else ""
            )
            print(f"  🟢 {s.ticker}: ${s.current_price:.2f} {sizing_str}")

    if sell_signals:
        print("\n  --- SELL/TRIM SIGNALS ---")
        for s in sell_signals:
            print(f"  🔴 {s.ticker}: {s.action} at ${s.current_price:.2f}")

    print("\n" + "=" * 70)
    print("  Run complete. See logs/ and db/ for details.")
    print("=" * 70)