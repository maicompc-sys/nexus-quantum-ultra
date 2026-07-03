"""
NEXUS QUANTUM ULTRA — Conclave
Orchestrates the Groq Council debate cycle and publishes results to EventBus.
"""

import asyncio
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Optional

from council.groq_client import GROQ
from core.event_bus import BUS, Events
from database.repository import (
    save_council_log, get_active_strategies,
    block_strategy, is_pattern_blocked
)
from utils.logger import agent_log
from utils.config import MIN_CONFIDENCE


class Conclave:
    def __init__(self):
        self._active = False
        self._cycle_count = 0

    async def analyze(
        self,
        symbol: str,
        market_context: Dict,
        indicators: Dict,
    ) -> Optional[Dict]:
        """
        Run a full council debate for a symbol.
        Returns the final decision or None if vetoed/blocked.
        """
        self._cycle_count += 1
        cycle_id = str(uuid.uuid4())

        agent_log("COUNCIL", f"━━ Conclave #{self._cycle_count} | {symbol} ━━")
        await BUS.emit(Events.COUNCIL_START, {"symbol": symbol, "cycle_id": cycle_id})

        # Check if pattern is blocked
        blocked = await is_pattern_blocked(symbol, indicators)
        if blocked:
            agent_log("COUNCIL", f"Padrão bloqueado para {symbol} — pulando debate")
            return None

        # Build strategy proposal from active strategies
        strategies = await get_active_strategies()
        if strategies:
            best = strategies[0]
            proposal = (
                f"Usar estratégia '{best.name}' (win_rate={best.win_rate}%) "
                f"com regras: {json.dumps(best.rules)}"
            )
        else:
            proposal = "Desenvolver nova estratégia baseada no contexto atual do mercado"

        # Run debate
        try:
            result = await GROQ.council_debate(symbol, market_context, proposal)
        except Exception as e:
            agent_log("COUNCIL", f"Erro no debate: {e}", logging.ERROR)
            return None

        # Log to database
        await save_council_log({
            "cycle_id":     cycle_id,
            "symbol":       symbol,
            "model_a_out":  json.dumps(result["model_a"]),
            "model_b_out":  json.dumps(result["model_b"]),
            "model_c_out":  json.dumps(result["model_c"]),
            "final_signal": result["final_signal"],
            "confidence":   result["confidence"],
            "strategy_ref": result["strategy"],
            "tokens_used":  result["tokens_used"],
            "latency_ms":   result["latency_ms"],
        })

        signal     = result["final_signal"]
        confidence = result["confidence"]
        approved   = result["approved"]

        agent_log(
            "COUNCIL",
            f"Debate concluído: {signal} | conf={confidence:.2f} | "
            f"aprovado={approved} | tokens={result['tokens_used']} | "
            f"{result['latency_ms']}ms"
        )

        # Veto check
        if not approved:
            agent_log("COUNCIL", f"VETADO: {result['veto_reason']}", logging.WARNING)
            return None

        # Confidence threshold
        if confidence < MIN_CONFIDENCE:
            agent_log(
                "COUNCIL",
                f"Confiança insuficiente: {confidence:.2f} < {MIN_CONFIDENCE}",
                logging.WARNING
            )
            return None

        decision = {
            "cycle_id":   cycle_id,
            "symbol":     symbol,
            "signal":     signal,
            "confidence": confidence,
            "strategy":   result["strategy"],
            "model_a":    result["model_a"],
            "model_b":    result["model_b"],
            "model_c":    result["model_c"],
            "latency_ms": result["latency_ms"],
        }

        await BUS.emit(Events.COUNCIL_DONE, decision)
        return decision


# ── Singleton ──────────────────────────────────────────────────────────────
CONCLAVE = Conclave()
