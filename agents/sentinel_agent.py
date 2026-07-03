"""
NEXUS QUANTUM ULTRA — Sentinel Agent
Detects market regime (trending/ranging/volatile) and guards entry conditions.
Blocks trading during unfavorable regimes.
"""

import asyncio
import logging
from typing import Dict, Set

import numpy as np

from core.event_bus import BUS, Events
from database.repository import get_candles
from utils.indicators import detect_regime, atr, rsi
from utils.logger import agent_log
from utils.config import SYMBOLS, ANALYSIS_INTERVAL


class SentinelAgent:
    NAME = "SENTINEL"

    # Regimes where trading is allowed
    ALLOWED_REGIMES: Set[str] = {"trending", "ranging"}

    def __init__(self):
        self._running  = False
        self._regimes: Dict[str, str]  = {}
        self._guards:  Dict[str, bool] = {}   # True = trading allowed

    def is_clear(self, symbol: str) -> bool:
        return self._guards.get(symbol, False)

    def get_regime(self, symbol: str) -> str:
        return self._regimes.get(symbol, "unknown")

    async def _evaluate(self, symbol: str) -> None:
        candles = await get_candles(symbol, 60, limit=100)
        if len(candles) < 50:
            return

        closes = np.array([c["close"] for c in candles], dtype=float)
        highs  = np.array([c["high"]  for c in candles], dtype=float)
        lows   = np.array([c["low"]   for c in candles], dtype=float)

        regime    = detect_regime(closes)
        atr_vals  = atr(highs, lows, closes)
        rsi_vals  = rsi(closes)

        current_atr = float(atr_vals[-1])
        current_rsi = float(rsi_vals[-1])
        mean_atr    = float(np.mean(atr_vals[-20:]))

        # Guard conditions
        atr_spike    = current_atr > mean_atr * 2.5   # extreme volatility spike
        rsi_extreme  = current_rsi > 85 or current_rsi < 15
        allowed      = regime in self.ALLOWED_REGIMES and not atr_spike

        old_regime = self._regimes.get(symbol)
        self._regimes[symbol] = regime
        self._guards[symbol]  = allowed

        if old_regime != regime:
            agent_log(
                self.NAME,
                f"{symbol} regime: {old_regime} → {regime} | "
                f"ATR={current_atr:.5f} | RSI={current_rsi:.1f} | "
                f"{'✓ LIBERADO' if allowed else '✗ BLOQUEADO'}"
            )

        await BUS.emit(Events.AGENT_SIGNAL, {
            "agent":      self.NAME,
            "symbol":     symbol,
            "signal":     "HOLD" if not allowed else "CLEAR",
            "confidence": 0.9 if allowed else 0.1,
            "data": {
                "regime":     regime,
                "atr":        current_atr,
                "atr_spike":  atr_spike,
                "rsi":        current_rsi,
                "allowed":    allowed,
            },
        })

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Sentinel Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            for symbol in SYMBOLS:
                try:
                    await self._evaluate(symbol)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    agent_log(self.NAME, f"Erro em {symbol}: {e}", logging.ERROR)
            await asyncio.sleep(ANALYSIS_INTERVAL)

    def stop(self):
        self._running = False
