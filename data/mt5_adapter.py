"""
MetaTrader 5 adapter — a thin wrapper around the MetaTrader 5 trading terminal.

Provides the same interface as the Trading 212 adapter so the rest of the
bot code doesn't need to change. Uses the mt5linux package which works on
macOS via a network connection to the MT5 terminal.

MetaTrader 5 must be running with the MT5 bridge enabled.

Key differences from Trading 212:
  - MT5 uses symbol names (e.g. "AAPL", "EURUSD") instead of tickers
  - MT5 uses lot sizes / volume instead of share quantities
  - MT5 has built-in stop-loss and take-profit in orders
  - MT5 provides positions, orders, and history natively
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import KILL_SWITCH_FILE

logger = logging.getLogger(__name__)

# Try to import mt5linux; fall back gracefully if not installed
try:
    from mt5linux import MetaTrader5 as _Mt5Client

    # Constants are accessible as class attributes on MetaTrader5
    # (inherited from the Constants base class)
    MT5 = _Mt5Client  # alias for constant access

    MT5_AVAILABLE = True
except ImportError:
    _Mt5Client = None
    MT5_AVAILABLE = False
    logger.warning("mt5linux not installed. MT5 adapter will not work.")


class MetaTrader5Client:
    """
    Client for MetaTrader 5 trading terminal.

    Connects to a running MT5 terminal via the mt5linux bridge.
    Default host is localhost:18812 (the default mt5linux bridge port).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 18812,
        timeout: int = 300,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
    ):
        """
        Args:
            host: MT5 bridge host (default: localhost).
            port: MT5 bridge port (default: 18812).
            timeout: Connection timeout in seconds.
            login: MT5 account login number. Falls back to config.
            password: MT5 account password. Falls back to config.
            server: MT5 server name. Falls back to config.
        """
        if not MT5_AVAILABLE:
            raise ImportError(
                "mt5linux is not installed. Run: pip install mt5linux"
            )

        self.host = host
        self.port = port
        self.timeout = timeout

        # Fall back to config values
        if login is None:
            from config import MT5_LOGIN as cfg_login
            login = cfg_login
        if password is None:
            from config import MT5_PASSWORD as cfg_pass
            password = cfg_pass
        if server is None:
            from config import MT5_SERVER as cfg_server
            server = cfg_server

        self.login = login
        self.password = password
        self.server = server

        self._client: _Mt5Client | None = None
        self._connected = False

        logger.info(
            "MetaTrader5Client initialised (host=%s:%d, server=%s, login=%s)",
            host, port, server, login,
        )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Establish connection to the MT5 terminal via the bridge."""
        try:
            self._client = _Mt5Client(
                host=self.host, port=self.port, timeout=self.timeout
            )
            logger.info("Connected to MT5 bridge at %s:%d", self.host, self.port)

            # If login credentials are provided, authorize
            if self.login and self.password and self.server:
                authorized = self._client.login(
                    login=self.login,
                    password=self.password,
                    server=self.server,
                )
                if authorized:
                    logger.info(
                        "Authorized to MT5 account %s on %s",
                        self.login, self.server,
                    )
                    self._connected = True
                else:
                    error = self._client.last_error()
                    logger.error("MT5 login failed: %s", error)
                    self._connected = False
                    return False
            else:
                # Already connected via terminal
                self._connected = True

            return self._connected

        except Exception as e:
            logger.error("Failed to connect to MT5 bridge: %s", e)
            self._connected = False
            return False

    def disconnect(self):
        """Shut down the connection to the MT5 terminal."""
        if self._client:
            try:
                self._client.shutdown()
            except Exception:
                pass
        self._connected = False
        logger.info("Disconnected from MT5")

    def ensure_connected(self):
        """Ensure we're connected; attempt reconnect if not."""
        if not self._connected or not self._client:
            if not self.connect():
                raise RuntimeError("Cannot connect to MetaTrader 5 terminal")

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account_summary(self) -> dict[str, Any]:
        """Fetch account summary (balance, equity, margin, etc.)."""
        self.ensure_connected()
        info = self._client.account_info()
        if info is None:
            error = self._client.last_error()
            raise RuntimeError(f"Failed to get account info: {error}")

        return {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_free": info.margin_free,
            "margin_level": info.margin_level,
            "profit": info.profit,
            "currency": info.currency,
            "name": info.name,
            "server": info.server,
            "leverage": info.leverage,
            "trade_allowed": info.trade_allowed,
        }

    def get_cash(self) -> dict[str, Any]:
        """
        Fetch cash details.

        In MT5 terms: free margin = available cash, balance = total deposited,
        equity = current total value.
        """
        self.ensure_connected()
        info = self._client.account_info()
        if info is None:
            error = self._client.last_error()
            raise RuntimeError(f"Failed to get account info: {error}")

        return {
            "free": info.margin_free,
            "invested": info.balance - info.margin_free,
            "total": info.equity,
            "balance": info.balance,
            "margin": info.margin,
        }

    # ------------------------------------------------------------------
    # Portfolio (open positions)
    # ------------------------------------------------------------------

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Fetch all open positions."""
        self.ensure_connected()
        positions = self._client.positions_get()
        if positions is None:
            error = self._client.last_error()
            logger.warning("Failed to get positions: %s", error)
            return []

        result = []
        for pos in positions:
            result.append({
                "ticker": pos.symbol,
                "symbol": pos.symbol,
                "type": (
                    "BUY" if pos.type == _Mt5Client.POSITION_TYPE_BUY else "SELL"
                ),
                "volume": pos.volume,
                "price": pos.price_open,
                "current_price": pos.price_current,
                "avgPrice": pos.price_open,
                "quantity": int(pos.volume * 100),
                "profit": pos.profit,
                "swap": pos.swap,
                "commission": pos.commission,
                "stop_loss": pos.sl,
                "take_profit": pos.tp,
                "ticket": pos.ticket,
                "time": str(datetime.fromtimestamp(pos.time, tz=timezone.utc)),
                "magic": pos.magic,
                "comment": pos.comment,
            })
        return result

    # ------------------------------------------------------------------
    # Instruments / Symbols
    # ------------------------------------------------------------------

    def get_instruments(self) -> list[dict[str, Any]]:
        """Fetch all available symbols from MT5."""
        self.ensure_connected()
        symbols = self._client.symbols_get()
        if symbols is None:
            error = self._client.last_error()
            logger.warning("Failed to get symbols: %s", error)
            return []

        result = []
        for sym in symbols:
            result.append({
                "ticker": sym.name,
                "name": sym.description or sym.name,
                "type": "stock",
                "currencyCode": sym.currency_base,
                "profit_currency": sym.currency_profit,
                "digits": sym.digits,
                "point": sym.point,
                "trade_mode": sym.trade_mode,
                "volume_min": sym.volume_min,
                "volume_max": sym.volume_max,
                "volume_step": sym.volume_step,
            })
        return result

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        """Get detailed info for a specific symbol."""
        self.ensure_connected()
        info = self._client.symbol_info(symbol)
        if info is None:
            return None
        return {
            "ticker": info.name,
            "name": info.description or info.name,
            "digits": info.digits,
            "point": info.point,
            "spread": info.spread,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_mode": info.trade_mode,
            "currency_base": info.currency_base,
            "currency_profit": info.currency_profit,
            "trade_calc_mode": info.trade_calc_mode,
        }

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_rates(
        self,
        symbol: str,
        count: int = 100,
        timeframe: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical rates for a symbol.

        Args:
            symbol: MT5 symbol name (e.g. "AAPL", "EURUSD").
            count: Number of bars to fetch.
            timeframe: MT5 timeframe constant (default: TIMEFRAME_D1).

        Returns:
            List of dicts with keys: time, open, high, low, close, tick_volume,
            spread, real_volume.
        """
        if timeframe is None:
            timeframe = _Mt5Client.TIMEFRAME_D1

        self.ensure_connected()
        rates = self._client.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            error = self._client.last_error()
            logger.warning("Failed to get rates for %s: %s", symbol, error)
            return []

        result = []
        for rate in rates:
            result.append({
                "time": rate.time,
                "open": rate.open,
                "high": rate.high,
                "low": rate.low,
                "close": rate.close,
                "tick_volume": rate.tick_volume,
                "spread": rate.spread,
                "real_volume": rate.real_volume,
            })
        return result

    def get_last_price(self, symbol: str) -> dict[str, Any] | None:
        """Get the latest bid/ask for a symbol."""
        self.ensure_connected()
        tick = self._client.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "time": tick.time,
        }

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_market_order(
        self,
        symbol: str,
        volume: float,
        side: str = "BUY",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "Bot order",
        magic: int = 123456,
    ) -> dict[str, Any]:
        """
        Place a market order.

        Args:
            symbol: MT5 symbol name (e.g. "AAPL").
            volume: Lot size (e.g. 0.01 = 1 share for stocks, 1.0 = 100 shares).
            side: 'BUY' or 'SELL'.
            stop_loss: Optional stop loss price.
            take_profit: Optional take profit price.
            comment: Order comment.
            magic: Expert advisor ID for identifying bot orders.

        Returns:
            Order result dict with 'ticket', 'price', etc.

        Raises:
            RuntimeError: If kill switch is active.
        """
        if Path(KILL_SWITCH_FILE).exists():
            raise RuntimeError("Kill switch is active — order placement blocked.")

        self.ensure_connected()

        # Get current prices for the symbol
        tick = self._client.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"Cannot get price for {symbol}")

        price = tick.ask if side == "BUY" else tick.bid

        # Prepare the trade request
        order_type = (
            _Mt5Client.ORDER_TYPE_BUY
            if side == "BUY"
            else _Mt5Client.ORDER_TYPE_SELL
        )

        request = {
            "action": _Mt5Client.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": 0,  # ORDER_TIME_GTC
            "type_filling": _Mt5Client.ORDER_FILLING_IOC,
        }

        if stop_loss is not None:
            request["sl"] = stop_loss
        if take_profit is not None:
            request["tp"] = take_profit

        logger.info("Placing market order: %s", request)
        result = self._client.order_send(request)

        if result is None:
            error = self._client.last_error()
            raise RuntimeError(f"Order failed: {error}")

        if result.retcode != _Mt5Client.TRADE_RETCODE_DONE:
            logger.error(
                "Order rejected: retcode=%d, comment=%s",
                result.retcode, result.comment,
            )
            raise RuntimeError(
                f"Order rejected (code {result.retcode}): {result.comment}"
            )

        logger.info("Order placed: ticket=%d, price=%.5f", result.order, result.price)
        return {
            "id": result.order,
            "ticket": result.order,
            "price": result.price,
            "volume": volume,
            "symbol": symbol,
            "side": side,
            "comment": result.comment,
            "retcode": result.retcode,
        }

    def place_limit_order(
        self,
        symbol: str,
        volume: float,
        limit_price: float,
        side: str = "BUY",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "Bot limit order",
        magic: int = 123456,
    ) -> dict[str, Any]:
        """Place a limit order."""
        if Path(KILL_SWITCH_FILE).exists():
            raise RuntimeError("Kill switch is active — order placement blocked.")

        self.ensure_connected()

        order_type = (
            _Mt5Client.ORDER_TYPE_BUY_LIMIT
            if side == "BUY"
            else _Mt5Client.ORDER_TYPE_SELL_LIMIT
        )

        request = {
            "action": _Mt5Client.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": limit_price,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": 0,
            "type_filling": _Mt5Client.ORDER_FILLING_RETURN,
        }

        if stop_loss is not None:
            request["sl"] = stop_loss
        if take_profit is not None:
            request["tp"] = take_profit

        logger.info("Placing limit order: %s", request)
        result = self._client.order_send(request)

        if result is None:
            error = self._client.last_error()
            raise RuntimeError(f"Limit order failed: {error}")

        if result.retcode != _Mt5Client.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"Limit order rejected (code {result.retcode}): {result.comment}"
            )

        return {
            "id": result.order,
            "ticket": result.order,
            "price": result.price,
            "volume": volume,
            "symbol": symbol,
            "side": side,
            "comment": result.comment,
            "retcode": result.retcode,
        }

    def place_stop_order(
        self,
        symbol: str,
        volume: float,
        stop_price: float,
        side: str = "SELL",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "Bot stop order",
        magic: int = 123456,
    ) -> dict[str, Any]:
        """Place a stop order (stop-loss entry)."""
        if Path(KILL_SWITCH_FILE).exists():
            raise RuntimeError("Kill switch is active — order placement blocked.")

        self.ensure_connected()

        order_type = (
            _Mt5Client.ORDER_TYPE_BUY_STOP
            if side == "BUY"
            else _Mt5Client.ORDER_TYPE_SELL_STOP
        )

        request = {
            "action": _Mt5Client.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": stop_price,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": 0,
            "type_filling": _Mt5Client.ORDER_FILLING_RETURN,
        }

        if stop_loss is not None:
            request["sl"] = stop_loss
        if take_profit is not None:
            request["tp"] = take_profit

        logger.info("Placing stop order: %s", request)
        result = self._client.order_send(request)

        if result is None:
            error = self._client.last_error()
            raise RuntimeError(f"Stop order failed: {error}")

        if result.retcode != _Mt5Client.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"Stop order rejected (code {result.retcode}): {result.comment}"
            )

        return {
            "id": result.order,
            "ticket": result.order,
            "price": result.price,
            "volume": volume,
            "symbol": symbol,
            "side": side,
            "comment": result.comment,
            "retcode": result.retcode,
        }

    def get_orders(self) -> list[dict[str, Any]]:
        """Fetch all pending orders."""
        self.ensure_connected()
        orders = self._client.orders_get()
        if orders is None:
            return []

        result = []
        for order in orders:
            result.append({
                "ticket": order.ticket,
                "symbol": order.symbol,
                "type": order.type,
                "volume": order.volume_current,
                "price": order.price_open,
                "stop_loss": order.sl,
                "take_profit": order.tp,
                "comment": order.comment,
                "magic": order.magic,
                "time_setup": str(
                    datetime.fromtimestamp(order.time_setup, tz=timezone.utc)
                ),
                "time_expiration": (
                    str(
                        datetime.fromtimestamp(
                            order.time_expiration, tz=timezone.utc
                        )
                    )
                    if order.time_expiration
                    else None
                ),
            })
        return result

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        """Cancel a pending order by its ticket number."""
        self.ensure_connected()

        request = {
            "action": _Mt5Client.TRADE_ACTION_REMOVE,
            "order": order_id,
        }

        result = self._client.order_send(request)
        if result is None:
            error = self._client.last_error()
            raise RuntimeError(f"Cancel order failed: {error}")

        if result.retcode != _Mt5Client.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"Cancel order rejected (code {result.retcode}): {result.comment}"
            )

        return {"id": order_id, "status": "cancelled"}

    def close_position(self, ticket: int) -> dict[str, Any]:
        """Close an open position by its ticket number."""
        self.ensure_connected()

        # Get position details
        positions = self._client.positions_get(ticket=ticket)
        if not positions:
            raise RuntimeError(f"Position {ticket} not found")

        pos = positions[0]
        tick = self._client.symbol_info_tick(pos.symbol)
        if tick is None:
            raise RuntimeError(f"Cannot get price for {pos.symbol}")

        # Close with opposite order
        close_side = (
            _Mt5Client.ORDER_TYPE_SELL
            if pos.type == _Mt5Client.POSITION_TYPE_BUY
            else _Mt5Client.ORDER_TYPE_BUY
        )
        close_price = (
            tick.bid
            if pos.type == _Mt5Client.POSITION_TYPE_BUY
            else tick.ask
        )

        request = {
            "action": _Mt5Client.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_side,
            "position": ticket,
            "price": close_price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "Bot close",
            "type_time": 0,
            "type_filling": _Mt5Client.ORDER_FILLING_IOC,
        }

        result = self._client.order_send(request)
        if result is None:
            error = self._client.last_error()
            raise RuntimeError(f"Close position failed: {error}")

        if result.retcode != _Mt5Client.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"Close position rejected (code {result.retcode}): {result.comment}"
            )

        return {"id": ticket, "status": "closed", "price": result.price}

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_order_history(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch historical orders/deals."""
        self.ensure_connected()

        if from_date is None:
            from_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        if to_date is None:
            to_date = datetime.now(timezone.utc)

        deals = self._client.history_deals_get(from_date, to_date)
        if deals is None:
            return []

        result = []
        for deal in deals[:limit]:
            result.append({
                "ticket": deal.ticket,
                "order": deal.order,
                "symbol": deal.symbol,
                "type": deal.type,
                "volume": deal.volume,
                "price": deal.price,
                "profit": deal.profit,
                "commission": deal.commission,
                "swap": deal.swap,
                "magic": deal.magic,
                "comment": deal.comment,
                "time": str(datetime.fromtimestamp(deal.time, tz=timezone.utc)),
            })
        return result

    def get_dividend_history(
        self,
        limit: int = 100,
        cursor: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Fetch dividend history.

        Note: MT5 doesn't have a direct dividend API. This is a placeholder
        that returns empty results. For dividend tracking, consider using
        yfinance or another data provider.
        """
        logger.warning("MT5 does not provide dividend history via API")
        return []

    # ------------------------------------------------------------------
    # Position management helpers
    # ------------------------------------------------------------------

    def modify_position(
        self,
        ticket: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any]:
        """Modify stop loss and/or take profit for an open position."""
        self.ensure_connected()

        request: dict[str, Any] = {
            "action": _Mt5Client.TRADE_ACTION_SLTP,
            "position": ticket,
        }
        if stop_loss is not None:
            request["sl"] = stop_loss
        if take_profit is not None:
            request["tp"] = take_profit

        result = self._client.order_send(request)
        if result is None:
            error = self._client.last_error()
            raise RuntimeError(f"Modify position failed: {error}")

        if result.retcode != _Mt5Client.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"Modify position rejected (code {result.retcode}): {result.comment}"
            )

        return {"id": ticket, "sl": stop_loss, "tp": take_profit, "status": "modified"}


# ------------------------------------------------------------------
# Convenience: build ticker map from instruments list
# ------------------------------------------------------------------

def build_ticker_map(instruments: list[dict]) -> dict[str, str]:
    """
    Build a mapping from Yahoo Finance-style ticker -> MT5 symbol name.

    MT5 symbols are usually the same as Yahoo tickers for US stocks
    (e.g. AAPL, MSFT). For forex, they use formats like EURUSD.
    """
    ticker_map: dict[str, str] = {}
    for inst in instruments:
        mt5_symbol = inst.get("ticker", "")

        # Direct match
        ticker_map[mt5_symbol.upper()] = mt5_symbol

        # Handle BRK.B style (Yahoo uses '.', MT5 may use '-')
        if "-" in mt5_symbol:
            alt = mt5_symbol.replace("-", ".")
            ticker_map[alt.upper()] = mt5_symbol

        # Handle forex pairs (Yahoo uses 'EURUSD=X', MT5 uses 'EURUSD')
        if "=" in mt5_symbol:
            alt = mt5_symbol.split("=")[0]
            ticker_map[alt.upper()] = mt5_symbol

    return ticker_map