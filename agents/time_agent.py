"""
NEXUS QUANTUM ULTRA — Time Agent
Detects best/worst trading windows per symbol based on historical win rates.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict
from collections import defaultdict

from core.event_bus import BUS, Events
from database.repository import get_recent_trades
from utils.logger import agent_log
from utils.config import SYMBOLS

# UTC hours to avoid (low liquidity / high spread windows)
BLACKOUT_HOURS_UTC = {0, 1, 2, 3, 22, 23}


class TimeAgent:
    NAME = "TIME"

    def __init__(self):
        self._running  = False
        self._hourly_stats: Dict[str, Dict[int, Dict]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "total": 0})
        )

    def is_good_time(self, symbol: str) -> bool:
        hour = datetime.now(timezone.utc).hour
        if hour in BLACKOUT_HOURS_UTC:
            return False

        stats = self._hourly_stats[symbol].get(hour, {})
        total = stats.get("total", 0)
        if total < 5:
            return True   # not enough data — allow

        win_rate = stats.get("wins", 0) / total
        return win_rate >= 0.45

    async def _update_stats(self) -> None:
        trades = await get_recent_trades(200)
        for t in trades:
            if not t.opened_at:
                continue
            hour   = t.opened_at.hour
            symbol = t.symbol
            self._hourly_stats[symbol][hour]["total"] += 1
            if t.outcome == "WIN":
                self._hourly_stats[symbol][hour]["wins"] += 1

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Time Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            await self._update_stats()
            hour = datetime.now(timezone.utc).hour
            for symbol in SYMBOLS:
                ok = self.is_good_time(symbol)
                await BUS.emit(Events.AGENT_SIGNAL, {
                    "agent":      self.NAME,
                    "symbol":     symbol,
                    "signal":     "CLEAR" if ok else "HOLD",
                    "confidence": 0.8 if ok else 0.2,
                    "data":       {"hour_utc": hour, "allowed": ok},
                })
            await asyncio.sleep(300)   # update every 5 min

    def stop(self):
        self._running = False
