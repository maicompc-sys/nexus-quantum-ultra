"""
NEXUS QUANTUM ULTRA — Chart Panel
Live candlestick chart with indicator overlays using pyqtgraph.
"""

import time
import numpy as np
from collections import deque
from typing import List, Dict

import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor

from core.event_bus import BUS, Events
from database.repository import get_candles
from utils.indicators import ema, rsi, bollinger_bands


pg.setConfigOptions(antialias=True, background="#060912", foreground="#4a6a9a")


class CandlestickItem(pg.GraphicsObject):
    def __init__(self, data: List[Dict]):
        super().__init__()
        self.data = data
        self.picture = None
        self.generatePicture()

    def generatePicture(self):
        self.picture = pg.QtGui.QPicture()
        p = QPainter(self.picture)
        p.setPen(pg.mkPen("w", width=0.5))

        w = 0.3
        for d in self.data:
            o, h, l, c = d["open"], d["high"], d["low"], d["close"]
            x = d["epoch"]
            color = QColor("#00ff88") if c >= o else QColor("#ff4444")
            p.setPen(pg.mkPen(color, width=0.5))
            p.setBrush(pg.mkBrush(color))
            p.drawLine(pg.QtCore.QPointF(x, l), pg.QtCore.QPointF(x, h))
            p.drawRect(pg.QtCore.QRectF(x - w, min(o, c), w * 2, abs(c - o) or 0.0001))

        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return pg.QtCore.QRectF(self.picture.boundingRect())


class ChartPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._symbol      = "R_50"
        self._granularity = 60
        self._candles:    List[Dict] = []
        self._setup_ui()
        self._setup_bus()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._load_candles)
        self._refresh_timer.start(5000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Controls ───────────────────────────────────────────────────────
        ctrl = QHBoxLayout()

        self.cmb_symbol = QComboBox()
        self.cmb_symbol.addItems([
            "R_10","R_25","R_50","R_75","R_100",
            "1HZ10V","1HZ25V","1HZ50V","1HZ75V","1HZ100V",
        ])
        self.cmb_symbol.setCurrentText("R_50")
        self.cmb_symbol.currentTextChanged.connect(self._on_symbol_change)

        self.cmb_gran = QComboBox()
        self.cmb_gran.addItems(["1m","5m","15m","1h"])
        self.cmb_gran.currentIndexChanged.connect(self._on_gran_change)

        self.lbl_price = QLabel("──────")
        self.lbl_price.setObjectName("lbl_value_blue")
        self.lbl_price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        ctrl.addWidget(QLabel("Símbolo:"))
        ctrl.addWidget(self.cmb_symbol)
        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("Granularidade:"))
        ctrl.addWidget(self.cmb_gran)
        ctrl.addStretch()
        ctrl.addWidget(self.lbl_price)
        layout.addLayout(ctrl)

        # ── Chart Layout ───────────────────────────────────────────────────
        self.plot_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.plot_widget)

        # Main price plot
        self.price_plot = self.plot_widget.addPlot(row=0, col=0)
        self.price_plot.setLabel("left", "Preço")
        self.price_plot.showGrid(x=True, y=True, alpha=0.15)
        self.price_plot.setMouseEnabled(x=True, y=True)

        # RSI plot
        self.rsi_plot = self.plot_widget.addPlot(row=1, col=0)
        self.rsi_plot.setLabel("left", "RSI")
        self.rsi_plot.setMaximumHeight(100)
        self.rsi_plot.showGrid(x=True, y=True, alpha=0.15)
        self.rsi_plot.setXLink(self.price_plot)

        # RSI reference lines
        self.rsi_plot.addItem(pg.InfiniteLine(70, angle=0, pen=pg.mkPen("#ff4444", width=0.8, style=Qt.PenStyle.DashLine)))
        self.rsi_plot.addItem(pg.InfiniteLine(30, angle=0, pen=pg.mkPen("#00ff88", width=0.8, style=Qt.PenStyle.DashLine)))
        self.rsi_plot.addItem(pg.InfiniteLine(50, angle=0, pen=pg.mkPen("#4a6a9a", width=0.5, style=Qt.PenStyle.DotLine)))

        self.plot_widget.ci.layout.setRowStretchFactor(0, 4)
        self.plot_widget.ci.layout.setRowStretchFactor(1, 1)

    def _setup_bus(self):
        BUS.subscribe(Events.TICK, self._on_tick)
        BUS.subscribe(Events.PRELOAD_ALL, lambda *_: self._load_candles())

    def _on_tick(self, _event: str, data: dict):
        if data.get("symbol") == self._symbol:
            price = data.get("price", 0)
            self.lbl_price.setText(f"  {price:.5f}  ")

    def _on_symbol_change(self, symbol: str):
        self._symbol = symbol
        self._load_candles()

    def _on_gran_change(self, idx: int):
        self._granularity = [60, 300, 900, 3600][idx]
        self._load_candles()

    def _load_candles(self):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._async_load())
        except Exception:
            pass

    async def _async_load(self):
        candles = await get_candles(self._symbol, self._granularity, limit=200)
        if candles:
            self._candles = candles
            self._draw()

    def _draw(self):
        if not self._candles:
            return

        self.price_plot.clear()
        self.rsi_plot.clear()

        candles = self._candles[-150:]

        # ── Candlesticks ───────────────────────────────────────────────────
        item = CandlestickItem(candles)
        self.price_plot.addItem(item)

        closes  = np.array([c["close"] for c in candles], dtype=float)
        epochs  = np.array([c["epoch"] for c in candles], dtype=float)

        # ── EMA overlays ───────────────────────────────────────────────────
        if len(closes) > 21:
            e9  = ema(closes, 9)
            e21 = ema(closes, 21)
            self.price_plot.plot(epochs, e9,  pen=pg.mkPen("#FFD700", width=1.2), name="EMA9")
            self.price_plot.plot(epochs, e21, pen=pg.mkPen("#FF69B4", width=1.2), name="EMA21")

        # ── Bollinger Bands ────────────────────────────────────────────────
        if len(closes) > 20:
            bb = bollinger_bands(closes)
            self.price_plot.plot(epochs, bb["upper"], pen=pg.mkPen("#2a4a8a", width=0.8))
            self.price_plot.plot(epochs, bb["lower"], pen=pg.mkPen("#2a4a8a", width=0.8))
            fill = pg.FillBetweenItem(
                self.price_plot.plot(epochs, bb["upper"]),
                self.price_plot.plot(epochs, bb["lower"]),
                brush=pg.mkBrush(QColor(42, 74, 138, 25))
            )
            self.price_plot.addItem(fill)

        # ── RSI ────────────────────────────────────────────────────────────
        if len(closes) > 15:
            rsi_vals = rsi(closes)
            valid    = ~np.isnan(rsi_vals)
            self.rsi_plot.addItem(
                pg.InfiniteLine(70, angle=0, pen=pg.mkPen("#ff4444", width=0.8, style=Qt.PenStyle.DashLine))
            )
            self.rsi_plot.addItem(
                pg.InfiniteLine(30, angle=0, pen=pg.mkPen("#00ff88", width=0.8, style=Qt.PenStyle.DashLine))
            )
            self.rsi_plot.plot(
                epochs[valid], rsi_vals[valid],
                pen=pg.mkPen("#a78bfa", width=1.5)
            )
