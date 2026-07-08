"""
Signal decay tracking — logs actual outcomes of past signals so the
"confidence" slider means something over time.

Without this, confidence is just a static guess based on entry criteria.
With decay tracking, we measure: when the bot says "90% confidence",
how often does the trade actually work out?

This module logs signal outcomes and computes empirical win rates
per confidence bucket so the user can calibrate their thresholds.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import LOG_DIR

logger = logging.getLogger(__name__)

# Path to the signal decay log file
SIGNAL_DECAY_FILE = str(Path(LOG_DIR) / "signal_decay.jsonl")


@dataclass
class SignalOutcome:
    """Record of a signal and its eventual outcome."""
    signal_id: str = ""
    ticker: str = ""
    signal_date: str = ""
    signal_action: str = ""  # BUY, SELL, TRIM
    confidence: float = 0.0
    entry_price: float = 0.0
    exit_price: float | None = None
    exit_date: str | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    bars_held: int | None = None
    exit_reason: str = ""
    regime_at_entry: str = ""
    recorded: str = ""


def record_signal_outcome(signal: SignalOutcome):
    """
    Record a signal outcome to the JSONL log file.
    
    Args:
        signal: SignalOutcome dataclass instance.
    """
    signal.recorded = datetime.now(timezone.utc).isoformat()
    
    os.makedirs(os.path.dirname(SIGNAL_DECAY_FILE), exist_ok=True)
    
    with open(SIGNAL_DECAY_FILE, "a") as f:
        f.write(json.dumps(asdict(signal)) + "\n")


def record_entry_signal(
    ticker: str,
    confidence: float,
    entry_price: float,
    action: str = "BUY",
    regime: str = "",
):
    """
    Record a new entry signal for later outcome tracking.
    
    Args:
        ticker: Stock ticker.
        confidence: Signal confidence (0-1).
        entry_price: Entry price.
        action: Signal action.
        regime: Market regime at entry.
    """
    signal = SignalOutcome(
        signal_id=f"{ticker}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        ticker=ticker,
        signal_date=datetime.now(timezone.utc).isoformat(),
        signal_action=action,
        confidence=confidence,
        entry_price=entry_price,
        regime_at_entry=regime,
    )
    record_signal_outcome(signal)
    return signal.signal_id


def record_exit_outcome(
    signal_id: str,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    bars_held: int,
    exit_reason: str = "",
):
    """
    Update a previously recorded entry signal with exit details.
    
    This finds the matching signal in the JSONL file and appends exit data.
    Since JSONL is append-only, this rewrites the line.
    
    Args:
        signal_id: The signal_id returned by record_entry_signal.
        exit_price: Exit price.
        pnl: Dollar P&L.
        pnl_pct: Percentage P&L.
        bars_held: Number of bars/days held.
        exit_reason: Reason for exit.
    """
    filepath = Path(SIGNAL_DECAY_FILE)
    if not filepath.exists():
        logger.warning("Signal decay file not found: %s", SIGNAL_DECAY_FILE)
        return
    
    lines = filepath.read_text().splitlines()
    found = False
    
    for i, line in enumerate(lines):
        try:
            record = json.loads(line)
            if record.get("signal_id") == signal_id:
                record["exit_price"] = exit_price
                record["exit_date"] = datetime.now(timezone.utc).isoformat()
                record["pnl"] = pnl
                record["pnl_pct"] = pnl_pct
                record["bars_held"] = bars_held
                record["exit_reason"] = exit_reason
                lines[i] = json.dumps(record)
                found = True
                break
        except json.JSONDecodeError:
            continue
    
    if found:
        filepath.write_text("\n".join(lines) + "\n")
    else:
        logger.warning("Signal ID %s not found in decay log", signal_id)


@dataclass
class ConfidenceCalibration:
    """Calibration data for a confidence bucket."""
    bucket_min: float
    bucket_max: float
    total_signals: int = 0
    winning: int = 0
    losing: int = 0
    empirical_win_rate: float = 0.0
    avg_pnl_pct: float = 0.0


def compute_calibration(
    min_signals_per_bucket: int = 5,
) -> list[ConfidenceCalibration]:
    """
    Compute empirical win rates per confidence bucket from historical signals.
    
    Confidence is bucketed into deciles (0-10%, 10-20%, etc.). For each
    bucket, we compute how many signals eventually won vs lost.
    
    Args:
        min_signals_per_bucket: Minimum signals required for a meaningful calc.
        
    Returns:
        List of ConfidenceCalibration, one per bucket that has data.
    """
    filepath = Path(SIGNAL_DECAY_FILE)
    if not filepath.exists():
        return []
    
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    
    if not records:
        return []
    
    # Filter to completed signals (have exit data)
    completed = [r for r in records if r.get("pnl") is not None]
    if not completed:
        return []
    
    # Bucket by confidence
    buckets: dict[int, list[dict]] = {}
    for r in completed:
        conf = r.get("confidence", 0.5)
        bucket = int(conf * 10)  # 0-10 -> bucket 0, 10-20 -> bucket 1, etc.
        bucket = min(bucket, 9)  # cap at 90-100%
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append(r)
    
    results = []
    for bucket_idx in sorted(buckets.keys()):
        signals = buckets[bucket_idx]
        if len(signals) < min_signals_per_bucket:
            continue
        
        winning = [s for s in signals if s.get("pnl", 0) > 0]
        losing = [s for s in signals if s.get("pnl", 0) <= 0]
        win_rate = len(winning) / len(signals) * 100 if signals else 0
        avg_pnl = sum(s.get("pnl_pct", 0) for s in signals) / len(signals)
        
        results.append(ConfidenceCalibration(
            bucket_min=bucket_idx / 10,
            bucket_max=(bucket_idx + 1) / 10,
            total_signals=len(signals),
            winning=len(winning),
            losing=len(losing),
            empirical_win_rate=win_rate,
            avg_pnl_pct=avg_pnl,
        ))
    
    return results


def print_calibration():
    """Print the confidence calibration table."""
    calibration = compute_calibration()
    if not calibration:
        print("No completed signals in decay log yet.")
        return
    
    print("\n" + "=" * 70)
    print("  CONFIDENCE CALIBRATION")
    print("  How often does each confidence level actually win?")
    print("=" * 70)
    print(f"  {'Bucket':<12} {'Signals':<8} {'Wins':<6} {'Losses':<7} {'Win Rate':<10} {'Avg P&L':<10}")
    print("-" * 70)
    
    for c in calibration:
        bucket_label = f"{c.bucket_min:.0%}-{c.bucket_max:.0%}"
        print(f"  {bucket_label:<12} {c.total_signals:<8} {c.winning:<6} "
              f"{c.losing:<7} {c.empirical_win_rate:.0f}%{'':<5} {c.avg_pnl_pct:+.1f}%")
    
    print("=" * 70)