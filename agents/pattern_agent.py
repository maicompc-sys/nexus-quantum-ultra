"""
NEXUS QUANTUM ULTRA — Pattern Agent (stub)
Detecta padrões de candles: engolfo, doji, pin bar, etc.
IMPLEMENTAÇÃO PENDENTE — stub mínimo para evitar AttributeError no main.
"""

import asyncio
import logging
from typing import Dict, Optional

from core.event_bus import BUS, Events
from utils.logger import agent_log
from utils.config import SYMBOLS, ANALYSIS_INTERVAL


class PatternAgent:
    NAME = "PATTERN"

    def __init__(self):
        self._running = False
        agent_log(self.NAME, "Pattern Agent (stub) instanciado — implementação pendente.", logging.WARNING)

    def get_pattern(self, symbol: str) -> Optional[Dict]:
        return None

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Pattern Agent iniciado (stub — sem análise ativa).")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "stub"})
        while self._running:
            await asyncio.sleep(ANALYSIS_INTERVAL)

    def stop(self) -> None:
        self._running = False
