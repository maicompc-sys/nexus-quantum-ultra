"""
NEXUS QUANTUM ULTRA — Adaptive Agent
Adjusts neural network learning rate and agent weights based on P&L feedback.
"""

import asyncio
import logging
from typing import Dict

from core.event_bus import BUS, Events
from utils.logger import agent_log


class AdaptiveAgent:
    NAME = "ADAPTIVE"

    def __init__(self):
        self._running       = False
        self._wins          = 0
        self._losses        = 0
        self._learning_rate = 0.001
        self._performance_window: list = []

        BUS.subscribe(Events.TRADE_CLOSE, self._on_trade_close)
        BUS.subscribe(Events.NN_DONE,     self._on_nn_done)

    async def _on_trade_close(self, _event: str, data: Dict) -> None:
        outcome = data.get("outcome", "")
        profit  = data.get("profit",  0.0)

        if outcome == "WIN":
            self._wins += 1
        else:
            self._losses += 1

        self._performance_window.append(profit)
        if len(self._performance_window) > 20:
            self._performance_window.pop(0)

        await self._adapt()

    async def _on_nn_done(self, _event: str, data: Dict) -> None:
        accuracy = data.get("accuracy", 0.0)
        agent_log(self.NAME, f"Neural retrained — accuracy={accuracy:.3f}")

        # Trigger retraining if accuracy dropped
        if accuracy < 0.55:
            agent_log(self.NAME, "Accuracy baixa — solicitando novo treino", logging.WARNING)
            await BUS.emit(Events.NN_RETRAIN, {"reason": "low_accuracy"})

    async def _adapt(self) -> None:
        total = self._wins + self._losses
        if total < 10:
            return

        win_rate = self._wins / total
        avg_pnl  = sum(self._performance_window) / len(self._performance_window)

        # Adjust learning rate
        if win_rate > 0.65:
            self._learning_rate = min(0.005, self._learning_rate * 1.1)
        elif win_rate < 0.45:
            self._learning_rate = max(0.0001, self._learning_rate * 0.9)

        agent_log(
            self.NAME,
            f"Adaptive: win_rate={win_rate:.1%} | avg_pnl={avg_pnl:+.3f} | lr={self._learning_rate:.5f}"
        )

        # Request retraining every 50 trades
        if total % 50 == 0:
            await BUS.emit(Events.NN_RETRAIN, {
                "reason":        "scheduled",
                "learning_rate": self._learning_rate,
            })

    def get_learning_rate(self) -> float:
        return self._learning_rate

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Adaptive Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})
        while self._running:
            await asyncio.sleep(30)

    def stop(self):
        self._running = False
