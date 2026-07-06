"""
NEXUS QUANTUM ULTRA — Auditor Agent
Monitora performance das estratégias e agentes. Auto-bloqueia padrões perdedores.
Gera relatórios periódicos de desempenho.
"""

import asyncio
import logging
from typing import Dict

from core.event_bus import BUS, Events
from database.repository import (
    get_trade_stats, get_active_strategies,
    block_strategy, update_daily_stats,
)
from utils.logger import agent_log


# Thresholds de auditoria
MIN_WIN_RATE_THRESHOLD = 35.0   # bloqueia estratégia abaixo disso
MIN_TRADES_TO_AUDIT    = 20     # mínimo de trades para avaliar
AUDIT_INTERVAL         = 300    # audita a cada 5 minutos


class AuditorAgent:
    NAME = "AUDITOR"

    def __init__(self):
        self._running = False
        self._cycle   = 0

        BUS.subscribe(Events.TRADE_CLOSE, self._on_trade_close)

    async def _on_trade_close(self, _event: str, data: Dict) -> None:
        """Registra resultado de cada trade para rastreamento em tempo real."""
        outcome = data.get("outcome", "")
        profit  = data.get("profit",  0.0)
        symbol  = data.get("symbol",  "")
        strat   = data.get("strategy_name", "N/A")
        icon    = "✅" if outcome == "WIN" else "❌"
        agent_log(
            self.NAME,
            f"{icon} {symbol} | {outcome} | profit={profit:+.2f} | strat={strat}"
        )

    async def _audit_strategies(self) -> None:
        """Verifica estratégias ativas e bloqueia as que estão abaixo do threshold."""
        try:
            strategies = await get_active_strategies()
            for strat in strategies:
                total = int(strat.total_trades or 0)
                if total < MIN_TRADES_TO_AUDIT:
                    continue
                win_rate = float(strat.win_rate or 0.0)
                if win_rate < MIN_WIN_RATE_THRESHOLD:
                    reason = (
                        f"Auditoria: win_rate={win_rate:.1f}% < "
                        f"{MIN_WIN_RATE_THRESHOLD}% após {total} trades"
                    )
                    await block_strategy(strat.name, reason)
                    agent_log(self.NAME, f"⛔ Estratégia bloqueada: '{strat.name}' — {reason}", logging.WARNING)
        except Exception as e:
            agent_log(self.NAME, f"Erro na auditoria de estratégias: {e}", logging.ERROR)

    async def _generate_report(self) -> None:
        """Gera relatório de desempenho global."""
        try:
            stats = await get_trade_stats()
            total    = stats.get("total",      0)
            wins     = stats.get("wins",       0)
            losses   = stats.get("losses",     0)
            win_rate = stats.get("win_rate",   0.0)
            net_pnl  = stats.get("net_profit", 0.0)

            if total == 0:
                return

            agent_log(
                self.NAME,
                f"📊 RELATÓRIO | Trades: {total} | Wins: {wins} | Losses: {losses} | "
                f"WR: {win_rate:.1f}% | P&L: ${net_pnl:+.2f}"
            )

            await update_daily_stats()
        except Exception as e:
            agent_log(self.NAME, f"Erro ao gerar relatório: {e}", logging.ERROR)

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Auditor Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            self._cycle += 1
            await self._audit_strategies()
            await self._generate_report()
            await asyncio.sleep(AUDIT_INTERVAL)

    def stop(self) -> None:
        self._running = False
