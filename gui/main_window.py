"""
NEXUS QUANTUM ULTRA — Main Window (PyQt6)
The most advanced Windows trading interface ever built.
"""

import asyncio
import sys
from datetime import datetime
from typing import Coroutine

import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QStatusBar,
    QSplitter, QTextEdit, QFrame, QProgressBar,
    QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QIcon, QTextCursor, QColor

from gui.panels.chart_panel   import ChartPanel
from gui.panels.agents_panel  import AgentsPanel
from gui.panels.risk_panel    import RiskPanel
from gui.panels.council_panel import CouncilPanel
from core.event_bus           import BUS, Events
from utils.logger             import get_emitter, AGENT_COLORS


# ── Async runner thread ────────────────────────────────────────────────────
class AsyncThread(QThread):
    started_sig = pyqtSignal()

    def __init__(self, coro_fn, parent=None):
        super().__init__(parent)
        self.coro_fn = coro_fn
        self.loop    = None

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.started_sig.emit()
        self.loop.run_until_complete(self.coro_fn())


# ── Trades table ───────────────────────────────────────────────────────────
class TradesTable(QTableWidget):
    COLS = ["Hora", "Símbolo", "Tipo", "Stake", "Profit", "Resultado", "Conf", "Estratégia"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.verticalHeader().setVisible(False)
        BUS.subscribe(Events.TRADE_CLOSE, self._on_trade)

    async def _on_trade(self, _e: str, data: dict):
        row  = self.rowCount()
        self.insertRow(row)
        ts   = datetime.now().strftime("%H:%M:%S")
        out  = data.get("outcome", "")
        prof = data.get("profit",  0.0)

        vals = [
            ts,
            data.get("symbol",        "──"),
            data.get("contract_type", "──"),
            f"$ {data.get('stake', 0):.2f}",
            f"{prof:+.2f}",
            out,
            f"{data.get('confidence', 0):.2f}",
            data.get("strategy_name", "──"),
        ]
        colors = {
            4: "#00ff88" if prof >= 0 else "#ff4444",
            5: "#00ff88" if out == "WIN" else "#ff4444",
        }
        for col, val in enumerate(vals):
            item = QTableWidgetItem(str(val))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if col in colors:
                item.setForeground(QColor(colors[col]))
            self.setItem(row, col, item)

        self.scrollToBottom()
        if self.rowCount() > 200:
            self.removeRow(0)


# ── Log panel ──────────────────────────────────────────────────────────────
class LogPanel(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.document().setMaximumBlockCount(2000)

        emitter = get_emitter()
        emitter.new_log.connect(self._append)

    def _append(self, agent: str, level: str, message: str):
        color  = AGENT_COLORS.get(agent, "#ffffff")
        lcolors = {
            "ERROR":    "#ff4444",
            "WARNING":  "#ffd700",
            "CRITICAL": "#ff00ff",
            "INFO":     "#c0cce0",
            "DEBUG":    "#4a6a9a",
        }
        lc  = lcolors.get(level, "#c0cce0")
        ts  = datetime.now().strftime("%H:%M:%S")

        self.append(
            f'<span style="color:#4a6a9a">[{ts}]</span> '
            f'<span style="color:{color};font-weight:700">[{agent}]</span> '
            f'<span style="color:{lc}">{message}</span>'
        )
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)


# ── Header bar ─────────────────────────────────────────────────────────────
class HeaderBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #060912, stop:0.4 #0a0e1a, stop:1 #060912);
                border-bottom: 1px solid #1e2d4a;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 8, 20, 8)

        # Logo / title
        title_box = QVBoxLayout()
        lbl_title = QLabel("NEXUS QUANTUM ULTRA")
        lbl_title.setObjectName("lbl_title")
        lbl_sub   = QLabel("MULTI-SYMBOL AI TRADING SYSTEM  ·  DERIV SYNTHETIC INDICES")
        lbl_sub.setObjectName("lbl_subtitle")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        layout.addLayout(title_box)
        layout.addStretch()

        # Live stats
        stats_box = QHBoxLayout()
        stats_box.setSpacing(24)

        self.lbl_balance    = self._stat("SALDO",    "──────",  "#00d4ff")
        self.lbl_pnl        = self._stat("P&L",      "──────",  "#00ff88")
        self.lbl_trades     = self._stat("TRADES",   "0",       "#ffd700")
        self.lbl_winrate    = self._stat("WIN RATE", "──",      "#a78bfa")
        self.lbl_conn       = self._stat("DERIV",    "⬤ OFF",   "#ff4444")
        self.lbl_time       = self._stat("HORA",     "──:──:──", "#4a6a9a")

        for box in [
            self.lbl_balance, self.lbl_pnl, self.lbl_trades,
            self.lbl_winrate, self.lbl_conn, self.lbl_time
        ]:
            stats_box.addLayout(box["layout"])

        layout.addLayout(stats_box)
        layout.addSpacing(20)

        # Buttons
        btn_box = QHBoxLayout()
        self.btn_start = QPushButton("▶  INICIAR")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setFixedWidth(130)

        self.btn_stop = QPushButton("■  PARAR")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setFixedWidth(130)
        self.btn_stop.setEnabled(False)

        btn_box.addWidget(self.btn_start)
        btn_box.addWidget(self.btn_stop)
        layout.addLayout(btn_box)

        # Clock update
        self._clock = QTimer()
        self._clock.timeout.connect(self._update_clock)
        self._clock.start(1000)

        BUS.subscribe(Events.BALANCE_UPDATE, self._on_balance)
        BUS.subscribe(Events.TRADE_CLOSE,    self._on_trade)

    def _stat(self, title: str, value: str, color: str) -> dict:
        box   = QVBoxLayout()
        box.setSpacing(1)
        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("color: #4a6a9a; font-size: 9px; font-weight: 700; letter-spacing: 1px;")
        lbl_v = QLabel(value)
        lbl_v.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: 900;")
        box.addWidget(lbl_t)
        box.addWidget(lbl_v)
        return {"layout": box, "value_lbl": lbl_v}

    def _update_clock(self):
        self.lbl_time["value_lbl"].setText(datetime.now().strftime("%H:%M:%S"))

    async def _on_balance(self, _e: str, data: dict):
        bal = data.get("balance", 0.0)
        self.lbl_balance["value_lbl"].setText(f"$ {bal:.2f}")
        self.lbl_conn["value_lbl"].setText("⬤ LIVE")
        self.lbl_conn["value_lbl"].setStyleSheet(
            "color: #00ff88; font-size: 15px; font-weight: 900;"
        )

    def _on_trade_stats(self, wins: int, total: int, pnl: float):
        wr = wins / total * 100 if total > 0 else 0.0
        self.lbl_trades["value_lbl"].setText(str(total))
        self.lbl_winrate["value_lbl"].setText(f"{wr:.1f}%")
        c = "#00ff88" if pnl >= 0 else "#ff4444"
        self.lbl_pnl["value_lbl"].setStyleSheet(
            f"color: {c}; font-size: 15px; font-weight: 900;"
        )
        self.lbl_pnl["value_lbl"].setText(f"$ {pnl:+.2f}")

    async def _on_trade(self, _e: str, _data: dict):
        pass   # delegated to RiskPanel


# ── Main Window ────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, boot_coro, parent=None):
        super().__init__(parent)
        self._boot_coro  = boot_coro
        self._async_thread: AsyncThread = None
        self._wins = self._losses = 0
        self._pnl  = 0.0

        self.setWindowTitle("NEXUS QUANTUM ULTRA — AI Trading System")
        self.setMinimumSize(1400, 900)
        self.resize(1600, 960)

        self._setup_ui()
        self._connect_buttons()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────
        self.header = HeaderBar()
        root.addWidget(self.header)

        # ── Preload progress bar (hidden after load) ────────────────────────
        self.preload_bar = QProgressBar()
        self.preload_bar.setRange(0, 100)
        self.preload_bar.setValue(0)
        self.preload_bar.setFormat("Carregando histórico... %v%")
        self.preload_bar.setFixedHeight(22)
        self.preload_bar.hide()
        root.addWidget(self.preload_bar)

        # ── Main splitter ──────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        # Left: tabs
        tabs = QTabWidget()
        tabs.setMinimumWidth(900)

        # Tab 1 — Chart
        self.chart_panel = ChartPanel()
        tabs.addTab(self.chart_panel, "📈  Gráfico")

        # Tab 2 — Agents
        self.agents_panel = AgentsPanel()
        tabs.addTab(self.agents_panel, "🤖  Agentes")

        # Tab 3 — Council
        self.council_panel = CouncilPanel()
        tabs.addTab(self.council_panel, "⚖️  Conselho Groq")

        # Tab 4 — Trades history
        self.trades_table = TradesTable()
        tabs.addTab(self.trades_table, "📋  Trades")

        splitter.addWidget(tabs)

        # Right: risk + log
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)
        right.setMinimumWidth(380)
        right.setMaximumWidth(480)

        self.risk_panel = RiskPanel()
        right_lay.addWidget(self.risk_panel, 55)

        log_grp = QGroupBox("📟  LOG DO SISTEMA")
        log_lay = QVBoxLayout(log_grp)
        log_lay.setContentsMargins(4, 4, 4, 4)
        self.log_panel = LogPanel()
        log_lay.addWidget(self.log_panel)
        right_lay.addWidget(log_grp, 45)

        splitter.addWidget(right)
        splitter.setSizes([1100, 420])
        root.addWidget(splitter)

        # ── Status bar ─────────────────────────────────────────────────────
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("NEXUS QUANTUM ULTRA  ·  Pronto para iniciar")

        # ── Bus subscriptions ──────────────────────────────────────────────
        BUS.subscribe("preload.progress", self._on_preload_progress)
        BUS.subscribe(Events.PRELOAD_ALL, self._on_preload_done)
        BUS.subscribe(Events.TRADE_CLOSE, self._on_trade_close)
        BUS.subscribe(Events.SYSTEM_STOP, self._on_system_stop)

    def _connect_buttons(self):
        self.header.btn_start.clicked.connect(self._start_system)
        self.header.btn_stop.clicked.connect(self._stop_system)

    def _start_system(self):
        self.header.btn_start.setEnabled(False)
        self.header.btn_stop.setEnabled(True)
        self.preload_bar.show()
        self.status_bar.showMessage("Iniciando NEXUS QUANTUM ULTRA...")

        self._async_thread = AsyncThread(self._boot_coro)
        self._async_thread.start()

    def _stop_system(self):
        if self._async_thread and self._async_thread.loop:
            self._async_thread.loop.call_soon_threadsafe(
                self._async_thread.loop.stop
            )
        self.header.btn_start.setEnabled(True)
        self.header.btn_stop.setEnabled(False)
        self.status_bar.showMessage("Sistema parado.")

    async def _on_preload_progress(self, _e: str, data: dict):
        pct = int(data.get("progress", 0))
        self.preload_bar.setValue(pct)
        self.status_bar.showMessage(
            f"Pré-carregando: {data.get('symbol','')} "
            f"[{data.get('completed',0)}/{data.get('total',0)}]  "
            f"— {data.get('candles',0):,} velas"
        )

    async def _on_preload_done(self, _e: str, data: dict):
        self.preload_bar.hide()
        total = data.get("total_candles", 0)
        self.status_bar.showMessage(
            f"✅ Pré-carga concluída — {total:,} velas carregadas  ·  Sistema operacional"
        )

    async def _on_trade_close(self, _e: str, data: dict):
        outcome = data.get("outcome", "")
        profit  = data.get("profit",  0.0)
        self._pnl += profit
        if outcome == "WIN":
            self._wins += 1
        else:
            self._losses += 1
        total = self._wins + self._losses
        self.header._on_trade_stats(self._wins, total, self._pnl)

    async def _on_system_stop(self, _e: str, data: dict):
        reason = data.get("reason", "unknown")
        self.status_bar.showMessage(f"⛔ Sistema parado — {reason}")
        self.header.btn_start.setEnabled(True)
        self.header.btn_stop.setEnabled(False)
