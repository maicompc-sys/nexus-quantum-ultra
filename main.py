"""
NEXUS QUANTUM ULTRA — Entry Point
Ordem correta: DB → Executor (ref) → Deriv → Preload → Agentes → GUI
"""

import asyncio
import logging
import sys

import qasync
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
    # Valida que PatternAgent é realmente uma classe instanciável
    if not callable(PatternAgent):
        PatternAgent = None
except (ImportError, AttributeError):
    PatternAgent = None

try:
    from agents.council_agent import CouncilAgent
    if not callable(CouncilAgent):
        CouncilAgent = None
except (ImportError, AttributeError):
    CouncilAgent = None


async def _agent_runner(agent, name: str) -> None:
    """Wrapper que loga exceções de agentes sem silenciá-las."""
    try:
        await agent.run()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        agent_log("SYSTEM", f"[CRASH] Agente {name}: {type(e).__name__}: {e}", logging.CRITICAL)


async def run_backend(deriv: DerivClient, app: QApplication) -> None:
    agent_log("SYSTEM", "=" * 60)
    agent_log("SYSTEM", "NEXUS QUANTUM ULTRA — INICIANDO")
    agent_log("SYSTEM", "=" * 60)

    # ── 1. Banco de dados ──────────────────────────────────────────────
    await init_db()
    agent_log("SYSTEM", "Banco de dados inicializado.")

    # ── 2. Instancia Executor ANTES de iniciar Deriv ───────────────────
    # CRÍTICO: executor deve existir antes do WS receber proposal_open_contract
    executor = ExecutorAgent(deriv_client=deriv)
    deriv._executor = executor

    # ── 3. Inicia Deriv em background ─────────────────────────────────
    deriv_task = asyncio.create_task(deriv.run(), name="deriv_main")

    # ── 4. Aguarda Deriv conectar (máx 90s) ───────────────────────────
    agent_log("SYSTEM", "Aguardando conexão Deriv...")
    connected = False
    for _ in range(90):
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

        # ── 5. Pré-carga de histórico ──────────────────────────────────
        agent_log("SYSTEM", "Pré-carregando histórico de velas...")
        preloader = Preloader()
        await preloader.run(incremental=True)

    # ── 6. Instancia demais agentes ────────────────────────────────────
    sentinel   = SentinelAgent()
    quant      = QuantAgent()
    risk       = RiskAgent()
    memory     = MemoryAgent()
    adaptive   = AdaptiveAgent()
    strategy   = StrategyAgent(quant_agent=quant)
    time_ag    = TimeAgent()
    arbitrator = ArbitratorAgent(risk_agent=risk, sentinel_agent=sentinel)
    neural     = NeuralTrainer(adaptive_agent=adaptive)
    telegram   = TelegramAgent()

    agents = [
        sentinel, quant, risk, executor,
        memory, strategy, adaptive, time_ag,
        arbitrator, neural, telegram,
    ]
    if PatternAgent:
        pattern = PatternAgent()
        agents.append(pattern)
    if CouncilAgent:
        council = CouncilAgent(deriv, executor)
        agents.append(council)

    # ── 7. Inicia todas as tasks com wrapper de log de crash ───────────
    tasks = [
        asyncio.create_task(
            _agent_runner(a, type(a).__name__),
            name=type(a).__name__
        )
        for a in agents
    ]
    agent_log("SYSTEM", f"[OK] {len(tasks)} tasks iniciadas.")
    agent_log("SYSTEM", "=" * 60)

    # ── 8. Publica referências no BUS para a GUI ───────────────────────
    await BUS.emit("system.agents_ready", {
        "agents": {type(a).__name__: a for a in agents},
        "deriv":  deriv,
    })

    # ── 9. Mantém backend vivo ─────────────────────────────────────────
    try:
        done, pending = await asyncio.wait(
            [deriv_task, *tasks],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            exc = task.exception()
            if exc:
                agent_log("SYSTEM", f"Task {task.get_name()} encerrou com erro: {exc}", logging.CRITICAL)
        for task in pending:
            task.cancel()
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
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.create_task(run_backend(deriv, app))
        loop.run_forever()


if __name__ == "__main__":
    main()
