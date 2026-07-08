"""
Central configuration for the MetaTrader 5 Trend-Following
Stock Screener & Alert Bot.

All strategy parameters, risk settings, and execution mode live here so they
can be tweaked without touching any logic code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Execution mode
# ---------------------------------------------------------------------------
#   "alert_only"    — bot analyses, logs, and sends notifications only
#   "manual_approval" — bot can propose orders; a human must confirm each
#   "auto_execute"  — bot places orders autonomously (use with extreme care)
EXECUTION_MODE: Literal["alert_only", "manual_approval", "auto_execute"] = "alert_only"

# Enable live (real-money) trading. MUST be False for paper/demo.
LIVE_TRADING: bool = os.environ.get("LIVE_TRADING", "0") == "1"

# Paper trading mode toggle — when True, simulates trades without real money
PAPER_TRADING: bool = os.environ.get("PAPER_TRADING", "1") == "1"

# Kill-switch file path. If this file exists, all order placement is blocked.
KILL_SWITCH_FILE: str = ".kill_switch"

# Daily loss kill-switch (% of account). If daily P&L drops below this, stop trading.
DAILY_LOSS_KILL_SWITCH_PCT: float = float(os.environ.get("DAILY_LOSS_KILL_SWITCH_PCT", "-5"))

# ---------------------------------------------------------------------------
# MetaTrader 5 connection
# ---------------------------------------------------------------------------
MT5_HOST: str = os.environ.get("MT5_HOST", "localhost")
MT5_PORT: int = int(os.environ.get("MT5_PORT", "18812"))
MT5_TIMEOUT: int = int(os.environ.get("MT5_TIMEOUT", "300"))
MT5_LOGIN: int | None = (
    int(os.environ["MT5_LOGIN"]) if os.environ.get("MT5_LOGIN") else None
)
MT5_PASSWORD: str = os.environ.get("MT5_PASSWORD", "")
MT5_SERVER: str = os.environ.get("MT5_SERVER", "")
BROKER: str = os.environ.get("BROKER", "mt5").lower()

# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
HISTORY_DAYS: int = 400
INTRADAY_INTERVAL: str | None = None

# Watchlist / blacklist
WATCHLIST_FILE: str = "data/watchlist.txt"
BLACKLIST_FILE: str = "data/blacklist.txt"

# ---------------------------------------------------------------------------
# Universe filtering
# ---------------------------------------------------------------------------
MIN_PRICE: float = 5.0
MIN_AVG_VOLUME: int = 500_000
UNIVERSE_FILE: str = "data/tradable_universe.csv"

# ---------------------------------------------------------------------------
# Market regime filter  (see strategy/regime.py)
# ---------------------------------------------------------------------------

@dataclass
class RegimeConfig:
    """Config for the market regime / trend-following gate."""

    # SMA period for trend determination
    sma_period: int = 200

    # S&P 500 must be at least this % above its SMA to be a "bull trend"
    bull_threshold: float = 0.0  # >0% = above SMA200

    # S&P 500 between correction_threshold and bull_threshold = "correction"
    correction_threshold: float = -5.0  # -5% = mild correction

    # Whether to allow trading during correction zones
    allow_correction: bool = False

    # VIX threshold: if VIX > this, market is considered choppy/unfavorable
    vix_threshold: float = 30.0


REGIME: RegimeConfig = RegimeConfig()


# ---------------------------------------------------------------------------
# Earnings blackout  (see strategy/earnings_blackout.py)
# ---------------------------------------------------------------------------

@dataclass
class EarningsBlackoutConfig:
    """Config for skipping trades around earnings announcements."""

    enabled: bool = True

    # Days before earnings to avoid entering new positions
    days_before: int = 2

    # Days after earnings to avoid entering new positions
    days_after: int = 1

    # If True, also exit existing positions before earnings
    exit_before_earnings: bool = False


EARNINGS_BLACKOUT: EarningsBlackoutConfig = EarningsBlackoutConfig()


# ---------------------------------------------------------------------------
# Entry rules  (see strategy/entry_rules.py)
# ---------------------------------------------------------------------------

@dataclass
class EntryConfig:
    """All configurable parameters for the 6 entry criteria."""

    # 1. Long-term trend
    sma_long_period: int = 200

    # 2. Medium-term trend / golden-cross alignment
    sma_short_period: int = 50

    # 3. Consolidation / volatility contraction
    consolidation_lookback: int = 20
    prior_lookback: int = 50
    atr_period: int = 14
    atr_contraction_ratio: float = 0.7

    # 4. Resistance breakout
    breakout_lookback: int = 20
    # Require close above resistance (not just intraday poke)
    require_close_above_resistance: bool = True

    # 5. Volume confirmation
    vol_avg_period: int = 20
    vol_multiplier: float = 1.5

    # 6. Relative strength (ranking, not hard gate)
    rs_lookback_months: int = 6
    rs_benchmark: str = "^GSPC"


ENTRY: EntryConfig = EntryConfig()


# ---------------------------------------------------------------------------
# Exit rules  (see strategy/exit_rules.py)
# ---------------------------------------------------------------------------

@dataclass
class ExitConfig:
    """Parameters that govern sell / trim decisions."""

    # Support-level exit
    support_type: Literal["breakout_level", "sma50"] = "breakout_level"

    # Profit target (static)
    reward_risk_min: float = 2.0
    profit_target_r: float = 2.0
    trim_fraction: float = 0.5

    # Trailing stop (if enabled, overrides static profit target)
    trailing_stop_enabled: bool = True
    # Trail activation: price must move this many R above entry before trail starts
    trail_activation_r: float = 1.0
    # Trail distance: stop trails price by this multiple of entry ATR
    trail_atr_mult: float = 1.5
    # Trail step: re-evaluate trail every N days
    trail_frequency_days: int = 1

    # Trend deterioration exit
    sma_short_period: int = 50
    sma_long_period: int = 200

    # Momentum decay exit
    rs_monitor_lookback_months: int = 3


EXIT: ExitConfig = ExitConfig()


# ---------------------------------------------------------------------------
# Risk management  (see strategy/risk.py)
# ---------------------------------------------------------------------------

@dataclass
class RiskConfig:
    """Position sizing, stop-loss, and portfolio-level risk limits."""

    # Per-trade risk (fraction of total account equity)
    risk_per_trade: float = 0.01   # 1%

    # Stop-loss: entry_price - stop_atr_mult * ATR
    stop_atr_mult: float = 1.5

    # Minimum reward:risk to even consider the trade
    min_reward_risk: float = 2.0

    # Portfolio-level total risk cap (fraction of equity at risk)
    max_total_risk: float = 0.08   # 8%

    # Per-sector concentration cap (fraction of equity)
    max_sector_exposure: float = 0.25

    # Per-ticker max position value (alternative cap, $)
    max_position_value: float = 0.0  # 0 = no cap

    # Correlation-aware sizing: max exposure to any correlated group (fraction of equity)
    max_correlation_group_exposure: float = 0.30

    # Correlation threshold: stocks with correlation > this are in the same group
    correlation_threshold: float = 0.70


RISK: RiskConfig = RiskConfig()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@dataclass
class NotifyConfig:
    """Decoupled notification layer — each channel is independent."""

    telegram_enabled: bool = False
    telegram_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    discord_enabled: bool = False
    discord_webhook_url: str = os.environ.get("DISCORD_WEBHOOK_URL", "")

    email_enabled: bool = False
    smtp_host: str = os.environ.get("SMTP_HOST", "")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user: str = os.environ.get("SMTP_USER", "")
    smtp_pass: str = os.environ.get("SMTP_PASS", "")
    email_from: str = os.environ.get("EMAIL_FROM", "")
    email_to: str = os.environ.get("EMAIL_TO", "")


NOTIFY: NotifyConfig = NotifyConfig()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH: str = str(Path(__file__).parent / "db" / "trading_bot.db")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: str = str(Path(__file__).parent / "logs")
LOG_LEVEL: str = "INFO"

# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------

def enable_order_placement() -> bool:
    """Return True if the bot is allowed to place orders right now."""
    if Path(KILL_SWITCH_FILE).exists():
        return False
    if EXECUTION_MODE == "auto_execute":
        return LIVE_TRADING or PAPER_TRADING
    return False