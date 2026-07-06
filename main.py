"""
NEXUS QUANTUM ULTRA — Entry Point
Ordem correta: DB → Deriv (aguarda conexão) → Preload → Agentes → GUI
"""

import asyncio
import logging
import sys
from PyQt6.QtWidgets import QApplication

from core.event_bus          import BUS, Events
from core.deriv_api          import DerivClient
from core.preloader          import Preloader
from database.repository     import init_db
from agents.sentinel_agent   import SentinelAgent
from agents.quant_agent      import QuantAgent
from agents.risk_agent       import RiskAgent
from agents.executor_agent   import ExecutorAgent
from agents.memory_agent     import MemoryAgent
from agents.strategy_agent   import StrategyAgent
from agents.adaptive_agent   import AdaptiveAgent
from agents.time_agent       import TimeAgent
from agents.arbitrator_agent import ArbitratorAgent
from neural.trainer          import NeuralTrainer
from agents.telegram_agent   import TelegramAgent
from gui.main_window         import MainWindow
from utils.logger            import agent_log

try:
    from agents.pattern_agent import PatternAgent
except ImportError:
    PatternAgent = None

try:
    from agents.auditor_agent import AuditorAgent
except ImportError:
    AuditorAgent = None


async def run_backend(deriv: DerivClient, app: QApplication) -> None:
    agent_log("SYSTEM", "=" * 60)
    agent_log("SYSTEM", "NEXUS QUANTUM ULTRA — INICIANDO")
    agent_log("SYSTEM", "=" * 60)

    # ── 0. Inicia EventBus — OBRIGATÓRIO antes de tudo ─────────────────
    asyncio.create_task(BUS.run(), name="event_bus")
    await asyncio.sleep(0)  # cede o loop para o BUS inicializar

    # ── 1. Banco de dados ──────────────────────────────────────────────
    await init_db()
    agent_log("SYSTEM", "Banco de dados inicializado.")

    # ── 2. Inicia Deriv em background ─────────────────────────────────
    deriv_task = asyncio.create_task(deriv.run(), name="deriv_main")

    # ── 3. Aguarda Deriv conectar (máx 90s) ───────────────────────────
    agent_log("SYSTEM", "Aguardando conexão Deriv...")
    connected = False
    for i in range(90):
        if deriv.is_connected():
            connected = True
            break
        await asyncio.sleep(1)

    if not connected:
        agent_log("SYSTEM",
            "⚠️ Deriv não conectou em 90s — continuando sem preload.\n"
            "  Verifique DERIV_APP_ID, DERIV_API_TOKEN e DERIV_ACCOUNT_ID no .env",
            logging.WARNING
        )
    else:
        agent_log("SYSTEM", "[OK] Deriv conectado.")

        # ── 4. Pré-carga de histórico ──────────────────────────────────
        agent_log("SYSTEM", "Pré-carregando histórico de velas...")
        preloader = Preloader()
        await preloader.run(incremental=True)

    # ── 5. Instancia agentes ───────────────────────────────────────────
    executor  = ExecutorAgent(deriv_client=deriv)
    deriv._executor = executor
    sentinel  = SentinelAgent()
    quant     = QuantAgent()
    risk      = RiskAgent()
    memory    = MemoryAgent()
    adaptive  = AdaptiveAgent()
    strategy  = StrategyAgent(quant_agent=quant)
    time_ag   = TimeAgent()
    arbitrator = ArbitratorAgent(risk_agent=risk, sentinel_agent=sentinel)
    neural    = NeuralTrainer(adaptive_agent=adaptive)
    telegram  = TelegramAgent()

    agents = [
        sentinel, quant, risk, executor,
        memory, strategy, adaptive, time_ag,
        arbitrator, neural, telegram,
    ]
    if PatternAgent:
        pattern = PatternAgent()
        agents.append(pattern)
    if AuditorAgent:
        auditor = AuditorAgent()
        agents.append(auditor)

    # ── 6. Inicia todas as tasks ───────────────────────────────────────
    tasks = [asyncio.create_task(a.run(), name=type(a).__name__) for a in agents]
    agent_log("SYSTEM", f"[OK] {len(tasks)} tasks iniciadas.")
    agent_log("SYSTEM", "=" * 60)

    # ── 7. Publica referências no BUS para a GUI ───────────────────────
    await BUS.emit("system.agents_ready", {
        "agents": {type(a).__name__: a for a in agents},
        "deriv":  deriv,
    })

    # ── 7b. NOVO: Emite SYSTEM_START para ativar arbitragem ──────────
    await asyncio.sleep(1)  # Aguarda agentes processarem agents_ready
    await BUS.emit(Events.SYSTEM_START, {})
    agent_log("SYSTEM", "[OK] Sistema operacional — arbitragem ativa")

    # ── 8. Mantém backend vivo ─────────────────────────────────────────
    try:
        await asyncio.gather(deriv_task, *tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        agent_log("SYSTEM", "Backend encerrado.")
        for a in agents:
            if hasattr(a, "stop"):
                a.stop()
        deriv.stop()


def main():
    # ── Qt Application ─────────────────────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName("NEXUS QUANTUM ULTRA")
    app.setStyle("Fusion")

    # ── Carrega stylesheet ──────────────────────────────────────────────
    try:
        with open("gui/styles/dark_premium.qss", "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except FileNotFoundError:
        agent_log("SYSTEM", "QSS não encontrado — usando estilo padrão", logging.WARNING)

    # ── DerivClient compartilhado ───────────────────────────────────────
    deriv = DerivClient()

    # ── Janela principal ────────────────────────────────────────────────
    window = MainWindow()
    window.show()

    # ── Event loop Qt + asyncio ─────────────────────────────────────────
    import qasync
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.create_task(run_backend(deriv, app))
        loop.run_forever()


if __name__ == "__main__":
    main()
