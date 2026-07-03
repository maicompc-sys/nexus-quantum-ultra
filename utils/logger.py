"""
NEXUS QUANTUM ULTRA — Structured Logger
Color-coded, agent-tagged, persisted to file + emitted to GUI via signal.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR  = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── ANSI colors for terminal ───────────────────────────────────────────────
COLORS = {
    "DEBUG":    "\033[36m",   # cyan
    "INFO":     "\033[32m",   # green
    "WARNING":  "\033[33m",   # yellow
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[35m",   # magenta
    "RESET":    "\033[0m",
}

AGENT_COLORS = {
    "SENTINEL":    "#00BFFF",
    "PATTERN":     "#FF69B4",
    "QUANT":       "#FFD700",
    "RISK":        "#FF4500",
    "EXECUTOR":    "#00FF7F",
    "MEMORY":      "#9370DB",
    "STRATEGY":    "#FF8C00",
    "AUDITOR":     "#DC143C",
    "ARBITRATOR":  "#1E90FF",
    "ADAPTIVE":    "#32CD32",
    "TIME":        "#87CEEB",
    "TELEGRAM":    "#29B6F6",
    "COUNCIL":     "#F0E68C",
    "SYSTEM":      "#FFFFFF",
    "NEURAL":      "#E040FB",
    "PRELOADER":   "#26C6DA",
}


class LogSignalEmitter(QObject):
    """Qt signal emitter so agents can push logs to the GUI safely."""
    new_log = pyqtSignal(str, str, str)   # agent, level, message


_emitter: Optional[LogSignalEmitter] = None


def get_emitter() -> LogSignalEmitter:
    global _emitter
    if _emitter is None:
        _emitter = LogSignalEmitter()
    return _emitter


class NexusFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color  = COLORS.get(record.levelname, COLORS["RESET"])
        reset  = COLORS["RESET"]
        agent  = getattr(record, "agent", "SYSTEM")
        ts     = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        return (
            f"{color}[{ts}] [{record.levelname[:4]}] "
            f"[{agent}]{reset} {record.getMessage()}"
        )


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("nexus")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(NexusFormatter())
    ch.setLevel(logging.DEBUG)

    # File handler (daily rotation)
    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(LOG_DIR / f"nexus_{today}.log", encoding="utf-8")
    fh.setFormatter(NexusFormatter())
    fh.setLevel(logging.DEBUG)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


_logger = _build_logger()


def agent_log(agent: str, message: str, level: int = logging.INFO) -> None:
    """Universal log function — use this everywhere in the system."""
    extra = {"agent": agent}
    _logger.log(level, message, extra=extra)

    # Emit to GUI if connected
    try:
        get_emitter().new_log.emit(agent, logging.getLevelName(level), message)
    except Exception:
        pass


def get_logger() -> logging.Logger:
    return _logger
