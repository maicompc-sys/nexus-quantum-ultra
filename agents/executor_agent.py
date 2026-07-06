"""
NEXUS QUANTUM ULTRA — Executor Agent
Sends proposals and buy orders to Deriv. Tracks open contracts.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, Optional

from core.event_bus import BUS, Events
from database.repository import save_trade, update_trade_outcome
from utils.logger import agent_log
from utils.config import MIN_CONFIDENCE


class ExecutorAgent:
    NAME = "EXECUTOR"

    def __init__(self, deriv_client):
        self._running         = False
        self._deriv           = deriv_client
        self._proposal_active = False
        self._open_trade: Optional[Dict] = None

        BUS.subscribe(Events.GO_SIGNAL,    self._on_go)
        BUS.subscribe(Events.SYSTEM_STOP,  self._on_stop)

    async def _on_stop(self, _event: str, _data) -> None:
        self._running = False

    async def _on_go(self, _event: str, data: Dict) -> None:
        if self._proposal_active:
            agent_log(self.NAME, "Proposta já ativa — GO ignorado", logging.WARNING)
            return

        symbol     = data.get("symbol")
        direction  = data.get("direction")    # CALL | PUT
        stake      = data.get("stake", 0.35)
        confidence = data.get("confidence", 0.0)
        strategy   = data.get("strategy", "")
        indicators = data.get("indicators", {})

        if not symbol or not direction:
            agent_log(self.NAME, "GO inválido: faltam symbol/direction", logging.ERROR)
            return

        self._proposal_active = True
        agent_log(self.NAME, f"GO recebido: {symbol} {direction} stake={stake} conf={confidence:.2f}")

        try:
            # 1. Request proposal
            proposal = await self._deriv.proposal(
                symbol        = symbol,
                contract_type = direction,
                stake         = stake,
                duration      = 5,
                duration_unit = "t",
            )

            if not proposal or "error" in proposal:
                err = proposal.get("error", {}).get("message", "unknown") if proposal else "timeout"
                agent_log(self.NAME, f"Proposta rejeitada: {err}", logging.ERROR)
                await BUS.emit(Events.TRADE_ERROR, {"reason": err})
                return

            proposal_id = proposal.get("proposal", {}).get("id")
            if not proposal_id:
                agent_log(self.NAME, "Proposta sem ID", logging.ERROR)
                return

            agent_log(self.NAME, f"Proposta aceita: id={proposal_id}")

            # 2. Buy contract
            buy_result = await self._deriv.buy(proposal_id, stake)

            if not buy_result or "error" in buy_result:
                err = buy_result.get("error", {}).get("message", "unknown") if buy_result else "timeout"
                agent_log(self.NAME, f"Buy rejeitado: {err}", logging.ERROR)
                await BUS.emit(Events.TRADE_ERROR, {"reason": err})
                return

            contract_id = buy_result.get("buy", {}).get("contract_id")
            buy_price   = buy_result.get("buy", {}).get("buy_price", stake)

            trade_id = str(uuid.uuid4())
            trade_data = {
                "trade_id":       trade_id,
                "symbol":         symbol,
                "contract_type":  direction,
                "stake":          stake,
                "duration":       5,          # 5 ticks (padrão)
                "entry_price":    buy_price,
                "confidence":     confidence,
                "strategy_name":  strategy,
                "indicators":     indicators,
                "opened_at":      datetime.utcnow(),
                "account_type":   "demo",
            }

            self._open_trade = {**trade_data, "contract_id": contract_id}
            await save_trade(trade_data)

            await BUS.emit(Events.TRADE_OPEN, self._open_trade)
            agent_log(self.NAME, f"[OK] Trade aberto: {symbol} {direction} | contract={contract_id}")

        except Exception as e:
            agent_log(self.NAME, f"Exceção no executor: {e}", logging.ERROR)
            await BUS.emit(Events.TRADE_ERROR, {"reason": str(e)})
        finally:
            self._proposal_active = False

    async def handle_contract_settled(self, data: Dict) -> None:
        """Called by DerivAPI when contract_open_contract is_expired."""
        if not self._open_trade:
            return

        contract_id = data.get("contract_id")
        if self._open_trade.get("contract_id") != contract_id:
            return

        profit     = float(data.get("profit", 0.0))
        payout     = float(data.get("payout", 0.0))
        exit_price = float(data.get("exit_tick", 0.0))
        outcome    = "WIN" if profit > 0 else "LOSS"

        trade_id = self._open_trade["trade_id"]
        await update_trade_outcome(trade_id, outcome, profit, exit_price, payout)

        await BUS.emit(Events.TRADE_CLOSE, {
            "trade_id":     trade_id,
            "contract_id":  contract_id,
            "symbol":       self._open_trade["symbol"],
            "outcome":      outcome,
            "profit":       profit,
            "payout":       payout,
            "strategy_name": self._open_trade.get("strategy_name", ""),
            "indicators":   self._open_trade.get("indicators", {}),
        })

        icon = "[OK]" if outcome == "WIN" else "[X]"
        agent_log(
            self.NAME,
            f"{icon} {outcome}: {self._open_trade['symbol']} | "
            f"profit={profit:+.2f} | payout={payout:.2f}"
        )
        self._open_trade = None

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Executor Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})
        while self._running:
            await asyncio.sleep(1)

    def stop(self):
        self._running = False
