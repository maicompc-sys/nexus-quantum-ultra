"""
NEXUS QUANTUM ULTRA — Risk Panel
Balance, drawdown, martingale levels, P&L in real-time.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QGroupBox, QProgressBar, QPushButton, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from core.event_bus import BUS, Events


class MetricBox(QFrame):
    def __init__(self, title: str, value: str = "──", color: str = "#00d4ff"):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: #0d1221;
                border: 1px solid #1e2d4a;
                border-top: 3px solid {color};
                border-radius: 8px;
                padding: 8px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        lbl_t = QLabel(title.upper())
        lbl_t.setStyleSheet("color: #4a6a9a; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        layout.addWidget(lbl_t)

        self.lbl_val = QLabel(value)
        self.lbl_val.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 900;")
        layout.addWidget(self.lbl_val)

    def set_value(self, v: str, color: str = None):
        self.lbl_val.setText(v)
        if color:
            self.lbl_val.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: 900;")


class RiskPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._balance   = 0.0
        self._peak      = 0.0
        self._net_pnl   = 0.0
        self._wins      = 0
        self._losses    = 0
        self._setup_ui()
        self._setup_bus()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        title = QLabel("⬡  PAINEL DE RISCO & PERFORMANCE")
        title.setStyleSheet(
            "color: #00d4ff; font-size: 14px; font-weight: 900; "
            "letter-spacing: 3px; padding: 4px 0;"
        )
        layout.addWidget(title)

        # ── Metric grid ────────────────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(8)

        self.box_balance  = MetricBox("Saldo",     "$ 0.00",  "#00d4ff")
        self.box_pnl      = MetricBox("P&L Hoje",  "$ 0.00",  "#00ff88")
        self.box_winrate  = MetricBox("Win Rate",  "0.0%",    "#ffd700")
        self.box_trades   = MetricBox("Trades",    "0",       "#a78bfa")
        self.box_drawdown = MetricBox("Drawdown",  "0.0%",    "#ff9944")
        self.box_martg    = MetricBox("Martingale","Nível 0", "#ff4444")

        grid.addWidget(self.box_balance,  0, 0)
        grid.addWidget(self.box_pnl,      0, 1)
        grid.addWidget(self.box_winrate,  0, 2)
        grid.addWidget(self.box_trades,   1, 0)
        grid.addWidget(self.box_drawdown, 1, 1)
        grid.addWidget(self.box_martg,    1, 2)
        layout.addLayout(grid)

        # ── Drawdown bar ───────────────────────────────────────────────────
        grp_dd = QGroupBox("Drawdown")
        dd_lay = QVBoxLayout(grp_dd)
        self.bar_drawdown = QProgressBar()
        self.bar_drawdown.setRange(0, 100)
        self.bar_drawdown.setValue(0)
        self.bar_drawdown.setFormat("%v%")
        self.bar_drawdown.setStyleSheet("""
            QProgressBar { background: #0d1221; border: 1px solid #1e2d4a; border-radius: 5px; }
            QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #00ff88, stop:0.5 #ffd700, stop:1 #ff4444); border-radius: 5px; }
        """)
        dd_lay.addWidget(self.bar_drawdown)
        layout.addWidget(grp_dd)

        # ── Win/Loss bars ──────────────────────────────────────────────────
        grp_wl = QGroupBox("Wins vs Losses")
        wl_lay = QVBoxLayout(grp_wl)

        self.bar_wins = QProgressBar()
        self.bar_wins.setRange(0, 100)
        self.bar_wins.setFormat("Wins: %v%")
        self.bar_wins.setStyleSheet("""
            QProgressBar::chunk { background: #00ff88; border-radius: 4px; }
        """)
        self.bar_losses = QProgressBar()
        self.bar_losses.setRange(0, 100)
        self.bar_losses.setFormat("Losses: %v%")
        self.bar_losses.setStyleSheet("""
            QProgressBar::chunk { background: #ff4444; border-radius: 4px; }
        """)
        wl_lay.addWidget(self.bar_wins)
        wl_lay.addWidget(self.bar_losses)
        layout.addWidget(grp_wl)

        # ── Controls ───────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        self.btn_reset_halt = QPushButton("⟳  RESETAR HALT")
        self.btn_reset_halt.clicked.connect(self._reset_halt)
        ctrl.addWidget(self.btn_reset_halt)
        ctrl.addStretch()
        layout.addLayout(ctrl)
        layout.addStretch()

    def _setup_bus(self):
        BUS.subscribe(Events.BALANCE_UPDATE, self._on_balance)
        BUS.subscribe(Events.TRADE_CLOSE,    self._on_trade_close)

    async def _on_balance(self, _e: str, data: dict):
        self._balance = data.get("balance", self._balance)
        if self._balance > self._peak:
            self._peak = self._balance
        self.box_balance.set_value(f"$ {self._balance:.2f}")
        if self._peak > 0:
            dd = (self._peak - self._balance) / self._peak * 100
            self.box_drawdown.set_value(
                f"{dd:.1f}%",
                "#ff4444" if dd > 10 else "#ffd700" if dd > 5 else "#00ff88"
            )
            self.bar_drawdown.setValue(int(dd))

    async def _on_trade_close(self, _e: str, data: dict):
        profit  = data.get("profit", 0.0)
        outcome = data.get("outcome", "")
        self._net_pnl += profit

        if outcome == "WIN":
            self._wins += 1
        else:
            self._losses += 1

        total    = self._wins + self._losses
        win_rate = self._wins / total * 100 if total > 0 else 0.0

        pnl_color = "#00ff88" if self._net_pnl >= 0 else "#ff4444"
        self.box_pnl.set_value(f"$ {self._net_pnl:+.2f}", pnl_color)
        self.box_winrate.set_value(f"{win_rate:.1f}%")
        self.box_trades.set_value(str(total))

        if total > 0:
            self.bar_wins.setValue(int(win_rate))
            self.bar_losses.setValue(int(100 - win_rate))

    def _reset_halt(self):
        from core.event_bus import BUS
        import asyncio
        asyncio.ensure_future(BUS.emit("risk.reset_halt", {}))
