"""Megalord Core Data Types and Models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
import pandas as pd


class MathFamily(str, Enum):
    FAMILY_SMC = "FAMILY_SMC"                  # Begonya00, Begonya, ChecklistTrigger
    FAMILY_MACRO_TREND = "FAMILY_MACRO_TREND"  # Convexity v2
    FAMILY_AMT_VWAP = "FAMILY_AMT_VWAP"        # Pragma
    FAMILY_ORDERFLOW = "FAMILY_ORDERFLOW"      # Trawma
    FAMILY_STAT_QUANT = "FAMILY_STAT_QUANT"    # Stigma


class StrategyRegime(str, Enum):
    TREND_MOMENTUM = "TREND_MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"


@dataclass
class TradeSignal:
    signal_id: str
    engine_name: str
    math_family: MathFamily
    strategy_regime: StrategyRegime
    symbol: str
    direction: str  # "LONG" or "SHORT"
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp]
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    risk_r: float = 1.0
    raw_r_result: float = 0.0
    sub_strategy: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArbiterDecision:
    allowed: bool
    final_risk_mult: float
    action: str  # "PROCEED", "CONFLUENCE_BOOST", "CLONE_CAPPED", "COUNTER_TREND_MR_DAMPED", "HTF_CONFLICT_VETO", "FIREWALL_BLOCKED"
    reason: str
    confluent_engines: list[str] = field(default_factory=list)
    confluent_families: list[str] = field(default_factory=list)
