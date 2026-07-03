"""
NEXUS QUANTUM ULTRA — Strategy Agent
Uses Groq Council to create, evaluate and reformulate strategies dynamically.
"""

import asyncio
import logging
from typing import Dict, Optional

from core.event_bus import BUS, Events
from council.conclave import CONCLAVE
from database.repository import get_active_strategies, save_strategy
from utils.logger import agent_log
from utils.config import SYMBOLS, ANALYSIS_INTERVAL


class StrategyAgent:
    NAME = "STRATEGY"

    def __init__(self, quant_agent):
        self._running    = False
        self._quant      = quant_agent
        self._cycle      = 0

    async def _run_cycle(self, symbol: str) -> None:
        context = self._quant.get_context(symbol)
        if not context:
            return

        decision = await CONCLAVE.analyze(
            symbol          = symbol,
            market_context  = context,
            indicators      = context.get("indicators", {}),
        )

        if not decision:
            return

        # Persist new/updated strategy
        strategy_name = decision.get("strategy", f"auto_{symbol}_{self._cycle}")
        model_c       = decision.get("model_c", {})
        rules         = model_c.get("strategy_rules", {})

        await save_strategy({
            "name":        strategy_name,
            "description": f"Auto-gerada pelo Conclave para {symbol}",
            "rules":       rules,
            "symbols":     [symbol],
            "created_by":  "COUNCIL",
        })

        agent_log(self.NAME, f"Estratégia salva: '{strategy_name}' para {symbol}")

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Strategy Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            self._cycle += 1
            for symbol in SYMBOLS:
                try:
                    await self._run_cycle(symbol)
                    await asyncio.sleep(2)
                except Exception as e:
                    agent_log(self.NAME, f"Erro em {symbol}: {e}", logging.ERROR)
            await asyncio.sleep(ANALYSIS_INTERVAL * 3)

    def stop(self):
        self._running = False
