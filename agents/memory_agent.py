"""
NEXUS QUANTUM ULTRA — Obsidian Memory Agent
Learns from trade history. Blocks losing patterns. Reinforces winners.
"""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Deque

from core.event_bus import BUS, Events
from database.repository import (
    get_recent_trades, add_blocked_pattern,
    update_strategy_stats, update_daily_stats
)
from utils.logger import agent_log
from utils.config import MEMORY_FILE


class MemoryAgent:
    NAME    = "MEMORY"
    MAX_MEM = 500   # trades kept in memory ring buffer

    def __init__(self):
        self._running  = False
        self._buffer:  Deque[Dict] = deque(maxlen=self.MAX_MEM)
        self._symbol_stats: Dict[str, Dict] = {}
        self._memory_file  = Path(MEMORY_FILE)

        BUS.subscribe(Events.TRADE_CLOSE, self._on_trade_close)

    async def _on_trade_close(self, _event: str, data: Dict) -> None:
        self._buffer.append(data)
        symbol   = data.get("symbol", "")
        outcome  = data.get("outcome", "")
        profit   = data.get("profit",  0.0)
        strategy = data.get("strategy_name", "")
        inds     = data.get("indicators", {})

        # Update strategy stats
        if strategy:
            await update_strategy_stats(strategy, outcome == "WIN", profit)

        # Track consecutive losses per symbol
        stats = self._symbol_stats.setdefault(symbol, {
            "wins": 0, "losses": 0, "streak": 0, "total_loss": 0.0
        })

        if outcome == "WIN":
            stats["wins"]   += 1
            stats["streak"]  = 0
            stats["total_loss"] = 0.0
        else:
            stats["losses"]     += 1
            stats["streak"]     += 1
            stats["total_loss"] += abs(profit)

            # Block pattern after 3 consecutive losses
            if stats["streak"] >= 3:
                await add_blocked_pattern(
                    symbol      = symbol,
                    description = f"{stats['streak']} perdas consecutivas em {symbol}",
                    indicators  = inds,
                    loss_streak = stats["streak"],
                    total_loss  = stats["total_loss"],
                    expires_hours = 6,
                )
                agent_log(
                    self.NAME,
                    f"⚠ Padrão bloqueado: {symbol} — {stats['streak']} perdas | "
                    f"Prejuízo: ${stats['total_loss']:.2f}",
                    logging.WARNING
                )

        await update_daily_stats()
        self._persist()

    def _persist(self) -> None:
        try:
            self._memory_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at":    datetime.utcnow().isoformat(),
                "symbol_stats":  self._symbol_stats,
                "recent_trades": list(self._buffer)[-20:],
            }
            self._memory_file.write_text(json.dumps(payload, indent=2, default=str))
        except Exception as e:
            agent_log(self.NAME, f"Erro ao persistir memória: {e}", logging.ERROR)

    def load(self) -> None:
        if self._memory_file.exists():
            try:
                data = json.loads(self._memory_file.read_text())
                self._symbol_stats = data.get("symbol_stats", {})
                agent_log(self.NAME, f"Memória carregada: {len(self._symbol_stats)} símbolos")
            except Exception as e:
                agent_log(self.NAME, f"Erro ao carregar memória: {e}", logging.ERROR)

    def get_summary(self) -> Dict:
        total_trades = sum(v["wins"] + v["losses"] for v in self._symbol_stats.values())
        total_wins   = sum(v["wins"] for v in self._symbol_stats.values())
        return {
            "total_trades": total_trades,
            "total_wins":   total_wins,
            "win_rate":     round(total_wins / total_trades * 100, 2) if total_trades else 0.0,
            "symbols":      self._symbol_stats,
        }

    async def run(self) -> None:
        self._running = True
        self.load()
        agent_log(self.NAME, "Memory Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})
        while self._running:
            await asyncio.sleep(60)

    def stop(self):
        self._running = False
