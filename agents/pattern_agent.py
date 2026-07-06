"""
NEXUS QUANTUM ULTRA — Pattern Agent
Detecta padrões de candles (pin bar, engulfing, doji, etc.) e emite sinais direcionais.
"""

import asyncio
import logging
from typing import Dict, List, Optional

import numpy as np

from core.event_bus import BUS, Events
from database.repository import get_candles
from utils.config import SYMBOLS, ANALYSIS_INTERVAL
from utils.logger import agent_log


class PatternAgent:
    NAME = "PATTERN"

    def __init__(self):
        self._running = False

    # ── Detecção de padrões ────────────────────────────────────────────────

    def _detect_patterns(self, candles: List[Dict]) -> Optional[Dict]:
        """Detecta padrões de reversão e continuação nos últimos candles."""
        if len(candles) < 5:
            return None

        c  = candles[-1]   # candle atual
        p1 = candles[-2]   # anterior
        p2 = candles[-3]   # 2 antes

        body_c  = abs(c["close"]  - c["open"])
        body_p1 = abs(p1["close"] - p1["open"])
        range_c  = c["high"]  - c["low"]
        range_p1 = p1["high"] - p1["low"]

        score_call = 0.0
        score_put  = 0.0
        patterns   = []

        # ── Doji: corpo muito pequeno vs range ─────────────────────────────
        if range_c > 0 and body_c / range_c < 0.1:
            patterns.append("doji")
            # Doji após queda → reversão CALL
            if p1["close"] < p1["open"]:
                score_call += 0.15
            else:
                score_put  += 0.15

        # ── Pin Bar (martelo / shooting star) ─────────────────────────────
        if range_c > 0:
            upper_wick = c["high"]  - max(c["close"], c["open"])
            lower_wick = min(c["close"], c["open"]) - c["low"]

            # Martelo: pavio inferior longo → CALL
            if lower_wick > body_c * 2 and lower_wick > upper_wick * 2:
                patterns.append("hammer")
                score_call += 0.25

            # Shooting star: pavio superior longo → PUT
            if upper_wick > body_c * 2 and upper_wick > lower_wick * 2:
                patterns.append("shooting_star")
                score_put  += 0.25

        # ── Engulfing ──────────────────────────────────────────────────────
        if body_c > 0 and body_p1 > 0:
            # Bullish engulfing: c verde engloba p1 vermelho
            if (c["close"] > c["open"] and p1["close"] < p1["open"]
                    and c["close"] > p1["open"] and c["open"] < p1["close"]):
                patterns.append("bullish_engulfing")
                score_call += 0.30

            # Bearish engulfing: c vermelho engloba p1 verde
            if (c["close"] < c["open"] and p1["close"] > p1["open"]
                    and c["close"] < p1["open"] and c["open"] > p1["close"]):
                patterns.append("bearish_engulfing")
                score_put  += 0.30

        # ── 3 velas consecutivas (three soldiers / crows) ─────────────────
        closes = [candles[-3]["close"], candles[-2]["close"], candles[-1]["close"]]
        opens  = [candles[-3]["open"],  candles[-2]["open"],  candles[-1]["open"]]

        three_bull = all(closes[i] > opens[i] and closes[i] > closes[i-1]
                         for i in range(1, 3))
        three_bear = all(closes[i] < opens[i] and closes[i] < closes[i-1]
                         for i in range(1, 3))

        if three_bull:
            patterns.append("three_soldiers")
            score_call += 0.20
        if three_bear:
            patterns.append("three_crows")
            score_put  += 0.20

        # ── Resultado ──────────────────────────────────────────────────────
        if not patterns:
            return None

        if score_call > score_put:
            direction  = "CALL"
            confidence = min(score_call, 0.90)
        else:
            direction  = "PUT"
            confidence = min(score_put, 0.90)

        return {
            "direction":  direction,
            "confidence": round(confidence, 4),
            "patterns":   patterns,
        }

    async def _analyze_symbol(self, symbol: str) -> None:
        candles = await get_candles(symbol, 60, limit=50)
        if len(candles) < 5:
            return

        result = self._detect_patterns(candles)
        if not result:
            return

        await BUS.emit(Events.AGENT_SIGNAL, {
            "agent":      self.NAME,
            "symbol":     symbol,
            "signal":     result["direction"],
            "confidence": result["confidence"],
            "data": {
                "patterns": result["patterns"],
            },
        })
        agent_log(
            self.NAME,
            f"{symbol} | {result['direction']} | conf={result['confidence']:.2f} | "
            f"patterns={result['patterns']}"
        )

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Pattern Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            for symbol in SYMBOLS:
                try:
                    await self._analyze_symbol(symbol)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    agent_log(self.NAME, f"Erro em {symbol}: {e}", logging.ERROR)
            await asyncio.sleep(ANALYSIS_INTERVAL)

    def stop(self) -> None:
        self._running = False
