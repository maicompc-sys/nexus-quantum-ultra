"""
NEXUS QUANTUM ULTRA — Arbitrator Agent
Collects signals from all agents, votes, and emits GO_SIGNAL.
Se GROQ nao estiver configurado, opera em modo DIRECT com QUANT + SENTINEL.
"""

import asyncio
import logging
from typing import Dict, List
from collections import defaultdict

from core.event_bus import BUS, Events
from utils.logger import agent_log
from utils.config import MIN_CONFIDENCE, SYMBOLS

# Weights per agent
AGENT_WEIGHTS = {
    "QUANT":    0.35,
    "SENTINEL": 0.25,
    "PATTERN":  0.20,
    "NEURAL":   0.30,
    "TIME":     0.10,
}

# Intervalo entre GO_SIGNALs por simbolo (segundos)
SIGNAL_COOLDOWN = 30


class ArbitratorAgent:
    NAME = "ARBITRATOR"

    def __init__(self, risk_agent, sentinel_agent):
        self._running  = False
        self._risk     = risk_agent
        self._sentinel = sentinel_agent
        self._signals: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        self._council_signals: Dict[str, Dict] = {}
        self._last_go:  Dict[str, float] = {}   # cooldown por simbolo
        self._system_started = False

        BUS.subscribe(Events.AGENT_SIGNAL,  self._on_agent_signal)
        BUS.subscribe(Events.COUNCIL_DONE,  self._on_council_done)
        BUS.subscribe(Events.SYSTEM_START,  self._on_system_start)
        BUS.subscribe(Events.SYSTEM_STOP,   self._on_system_stop)

    async def _on_system_start(self, _event: str, _data: Dict) -> None:
        self._system_started = True
        agent_log(self.NAME, "[OK] Sistema iniciado — arbitragem ativa")

    async def _on_system_stop(self, _event: str, _data: Dict) -> None:
        self._system_started = False
        agent_log(self.NAME, "Sistema parado — arbitragem suspensa")

    async def _on_agent_signal(self, _event: str, data: Dict) -> None:
        if not self._system_started:
            return
        agent  = data.get("agent", "")
        symbol = data.get("symbol", "")
        if agent and symbol:
            self._signals[symbol][agent] = data
            # Modo DIRECT: arbitra imediatamente com sinais dos agentes
            # sem esperar COUNCIL_DONE (fallback quando Groq nao configurado)
            await self._arbitrate_direct(symbol)

    async def _on_council_done(self, _event: str, data: Dict) -> None:
        if not self._system_started:
            return
        symbol = data.get("symbol", "")
        if symbol:
            self._council_signals[symbol] = data
            await self._arbitrate_full(symbol)

    async def _arbitrate_direct(self, symbol: str) -> None:
        """
        Arbitra usando apenas sinais dos agentes (sem Council/Groq).
        Requer pelo menos sinal do QUANT.
        """
        # Cooldown
        now = asyncio.get_event_loop().time()
        if now - self._last_go.get(symbol, 0) < SIGNAL_COOLDOWN:
            return

        # Sentinel
        if not self._sentinel.is_clear(symbol):
            return

        # Risk halt
        if self._risk._trading_halted:
            return

        signals = self._signals.get(symbol, {})
        quant   = signals.get("QUANT")
        if not quant:
            return

        vote_call = 0.0
        vote_put  = 0.0
        total_w   = 0.0

        for agent, weight in AGENT_WEIGHTS.items():
            sig = signals.get(agent, {})
            s   = sig.get("signal", "HOLD")
            c   = sig.get("confidence", 0.0)
            if s == "CALL":
                vote_call += weight * c
            elif s == "PUT":
                vote_put  += weight * c
            total_w += weight

        if total_w > 0:
            vote_call /= total_w
            vote_put  /= total_w

        if vote_call > vote_put and vote_call >= MIN_CONFIDENCE:
            direction  = "CALL"
            confidence = vote_call
        elif vote_put > vote_call and vote_put >= MIN_CONFIDENCE:
            direction  = "PUT"
            confidence = vote_put
        else:
            return   # HOLD

        stake = self._risk.compute_stake(symbol, confidence)
        if stake is None:
            return

        self._last_go[symbol] = now
        agent_log(
            self.NAME,
            f"🎯 GO [DIRECT]: {symbol} {direction} | "
            f"conf={confidence:.2f} | stake={stake}"
        )

        await BUS.emit(Events.GO_SIGNAL, {
            "symbol":     symbol,
            "direction":  direction,
            "stake":      stake,
            "confidence": confidence,
            "strategy":   "direct_quant",
            "indicators": quant.get("data", {}),
        })

    async def _arbitrate_full(self, symbol: str) -> None:
        """
        Arbitra com votos de todos os agentes + Council Groq.
        """
        # Cooldown
        now = asyncio.get_event_loop().time()
        if now - self._last_go.get(symbol, 0) < SIGNAL_COOLDOWN:
            return

        if not self._sentinel.is_clear(symbol):
            agent_log(self.NAME, f"{symbol} bloqueado pelo Sentinel — sem GO")
            return

        if self._risk._trading_halted:
            agent_log(self.NAME, "Trading HALTED — sem GO")
            return

        signals = self._signals.get(symbol, {})
        council = self._council_signals.get(symbol, {})

        vote_call = 0.0
        vote_put  = 0.0
        total_w   = 0.0

        for agent, weight in AGENT_WEIGHTS.items():
            sig = signals.get(agent, {})
            s   = sig.get("signal", "HOLD")
            c   = sig.get("confidence", 0.0)
            if s == "CALL":
                vote_call += weight * c
            elif s == "PUT":
                vote_put  += weight * c
            total_w += weight

        council_signal = council.get("signal", "HOLD")
        council_conf   = council.get("confidence", 0.0)
        COUNCIL_W      = 0.40

        if council_signal == "CALL":
            vote_call += COUNCIL_W * council_conf
        elif council_signal == "PUT":
            vote_put  += COUNCIL_W * council_conf

        total_w += COUNCIL_W

        if total_w > 0:
            vote_call /= total_w
            vote_put  /= total_w

        if vote_call > vote_put and vote_call >= MIN_CONFIDENCE:
            direction  = "CALL"
            confidence = vote_call
        elif vote_put > vote_call and vote_put >= MIN_CONFIDENCE:
            direction  = "PUT"
            confidence = vote_put
        else:
            agent_log(
                self.NAME,
                f"{symbol} HOLD — CALL={vote_call:.2f} PUT={vote_put:.2f}"
            )
            return

        stake = self._risk.compute_stake(symbol, confidence)
        if stake is None:
            return

        self._last_go[symbol] = now
        agent_log(
            self.NAME,
            f"🎯 GO [FULL]: {symbol} {direction} | conf={confidence:.2f} | stake={stake}"
        )

        await BUS.emit(Events.GO_SIGNAL, {
            "symbol":     symbol,
            "direction":  direction,
            "stake":      stake,
            "confidence": confidence,
            "strategy":   council.get("strategy", ""),
            "indicators": signals.get("QUANT", {}).get("data", {}),
        })

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Arbitrator Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})
        while self._running:
            await asyncio.sleep(1)

    def stop(self):
        self._running = False
