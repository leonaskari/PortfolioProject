"""
MetaTrader 5 Bot - Streamlit Dashboard
======================================

A local web UI that **automatically discovers stocks to invest in** by
scanning a broad universe (S&P 500, Nasdaq 100, or from your MT5 terminal)
against the trend-following strategy rules.

You don't need to maintain a watchlist — the bot tells you which stocks
meet the entry criteria, ranked by confidence.

Includes:
  - Market regime filter (S&P 200-MA gate)
  - Earnings blackout filter
  - Per-stock signal breakdown
  - Sector concentration limits
  - Correlation-aware position sizing
  - Signal decay calibration
  - Paper trading mode
  - Live position management
  - Daily loss kill-switch
  - CSV export / scan history

-----------------------------------------------------------------------------
SETUP
-----------------------------------------------------------------------------
1. Install dependencies:
     pip install -r requirements.txt

2. Set your MT5 environment variables (or edit config.py):
     export MT5_HOST="localhost"
     export MT5_PORT="18812"

3. Ensure MetaTrader 5 is running with the mt5linux bridge.

4. Run it:
     streamlit run mt5_dashboard.py

-----------------------------------------------------------------------------
SECURITY NOTE
-----------------------------------------------------------------------------
This dashboard runs entirely on your machine (server-side Python). Your
MT5 credentials never get exposed to a remote server.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# Must be first page config
st.set_page_config(page_title="MetaTrader 5 Bot", page_icon="📈", layout="wide")

from mt5_bot import (
    MetaTrader5Client,
    analyze_symbol,
    build_ticker_map,
    SignalResult,
    CompositeEntryResult,
    get_mt5_client,
    run_screener,
    load_blacklist,
    load_watchlist,
    compute_calibration,
    print_calibration,
    record_daily_pnl,
    check_daily_pnl_kill_switch,
)
from config import (
    ENTRY,
    EXIT,
    RISK,
    REGIME,
    EARNINGS_BLACKOUT,
    MT5_HOST,
    MT5_PORT,
    MT5_LOGIN,
    MT5_PASSWORD,
    MT5_SERVER,
    PAPER_TRADING,
    LIVE_TRADING,
    BLACKLIST_FILE,
    WATCHLIST_FILE,
    LOG_DIR,
    DB_PATH,
    DAILY_LOSS_KILL_SWITCH_PCT,
    KILL_SWITCH_FILE,
)
from data.market_data import (
    SP500_TICKERS,
    NASDAQ100_TICKERS,
    MAJOR_ETFS,
    fetch_benchmark,
    fetch_ohlcv,
    filter_universe,
)
from strategy.regime import check_market_regime
from strategy.signal_decay import compute_calibration, print_calibration

# ------------------------------------------------------------------------
# Sidebar: connection & settings
# ------------------------------------------------------------------------

st.sidebar.header("🔌 MT5 Connection")

mt5_host = st.sidebar.text_input("Bridge Host", value=MT5_HOST)
mt5_port = st.sidebar.number_input("Bridge Port", value=MT5_PORT, min_value=1, max_value=65535)

st.sidebar.header("🎯 Market to Scan")

universe_option = st.sidebar.selectbox(
    "Universe",
    options=[
        "S&P 500 (100 stocks)",
        "Nasdaq 100 (100 stocks)",
        "S&P 500 + Nasdaq 100 (combined, ~150 unique)",
        "Major ETFs (20)",
        "All of the above (~170 stocks)",
        "Custom watchlist",
        "Watchlist file",
    ],
    index=4,
)

custom_watchlist: list[str] = []
if universe_option == "Custom watchlist":
    watchlist_raw = st.sidebar.text_input(
        "Enter tickers (comma-separated)",
        value="AAPL, MSFT, NVDA, GOOGL, AMZN, META",
    )
    custom_watchlist = [s.strip().upper() for s in watchlist_raw.split(",") if s.strip()]
elif universe_option == "Watchlist file":
    wl = load_watchlist()
    if wl:
        custom_watchlist = wl
        st.sidebar.info(f"Loaded {len(wl)} tickers from {WATCHLIST_FILE}")
    else:
        st.sidebar.warning(f"Watchlist file {WATCHLIST_FILE} not found. Using defaults.")
        custom_watchlist = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"]

st.sidebar.header("⚙️ Risk Settings")

risk_fraction = st.sidebar.slider(
    "Risk per trade (% of equity)",
    min_value=0.5, max_value=3.0, value=1.0, step=0.5, format="%.1f%%"
) / 100.0

max_position_value = st.sidebar.number_input(
    "Max $ per position (0 = no cap)", min_value=0.0, value=2000.0, step=500.0
)

min_confidence = st.sidebar.slider(
    "Min confidence to show", min_value=0.0, max_value=1.0, value=0.3, step=0.05
)

max_sector_exposure = st.sidebar.slider(
    "Max sector exposure (% of equity)",
    min_value=5, max_value=50, value=25, step=5, format="%d%%"
) / 100.0

st.sidebar.header("🛡️ Filters")

enable_regime_filter = st.sidebar.checkbox("Market regime filter", value=True,
    help="Blocks trading when S&P 500 is below its 200-day MA")

enable_earnings_filter = st.sidebar.checkbox("Earnings blackout filter", value=True,
    help="Blocks entry around earnings announcements")

# ------------------------------------------------------------------------
# Sidebar: Entry Criteria Settings (collapsible)
# ------------------------------------------------------------------------

with st.sidebar.expander("📐 Entry Criteria Settings", expanded=False):
    st.caption("Tune the 6 entry criteria parameters")

    # 1. Long-term uptrend
    st.markdown("**1️⃣ Long-term Uptrend**")
    sma_long_period = st.number_input(
        "SMA long period (days)", min_value=50, max_value=500, value=200, step=10,
        help="Price must be above this SMA to confirm long-term uptrend"
    )

    # 2. Medium-term alignment
    st.markdown("**2️⃣ Medium-term Alignment**")
    sma_short_period = st.number_input(
        "SMA short period (days)", min_value=10, max_value=200, value=50, step=5,
        help="Short SMA must be above long SMA (golden cross)"
    )

    # 3. Consolidation / volatility contraction
    st.markdown("**3️⃣ Consolidation (Volatility Contraction)**")
    col_con1, col_con2 = st.columns(2)
    with col_con1:
        consolidation_lookback = st.number_input(
            "Recent lookback (days)", min_value=5, max_value=100, value=20, step=5,
            help="Window for recent ATR average"
        )
    with col_con2:
        prior_lookback = st.number_input(
            "Prior lookback (days)", min_value=10, max_value=200, value=50, step=5,
            help="Window for prior ATR average"
        )
    atr_period = st.number_input(
        "ATR period (days)", min_value=5, max_value=50, value=14, step=1,
        help="Period for ATR calculation"
    )
    atr_contraction_ratio = st.slider(
        "ATR contraction ratio", min_value=0.3, max_value=1.0, value=0.7, step=0.05,
        help="Recent ATR must be < this × prior ATR to qualify as contraction"
    )

    # 4. Resistance breakout
    st.markdown("**4️⃣ Resistance Breakout**")
    breakout_lookback = st.number_input(
        "Breakout lookback (days)", min_value=5, max_value=100, value=20, step=5,
        help="Highest high of this window is the resistance level"
    )
    require_close_above_resistance = st.checkbox(
        "Require close above resistance", value=True,
        help="If checked, close must be above resistance (not just intraday poke)"
    )

    # 5. Volume confirmation
    st.markdown("**5️⃣ Volume Confirmation**")
    col_vol1, col_vol2 = st.columns(2)
    with col_vol1:
        vol_avg_period = st.number_input(
            "Volume avg period (days)", min_value=5, max_value=100, value=20, step=5,
            help="Window for average volume calculation"
        )
    with col_vol2:
        vol_multiplier = st.slider(
            "Volume multiplier (× avg)", min_value=1.0, max_value=5.0, value=1.5, step=0.1,
            help="Breakout volume must exceed this × average volume"
        )

    # 6. Relative strength
    st.markdown("**6️⃣ Relative Strength (Ranking)**")
    rs_lookback_months = st.number_input(
        "RS lookback (months)", min_value=1, max_value=24, value=6, step=1,
        help="Period for relative strength calculation vs S&P 500"
    )

# ------------------------------------------------------------------------
# Sidebar: Filter Settings (collapsible)
# ------------------------------------------------------------------------

with st.sidebar.expander("⚙️ Filter Settings", expanded=False):
    st.caption("Fine-tune market regime, earnings, and risk filters")

    st.markdown("**🛡️ Market Regime**")
    regime_sma_period = st.number_input(
        "Regime SMA period", min_value=50, max_value=500, value=200, step=10,
        help="SMA period for S&P 500 trend determination"
    )
    bull_threshold = st.slider(
        "Bull threshold (%)", min_value=-5.0, max_value=10.0, value=0.0, step=0.5,
        help="S&P 500 must be at least this % above SMA to be 'bull trend'"
    )
    correction_threshold = st.slider(
        "Correction threshold (%)", min_value=-20.0, max_value=0.0, value=-5.0, step=1.0,
        help="S&P 500 between this and bull threshold = correction zone"
    )
    allow_correction = st.checkbox(
        "Allow trading in correction", value=False,
        help="If checked, trading is allowed during mild corrections"
    )
    vix_threshold = st.slider(
        "VIX threshold", min_value=20.0, max_value=50.0, value=30.0, step=1.0,
        help="If VIX exceeds this, market is considered choppy/unfavorable"
    )

    st.markdown("**📅 Earnings Blackout**")
    earnings_days_before = st.number_input(
        "Days before earnings", min_value=0, max_value=14, value=2, step=1,
        help="Block entry this many days before earnings"
    )
    earnings_days_after = st.number_input(
        "Days after earnings", min_value=0, max_value=14, value=1, step=1,
        help="Block entry this many days after earnings"
    )
    exit_before_earnings = st.checkbox(
        "Exit before earnings", value=False,
        help="If checked, also exit existing positions before earnings"
    )

    st.markdown("**💰 Portfolio Risk**")
    max_total_risk = st.slider(
        "Max total portfolio risk (%)", min_value=2, max_value=20, value=8, step=1,
        help="Total portfolio risk cap as % of equity"
    ) / 100.0
    min_reward_risk = st.slider(
        "Min reward:risk ratio", min_value=1.0, max_value=5.0, value=2.0, step=0.5,
        help="Minimum reward-to-risk ratio to consider a trade"
    )
    stop_atr_mult = st.slider(
        "Stop-loss ATR multiplier", min_value=1.0, max_value=3.0, value=1.5, step=0.1,
        help="Stop-loss = entry price − this × ATR"
    )

st.sidebar.header("🚀 Mode")

paper_mode = st.sidebar.checkbox("Paper trading mode", value=True,
    help="Simulate trades without real money. Turn OFF for live.")

daily_loss_kill = st.sidebar.number_input(
    "Daily loss kill-switch (%)", value=-5.0, step=1.0, format="%.1f",
    help="Auto-halt trading if daily P&L drops below this %")

scan_clicked = st.sidebar.button("🔍 Scan Now", type="primary", use_container_width=True)

st.sidebar.divider()
show_calibration = st.sidebar.button("📊 Signal Decay Calibration")
show_backtest_page = st.sidebar.button("📜 Run Backtest")

# ------------------------------------------------------------------------
# Header
# ------------------------------------------------------------------------

st.title("📈 MetaTrader 5 Opportunity Scanner")
st.caption(
    f"Auto-scans S&P 500, Nasdaq 100, and ETFs for trend-following setups. "
    f"{'📄 Paper mode' if paper_mode else '🔴 LIVE mode'}"
)

# Show strategy explanation
with st.expander("🧠 How the screening works", expanded=False):
    st.markdown("""
    The bot scans every stock against **6 entry criteria**:
    
    1. **Long-term uptrend** — Price > 200-day MA
    2. **Medium-term alignment** — 50-day MA > 200-day MA (golden cross)
    3. **Consolidation** — Volatility contraction (tightening range)
    4. **Breakout** — Price closes above resistance (not intraday poke)
    5. **Volume confirmation** — Breakout on above-average volume
    6. **Relative strength** — Outperforming S&P 500 (for ranking)
    
    **Additional filters:**
    - 🛡️ **Market regime**: Blocks trading when S&P 500 is below its 200-day MA
    - 📅 **Earnings blackout**: Blocks entry around earnings announcements
    - 🏢 **Sector concentration**: Limits exposure to any single sector
    - 💰 **Portfolio risk cap**: Max 8% total portfolio risk
    """)

# ------------------------------------------------------------------------
# Tabs
# ------------------------------------------------------------------------

tab_scan, tab_positions, tab_history, tab_calibrate, tab_backtest = st.tabs(
    ["🔍 Scan", "💼 Positions", "📊 History", "📈 Calibration", "📜 Backtest"]
)

# ======================================================================
# TAB: Scan
# ======================================================================

with tab_scan:
    if show_calibration:
        st.subheader("📊 Signal Decay Calibration")
        calibration = compute_calibration()
        if calibration:
            rows = []
            for c in calibration:
                rows.append({
                    "Bucket": f"{c.bucket_min:.0%}-{c.bucket_max:.0%}",
                    "Signals": c.total_signals,
                    "Wins": c.winning,
                    "Losses": c.losing,
                    "Win Rate": f"{c.empirical_win_rate:.0f}%",
                    "Avg P&L": f"{c.avg_pnl_pct:+.1f}%",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No completed signals in decay log yet. Signals will be recorded automatically as you trade.")
        st.stop()

    if not scan_clicked:
        st.info(
            "👈 Select which market to scan in the sidebar, then click **Scan Now**.\n\n"
            "The bot will automatically discover which stocks to invest in."
        )
    else:
        # Build the universe
        if universe_option == "S&P 500 (100 stocks)":
            universe_base = SP500_TICKERS
        elif universe_option == "Nasdaq 100 (100 stocks)":
            universe_base = NASDAQ100_TICKERS
        elif universe_option == "S&P 500 + Nasdaq 100 (combined, ~150 unique)":
            universe_base = list(set(SP500_TICKERS + NASDAQ100_TICKERS))
        elif universe_option == "Major ETFs (20)":
            universe_base = MAJOR_ETFS
        elif universe_option == "All of the above (~170 stocks)":
            universe_base = list(set(SP500_TICKERS + NASDAQ100_TICKERS + MAJOR_ETFS))
        elif universe_option == "Watchlist file":
            universe_base = custom_watchlist
        else:
            universe_base = custom_watchlist

        if not universe_base:
            st.error("Universe is empty. Configure a valid universe in the sidebar.")
            st.stop()

        # Connect to MT5
        client = None
        try:
            client = MetaTrader5Client(
                host=mt5_host,
                port=int(mt5_port),
            )
            if not client.connect():
                st.warning("⚠️  Could not connect to MT5. Offline mode (demo data).")
                client = None
        except Exception as e:
            st.warning(f"⚠️  MT5 connection failed: {e}. Offline mode.")
            client = None

        # Fetch account data
        free_cash = 10000.0
        invested = 0.0
        total_value = 10000.0
        portfolio: list[dict] = []
        instruments: list[dict] = []

        if client:
            with st.spinner("Fetching account data from MT5..."):
                try:
                    cash = client.get_cash()
                    portfolio = client.get_portfolio()
                    instruments = client.get_instruments()
                except Exception as e:
                    st.error(f"Failed to reach MT5: {e}")

            free_cash = float(cash.get("free", 0))
            invested = float(cash.get("invested", 0))
            total_value = float(cash.get("total", free_cash + invested))

        # Apply all UI customisations to config objects
        # --- Entry criteria ---
        ENTRY.sma_long_period = sma_long_period
        ENTRY.sma_short_period = sma_short_period
        ENTRY.consolidation_lookback = consolidation_lookback
        ENTRY.prior_lookback = prior_lookback
        ENTRY.atr_period = atr_period
        ENTRY.atr_contraction_ratio = atr_contraction_ratio
        ENTRY.breakout_lookback = breakout_lookback
        ENTRY.require_close_above_resistance = require_close_above_resistance
        ENTRY.vol_avg_period = vol_avg_period
        ENTRY.vol_multiplier = vol_multiplier
        ENTRY.rs_lookback_months = rs_lookback_months

        # --- Risk settings ---
        RISK.risk_per_trade = risk_fraction
        RISK.max_position_value = max_position_value
        RISK.max_sector_exposure = max_sector_exposure
        RISK.max_total_risk = max_total_risk
        RISK.min_reward_risk = min_reward_risk
        RISK.stop_atr_mult = stop_atr_mult

        # --- Regime filter ---
        REGIME.sma_period = regime_sma_period
        REGIME.bull_threshold = bull_threshold
        REGIME.correction_threshold = correction_threshold
        REGIME.allow_correction = allow_correction
        REGIME.vix_threshold = vix_threshold

        # --- Earnings blackout ---
        EARNINGS_BLACKOUT.enabled = enable_earnings_filter
        EARNINGS_BLACKOUT.days_before = earnings_days_before
        EARNINGS_BLACKOUT.days_after = earnings_days_after
        EARNINGS_BLACKOUT.exit_before_earnings = exit_before_earnings

        # Account overview
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Free margin", f"${free_cash:,.2f}")
        col2.metric("Invested", f"${invested:,.2f}")
        col3.metric("Total equity", f"${total_value:,.2f}")
        col4.metric("Universe", f"{len(universe_base)} stocks")
        col5.metric("Paper trading", "✅ ON" if paper_mode else "🔴 OFF")

        # Phase 1: Quick filter
        st.subheader("🔍 Scanning...")
        phase1 = st.empty()
        phase1.info(f"Phase 1/3: Filtering by price (>$5) and liquidity (>500k avg vol)...")

        filtered_tickers = filter_universe(
            universe_base,
            min_price=5.0,
            min_avg_volume=500_000,
        )

        phase1.success(
            f"Phase 1/3: {len(filtered_tickers)}/{len(universe_base)} passed filters"
        )

        if not filtered_tickers:
            st.warning("No stocks passed filters.")
            st.stop()

        # Phase 2: Regime check
        bench_df = fetch_benchmark()
        vix_df = fetch_ohlcv("^VIX")
        regime_result = check_market_regime(bench_df, vix_df)

        if enable_regime_filter:
            regime_ok = st.empty()
            if regime_result.is_favorable:
                regime_ok.success(f"✅ Market Regime: {regime_result.regime} — Favorable for trend-following")
            else:
                regime_ok.error(f"❌ Market Regime: {regime_result.regime} — Trading disabled by regime filter")
                if not paper_mode:
                    st.warning("Regime filter blocks trading. Override by unchecking 'Market regime filter' in sidebar.")

        # Phase 2: Run analysis
        phase2 = st.info(f"Phase 2/3: Analysing {len(filtered_tickers)} stocks...")
        progress_bar = st.progress(0.0, text="Analysing...")

        account_equity = total_value
        signals: list[SignalResult] = []
        errors = 0

        for i, ticker in enumerate(filtered_tickers):
            try:
                signal = analyze_symbol(
                    ticker=ticker,
                    account_equity=account_equity,
                    existing_positions=portfolio,
                    bench_df=bench_df,
                    vix_df=vix_df,
                )
                signals.append(signal)
            except Exception as e:
                errors += 1
                signals.append(SignalResult(
                    ticker=ticker, current_price=0, last_close=0,
                    entry_result=CompositeEntryResult(
                        passed=False, confidence=0, summary=f"Error: {e}"
                    ),
                ))

            if (i + 1) % 10 == 0 or i == len(filtered_tickers) - 1:
                progress_bar.progress((i + 1) / len(filtered_tickers),
                                      text=f"Analysed {i+1}/{len(filtered_tickers)}...")

        progress_bar.empty()
        phase2.success(f"Phase 2/3: {len(signals)} analysed ({errors} errors)")

        if client:
            client.disconnect()

        # Phase 3: Rank & display
        # Classify
        buy_signals = [s for s in signals if s.action == "BUY"]
        sell_signals = [s for s in signals if s.action in ("SELL", "TRIM")]
        hold_signals = [s for s in signals if s.action == "HOLD"]
        held_capped = [s for s in signals if s.action == "HOLD_CAPPED"]
        held_regime = [s for s in signals if s.action == "HOLD_REGIME"]
        held_earnings = [s for s in signals if s.action == "HOLD_EARNINGS"]
        held_sector = [s for s in signals if s.action == "HOLD_SECTOR"]
        error_signals = [s for s in signals if s.current_price == 0]

        # Rank BUY signals by confidence
        buy_signals.sort(key=lambda s: s.confidence, reverse=True)
        buy_signals = [s for s in buy_signals if s.confidence >= min_confidence]

        st.subheader("🎯 Recommendations")
        st.caption("Phase 3/3 complete")

        # Summary stats
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("🟢 BUY", len(buy_signals))
        col2.metric("🔴 SELL/TRIM", len(sell_signals))
        col3.metric("🟡 Capped", len(held_capped))
        col4.metric("⛔ Regime", len(held_regime))
        col5.metric("📅 Earnings", len(held_earnings))
        col6.metric("🏢 Sector", len(held_sector))

        if not buy_signals:
            st.warning(
                "No stocks met all criteria today. Trend-following is selective. "
                "Try again tomorrow or expand the universe."
            )
        else:
            # Top pick highlight
            top = buy_signals[0]
            st.success(f"### 🏆 Top Pick: {top.ticker} — Confidence {top.confidence:.0%}")

            if top.sizing:
                rr_color = "🟢" if (top.sizing.reward_risk_ratio or 0) >= 3 else "🟡" if (top.sizing.reward_risk_ratio or 0) >= 2 else "🔴"
                st.markdown(
                    f"**Entry:** ${top.current_price:.2f} | "
                    f"**Stop:** ${top.sizing.stop_loss_price:.2f} | "
                    f"**Target:** ${top.sizing.target_price:.2f} | "
                    f"{rr_color} **R:R:** {top.sizing.reward_risk_ratio:.1f}:1 | "
                    f"**Shares:** {top.sizing.recommended_shares} | "
                    f"**Risk:** ${top.sizing.total_risk_dollars:.2f}"
                )

            if top.regime_check:
                st.caption(f"📊 Regime: {top.regime_check.summary}")
            if top.earnings_check:
                st.caption(f"📅 Earnings: {top.earnings_check.blackout_reason}")
            if top.context_notes:
                st.caption(f"📝 Context: {top.context_notes}")

            if top.info:
                st.caption(f"🏢 {top.info.get('sector', '?')} | {top.info.get('industry', '?')}")

            # Full ranked table
            st.subheader(f"📊 Ranked BUY Opportunities ({len(buy_signals)})")

            rows = []
            for rank, s in enumerate(buy_signals, 1):
                sizing = s.sizing
                sector = s.info.get("sector", "N/A") if s.info else "N/A"
                rr_val = sizing.reward_risk_ratio if sizing else 0
                rows.append({
                    "Rank": rank,
                    "Symbol": s.ticker,
                    "Sector": sector[:20],
                    "Price": round(s.current_price, 2),
                    "Confidence": f"{s.confidence:.0%}",
                    "Signal": "🟢 BUY",
                    "Qty": sizing.recommended_shares if sizing else "",
                    "Stop": f"${sizing.stop_loss_price:.2f}" if sizing else "",
                    "Target": f"${sizing.target_price:.2f}" if sizing else "",
                    "R:R": f"{sizing.reward_risk_ratio:.1f}" if sizing else "",
                    "Risk $": f"${sizing.total_risk_dollars:.0f}" if sizing else "",
                })

            df_buys = pd.DataFrame(rows)
            st.dataframe(df_buys, use_container_width=True, hide_index=True,
                         column_config={
                             "Confidence": st.column_config.ProgressColumn(
                                 "Confidence", format="%.0f%%",
                                 min_value=0, max_value=100,
                             ),
                         })

            # Per-stock signal breakdown (drill-down)
            st.subheader("📋 Per-Stock Signal Breakdown")
            for s in buy_signals[:10]:  # Top 10
                rank = buy_signals.index(s) + 1
                with st.expander(f"#{rank} {s.ticker} — ${s.current_price:.2f} (conf: {s.confidence:.0%})"):
                    col_left, col_mid, col_right = st.columns([2, 2, 2])

                    with col_left:
                        st.markdown("**Entry Rules Breakdown:**")
                        if s.entry_result.rule_results:
                            for rule in s.entry_result.rule_results:
                                icon = "✅" if rule.passed else "❌"
                                st.markdown(f"{icon} **{rule.rule_name}**")
                                st.caption(rule.details[:200])

                    with col_mid:
                        st.markdown("**Position Sizing:**")
                        if s.sizing:
                            st.markdown(s.sizing.details)
                        st.markdown("**Regime:**")
                        if s.regime_check:
                            st.markdown(f"{'✅' if s.regime_check.is_favorable else '❌'} {s.regime_check.regime}")
                        st.markdown("**Earnings:**")
                        if s.earnings_check:
                            st.markdown(f"{'⛔' if s.earnings_check.in_blackout else '✅'} {s.earnings_check.blackout_reason}")

                    with col_right:
                        st.markdown("**Portfolio Check:**")
                        if s.portfolio_check:
                            st.markdown(s.portfolio_check.details)
                        st.markdown("**Context:**")
                        if s.context_notes:
                            st.markdown(s.context_notes)

        # Exit signals
        if sell_signals:
            st.subheader("🔴 Exit Signals")
            sell_rows = []
            for s in sell_signals:
                exit_reason = s.exit_result.summary if s.exit_result else ""
                sell_rows.append({
                    "Symbol": s.ticker,
                    "Action": s.action,
                    "Price": round(s.current_price, 2),
                    "Confidence": f"{s.confidence:.0%}",
                    "Reason": exit_reason[:100] if exit_reason else "",
                })
            st.dataframe(pd.DataFrame(sell_rows), use_container_width=True, hide_index=True)

        # Blocked signals explanation
        blocked_signals = held_regime + held_earnings + held_capped + held_sector
        if blocked_signals:
            st.subheader("🟡 Blocked / Capped Signals")
            blocked_rows = []
            for s in blocked_signals[:20]:
                reason = s.action.replace("HOLD_", "").title()
                blocked_rows.append({
                    "Symbol": s.ticker,
                    "Reason": reason,
                    "Confidence": f"{s.confidence:.0%}",
                    "Price": round(s.current_price, 2),
                })
            st.dataframe(pd.DataFrame(blocked_rows), use_container_width=True, hide_index=True)

        # CSV Export
        st.subheader("📥 Export")
        if st.button("Export all signals to CSV"):
            csv_rows = []
            for s in signals:
                csv_rows.append({
                    "ticker": s.ticker,
                    "action": s.action,
                    "price": s.current_price,
                    "confidence": s.confidence,
                    "entry_passed": s.entry_result.passed,
                    "regime": s.regime_check.regime if s.regime_check else "",
                    "regime_fav": s.regime_check.is_favorable if s.regime_check else "",
                    "earnings_blackout": s.earnings_check.in_blackout if s.earnings_check else "",
                    "rr": s.sizing.reward_risk_ratio if s.sizing else "",
                    "shares": s.sizing.recommended_shares if s.sizing else "",
                    "stop": s.sizing.stop_loss_price if s.sizing else "",
                    "target": s.sizing.target_price if s.sizing else "",
                    "sector": s.info.get("sector", "") if s.info else "",
                    "rationale": s.rationale,
                })

            df_export = pd.DataFrame(csv_rows)
            csv_data = df_export.to_csv(index=False)
            st.download_button(
                label="⬇️ Download CSV",
                data=csv_data,
                file_name=f"signals_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
            )

# ======================================================================
# TAB: Positions
# ======================================================================

with tab_positions:
    st.subheader("💼 Live Position Management")

    # Try to connect to MT5 for current positions
    pos_client = None
    try:
        pos_client = MetaTrader5Client(host=mt5_host, port=int(mt5_port))
        pos_client.connect()
    except Exception:
        pass

    if pos_client:
        try:
            portfolio_raw = pos_client.get_portfolio()
            cash_data = pos_client.get_cash()

            if portfolio_raw:
                pos_df = pd.DataFrame(portfolio_raw)
                st.dataframe(pos_df, use_container_width=True)

                # Calculate sector exposure
                total_value = float(cash_data.get("total", 0))
                if total_value > 0:
                    st.subheader("🏢 Sector Exposure")
                    # Group by sector if available
                    if "sector" in pos_df.columns:
                        sector_summary = pos_df.groupby("sector")["value"].sum() if "value" in pos_df.columns else None
                        if sector_summary is not None:
                            sector_pct = sector_summary / total_value * 100
                            sector_df = pd.DataFrame({
                                "Sector": sector_summary.index,
                                "Exposure $": sector_summary.values,
                                "Exposure %": [f"{p:.1f}%" for p in sector_pct.values],
                                "Within Cap": ["✅" if p <= max_sector_exposure * 100 else "❌" for p in sector_pct.values],
                            })
                            st.dataframe(sector_df, use_container_width=True, hide_index=True)
            else:
                st.info("No open positions.")

            # Account summary
            col1, col2, col3 = st.columns(3)
            col1.metric("Free margin", f"${float(cash_data.get('free', 0)):,.2f}")
            col2.metric("Invested", f"${float(cash_data.get('invested', 0)):,.2f}")
            col3.metric("Total", f"${float(cash_data.get('total', 0)):,.2f}")

        except Exception as e:
            st.error(f"Failed to fetch positions: {e}")

        pos_client.disconnect()
    else:
        st.info("Connect to MT5 via the sidebar to see live positions.")

    # Blacklist management
    st.subheader("⛔ Blacklist Management")
    current_blacklist = load_blacklist()
    if current_blacklist:
        st.write(f"Currently blacklisted: {', '.join(sorted(current_blacklist))}")
    else:
        st.write("No blacklisted tickers.")

    new_blacklist = st.text_input("Add tickers to blacklist (comma-separated)", "")
    if st.button("Save Blacklist") and new_blacklist:
        tickers_to_add = [t.strip().upper() for t in new_blacklist.split(",") if t.strip()]
        current_blacklist.update(tickers_to_add)
        with open(BLACKLIST_FILE, "w") as f:
            for t in sorted(current_blacklist):
                f.write(f"{t}\n")
        st.success(f"Added {len(tickers_to_add)} tickers to blacklist: {', '.join(tickers_to_add)}")
        st.rerun()

# ======================================================================
# TAB: History
# ======================================================================

with tab_history:
    st.subheader("📊 Scan History")

    # List all CSV files in logs directory
    log_path = Path(LOG_DIR)
    csv_files = sorted(log_path.glob("signals_*.csv"), reverse=True)

    if csv_files:
        selected_file = st.selectbox(
            "Select a scan date to view historical signals",
            options=[f.name for f in csv_files],
            format_func=lambda x: x.replace("signals_", "").replace(".csv", ""),
        )

        if selected_file:
            df_history = pd.read_csv(log_path / selected_file)
            st.dataframe(df_history, use_container_width=True)

            # Summary stats for that day
            if "action" in df_history.columns:
                col1, col2, col3 = st.columns(3)
                col1.metric("BUY signals", len(df_history[df_history["action"] == "BUY"]))
                col2.metric("SELL/TRIM", len(df_history[df_history["action"].isin(["SELL", "TRIM"])]))
                col3.metric("Total scanned", len(df_history))
    else:
        st.info("No scan history found. Run scans first.")

    # Daily P&L history
    st.subheader("💰 Daily P&L History")
    conn = __import__("sqlite3").connect(DB_PATH)
    try:
        df_pnl = pd.read_sql_query(
            "SELECT date, starting_equity, current_equity, pnl, pnl_pct "
            "FROM daily_pnl ORDER BY id DESC LIMIT 30",
            conn,
        )
        if not df_pnl.empty:
            df_pnl["pnl_pct_str"] = df_pnl["pnl_pct"].apply(lambda x: f"{x:+.1f}%")
            st.dataframe(df_pnl, use_container_width=True, hide_index=True)

            # Quick chart
            st.subheader("P&L Over Time")
            st.line_chart(df_pnl.set_index("date")["pnl"].reindex(index=df_pnl["date"][::-1]))
        else:
            st.info("No daily P&L data yet.")
    except Exception:
        st.info("No daily P&L data yet.")
    finally:
        conn.close()

    # Kill switch status
    st.subheader("🛡️ Kill Switch Status")
    ks_triggered, ks_reason = check_daily_pnl_kill_switch()
    if ks_triggered:
        st.error(f"⚠️ Kill-switch ACTIVE: {ks_reason}")
    else:
        st.success(f"✅ Kill-switch not triggered. {ks_reason}")

    # Toggle kill switch file
    kill_file = Path(KILL_SWITCH_FILE)
    col1, col2 = st.columns(2)
    with col1:
        if not kill_file.exists():
            if st.button("🔴 Enable Kill Switch", type="secondary"):
                kill_file.touch()
                st.success("Kill switch enabled! All trading halted.")
                st.rerun()
    with col2:
        if kill_file.exists():
            if st.button("✅ Disable Kill Switch", type="primary"):
                kill_file.unlink()
                st.success("Kill switch disabled. Trading resumed.")
                st.rerun()

# ======================================================================
# TAB: Calibration
# ======================================================================

with tab_calibrate:
    st.subheader("📈 Signal Decay Calibration")
    st.caption(
        "Shows how well the bot's confidence scores predict actual outcomes. "
        "If the 80-90% bucket only wins 60% of the time, confidence is overconfident."
    )

    calibration = compute_calibration(min_signals_per_bucket=3)
    if calibration:
        cal_rows = []
        for c in calibration:
            cal_rows.append({
                "Confidence Bucket": f"{c.bucket_min:.0%}-{c.bucket_max:.0%}",
                "Total Signals": c.total_signals,
                "Wins": c.winning,
                "Losses": c.losing,
                "Empirical Win Rate": f"{c.empirical_win_rate:.0f}%",
                "Avg P&L": f"{c.avg_pnl_pct:+.1f}%",
            })

        df_cal = pd.DataFrame(cal_rows)
        st.dataframe(df_cal, use_container_width=True, hide_index=True)

        # Visual: confidence vs actual win rate
        st.subheader("Confidence vs Actual Win Rate")
        chart_data = pd.DataFrame({
            "Confidence Band": [f"{c.bucket_min:.0%}-{c.bucket_max:.0%}" for c in calibration],
            "Empirical Win Rate": [c.empirical_win_rate for c in calibration],
        })
        st.bar_chart(chart_data.set_index("Confidence Band"))

        best_bucket = max(calibration, key=lambda c: c.empirical_win_rate if c.total_signals >= 5 else 0)
        st.info(
            f"Best performing confidence band: **{best_bucket.bucket_min:.0%}-{best_bucket.bucket_max:.0%}** "
            f"with {best_bucket.empirical_win_rate:.0f}% win rate "
            f"({best_bucket.total_signals} signals)"
        )
    else:
        st.info("No completed signals in decay log yet. Signals are recorded automatically as you run scans and trades.")

    # Calibration note
    with st.expander("📖 How to use signal decay calibration"):
        st.markdown("""
        **What this shows:**
        
        For each confidence bucket (e.g., 80-90%), this shows how often those signals
        actually resulted in winning trades. This helps you:
        
        1. **Trust your confidence scores** — If 90% confidence actually wins 85% of the time,
           the system is well-calibrated.
        2. **Set better thresholds** — If the 30-40% bucket only wins 20%, increase your
           minimum confidence threshold.
        3. **Detect overconfidence** — If low-confidence signals perform as well as high-confidence
           ones, the confidence calculation needs adjustment.
        
        **Minimum signals:** A bucket needs at least 5 completed trades before its win rate
        becomes statistically meaningful.
        """)

# ======================================================================
# TAB: Backtest (inline redirect)
# ======================================================================

with tab_backtest:
    st.subheader("📜 Historical Backtest")
    st.markdown("""
    Run a historical backtest to see how the strategy would have performed.
    
    Use the command line tool for full backtesting:
    ```bash
    python run_backtest.py --ticker AAPL MSFT NVDA --start 2022-01-01
    python run_backtest.py --all-sp500 --start 2023-01-01
    ```
    
    Or configure a quick backtest here:
    """)

    bt_tickers = st.text_input("Tickers (comma-separated)", value="AAPL, MSFT, NVDA")
    bt_start = st.date_input("Start date", value=datetime.now() - timedelta(days=365 * 3))
    bt_end = st.date_input("End date", value=datetime.now())
    bt_equity = st.number_input("Starting equity ($)", value=100_000, step=10_000)

    if st.button("Run Backtest (this may take a while)", type="primary"):
        tickers = [t.strip().upper() for t in bt_tickers.split(",") if t.strip()]
        st.info(f"Running backtest for {', '.join(tickers)} from {bt_start} to {bt_end}...")

        try:
            from strategy.backtest import backtest_symbol

            results = []
            progress = st.progress(0.0)
            for i, ticker in enumerate(tickers):
                result = backtest_symbol(
                    ticker,
                    start_date=bt_start.strftime("%Y-%m-%d"),
                    end_date=bt_end.strftime("%Y-%m-%d"),
                    initial_equity=bt_equity,
                    risk_per_trade=risk_fraction,
                )
                results.append(result)
                progress.progress((i + 1) / len(tickers))

            st.success(f"Backtest complete for {len(tickers)} tickers")

            # Display results
            for result in results:
                with st.expander(f"{result.ticker} — {result.total_trades} trades (Win: {result.win_rate:.0f}%)"):
                    col1, col2, col3, col4, col5 = st.columns(5)
                    col1.metric("Win Rate", f"{result.win_rate:.0f}%")
                    col2.metric("Total P&L", f"${result.total_pnl:+,.2f}" if result.total_pnl else "$0")
                    col3.metric("Profit Factor", f"{result.profit_factor:.2f}")
                    col4.metric("Max DD", f"{result.max_drawdown:.1f}%")
                    col5.metric("Avg Hold", f"{result.avg_bars_held:.0f}d")

                    if result.total_pnl_pct:
                        st.metric("Return", f"{result.total_pnl_pct:+.1f}%")

                    if result.trades:
                        st.markdown("**Recent Trades:**")
                        trade_rows = []
                        for t in result.trades[-10:]:
                            pnl_icon = "✅" if t.pnl > 0 else "❌"
                            trade_rows.append({
                                "Entry": t.entry_date.strftime("%Y-%m-%d") if t.entry_date else "",
                                "Exit": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "",
                                "Entry $": f"${t.entry_price:.2f}",
                                "Exit $": f"${t.exit_price:.2f}" if t.exit_price else "",
                                "P&L": f"{pnl_icon} ${t.pnl:+,.2f}",
                                "Return": f"{t.pnl_pct:+.1f}%",
                                "Reason": t.exit_reason[:50],
                            })
                        st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)

        except Exception as e:
            st.error(f"Backtest failed: {e}")

    st.markdown("---")
    st.caption("For full backtesting with all features, use: `python run_backtest.py --help`")

# ------------------------------------------------------------------------
# Footer
# ------------------------------------------------------------------------

st.divider()
st.caption(
    f"MetaTrader 5 Bot — Running in {'📄 Paper' if paper_mode else '🔴 Live'} mode | "
    f"Risk per trade: {risk_fraction:.1%} | "
    f"Sector cap: {max_sector_exposure:.0%} | "
    f"Regime filter: {'ON' if enable_regime_filter else 'OFF'} | "
    f"Earnings filter: {'ON' if enable_earnings_filter else 'OFF'}"
)