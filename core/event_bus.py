"""
NEXUS QUANTUM ULTRA — Central Event Bus
Async pub/sub backbone. All agents communicate exclusively through here.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Any, Dict, List
from utils.logger import agent_log


class EventBus:
    """
    Lightweight async event bus.
    Usage:
        BUS.subscribe("tick.R_50", my_handler)
        await BUS.emit("tick.R_50", {"price": 1234.5})
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._wildcard:    List[Callable]             = []
        self._queue:       asyncio.Queue              = asyncio.Queue(maxsize=10_000)
        self._running:     bool                       = False

    def subscribe(self, event: str, handler: Callable) -> None:
        self._subscribers[event].append(handler)

    def subscribe_all(self, handler: Callable) -> None:
        """Subscribe to every event (useful for logging/monitoring)."""
        self._wildcard.append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        self._subscribers[event] = [
            h for h in self._subscribers[event] if h != handler
        ]

    async def emit(self, event: str, data: Any = None) -> None:
        await self._queue.put((event, data))

    def emit_sync(self, event: str, data: Any = None) -> None:
        """Fire-and-forget from sync context."""
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.emit(event, data))
            )
        except Exception as e:
            agent_log("SYSTEM", f"EventBus emit_sync error: {e}", logging.ERROR)

    async def _dispatch(self, event: str, data: Any) -> None:
        handlers = list(self._subscribers.get(event, []))
        handlers += self._wildcard

        # Wildcard pattern matching (e.g. "tick.*")
        for pattern, hs in self._subscribers.items():
            if pattern.endswith("*") and event.startswith(pattern[:-1]):
                handlers += hs

        if not handlers:
            return

        await asyncio.gather(
            *(self._safe_call(h, event, data) for h in handlers),
            return_exceptions=True,
        )

    async def _safe_call(self, handler: Callable, event: str, data: Any) -> None:
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event, data)
            else:
                handler(event, data)
        except Exception as e:
            agent_log("SYSTEM", f"EventBus handler error [{event}]: {e}", logging.ERROR)

    async def run(self) -> None:
        """Main dispatch loop — run as a background task."""
        self._running = True
        agent_log("SYSTEM", "EventBus iniciado.")
        while self._running:
            try:
                event, data = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                await self._dispatch(event, data)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                agent_log("SYSTEM", f"EventBus loop error: {e}", logging.ERROR)

    def stop(self) -> None:
        self._running = False


# ── Singleton ──────────────────────────────────────────────────────────────
BUS = EventBus()


# ── Event Name Constants ───────────────────────────────────────────────────
class Events:
    # Ticks & Candles
    TICK            = "tick"            # data: {symbol, price, epoch}
    CANDLE          = "candle"          # data: {symbol, gran, ohlcv}
    PRELOAD_DONE    = "preload.done"    # data: {symbol, count}
    PRELOAD_ALL     = "preload.all"     # data: None

    # Agent signals
    AGENT_SIGNAL    = "agent.signal"    # data: {agent, symbol, signal, confidence}
    AGENT_STATUS    = "agent.status"    # data: {agent, status}

    # Council
    COUNCIL_START   = "council.start"
    COUNCIL_DONE    = "council.done"    # data: {symbol, signal, confidence}

    # Trading
    GO_SIGNAL       = "trading.go"     # data: {symbol, direction, stake, confidence}
    TRADE_OPEN      = "trade.open"     # data: Trade dict
    TRADE_CLOSE     = "trade.close"    # data: {trade_id, outcome, profit}
    TRADE_ERROR     = "trade.error"    # data: {reason}

    # Neural
    NN_RETRAIN      = "neural.retrain"
    NN_DONE         = "neural.done"    # data: {accuracy, version}
    NN_PREDICT      = "neural.predict" # data: {symbol, prediction, confidence}

    # System
    SYSTEM_START    = "system.start"
    SYSTEM_STOP     = "system.stop"
    BALANCE_UPDATE  = "balance.update" # data: {balance, currency}
    ERROR           = "system.error"   # data: {source, message}
