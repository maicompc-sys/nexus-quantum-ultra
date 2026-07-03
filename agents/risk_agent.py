"""
NEXUS QUANTUM ULTRA — Risk Agent
Dynamic stake sizing, Martingale control, drawdown protection.
"""

import asyncio
import logging
from typing import Dict, Optional

from core.event_bus import BUS, Events
from database.repository import get_trade_stats, get_recent_trades
from utils.logger import agent_log
from utils.config import (
    MIN_STAKE, MAX_STAKE, STOP_LOSS, TAKE_PROFIT,
    MARTINGALE_MULT, MARTINGALE_SAFE, MAX_MARTINGALE_LVL, MIN_CONFIDENCE
)


class RiskAgent:
    NAME = "RISK"

    def __init__(self):
        self._running        = False
        self._balance        = 0.0
        self._peak_balance   = 0.0
        self._martingale_lvl: Dict[str, int]   = {}
        self._last_stake:     Dict[str, float] = {}
        self._consecutive_losses: Dict[str, int] = {}
        self._trading_halted = False

        BUS.subscribe(Events.BALANCE_UPDATE, self._on_balance)
        BUS.subscribe(Events.TRADE_CLOSE,    self._on_trade_close)

    async def _on_balance(self, _event: str, data: Dict) -> None:
        self._balance = data.get("balance", self._balance)
        if self._balance > self._peak_balance:
            self._peak_balance = self._balance

    async def _on_trade_close(self, _event: str, data: Dict) -> None:
        symbol  = data.get("symbol", "")
        outcome = data.get("outcome", "")
        profit  = data.get("profit", 0.0)

        if outcome == "LOSS":
            self._consecutive_losses[symbol] = self._consecutive_losses.get(symbol, 0) + 1
            lvl = self._martingale_lvl.get(symbol, 0) + 1
            self._martingale_lvl[symbol] = min(lvl, MAX_MARTINGALE_LVL)
        else:
            self._consecutive_losses[symbol] = 0
            self._martingale_lvl[symbol]     = 0

        # Drawdown check
        if self._peak_balance > 0:
            drawdown = (self._peak_balance - self._balance) / self._peak_balance * 100
            if drawdown >= STOP_LOSS:
                self._trading_halted = True
                agent_log(
                    self.NAME,
                    f"⛔ HALT: Drawdown {drawdown:.1f}% atingiu SL={STOP_LOSS}%",
                    logging.CRITICAL
                )
                await BUS.emit(Events.SYSTEM_STOP, {"reason": f"drawdown_{drawdown:.1f}pct"})

    def compute_stake(self, symbol: str, confidence: float) -> Optional[float]:
        """Returns stake or None if trade should be blocked."""
        if self._trading_halted:
            agent_log(self.NAME, "Trading HALTED — stake negado", logging.WARNING)
            return None

        if confidence < MIN_CONFIDENCE:
            return None

        base_stake = MIN_STAKE

        # Confidence scaling
        conf_mult = 1.0 + (confidence - MIN_CONFIDENCE) * 2.0
        stake     = base_stake * conf_mult

        # Martingale
        lvl = self._martingale_lvl.get(symbol, 0)
        if lvl > 0:
            last = self._last_stake.get(symbol, base_stake)
            stake = last * MARTINGALE_SAFE if lvl <= 2 else last * MARTINGALE_MULT

        stake = round(max(MIN_STAKE, min(MAX_STAKE, stake)), 2)
        self._last_stake[symbol] = stake

        agent_log(
            self.NAME,
            f"{symbol} stake={stake} | lvl={lvl} | conf={confidence:.2f}"
        )
        return stake

    def get_status(self) -> Dict:
        return {
            "halted":      self._trading_halted,
            "balance":     self._balance,
            "peak":        self._peak_balance,
            "drawdown_pct": round(
                (self._peak_balance - self._balance) / self._peak_balance * 100, 2
            ) if self._peak_balance > 0 else 0.0,
            "martingale":  dict(self._martingale_lvl),
        }

    def reset_halt(self) -> None:
        self._trading_halted = False
        agent_log(self.NAME, "Trading HALT resetado manualmente.")

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, "Risk Agent iniciado.")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})
        while self._running:
            await asyncio.sleep(30)

    def stop(self):
        self._running = False
