"""
NEXUS QUANTUM ULTRA — Main Window (PyQt6)
"""

import asyncio
from datetime import datetime
from typing import Optional

import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QStatusBar,
    QSplitter, QTextEdit, QFrame, QProgressBar,
    QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QTextCursor, QColor

from gui.panels.chart_panel   import ChartPanel
from gui.panels.agents_panel  import AgentsPanel
from gui.panels.risk_panel    import RiskPanel
from core.event_bus           import BUS, Events
from utils.logger             import get_emitter, AGENT_COLORS


# ── Trades Table ───────────────────────────────────────────────────────────

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
        row = self.rowCount()
        self.insertRow(row)
        ts   = datetime.now().strftime("%H:%M:%S")
        out  = data.get("outcome", "")
        prof = data.get("profit", 0.0)
        vals = [
            ts,
            data.get("symbol",        "\u2500\u2500"),
            data.get("contract_type", "\u2500\u2500"),
            f"$ {data.get('stake', 0):.2f}",
            f"{prof:+.2f}",
            out,
            f"{data.get('confidence', 0):.2f}",
            data.get("strategy_name", "\u2500\u2500"),
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


# ── Log Panel ──────────────────────────────────────────────────────────────

class LogPanel(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.document().setMaximumBlockCount(2000)
        get_emitter().new_log.connect(self._append)

    def _append(self, agent: str, level: str, message: str):
        color = AGENT_COLORS.get(agent, "#ffffff")
        lcolors = {
            "ERROR":    "#ff4444",
            "WARNING":  "#ffd700",
            "CRITICAL": "#ff00ff",
            "INFO":     "#c0cce0",
            "DEBUG":    "#4a6a9a",
        }
        lc = lcolors.get(level, "#c0cce0")
        ts = datetime.now().strftime("%H:%M:%S")
        self.append(
            f'<span style="color:#4a6a9a">[{ts}]</span> '
            f'<span style="color:{color};font-weight:700">[{agent}]</span> '
            f'<span style="color:{lc}">{message}</span>'
        )
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)


# ── Header Bar ─────────────────────────────────────────────────────────────

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

        title_box = QVBoxLayout()
        lbl_title = QLabel("NEXUS QUANTUM ULTRA")
        lbl_title.setObjectName("lbl_title")
        lbl_sub   = QLabel("MULTI-SYMBOL AI TRADING SYSTEM  \u00b7  DERIV SYNTHETIC INDICES")
        lbl_sub.setObjectName("lbl_subtitle")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        layout.addLayout(title_box)
        layout.addStretch()

        stats_box = QHBoxLayout()
        stats_box.setSpacing(24)
        self.lbl_balance = self._stat("SALDO",    "\u2500\u2500\u2500\u2500\u2500\u2500", "#00d4ff")
        self.lbl_pnl     = self._stat("P&L",      "\u2500\u2500\u2500\u2500\u2500\u2500", "#00ff88")
        self.lbl_trades  = self._stat("TRADES",   "0",        "#ffd700")
        self.lbl_winrate = self._stat("WIN RATE", "\u2500\u2500",       "#a78bfa")
        self.lbl_conn    = self._stat("DERIV",    "\u2b24 OFF",    "#ff4444")
        self.lbl_time    = self._stat("HORA",     "\u2500\u2500:\u2500\u2500:\u2500\u2500", "#4a6a9a")
        for box in [self.lbl_balance, self.lbl_pnl, self.lbl_trades,
                    self.lbl_winrate, self.lbl_conn, self.lbl_time]:
            stats_box.addLayout(box["layout"])
        layout.addLayout(stats_box)
        layout.addSpacing(20)

        btn_box = QHBoxLayout()
        self.btn_start = QPushButton("\u25b6  INICIAR")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setFixedWidth(130)
        # Habilitado por default — será desabilitado só durante operação
        self.btn_start.setEnabled(True)

        self.btn_stop = QPushButton("\u25a0  PARAR")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setFixedWidth(130)
        self.btn_stop.setEnabled(False)

        btn_box.addWidget(self.btn_start)
        btn_box.addWidget(self.btn_stop)
        layout.addLayout(btn_box)

        self._clock = QTimer()
        self._clock.timeout.connect(self._update_clock)
        self._clock.start(1000)

        BUS.subscribe(Events.BALANCE_UPDATE, self._on_balance)

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
        def _update():
            self.lbl_balance["value_lbl"].setText(f"$ {bal:.2f}")
            self.lbl_conn["value_lbl"].setText("\u2b24 LIVE")
            self.lbl_conn["value_lbl"].setStyleSheet(
                "color: #00ff88; font-size: 15px; font-weight: 900;"
            )
        QTimer.singleShot(0, _update)

    def set_trading_stats(self, wins: int, total: int, pnl: float):
        wr = wins / total * 100 if total > 0 else 0.0
        self.lbl_trades["value_lbl"].setText(str(total))
        self.lbl_winrate["value_lbl"].setText(f"{wr:.1f}%")
        c = "#00ff88" if pnl >= 0 else "#ff4444"
        self.lbl_pnl["value_lbl"].setStyleSheet(
            f"color: {c}; font-size: 15px; font-weight: 900;"
        )
        self.lbl_pnl["value_lbl"].setText(f"$ {pnl:+.2f}")

    def enable_start(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)


# ── Main Window ────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """
    A janela nao inicia o backend.
    O backend ja roda via main.py desde o launch.
    O botao INICIAR apenas emite Events.SYSTEM_START.
    O botao PARAR emite Events.SYSTEM_STOP.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wins = self._losses = 0
        self._pnl  = 0.0
        self._trading = False
        self._preload_started = False   # evita liberar o botão antes do preload terminar

        self.setWindowTitle("NEXUS QUANTUM ULTRA \u2014 AI Trading System")
        self.setMinimumSize(1400, 900)
        self.resize(1600, 960)

        self._setup_ui()
        self._connect_buttons()
        self._subscribe_events()

        # Verifica apos 5s se preload ja terminou (caso evento tenha sido
        # emitido antes da GUI estar pronta para receber).
        # Só libera se o preload ainda não foi iniciado (conexão Deriv falhou).
        QTimer.singleShot(5000, self._check_preload_already_done)

    # ── UI Setup ───────────────────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = HeaderBar()
        root.addWidget(self.header)

        # Preload bar
        self.preload_bar = QProgressBar()
        self.preload_bar.setRange(0, 100)
        self.preload_bar.setValue(0)
        self.preload_bar.setFormat("Carregando histórico... %p%")
        self.preload_bar.setFixedHeight(22)
        self.preload_bar.show()
        root.addWidget(self.preload_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        tabs = QTabWidget()
        tabs.setMinimumWidth(900)

        self.chart_panel   = ChartPanel()
        self.agents_panel  = AgentsPanel()
        self.trades_table  = TradesTable()

        tabs.addTab(self.chart_panel,   "\U0001f4c8  Gráfico")
        tabs.addTab(self.agents_panel,  "\U0001f916  Agentes")
        tabs.addTab(self.trades_table,  "\U0001f4cb  Trades")

        splitter.addWidget(tabs)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)
        right.setMinimumWidth(380)
        right.setMaximumWidth(480)

        self.risk_panel = RiskPanel()
        right_lay.addWidget(self.risk_panel, 55)

        log_grp = QGroupBox("\U0001f4df  LOG DO SISTEMA")
        log_lay = QVBoxLayout(log_grp)
        log_lay.setContentsMargins(4, 4, 4, 4)
        self.log_panel = LogPanel()
        log_lay.addWidget(self.log_panel)
        right_lay.addWidget(log_grp, 45)

        splitter.addWidget(right)
        splitter.setSizes([1100, 420])
        root.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("NEXUS QUANTUM ULTRA  \u00b7  Inicializando...")

    def _connect_buttons(self):
        self.header.btn_start.clicked.connect(self._on_start_clicked)
        self.header.btn_stop.clicked.connect(self._on_stop_clicked)

    def _subscribe_events(self):
        BUS.subscribe("preload.progress",  self._on_preload_progress)
        BUS.subscribe(Events.PRELOAD_ALL,  self._on_preload_done)
        BUS.subscribe(Events.TRADE_CLOSE,  self._on_trade_close)
        BUS.subscribe(Events.SYSTEM_STOP,  self._on_system_stop)
        BUS.subscribe(Events.AGENT_STATUS, self._on_agent_status)

    def _check_preload_already_done(self):
        """Se o preload ja terminou antes da GUI estar pronta, libera o botao.
        Mas APENAS se o preload nao foi iniciado (Deriv nao conectou)."""
        if not self._preload_started:
            # Deriv nao conectou em tempo — libera para operar mesmo assim
            self._release_start_button("Pronto (sem preload) — clique em INICIAR para operar")

    def _release_start_button(self, msg: str = ""):
        self.preload_bar.setValue(100)
        self.preload_bar.hide()
        self.header.btn_start.setEnabled(True)
        self.header.btn_stop.setEnabled(False)
        if msg:
            self.status_bar.showMessage(msg)

    # ── Button Handlers ────────────────────────────────────────────────────

    def _on_start_clicked(self):
        """Apenas emite sinal — NAO reinicia backend nem preload."""
        if self._trading:
            return
        self._trading = True
        self.header.btn_start.setEnabled(False)
        self.header.btn_stop.setEnabled(True)
        self.status_bar.showMessage("\u25b6 Operação iniciada \u2014 agentes ativos")

        asyncio.ensure_future(
            BUS.emit(Events.SYSTEM_START, {"mode": "auto"})
        )

    def _on_stop_clicked(self):
        """Pausa operação sem matar o backend."""
        self._trading = False
        self.header.btn_start.setEnabled(True)
        self.header.btn_stop.setEnabled(False)
        self.status_bar.showMessage("\u25a0 Operação pausada")

        asyncio.ensure_future(
            BUS.emit(Events.SYSTEM_STOP, {"reason": "user_stop", "restart": False})
        )

    # ── Event Handlers ─────────────────────────────────────────────────────

    async def _on_preload_progress(self, _e: str, data: dict):
        self._preload_started = True
        pct = int(data.get("progress", 0))
        def _update():
            self.preload_bar.setValue(pct)
            self.status_bar.showMessage(
                f"Pr\u00e9-carregando: {data.get('symbol', '')} "
                f"[{data.get('completed', 0)}/{data.get('total', 0)}]"
                f"  \u2014  {data.get('candles', 0):,} velas"
            )
        QTimer.singleShot(0, _update)

    async def _on_preload_done(self, _e: str, data: dict):
        """Preload terminou — libera botao INICIAR via QTimer (thread-safe)."""
        total = data.get("total_candles", 0)
        msg   = f"\u2705 {total:,} velas carregadas  \u00b7  Clique em INICIAR para operar"
        QTimer.singleShot(0, lambda: self._release_start_button(msg))

    async def _on_trade_close(self, _e: str, data: dict):
        outcome = data.get("outcome", "")
        profit  = data.get("profit",  0.0)
        self._pnl += profit
        if outcome == "WIN":
            self._wins += 1
        else:
            self._losses += 1
        total = self._wins + self._losses
        def _update():
            self.header.set_trading_stats(self._wins, total, self._pnl)
        QTimer.singleShot(0, _update)

    async def _on_system_stop(self, _e: str, data: dict):
        if data.get("restart", True):
            return
        reason = data.get("reason", "unknown")
        self._trading = False
        def _update():
            self.header.btn_start.setEnabled(True)
            self.header.btn_stop.setEnabled(False)
            self.status_bar.showMessage(f"\u26d4 Parado \u2014 {reason}")
        QTimer.singleShot(0, _update)

    async def _on_agent_status(self, _e: str, data: dict):
        agent  = data.get("agent",  "")
        status = data.get("status", "")
        if agent == "DERIV" and status == "running":
            def _update():
                self.status_bar.showMessage("\U0001f517 Deriv conectado \u2014 aguardando preload...")
            QTimer.singleShot(0, _update)
