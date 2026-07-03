"""
NEXUS QUANTUM ULTRA — Telegram Agent
Sends trade notifications and accepts commands via Telegram Bot.
"""

import asyncio
import logging
from typing import Optional

from core.event_bus import BUS, Events
from utils.logger import agent_log
from utils.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

try:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramAgent:
    NAME = "TELEGRAM"

    def __init__(self):
        self._running = False
        self._bot: Optional[object] = None
        self._app = None

        BUS.subscribe(Events.TRADE_OPEN,    self._on_trade_open)
        BUS.subscribe(Events.TRADE_CLOSE,   self._on_trade_close)
        BUS.subscribe(Events.SYSTEM_STOP,   self._on_system_stop)

    async def _send(self, message: str) -> None:
        if not TELEGRAM_AVAILABLE or not self._bot:
            return
        try:
            await self._bot.send_message(
                chat_id    = TELEGRAM_CHAT_ID,
                text       = message,
                parse_mode = "HTML",
            )
        except Exception as e:
            agent_log(self.NAME, f"Erro ao enviar msg: {e}", logging.WARNING)

    async def _on_trade_open(self, _event: str, data: dict) -> None:
        await self._send(
            f"🟢 <b>TRADE ABERTO</b>\n"
            f"Symbol: {data.get('symbol')}\n"
            f"Tipo: {data.get('contract_type')}\n"
            f"Stake: ${data.get('stake', 0):.2f}\n"
            f"Conf: {data.get('confidence', 0):.0%}"
        )

    async def _on_trade_close(self, _event: str, data: dict) -> None:
        outcome = data.get("outcome", "")
        icon    = "✅" if outcome == "WIN" else "❌"
        await self._send(
            f"{icon} <b>{outcome}</b>\n"
            f"Symbol: {data.get('symbol')}\n"
            f"Profit: {data.get('profit', 0):+.2f}\n"
            f"Estratégia: {data.get('strategy_name', 'N/A')}"
        )

    async def _on_system_stop(self, _event: str, data: dict) -> None:
        await self._send(f"⛔ <b>SISTEMA PARADO</b>\nMotivo: {data.get('reason', 'unknown')}")

    async def run(self) -> None:
        self._running = True
        if not TELEGRAM_AVAILABLE:
            agent_log(self.NAME, "Telegram não configurado — agente inativo")
            return

        agent_log(self.NAME, "Telegram Agent iniciado.")
        self._bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await self._send("🚀 <b>NEXUS QUANTUM ULTRA iniciado!</b>")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        while self._running:
            await asyncio.sleep(60)

    def stop(self):
        self._running = False
