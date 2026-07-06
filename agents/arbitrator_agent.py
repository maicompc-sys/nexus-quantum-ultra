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

# Intervalo entre GO_SIGNALs por simbolo (segundos)
SIGNAL_COOLDOWN = 30

def get_dynamic_weights(regime: str) -> Dict[str, float]:
    """Retorna os pesos dos agentes com base no regime de mercado atual."""
    if regime == "trending":
        # Em tendências, Quant e Neural (seguidores) têm prioridade
        return {
            "QUANT":   0.45,
            "NEURAL":  0.40,
            "PATTERN": 0.15,
        }
    elif regime == "ranging":
        # Em lateralização, Padrões (reversão) e Quant (osciladores) têm prioridade
        return {
            "PATTERN": 0.45,
            "QUANT":   0.35,
            "NEURAL":  0.20,
        }
    else:
        # Default (fallback)
        return {
            "QUANT":   0.40,
            "PATTERN": 0.30,
            "NEURAL":  0.30,
        }


class ArbitratorAgent:
    NAME = "ARBITRATOR"

    def __init__(self, risk_agent, sentinel_agent):
        self._running  = False
        self._risk     = risk_agent
        self._sentinel = sentinel_agent
        self._signals: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        self._last_go: Dict[str, float] = {}   # cooldown por simbolo
        self._system_started = False

        BUS.subscribe(Events.AGENT_SIGNAL,  self._on_agent_signal)
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
            agent_log(
                self.NAME,
                f"Signal RX: {agent:8s} {symbol} | {data.get('signal')} conf={data.get('confidence', 0):.2f}"
            )
            # Arbitra imediatamente com os sinais dos agentes
            await self._arbitrate(symbol)

    async def _arbitrate(self, symbol: str) -> None:
        """
        Arbitra usando os sinais dos agentes do sistema.
        Requer pelo menos sinal do QUANT e aprovação do Sentinel + Time.
        """
        # Cooldown
        now = asyncio.get_event_loop().time()
        if now - self._last_go.get(symbol, 0) < SIGNAL_COOLDOWN:
            agent_log(self.NAME, f"[COOLDOWN] {symbol} — aguardando {SIGNAL_COOLDOWN}s")
            return

        # Sentinel
        if not self._sentinel.is_clear(symbol):
            agent_log(self.NAME, f"[BLOCKED] {symbol} — regime não liberado")
            return

        # Risk halt
        if self._risk._trading_halted:
            agent_log(self.NAME, f"[HALTED] {symbol} — risk halt ativo")
            return

        # Verifica bloqueio de horário (Time Agent)
        signals = self._signals.get(symbol, {})
        time_sig = signals.get("TIME", {}).get("signal", "CLEAR")
        if time_sig == "HOLD":
            agent_log(self.NAME, f"[BLOCKED] {symbol} — janela de horário ruim (TIME)")
            return

        signals = self._signals.get(symbol, {})
        quant   = signals.get("QUANT")
        if not quant:
            agent_log(self.NAME, f"[WAIT] {symbol} — aguardando QUANT (agents: {list(signals.keys())})")
            return

        vote_call = 0.0
        vote_put  = 0.0
        total_w   = 0.0

        # Obtém o regime de mercado para o símbolo
        regime = self._sentinel.get_regime(symbol)
        current_weights = get_dynamic_weights(regime)

        for agent, weight in current_weights.items():
            if agent not in signals:
                continue
            sig = signals.get(agent, {})
            s   = sig.get("signal", "HOLD")
            c   = sig.get("confidence", 0.0)
            if s in ["CALL", "PUT", "HOLD"]:
                total_w += weight
                if s == "CALL":
                    vote_call += weight * c
                elif s == "PUT":
                    vote_put  += weight * c

        if total_w > 0:
            vote_call /= total_w
            vote_put  /= total_w

        agent_log(
            self.NAME,
            f"[VOTE] {symbol} | CALL={vote_call:.2f} PUT={vote_put:.2f} | "
            f"agents={list(signals.keys())} | MIN_CONF={MIN_CONFIDENCE}"
        )

        if vote_call > vote_put and vote_call >= MIN_CONFIDENCE:
            direction  = "CALL"
            confidence = vote_call
        elif vote_put > vote_call and vote_put >= MIN_CONFIDENCE:
            direction  = "PUT"
            confidence = vote_put
        else:
            agent_log(self.NAME, f"[HOLD] {symbol} — confiança insuficiente")
            return   # HOLD

        stake = self._risk.compute_stake(symbol, confidence)
        if stake is None:
            agent_log(self.NAME, f"[NO_STAKE] {symbol} — risco muito alto")
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


    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Arbitrator Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})
        while self._running:
            await asyncio.sleep(1)

    def stop(self):
        self._running = False
