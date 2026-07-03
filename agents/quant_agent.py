"""
NEXUS QUANTUM ULTRA — Quant Agent
Computes all technical indicators and publishes market context.
"""

import asyncio
import logging
from typing import Dict, Optional

from core.event_bus import BUS, Events
from database.repository import get_candles
from utils.indicators import compute_all
from utils.logger import agent_log
from utils.config import SYMBOLS, ANALYSIS_INTERVAL


class QuantAgent:
    NAME = "QUANT"

    def __init__(self):
        self._running  = False
        self._contexts: Dict[str, Dict] = {}

    def get_context(self, symbol: str) -> Optional[Dict]:
        return self._contexts.get(symbol)

    async def analyze_symbol(self, symbol: str, granularity: int = 60) -> Optional[Dict]:
        candles = await get_candles(symbol, granularity, limit=200)
        if len(candles) < 30:
            agent_log(self.NAME, f"Velas insuficientes para {symbol}: {len(candles)}", logging.WARNING)
            return None

        indicators = compute_all(candles)
        if not indicators:
            return None

        context = {
            "symbol":      symbol,
            "granularity": granularity,
            "candles":     len(candles),
            "indicators":  indicators,
            "last_close":  candles[-1]["close"],
            "last_epoch":  candles[-1]["epoch"],
        }

        self._contexts[symbol] = context

        await BUS.emit(Events.AGENT_SIGNAL, {
            "agent":      self.NAME,
            "symbol":     symbol,
            "signal":     self._signal_from_indicators(indicators),
            "confidence": self._confidence_from_indicators(indicators),
            "data":       indicators,
        })

        return context

    def _signal_from_indicators(self, ind: Dict) -> str:
        score = 0

        # RSI
        if ind.get("rsi", 50) < 35:   score += 2
        elif ind.get("rsi", 50) > 65: score -= 2

        # EMA cross
        if ind.get("ema_cross") == "bullish": score += 1
        else:                                  score -= 1

        # MACD histogram
        hist = ind.get("macd_hist", 0)
        if hist > 0:   score += 1
        elif hist < 0: score -= 1

        # Bollinger
        pos = ind.get("price_vs_bb", "inside")
        if pos == "below":  score += 1
        elif pos == "above": score -= 1

        # Stochastic
        k = ind.get("stoch_k", 50)
        if k < 25:   score += 1
        elif k > 75: score -= 1

        if score >= 3:  return "CALL"
        if score <= -3: return "PUT"
        return "HOLD"

    def _confidence_from_indicators(self, ind: Dict) -> float:
        signals = []

        rsi_val = ind.get("rsi", 50)
        if rsi_val < 30 or rsi_val > 70:   signals.append(0.9)
        elif rsi_val < 40 or rsi_val > 60: signals.append(0.6)
        else:                               signals.append(0.3)

        if ind.get("ema_cross") in ("bullish", "bearish"): signals.append(0.7)
        if abs(ind.get("macd_hist", 0)) > 0:               signals.append(0.6)
        if ind.get("price_vs_bb") != "inside":             signals.append(0.8)

        return round(min(1.0, sum(signals) / len(signals)), 3) if signals else 0.5

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Quant Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            for symbol in SYMBOLS:
                try:
                    await self.analyze_symbol(symbol)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    agent_log(self.NAME, f"Erro em {symbol}: {e}", logging.ERROR)

            await asyncio.sleep(ANALYSIS_INTERVAL)

    def stop(self):
        self._running = False
