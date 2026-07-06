"""
NEXUS QUANTUM ULTRA — Quant Agent
Calcula indicadores e gera sinais para todos os simbolos.
Recarrega velas apos preload concluido.
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


MIN_CANDLES   = 50
PRIMARY_GRAN  = 60
ANALYSIS_GRAN = 300


class QuantAgent:

    def __init__(self):
        self._running  = False
        self._candles: Dict[str, Dict[int, List[Dict]]] = {}
        self._signals: Dict[str, Dict]                  = {}
        self._loaded   = False
        # ── Construção de candles em tempo real ──────────────────
        self._tick_buf: Dict[str, Dict[int, List[Dict]]] = {}  # symbol -> {epoch: [ticks]}
        self._candle_state: Dict[str, Dict[int, Dict]] = {}     # symbol -> {gran: current_candle}

    async def run(self) -> None:
        self._running = True
        agent_log("QUANT", "Quant Agent iniciado.")

        # BUS.subscribe e sincrono - nao usar await
        BUS.subscribe(Events.TICK,            self._on_tick)       # NOVO: escuta ticks
        BUS.subscribe(Events.CANDLE,          self._on_candle)
        BUS.subscribe(Events.PRELOAD_ALL,     self._on_preload_done)
        BUS.subscribe("system.agents_ready",  self._on_agents_ready)

        await self._load_all_candles()

        # Analisa imediatamente após carregar — sem esperar o primeiro intervalo
        if self._loaded:
            await self._analyze_all()

        while self._running:
            await asyncio.sleep(ANALYSIS_INTERVAL)
            if self._loaded:
                await self._analyze_all()

    async def _on_preload_done(self, _event: str, data: Dict) -> None:
        total = data.get("total_candles", 0)
        agent_log("QUANT", f"Preload concluido: {total:,} velas - recarregando...")
        await self._load_all_candles()
        await self._analyze_all()

    async def _on_agents_ready(self, _event: str, data: Dict) -> None:
        if not self._loaded:
            await self._load_all_candles()

    async def _on_tick(self, _event: str, data: Dict) -> None:
        """Processa ticks e constrói candles em tempo real."""
        symbol = data.get("symbol")
        if not symbol:
            return
        
        price = float(data.get("price", 0))
        epoch = int(data.get("epoch", 0))
        if epoch == 0 or price == 0:
            return
        
        # Inicializa buffer do símbolo
        if symbol not in self._tick_buf:
            self._tick_buf[symbol] = {}
            self._candle_state[symbol] = {}
        
        # Agrupa ticks por segundo (epoch)
        if epoch not in self._tick_buf[symbol]:
            self._tick_buf[symbol][epoch] = []
        self._tick_buf[symbol][epoch].append(price)
        
        # Tenta construir candles para granularidades
        for gran in PRELOAD_GRANULARITIES:
            epoch_aligned = (epoch // gran) * gran
            
            # Inicializa candle state
            if gran not in self._candle_state[symbol]:
                self._candle_state[symbol][gran] = None
            
            # Atualiza ou cria candle
            curr = self._candle_state[symbol][gran]
            if curr is None or curr["epoch"] != epoch_aligned:
                # Nova vela começou
                if curr is not None:
                    # Emite vela anterior completa
                    await BUS.emit(Events.CANDLE, curr)
                curr = {
                    "symbol": symbol,
                    "gran": gran,
                    "epoch": epoch_aligned,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                }
            else:
                # Atualiza vela atual
                curr["high"] = max(curr["high"], price)
                curr["low"] = min(curr["low"], price)
                curr["close"] = price
            
            self._candle_state[symbol][gran] = curr

    async def _on_candle(self, _event: str, data: Dict) -> None:
        symbol = data.get("symbol")
        gran   = data.get("gran", PRIMARY_GRAN)

        if symbol not in self._candles:
            self._candles[symbol] = {}
        if gran not in self._candles[symbol]:
            self._candles[symbol][gran] = []

        candle = {
            "epoch": data.get("epoch", 0),
            "open":  float(data.get("open",  0.0)),
            "high":  float(data.get("high",  0.0)),
            "low":   float(data.get("low",   0.0)),
            "close": float(data.get("close", 0.0)),
        }

        candles = self._candles[symbol][gran]
        if candles and candles[-1]["epoch"] == candle["epoch"]:
            candles[-1] = candle
        else:
            candles.append(candle)
            if len(candles) > 10000:
                candles.pop(0)

    async def _load_all_candles(self) -> None:
        loaded = 0
        for symbol in SYMBOLS:
            self._candles[symbol] = {}
            for gran in PRELOAD_GRANULARITIES:
                raw = await get_candles(symbol, gran, limit=2000)
                # Garante que todos os valores sao float puro (nao Decimal do SQLite)
                candles = [
                    {
                        "epoch": int(c["epoch"]),
                        "open":  float(c["open"]),
                        "high":  float(c["high"]),
                        "low":   float(c["low"]),
                        "close": float(c["close"]),
                    }
                    for c in (raw or [])
                ]
                self._candles[symbol][gran] = candles
                loaded += len(candles)

        if loaded > 0:
            self._loaded = True
            agent_log("QUANT", f"[OK] {loaded:,} velas carregadas do DB")
        else:
            agent_log("QUANT", "[AVISO] DB vazio - aguardando preload", logging.WARNING)

    async def _analyze_all(self) -> None:
        for symbol in SYMBOLS:
            try:
                signal = await self._analyze_symbol(symbol)
                if signal:
                    self._signals[symbol] = signal
                    agent_log(
                        "QUANT",
                        f"{symbol} | {signal['signal']} | conf={signal['confidence']:.2f} | "
                        f"score_call={signal['score_call']} score_put={signal['score_put']}"
                    )
                    await BUS.emit(Events.AGENT_SIGNAL, signal)
            except Exception as e:
                agent_log("QUANT", f"Erro analise {symbol}: {e}", logging.ERROR)

    async def _analyze_symbol(self, symbol: str) -> Optional[Dict]:
        candles_1m = self._candles.get(symbol, {}).get(PRIMARY_GRAN, [])
        candles_5m = self._candles.get(symbol, {}).get(ANALYSIS_GRAN, [])

        if len(candles_1m) < MIN_CANDLES:
            agent_log("QUANT", f"Velas insuficientes para {symbol}: {len(candles_1m)}")
            return None

        closes_1m = np.array([c["close"] for c in candles_1m], dtype=np.float64)
        closes_5m = np.array([c["close"] for c in candles_5m], dtype=np.float64) if len(candles_5m) >= 20 else closes_1m
        highs_1m  = np.array([c["high"]  for c in candles_1m], dtype=np.float64)
        lows_1m   = np.array([c["low"]   for c in candles_1m], dtype=np.float64)

        ema_fast              = ema(closes_1m, 9)
        ema_slow              = ema(closes_1m, 21)
        ema_trend             = ema(closes_5m, 50) if len(closes_5m) >= 50 else ema(closes_1m, 50)
        rsi_val               = rsi(closes_1m, 14)
        bb_upper, bb_mid, bb_lower = bollinger_bands(closes_1m, 20, 2.0)
        atr_val               = atr(highs_1m, lows_1m, closes_1m, 14)

        current_price = float(closes_1m[-1])
        atr_norm      = float(atr_val[-1]) / current_price if current_price > 0 else 0.0

        score_call = 0.0
        score_put  = 0.0

        if ema_fast[-1] > ema_slow[-1]:
            score_call += 0.20
        else:
            score_put  += 0.20

        if ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1]:
            score_call += 0.15
        elif ema_fast[-2] >= ema_slow[-2] and ema_fast[-1] < ema_slow[-1]:
            score_put  += 0.15

        if current_price > ema_trend[-1]:
            score_call += 0.10
        else:
            score_put  += 0.10

        rsi_last = float(rsi_val[-1]) if not np.isnan(rsi_val[-1]) else 50.0
        if rsi_last < 35:
            score_call += 0.20
        elif rsi_last > 65:
            score_put  += 0.20
        elif rsi_last < 50:
            score_call += 0.05
        else:
            score_put  += 0.05

        if current_price < bb_lower[-1]:
            score_call += 0.20
        elif current_price > bb_upper[-1]:
            score_put  += 0.20
        elif current_price > bb_mid[-1]:
            score_call += 0.05
        else:
            score_put  += 0.05

        if len(closes_1m) >= 4:
            recent = closes_1m[-4:]
            if recent[-1] > recent[0]:
                score_call += 0.10
            else:
                score_put  += 0.10

        if atr_norm > 0.005:
            score_call *= 0.85
            score_put  *= 0.85

        if score_call > score_put:
            direction  = "CALL"
            confidence = min(score_call, 0.95)
        else:
            direction  = "PUT"
            confidence = min(score_put, 0.95)

        return {
            "agent":        "QUANT",
            "symbol":       symbol,
            "signal":       direction,
            "direction":    direction,
            "confidence":   round(confidence, 4),
            "price":        current_price,
            "rsi":          round(rsi_last, 2),
            "ema_fast":     round(float(ema_fast[-1]), 5),
            "ema_slow":     round(float(ema_slow[-1]), 5),
            "bb_upper":     round(float(bb_upper[-1]), 5),
            "bb_lower":     round(float(bb_lower[-1]), 5),
            "atr":          round(float(atr_val[-1]),  5),
            "score_call":   round(score_call, 4),
            "score_put":    round(score_put,  4),
            "candles_used": len(candles_1m),
        }

    def get_signal(self, symbol: str) -> Optional[Dict]:
        return self._signals.get(symbol)

    def get_all_signals(self) -> Dict[str, Dict]:
        return dict(self._signals)

    def is_loaded(self) -> bool:
        return self._loaded

    def stop(self) -> None:
        self._running = False
        agent_log("QUANT", "Quant Agent parado.")

    def get_context(self, symbol: str) -> Optional[Dict]:
        signal = self._signals.get(symbol)
        if not signal:
            return None
        candles_1m = self._candles.get(symbol, {}).get(PRIMARY_GRAN, [])
        return {
            "symbol":        symbol,
            "signal":        signal,
            "direction":     signal.get("direction"),
            "confidence":    signal.get("confidence", 0.0),
            "rsi":           signal.get("rsi", 50.0),
            "ema_fast":      signal.get("ema_fast", 0.0),
            "ema_slow":      signal.get("ema_slow", 0.0),
            "bb_upper":      signal.get("bb_upper", 0.0),
            "bb_lower":      signal.get("bb_lower", 0.0),
            "atr":           signal.get("atr", 0.0),
            "price":         signal.get("price", 0.0),
            "score_call":    signal.get("score_call", 0.0),
            "score_put":     signal.get("score_put",  0.0),
            "candles":       candles_1m[-50:] if candles_1m else [],
            "candles_count": len(candles_1m),
            "is_loaded":     self._loaded,
        }
