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


# The 5%ers Full Institutional Universe (64 Instruments: Forex, Commodities, Indices, Crypto)
THE5ERS_ALL_SYMBOLS: set[str] = {
    # Forex Majors & Crosses (28)
    "EURUSD", "GBPUSD", "USDCHF", "USDJPY", "USDCAD", "AUDUSD", "AUDNZD", "AUDCAD", "AUDCHF", "AUDJPY",
    "CADCHF", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURCHF", "EURGBP", "EURJPY", "EURNZD",
    "GBPCHF", "GBPJPY", "GBPAUD", "GBPCAD", "GBPNZD", "NZDCAD", "NZDCHF", "NZDJPY", "NZDUSD",
    # Forex Exotics (7)
    "USDBRL", "USDDKK", "USDHKD", "USDMXN", "USDNOK", "USDPLN", "USDZAR",
    # Commodities / Metals / Energy (11)
    "XAUUSD", "XAUEUR", "XAGUSD", "XAGEUR", "XPTUSD", "XPDUSD", "XTIUSD", "XBRUSD", "NGCUSD", "CUCUSD", "ASXAUD",
    # Indices (10)
    "DAX40", "ESXEUR", "F40EUR", "HSIHKD", "IBXEUR", "JPN225", "NAS100", "SP500", "UK100", "US30",
    # 24/7 Crypto (8)
    "BTCUSD", "BTCEUR", "ETHUSD", "ETHEUR", "SOLUSD", "LTCUSD", "DOGUSD", "XRPUSD",
    # Binance pairs mapped to The 5%ers
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LTCUSDT", "DOGEUSDT", "DOGEUSD"
}

# Makro Motorumuzun (Macro Engine & Gate) Desteklediği The 5%ers Pariteleri
# Makro desteği olmayan (haritası veya yön filtresi bulunmayan) egzotikler ve desteklenmeyen enstrümanlar elenmiştir.
THE5ERS_MACRO_SUPPORTED_SYMBOLS: set[str] = {
    # Forex Majörler & Çaprazlar (21 Parite)
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "AUDCAD", "AUDCHF", "AUDJPY", "CADCHF", "CADJPY", "CHFJPY",
    "EURAUD", "EURCHF", "EURGBP", "EURJPY", "GBPCHF", "GBPJPY",
    "NZDCAD", "NZDCHF",
    # Emtia (1 Enstrüman)
    "XAUUSD",
    # Endeks (1 Enstrüman)
    "NAS100",
    # 24/7 Kripto (6 Parite)
    "BTCUSD", "ETHUSD", "SOLUSD", "LTCUSD", "DOGUSD", "XRPUSD",
    # Binance eşlenikleri
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "LTCUSDT", "DOGEUSDT", "DOGEUSD", "XRPUSDT"
}

# Tum piyasalarda (Forex, Endeks, Emtia, The 5%ers ve Binance Altcoin'ler dahil tum kriptolarda)
# maksimum islem ozgurlugu saglamak amaciyla Global Whitelist devre disi (None = Tum pariteler serbest).
GLOBAL_EXECUTION_WHITELIST: Optional[set[str]] = None
