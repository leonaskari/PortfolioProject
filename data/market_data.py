"""
Market data adapter — fetches OHLCV data for analysis.

Currently uses yfinance (Yahoo Finance) as the provider. The interface is
designed so the provider can be swapped (e.g. to Alpha Vantage, Polygon.io,
Twelve Data) without changing any strategy code.

All functions return pandas DataFrames with columns:
    'Open', 'High', 'Low', 'Close', 'Volume'
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

from config import HISTORY_DAYS, INTRADAY_INTERVAL

logger = logging.getLogger(__name__)


def fetch_ohlcv(
    ticker: str,
    period: str | None = None,
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Fetch daily OHLCV data for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').
        period: Data period string (e.g. '1y', '6mo', 'max').
                Defaults to HISTORY_DAYS trading days.
        interval: Data interval ('1d' for daily, '1wk', '1mo').

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume.
        Index is DatetimeIndex. Returns empty DataFrame on failure.
    """
    if period is None:
        period = f"{HISTORY_DAYS}d"

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)

        if df.empty:
            logger.warning("No data returned for %s (period=%s, interval=%s)", ticker, period, interval)
            return pd.DataFrame()

        # Standardise column names (yfinance returns 'Adj Close' as well)
        df = df.rename(columns={
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume",
        })

        # Keep only the columns we need
        cols = ["Open", "High", "Low", "Close", "Volume"]
        df = df[[c for c in cols if c in df.columns]]

        # Drop rows with NaN in critical columns
        df = df.dropna(subset=["Close", "Volume"])

        logger.info("Fetched %d rows for %s", len(df), ticker)
        return df

    except Exception as e:
        logger.error("Failed to fetch data for %s: %s", ticker, e)
        return pd.DataFrame()


def fetch_intraday(ticker: str, interval: str = "5m") -> pd.DataFrame:
    """
    Fetch intraday OHLCV data.

    Note: yfinance intraday data is limited to the last 7 days for intervals
    <= 1h, and last 60 days for 1h. For production, consider a paid provider.

    Args:
        ticker: Stock ticker symbol.
        interval: Intraday interval ('1m', '5m', '15m', '30m', '1h').

    Returns:
        DataFrame with OHLCV columns, or empty on failure.
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="7d", interval=interval)

        if df.empty:
            return pd.DataFrame()

        cols = ["Open", "High", "Low", "Close", "Volume"]
        df = df[[c for c in cols if c in df.columns]]
        return df

    except Exception as e:
        logger.error("Failed to fetch intraday data for %s: %s", ticker, e)
        return pd.DataFrame()


def fetch_benchmark(interval: str = "1d") -> pd.DataFrame:
    """
    Fetch S&P 500 benchmark data for relative strength calculations.

    Returns:
        DataFrame with OHLCV for ^GSPC, or empty on failure.
    """
    return fetch_ohlcv("^GSPC", interval=interval)


def fetch_multiple(
    tickers: list[str],
    period: str | None = None,
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV data for multiple tickers.

    Args:
        tickers: List of ticker symbols.
        period: Data period string.
        interval: Data interval.

    Returns:
        Dict mapping ticker -> DataFrame.
    """
    result: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = fetch_ohlcv(ticker, period, interval)
        if not df.empty:
            result[ticker] = df
    return result


