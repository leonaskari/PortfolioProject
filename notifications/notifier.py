"""
Decoupled notification layer — sends recommendations via Telegram, Discord,
and/or email. Each channel is independent and can be enabled/disabled in config.

All notification functions are fire-and-forget: they log errors but never
raise exceptions to the caller.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any

import requests

from config import NOTIFY

logger = logging.getLogger(__name__)


def send_telegram(message: str) -> bool:
    """
    Send a message via Telegram bot.

    Requires NOTIFY.telegram_token and NOTIFY.telegram_chat_id to be set.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not NOTIFY.telegram_enabled:
        return False

    token = NOTIFY.telegram_token
    chat_id = NOTIFY.telegram_chat_id

    if not token or not chat_id:
        logger.warning("Telegram enabled but token or chat_id missing.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Telegram notification sent.")
        return True
    except Exception as e:
        logger.error("Failed to send Telegram notification: %s", e)
        return False


def send_discord(message: str) -> bool:
    """
    Send a message via Discord webhook.

    Requires NOTIFY.discord_webhook_url to be set.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not NOTIFY.discord_enabled:
        return False

    webhook_url = NOTIFY.discord_webhook_url
    if not webhook_url:
        logger.warning("Discord enabled but webhook URL missing.")
        return False

    payload = {"content": message}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Discord notification sent.")
        return True
    except Exception as e:
        logger.error("Failed to send Discord notification: %s", e)
        return False


def send_email(subject: str, body: str) -> bool:
    """
    Send an email notification.

    Requires SMTP settings in NOTIFY config.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not NOTIFY.email_enabled:
        return False

    if not all([NOTIFY.smtp_host, NOTIFY.smtp_user, NOTIFY.smtp_pass,
                NOTIFY.email_from, NOTIFY.email_to]):
        logger.warning("Email enabled but SMTP settings incomplete.")
        return False

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = NOTIFY.email_from
    msg["To"] = NOTIFY.email_to

    try:
        with smtplib.SMTP(NOTIFY.smtp_host, NOTIFY.smtp_port) as server:
            server.starttls()
            server.login(NOTIFY.smtp_user, NOTIFY.smtp_pass)
            server.send_message(msg)
        logger.info("Email notification sent.")
        return True
    except Exception as e:
        logger.error("Failed to send email notification: %s", e)
        return False


def notify_all(message: str, subject: str = "Trading Bot Alert") -> dict[str, bool]:
    """
    Send a notification to all enabled channels.

    Args:
        message: The notification text.
        subject: Email subject line (only used for email).

    Returns:
        Dict mapping channel name -> success bool.
    """
    results = {
        "telegram": send_telegram(message),
        "discord": send_discord(message),
        "email": send_email(subject, message),
    }
    return results


def format_signal_message(
    ticker: str,
    action: str,
    price: float,
    shares: int | None = None,
    stop_loss: float | None = None,
    target: float | None = None,
    rr: float | None = None,
    confidence: float | None = None,
    rationale: str = "",
    context_notes: str = "",
) -> str:
    """
    Format a trading signal into a human-readable notification message.

    Suitable for Telegram (HTML), Discord, and email.
    """
    lines = [
        f"<b>{'🔴' if action == 'SELL' else '🟢' if action == 'BUY' else '🟡'} {action} SIGNAL: {ticker}</b>",
        f"Price: ${price:.2f}",
    ]

    if shares is not None:
        lines.append(f"Suggested size: {shares} shares")
    if stop_loss is not None:
        lines.append(f"Stop-loss: ${stop_loss:.2f}")
    if target is not None:
        lines.append(f"Target: ${target:.2f}")
    if rr is not None:
        lines.append(f"R:R: {rr:.1f}:1")
    if confidence is not None:
        lines.append(f"Confidence: {confidence:.0%}")

    if rationale:
        lines.append(f"\n{rationale}")
    if context_notes:
        lines.append(f"\n<i>Context:</i> {context_notes}")

    return "\n".join(lines)