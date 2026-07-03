"""
NEXUS QUANTUM ULTRA — Council Panel
Real-time Groq council debate viewer.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QTextEdit, QGroupBox, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QTextCursor

from core.event_bus import BUS, Events


class CouncilPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._setup_bus()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("⬡  CONSELHO GROQ — DEBATE AO VIVO")
        title.setStyleSheet(
            "color: #ffd700; font-size: 14px; font-weight: 900; "
            "letter-spacing: 3px; padding: 4px 0;"
        )
        layout.addWidget(title)

        # ── Decision header ────────────────────────────────────────────────
        hdr = QHBoxLayout()

        self.lbl_symbol = QLabel("──────")
        self.lbl_symbol.setStyleSheet("color: #00d4ff; font-size: 18px; font-weight: 900;")

        self.lbl_decision = QLabel("AGUARDANDO")
        self.lbl_decision.setStyleSheet("color: #ffd700; font-size: 18px; font-weight: 900;")

        self.lbl_conf = QLabel("conf: 0.00")
        self.lbl_conf.setStyleSheet("color: #a78bfa; font-size: 14px;")

        self.lbl_latency = QLabel("0ms")
        self.lbl_latency.setStyleSheet("color: #4a6a9a; font-size: 12px;")

        hdr.addWidget(QLabel("Símbolo:"))
        hdr.addWidget(self.lbl_symbol)
        hdr.addSpacing(24)
        hdr.addWidget(QLabel("Decisão:"))
        hdr.addWidget(self.lbl_decision)
        hdr.addSpacing(16)
        hdr.addWidget(self.lbl_conf)
        hdr.addStretch()
        hdr.addWidget(self.lbl_latency)
        layout.addLayout(hdr)

        # ── 3 model outputs ────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        model_configs = [
            ("AGENTE A  —  ANÁLISE", "#00d4ff",  "moonshotai/kimi-k2"),
            ("AGENTE B  —  DESAFIO", "#ff9944",  "llama-4-maverick"),
            ("AGENTE C  —  SÍNTESE", "#00ff88",  "llama-3.3-70b"),
        ]

        self.model_boxes = []
        for label, color, model in model_configs:
            grp = QGroupBox(f"{label}\n{model}")
            grp.setStyleSheet(f"""
                QGroupBox {{
                    border: 1px solid {color}44;
                    border-top: 2px solid {color};
                    border-radius: 6px;
                    color: {color};
                    font-weight: 700;
                    font-size: 10px;
                    margin-top: 14px;
                    padding-top: 10px;
                }}
            """)
            g_lay = QVBoxLayout(grp)
            txt   = QTextEdit()
            txt.setReadOnly(True)
            txt.setFont(QFont("Consolas", 10))
            g_lay.addWidget(txt)
            splitter.addWidget(grp)
            self.model_boxes.append(txt)

        layout.addWidget(splitter)

        # ── History log ───────────────────────────────────────────────────
        grp_hist = QGroupBox("Histórico de Debates")
        hist_lay = QVBoxLayout(grp_hist)
        self.log_history = QTextEdit()
        self.log_history.setReadOnly(True)
        self.log_history.setMaximumHeight(140)
        self.log_history.setFont(QFont("Consolas", 10))
        hist_lay.addWidget(self.log_history)
        layout.addWidget(grp_hist)

    def _setup_bus(self):
        BUS.subscribe(Events.COUNCIL_DONE, self._on_council_done)

    async def _on_council_done(self, _e: str, data: dict):
        import json

        symbol     = data.get("symbol",     "──")
        signal     = data.get("signal",     "HOLD")
        confidence = data.get("confidence", 0.0)
        latency    = data.get("latency_ms", 0)

        self.lbl_symbol.setText(symbol)
        self.lbl_latency.setText(f"{latency}ms")
        self.lbl_conf.setText(f"conf: {confidence:.2f}")

        colors = {"CALL": "#00ff88", "PUT": "#ff4444", "HOLD": "#ffd700"}
        c = colors.get(signal, "#ffd700")
        self.lbl_decision.setStyleSheet(f"color: {c}; font-size: 18px; font-weight: 900;")
        self.lbl_decision.setText(signal)

        for i, key in enumerate(["model_a", "model_b", "model_c"]):
            content = data.get(key, {})
            self.model_boxes[i].setPlainText(
                json.dumps(content, indent=2, ensure_ascii=False)
            )

        # Append to history
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_history.append(
            f'<span style="color:#4a6a9a">[{ts}]</span> '
            f'<span style="color:#00d4ff">{symbol}</span> → '
            f'<span style="color:{c}"><b>{signal}</b></span> '
            f'<span style="color:#a78bfa">conf={confidence:.2f}</span>'
        )
        cursor = self.log_history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_history.setTextCursor(cursor)
