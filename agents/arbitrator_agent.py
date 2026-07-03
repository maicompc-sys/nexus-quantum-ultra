"""
NEXUS QUANTUM ULTRA — Arbitrator Agent
Collects signals from all agents, votes, and emits GO_SIGNAL.
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
    "QUANT":    0.25,
    "SENTINEL": 0.20,
    "PATTERN":  0.20,
    "NEURAL":   0.25,
    "TIME":     0.10,
}


class ArbitratorAgent:
    NAME = "ARBITRATOR"

    def __init__(self, risk_agent, sentinel_agent):
        self._running  = False
        self._risk     = risk_agent
        self._sentinel = sentinel_agent
        self._signals: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        self._council_signals: Dict[str, Dict] = {}

        BUS.subscribe(Events.AGENT_SIGNAL,  self._on_agent_signal)
        BUS.subscribe(Events.COUNCIL_DONE,  self._on_council_done)

    async def _on_agent_signal(self, _event: str, data: Dict) -> None:
        agent  = data.get("agent", "")
        symbol = data.get("symbol", "")
        if agent and symbol:
            self._signals[symbol][agent] = data

    async def _on_council_done(self, _event: str, data: Dict) -> None:
        symbol = data.get("symbol", "")
        if symbol:
            self._council_signals[symbol] = data
            await self._arbitrate(symbol)

    async def _arbitrate(self, symbol: str) -> None:
        # Check sentinel clearance
        if not self._sentinel.is_clear(symbol):
            agent_log(self.NAME, f"{symbol} bloqueado pelo Sentinel — sem GO")
            return

        # Check risk halt
        if self._risk._trading_halted:
            agent_log(self.NAME, "Trading HALTED — sem GO")
            return

        signals = self._signals.get(symbol, {})
        council = self._council_signals.get(symbol, {})

        # Weighted vote
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

        # Council carries heavy weight
        council_signal = council.get("signal", "HOLD")
        council_conf   = council.get("confidence", 0.0)
        COUNCIL_W      = 0.40

        if council_signal == "CALL":
            vote_call += COUNCIL_W * council_conf
        elif council_signal == "PUT":
            vote_put  += COUNCIL_W * council_conf

        total_w += COUNCIL_W

        # Normalize
        if total_w > 0:
            vote_call /= total_w
            vote_put  /= total_w

        # Decision
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

        agent_log(
            self.NAME,
            f"🎯 GO: {symbol} {direction} | conf={confidence:.2f} | stake={stake}"
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
