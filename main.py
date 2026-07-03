"""
NEXUS QUANTUM ULTRA — Entry Point
Boots all subsystems in order, launches GUI.
"""

import asyncio
import sys
import logging
from PyQt6.QtWidgets import QApplication

from utils.logger import agent_log
from utils.config import DERIV_APP_ID, GROQ_KEYS
from database.repository import init_db
from core.event_bus import BUS
from core.preloader import Preloader
from core.deriv_api import DerivClient

from agents.quant_agent      import QuantAgent
from agents.sentinel_agent   import SentinelAgent
from agents.risk_agent       import RiskAgent
from agents.memory_agent     import MemoryAgent
from agents.executor_agent   import ExecutorAgent
from agents.arbitrator_agent import ArbitratorAgent
from agents.strategy_agent   import StrategyAgent
from agents.adaptive_agent   import AdaptiveAgent
from agents.time_agent       import TimeAgent
from agents.telegram_agent   import TelegramAgent

from neural.trainer          import NeuralTrainer
from gui.main_window         import MainWindow


async def boot() -> None:
    agent_log("SYSTEM", "=" * 60)
    agent_log("SYSTEM", "  NEXUS QUANTUM ULTRA — INICIANDO")
    agent_log("SYSTEM", "=" * 60)

    # 1. Database
    await init_db()

    # 2. Instantiate all agents
    quant     = QuantAgent()
    sentinel  = SentinelAgent()
    risk      = RiskAgent()
    memory    = MemoryAgent()
    adaptive  = AdaptiveAgent()
    time_ag   = TimeAgent()
    telegram  = TelegramAgent()
    neural    = NeuralTrainer(adaptive_agent=adaptive)
    deriv     = DerivClient()
    executor  = ExecutorAgent(deriv_client=deriv)
    deriv._executor = executor
    arbitrator = ArbitratorAgent(risk_agent=risk, sentinel_agent=sentinel)
    strategy   = StrategyAgent(quant_agent=quant)

    # 3. Preload 5000+ candles
    agent_log("SYSTEM", "Pré-carregando histórico de velas...")
    preloader = Preloader()
    await preloader.run(incremental=True)

    agent_log("SYSTEM", "=" * 60)
    agent_log("SYSTEM", "Iniciando todos os agentes...")

    # 4. Launch all agents as async tasks
    tasks = [
        asyncio.create_task(BUS.run(),            name="event_bus"),
        asyncio.create_task(deriv.run(),           name="deriv"),
        asyncio.create_task(quant.run(),           name="quant"),
        asyncio.create_task(sentinel.run(),        name="sentinel"),
        asyncio.create_task(risk.run(),            name="risk"),
        asyncio.create_task(memory.run(),          name="memory"),
        asyncio.create_task(executor.run(),        name="executor"),
        asyncio.create_task(arbitrator.run(),      name="arbitrator"),
        asyncio.create_task(strategy.run(),        name="strategy"),
        asyncio.create_task(adaptive.run(),        name="adaptive"),
        asyncio.create_task(time_ag.run(),         name="time"),
        asyncio.create_task(telegram.run(),        name="telegram"),
        asyncio.create_task(neural.run(),          name="neural"),
    ]

    agent_log("SYSTEM", f"[OK] {len(tasks)} tasks iniciadas.")
    agent_log("SYSTEM", "=" * 60)

    # 5. Expose agents to GUI via BUS
    await BUS.emit("system.agents_ready", {
        "quant":      quant,
        "sentinel":   sentinel,
        "risk":       risk,
        "memory":     memory,
        "adaptive":   adaptive,
        "neural":     neural,
        "arbitrator": arbitrator,
    })

    # Keep running
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        agent_log("SYSTEM", "Sistema encerrado.")
    except Exception as e:
        agent_log("SYSTEM", f"Erro crítico: {e}", logging.CRITICAL)
    finally:
        for t in tasks:
            t.cancel()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("NEXUS QUANTUM ULTRA")
    app.setStyle("Fusion")

    # Load QSS theme
    try:
        from pathlib import Path
        qss = (Path(__file__).parent / "gui" / "styles" / "dark_premium.qss").read_text()
        app.setStyleSheet(qss)
    except Exception:
        pass

    window = MainWindow(boot_coro=boot)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