def get_ticker_info(ticker: str) -> dict[str, Any]:
    """
    Fetch fundamental info for a ticker (sector, market cap, etc.).

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict with info fields, or empty dict on failure.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap", 0),
            "avg_volume": info.get("averageVolume", 0),
            "current_price": info.get("currentPrice", info.get("regularMarketPrice", 0)),
            "name": info.get("longName", ticker),
            "currency": info.get("currency", "USD"),
        }
    except Exception as e:
        logger.warning("Failed to fetch info for %s: %s", ticker, e)
        return {}


def filter_universe(
    tickers: list[str],
    min_price: float = 5.0,
    min_avg_volume: int = 500_000,
) -> list[str]:
    """
    Filter a list of tickers by minimum price and average volume.

    Args:
        tickers: List of ticker symbols to filter.
        min_price: Minimum current price.
        min_avg_volume: Minimum average daily volume.

    Returns:
        Filtered list of tickers that pass the liquidity/price checks.
    """
    passed: list[str] = []
    for ticker in tickers:
        info = get_ticker_info(ticker)
        price = info.get("current_price", 0)
        vol = info.get("avg_volume", 0)

        if price >= min_price and vol >= min_avg_volume:
            passed.append(ticker)
        else:
            logger.info(
                "Filtered out %s: price=%.2f (min=%.2f), avg_vol=%d (min=%d)",
                ticker, price, min_price, vol, min_avg_volume,
            )

    logger.info("Universe filter: %d/%d tickers passed", len(passed), len(tickers))
    return passed


# ---------------------------------------------------------------------------
# Predefined broad universes for automatic discovery
# ---------------------------------------------------------------------------

# S&P 500 constituents (current as of 2025 major holdings)
# In production, fetch this dynamically from a source like Wikipedia or an API
SP500_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "BRK.B", "TSLA", "AVGO",
    "JPM", "V", "PG", "JNJ", "WMT", "XOM", "MA", "COST", "UNH", "HD",
    "ORCL", "BAC", "NFLX", "CRM", "ABBV", "CVX", "KO", "ADBE", "PEP", "DIS",
    "AMD", "LIN", "TMO", "QCOM", "ACN", "MCD", "ABT", "CMCSA", "VZ", "DHR",
    "NKE", "TXN", "NEE", "PM", "IBM", "LOW", "INTU", "BA", "SPGI", "GS",
    "AMAT", "MS", "C", "CAT", "RTX", "GE", "HON", "PFE", "BLK", "T",
    "PLD", "UNP", "COP", "LMT", "AXP", "NOW", "SBUX", "AMGN", "ELV", "ADI",
    "GILD", "MDT", "ISRG", "ETN", "MMC", "ADP", "LRCX", "BDX", "MU", "SYK",
    "DE", "TMUS", "SCHW", "ZTS", "CL", "CB", "DUK", "ITW", "BMY", "SO",
    "UPS", "ATVI", "SNPS", "PGR", "CI", "TGT", "BSX", "CME", "EOG", "NXPI",
]

# Nasdaq 100 constituents (major tech/growth)
NASDAQ100_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO", "COST",
    "NFLX", "AMD", "ADBE", "PEP", "QCOM", "TXN", "INTU", "AMAT", "SBUX", "AMGN",
    "GILD", "ISRG", "ADI", "LRCX", "MU", "SYK", "ATVI", "SNPS", "CME", "NXPI",
    "CSCO", "INTC", "MRVL", "WBD", "BKNG", "MDLZ", "REGN", "VRTX", "PANW", "KLAC",
    "ASML", "FTNT", "MCHP", "CDNS", "MAR", "ABNB", "DASH", "TEAM", "CRWD", "WDAY",
    "PYPL", "ROP", "ORLY", "PAYX", "AZN", "FAST", "ODFL", "BIIB", "ILMN", "EA",
    "ANSS", "CTSH", "EBAY", "MRNA", "DXCM", "IDXX", "VRSK", "ALGN", "CHTR", "EXC",
    "KDP", "MELI", "MNST", "PDD", "DLTR", "SIRI", "CPRT", "SWKS", "LULU", "TTWO",
    "VRSN", "WBA", "XEL", "ZM", "ZS", "LCID", "RIVN", "HOOD", "ROKU", "DOCU",
    "OKTA", "DDOG", "MDB", "ESTC", "CFLT", "NET", "PATH", "TOST", "GTLB", "FVRR",
]

# Major ETFs (for quick market exposure scanning)
MAJOR_ETFS: list[str] = [
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV", "VEA", "VWO", "BND",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE",
]