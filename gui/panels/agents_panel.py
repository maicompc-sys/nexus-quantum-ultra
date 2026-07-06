from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout,
    QLabel, QFrame, QGroupBox, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPalette

from core.event_bus import BUS, Events
from utils.logger import AGENT_COLORS


AGENT_LIST = [
    "SENTINEL","QUANT","PATTERN","RISK","EXECUTOR",
    "MEMORY","STRATEGY","AUDITOR",
    "ADAPTIVE","TIME","TELEGRAM","NEURAL",
]


class AgentCard(QFrame):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name   = name
        self.color  = AGENT_COLORS.get(name, "#ffffff")
        self.setFixedHeight(110)
        self.setStyleSheet(f"""
            QFrame {{
                background: #0d1221;
                border: 1px solid #1e2d4a;
                border-radius: 8px;
                border-left: 3px solid {self.color};
            }}
        """)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        # Header
        hdr = QLabel(self.name)
        hdr.setStyleSheet(f"color: {self.color}; font-weight: 900; font-size: 12px; letter-spacing: 1px;")
        layout.addWidget(hdr)

        # Status
        self.lbl_status = QLabel("⬤  AGUARDANDO")
        self.lbl_status.setStyleSheet("color: #4a6a9a; font-size: 11px;")
        layout.addWidget(self.lbl_status)

        # Signal + Confidence
        row = QLabel("HOLD  |  0.00")
        row.setObjectName("signal_row")
        row.setStyleSheet("color: #6b7fa3; font-size: 11px; font-weight: 600;")
        self.lbl_signal = row
        layout.addWidget(row)

        # Confidence bar
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background: #1a2540; border-radius: 2px; border: none; }}
            QProgressBar::chunk {{ background: {self.color}; border-radius: 2px; }}
        """)
        layout.addWidget(self.bar)

    def update_status(self, status: str):
        colors = {"running": "#00ff88", "error": "#ff4444", "idle": "#ffd700"}
        c = colors.get(status, "#4a6a9a")
        self.lbl_status.setStyleSheet(f"color: {c}; font-size: 11px;")
        icons  = {"running": "⬤  RUNNING", "error": "⬤  ERROR", "idle": "⬤  IDLE"}
        self.lbl_status.setText(icons.get(status, f"⬤  {status.upper()}"))

    def update_signal(self, signal: str, confidence: float):
        colors = {"CALL": "#00ff88", "PUT": "#ff4444", "HOLD": "#ffd700", "CLEAR": "#00d4ff"}
        c = colors.get(signal, "#6b7fa3")
        self.lbl_signal.setStyleSheet(f"color: {c}; font-size: 11px; font-weight: 700;")
        self.lbl_signal.setText(f"{signal}  |  {confidence:.2f}")
        self.bar.setValue(int(confidence * 100))


class AgentsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict = {}
        self._setup_ui()
        self._setup_bus()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(8, 8, 8, 8)
        main.setSpacing(8)

        title = QLabel("⬡  EXÉRCITO DE AGENTES IA")
        title.setStyleSheet(
            "color: #00d4ff; font-size: 14px; font-weight: 900; "
            "letter-spacing: 3px; padding: 4px 0;"
        )
        main.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(8)

        for i, name in enumerate(AGENT_LIST):
            card = AgentCard(name)
            self._cards[name] = card
            grid.addWidget(card, i // 3, i % 3)

        main.addLayout(grid)
        main.addStretch()

    def _setup_bus(self):
        BUS.subscribe(Events.AGENT_STATUS, self._on_status)
        BUS.subscribe(Events.AGENT_SIGNAL, self._on_signal)
        BUS.subscribe(Events.NN_DONE,      self._on_nn_done)

    async def _on_status(self, _e: str, data: dict):
        agent  = data.get("agent", "")
        status = data.get("status", "idle")
        if agent in self._cards:
            card = self._cards[agent]
            QTimer.singleShot(0, lambda c=card, s=status: c.update_status(s))

    async def _on_signal(self, _e: str, data: dict):
        agent      = data.get("agent", "")
        signal     = data.get("signal",     "HOLD")
        confidence = data.get("confidence", 0.0)
        if agent in self._cards:
            card = self._cards[agent]
            QTimer.singleShot(0, lambda c=card, sg=signal, cf=confidence: c.update_signal(sg, cf))

    async def _on_nn_done(self, _e: str, data: dict):
        if "NEURAL" in self._cards:
            acc  = data.get("accuracy", 0.0)
            card = self._cards["NEURAL"]
            QTimer.singleShot(0, lambda c=card, a=acc: c.update_signal("TRAINED", a))
