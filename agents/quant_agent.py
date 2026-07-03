"""
NEXUS QUANTUM ULTRA — Quant Agent
Calcula indicadores e gera sinais para todos os símbolos.
Recarrega velas após preload concluído.
"""

import asyncio
import logging
from typing import Dict, List, Optional

import numpy as np

from core.event_bus      import BUS, Events
from database.repository import get_candles
from utils.config        import (
    SYMBOLS, PRELOAD_GRANULARITIES,
    ANALYSIS_INTERVAL, MIN_CONFIDENCE, NN_LOOKBACK,
)
from utils.indicators    import ema, rsi, bollinger_bands, atr
from utils.logger        import agent_log


MIN_CANDLES   = 50      # mínimo para análise
PRIMARY_GRAN  = 60      # granularidade principal (1m)
ANALYSIS_GRAN = 300     # granularidade secundária (5m)


class QuantAgent:

    def __init__(self):
        self._running    = False
        self._candles:   Dict[str, Dict[int, List[Dict]]] = {}
        self._signals:   Dict[str, Dict]                  = {}
        self._loaded     = False

    async def run(self) -> None:
        self._running = True
        agent_log("QUANT", "Quant Agent iniciado.")

        # ── Subscriptions ─────────────────────────────────────────────
        await BUS.subscribe(Events.CANDLE,      self._on_candle)
        await BUS.subscribe(Events.PRELOAD_ALL, self._on_preload_done)
        await BUS.subscribe("system.agents_ready", self._on_agents_ready)

        # ── Tenta carregar do DB imediatamente ────────────────────────
        await self._load_all_candles()

        # ── Loop de análise periódica ─────────────────────────────────
        while self._running:
            await asyncio.sleep(ANALYSIS_INTERVAL)
            if self._loaded:
                await self._analyze_all()

    # ── Event Handlers ─────────────────────────────────────────────────

    async def _on_preload_done(self, data: Dict) -> None:
        total = data.get("total_candles", 0)
        agent_log("QUANT", f"Preload concluído: {total:,} velas — recarregando...")
        await self._load_all_candles()
        await self._analyze_all()

    async def _on_agents_ready(self, data: Dict) -> None:
        """Recarrega quando sistema está 100% pronto."""
        if not self._loaded:
            await self._load_all_candles()

    async def _on_candle(self, data: Dict) -> None:
        """Atualiza última vela em tempo real."""
        symbol = data.get("symbol")
        gran   = data.get("gran", PRIMARY_GRAN)

        if symbol not in self._candles:
            self._candles[symbol] = {}
        if gran not in self._candles[symbol]:
            self._candles[symbol][gran] = []

        candle = {
            "epoch": data.get("epoch", 0),
            "open":  data.get("open",  0.0),
            "high":  data.get("high",  0.0),
            "low":   data.get("low",   0.0),
            "close": data.get("close", 0.0),
        }

        candles = self._candles[symbol][gran]

        # Atualiza última ou adiciona
        if candles and candles[-1]["epoch"] == candle["epoch"]:
            candles[-1] = candle
        else:
            candles.append(candle)
            if len(candles) > 10000:
                candles.pop(0)

    # ── Data Loading ────────────────────────────────────────────────────

    async def _load_all_candles(self) -> None:
        loaded = 0
        for symbol in SYMBOLS:
            self._candles[symbol] = {}
            for gran in PRELOAD_GRANULARITIES:
                candles = await get_candles(symbol, gran, limit=2000)
                self._candles[symbol][gran] = candles or []
                loaded += len(self._candles[symbol][gran])

        if loaded > 0:
            self._loaded = True
            agent_log("QUANT", f"[OK] {loaded:,} velas carregadas do DB")
        else:
            agent_log("QUANT", "⚠️ DB vazio — aguardando preload", logging.WARNING)

    # ── Analysis ────────────────────────────────────────────────────────

    async def _analyze_all(self) -> None:
        for symbol in SYMBOLS:
            try:
                signal = await self._analyze_symbol(symbol)
                if signal:
                    self._signals[symbol] = signal
                    await BUS.emit(Events.AGENT_SIGNAL, signal)
            except Exception as e:
                agent_log("QUANT", f"Erro análise {symbol}: {e}", logging.ERROR)

    async def _analyze_symbol(self, symbol: str) -> Optional[Dict]:
        candles_1m = self._candles.get(symbol, {}).get(PRIMARY_GRAN, [])
        candles_5m = self._candles.get(symbol, {}).get(ANALYSIS_GRAN, [])

        if len(candles_1m) < MIN_CANDLES:
            agent_log("QUANT", f"Velas insuficientes para {symbol}: {len(candles_1m)}")
            return None

        closes_1m = np.array([c["close"] for c in candles_1m], dtype=float)
        closes_5m = np.array([c["close"] for c in candles_5m], dtype=float) if len(candles_5m) >= 20 else closes_1m
        highs_1m  = np.array([c["high"]  for c in candles_1m], dtype=float)
        lows_1m   = np.array([c["low"]   for c in candles_1m], dtype=float)

        # ── Indicadores ─────────────────────────────────────────────
        ema_fast  = ema(closes_1m, 9)
        ema_slow  = ema(closes_1m, 21)
        ema_trend = ema(closes_5m, 50) if len(closes_5m) >= 50 else ema(closes_1m, 50)
        rsi_val   = rsi(closes_1m, 14)
        bb_upper, bb_mid, bb_lower = bollinger_bands(closes_1m, 20, 2.0)
        atr_val   = atr(highs_1m, lows_1m, closes_1m, 14)

        current_price = closes_1m[-1]
        atr_norm      = atr_val[-1] / current_price if current_price > 0 else 0

        # ── Pontuação de sinal ───────────────────────────────────────
        score_call = 0.0
        score_put  = 0.0

        # EMA cross
        if ema_fast[-1] > ema_slow[-1]:
            score_call += 0.20
        else:
            score_put  += 0.20

        # EMA cross confirmação
        if ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1]:
            score_call += 0.15   # crossover recente
        elif ema_fast[-2] >= ema_slow[-2] and ema_fast[-1] < ema_slow[-1]:
            score_put  += 0.15

        # Tendência macro
        if current_price > ema_trend[-1]:
            score_call += 0.10
        else:
            score_put  += 0.10

        # RSI
        if rsi_val[-1] < 35:
            score_call += 0.20   # oversold
        elif rsi_val[-1] > 65:
            score_put  += 0.20   # overbought
        elif 45 <= rsi_val[-1] <= 55:
            pass                 # neutro — não pontua
        elif rsi_val[-1] < 50:
            score_call += 0.05
        else:
            score_put  += 0.05

        # Bollinger Bands
        if current_price < bb_lower[-1]:
            score_call += 0.20   # abaixo da banda
        elif current_price > bb_upper[-1]:
            score_put  += 0.20
        elif current_price > bb_mid[-1]:
            score_call += 0.05
        else:
            score_put  += 0.05

        # Momentum (últimas 3 velas)
        if len(closes_1m) >= 4:
            recent = closes_1m[-4:]
            if recent[-1] > recent[0]:
                score_call += 0.10
            else:
                score_put  += 0.10

        # Volatilidade penaliza em extremos
        if atr_norm > 0.005:
            score_call *= 0.85
            score_put  *= 0.85

        # ── Decisão ─────────────────────────────────────────────────
        if score_call > score_put:
            direction   = "CALL"
            confidence  = min(score_call, 0.95)
        else:
            direction   = "PUT"
            confidence  = min(score_put, 0.95)

        return {
            "symbol":      symbol,
            "direction":   direction,
            "confidence":  round(confidence, 4),
            "price":       current_price,
            "rsi":         round(float(rsi_val[-1]), 2),
            "ema_fast":    round(float(ema_fast[-1]), 5),
            "ema_slow":    round(float(ema_slow[-1]), 5),
            "bb_upper":    round(float(bb_upper[-1]), 5),
            "bb_lower":    round(float(bb_lower[-1]), 5),
            "atr":         round(float(atr_val[-1]),  5),
            "score_call":  round(score_call, 4),
            "score_put":   round(score_put,  4),
            "candles_used": len(candles_1m),
        }

    # ── Public API ──────────────────────────────────────────────────────

    def get_signal(self, symbol: str) -> Optional[Dict]:
        return self._signals.get(symbol)

    def get_all_signals(self) -> Dict[str, Dict]:
        return dict(self._signals)

    def is_loaded(self) -> bool:
        return self._loaded

    def stop(self) -> None:
        self._running = False
        agent_log("QUANT", "Quant Agent parado.")
