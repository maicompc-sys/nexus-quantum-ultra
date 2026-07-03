"""
NEXUS QUANTUM ULTRA — Preloader
Pré-carrega histórico massivo de velas via Deriv WS.
Suporta carga incremental — só baixa o que está faltando no DB.

New API obrigatório:
  - adjust_start_time: 1  em todo ticks_history
  - end: "latest" | epoch string
  - máximo 5000 velas por request
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

import websockets

from core.event_bus  import BUS, Events
from database.repository import (
    save_candles_batch,
    get_latest_candle_epoch,
    get_candle_count,
)
from utils.config    import (
    DERIV_WS_URL, DERIV_API_TOKEN,
    SYMBOLS, PRELOAD_GRANULARITIES, PRELOAD_TARGET,
)
from utils.logger    import agent_log


# ─────────────────────────────────────────────────────────────────────────────
BATCH_SIZE       = 5000     # max per WS request (Deriv limit)
RATE_LIMIT_DELAY = 0.5      # seconds between requests
SAVE_CHUNK_SIZE  = 1000     # candles per DB write


class PreloadSession:
    """
    Isolated WS session for preloading — avoids interfering with live trading WS.
    """

    def __init__(self):
        self._ws       = None
        self._req_id   = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._lock     = asyncio.Lock()

    async def connect(self) -> bool:
        try:
            self._ws = await websockets.connect(
                DERIV_WS_URL,
                ping_interval = 30,
                ping_timeout  = 20,
                close_timeout = 10,
                max_size      = 2 ** 22,    # 4MB — large candle batches
            )
            asyncio.create_task(self._listen(), name="preload_listener")

            # Authorize
            resp = await self._request({"authorize": DERIV_API_TOKEN}, timeout=15.0)
            if not resp or "error" in resp:
                err = resp.get("error", {}).get("message", "timeout") if resp else "timeout"
                agent_log("PRELOAD", f"Auth falhou: {err}", logging.ERROR)
                return False

            agent_log("PRELOAD", f"Sessão autorizada: {resp['authorize'].get('loginid','?')}")
            return True

        except Exception as e:
            agent_log("PRELOAD", f"Conexão falhou: {e}", logging.ERROR)
            return False

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                msg    = json_loads_safe(raw)
                req_id = msg.get("req_id")
                if req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
        except Exception:
            # Fail all pending
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Preload WS fechado"))
            self._pending.clear()

    async def _request(self, payload: Dict, timeout: float = 30.0) -> Optional[Dict]:
        async with self._lock:
            rid           = self._req_id
            self._req_id += 1

        fut                = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        payload["req_id"]  = rid

        try:
            await self._ws.send(json_dumps(payload))
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return None
        except Exception as e:
            self._pending.pop(rid, None)
            agent_log("PRELOAD", f"Request error: {e}", logging.WARNING)
            return None

    async def fetch_candle_batch(
        self,
        symbol:      str,
        granularity: int,
        count:       int,
        end_epoch:   Optional[int] = None,
    ) -> Optional[List[Dict]]:
        """
        Fetch up to `count` candles ending at `end_epoch`.
        New API requires adjust_start_time=1.
        """
        payload = {
            "ticks_history":     symbol,
            "style":             "candles",
            "granularity":       granularity,
            "count":             min(count, BATCH_SIZE),
            "end":               str(end_epoch) if end_epoch else "latest",
            "adjust_start_time": 1,          # ← New API obrigatório
        }

        resp = await self._request(payload, timeout=40.0)

        if not resp:
            agent_log("PRELOAD", f"Timeout: {symbol}/{granularity}s", logging.WARNING)
            return None

        if "error" in resp:
            code = resp["error"].get("code",    "")
            msg  = resp["error"].get("message", "")

            # Non-fatal errors
            if code in ("NoDataFound", "MarketIsClosed"):
                agent_log("PRELOAD", f"{symbol}: {msg} — pulando", logging.INFO)
                return []

            agent_log("PRELOAD", f"Error [{code}]: {msg}", logging.ERROR)
            return None

        candles = resp.get("candles", [])
        return [
            {
                "symbol":      symbol,
                "granularity": granularity,
                "epoch":       int(c["epoch"]),
                "open":        float(c["open"]),
                "high":        float(c["high"]),
                "low":         float(c["low"]),
                "close":       float(c["close"]),
            }
            for c in candles
            if all(k in c for k in ("epoch", "open", "high", "low", "close"))
        ]

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()


# ─────────────────────────────────────────────────────────────────────────────

class Preloader:
    """
    Pré-carrega PRELOAD_TARGET velas por símbolo/granularidade.
    Modo incremental: detecta onde o DB parou e continua de lá.
    """

    def __init__(self):
        self._session: Optional[PreloadSession] = None

    async def run(self, incremental: bool = True) -> Dict:
        agent_log("PRELOAD", "═" * 50)
        agent_log("PRELOAD", "Iniciando pré-carga de histórico...")
        agent_log("PRELOAD", f"Símbolos: {SYMBOLS}")
        agent_log("PRELOAD", f"Granularidades: {PRELOAD_GRANULARITIES}")
        agent_log("PRELOAD", f"Alvo por série: {PRELOAD_TARGET:,} velas")
        agent_log("PRELOAD", "═" * 50)

        self._session = PreloadSession()
        if not await self._session.connect():
            agent_log("PRELOAD", "Sessão de pré-carga falhou", logging.ERROR)
            return {}

        total_candles    = 0
        total_series     = len(SYMBOLS) * len(PRELOAD_GRANULARITIES)
        completed        = 0
        results          = {}

        for symbol in SYMBOLS:
            results[symbol] = {}
            for gran in PRELOAD_GRANULARITIES:
                series_key = f"{symbol}/{gran}s"
                try:
                    count = await self._preload_series(
                        symbol      = symbol,
                        granularity = gran,
                        incremental = incremental,
                    )
                    results[symbol][gran] = count
                    total_candles        += count
                    completed            += 1

                    pct = int(completed / total_series * 100)
                    agent_log("PRELOAD",
                        f"[{completed}/{total_series}] {series_key}: "
                        f"+{count:,} velas | Total: {total_candles:,}"
                    )

                    await BUS.emit("preload.progress", {
                        "symbol":    symbol,
                        "gran":      gran,
                        "progress":  pct,
                        "completed": completed,
                        "total":     total_series,
                        "candles":   total_candles,
                    })

                    await asyncio.sleep(RATE_LIMIT_DELAY)

                except Exception as e:
                    agent_log("PRELOAD", f"Erro em {series_key}: {e}", logging.ERROR)
                    completed += 1

        await self._session.close()

        agent_log("PRELOAD", "═" * 50)
        agent_log("PRELOAD", f"✅ Pré-carga concluída: {total_candles:,} velas")
        agent_log("PRELOAD", "═" * 50)

        await BUS.emit(Events.PRELOAD_ALL, {
            "total_candles": total_candles,
            "results":       results,
        })

        return results

    async def _preload_series(
        self,
        symbol:      str,
        granularity: int,
        incremental: bool,
    ) -> int:
        """
        Carrega uma série completa (symbol + granularity).
        Retorna quantidade de velas novas salvas.
        """
        # ── Incremental: check what we already have ───────────────────────
        existing_count = 0
        latest_epoch   = None

        if incremental:
            existing_count = await get_candle_count(symbol, granularity)
            latest_epoch   = await get_latest_candle_epoch(symbol, granularity)

            remaining = PRELOAD_TARGET - existing_count
            if remaining <= 0:
                agent_log("PRELOAD",
                    f"{symbol}/{granularity}s: já tem {existing_count:,} — pulando"
                )
                return 0

            target = remaining
            agent_log("PRELOAD",
                f"{symbol}/{granularity}s: tem {existing_count:,}, "
                f"baixando {target:,} faltantes..."
            )
        else:
            target = PRELOAD_TARGET

        # ── Fetch in batches walking backwards ────────────────────────────
        total_saved  = 0
        end_epoch    = latest_epoch    # None = "latest" on first call
        retries      = 0
        max_retries  = 3

        while total_saved < target:
            batch_size = min(BATCH_SIZE, target - total_saved)

            candles = await self._session.fetch_candle_batch(
                symbol      = symbol,
                granularity = granularity,
                count       = batch_size,
                end_epoch   = end_epoch,
            )

            # Retry logic
            if candles is None:
                retries += 1
                if retries >= max_retries:
                    agent_log("PRELOAD",
                        f"{symbol}/{granularity}s: {max_retries} retries esgotados",
                        logging.ERROR
                    )
                    break
                await asyncio.sleep(2 ** retries)
                continue

            retries = 0

            if not candles:
                break    # No more data available

            # ── Save in chunks ────────────────────────────────────────────
            for i in range(0, len(candles), SAVE_CHUNK_SIZE):
                chunk = candles[i:i + SAVE_CHUNK_SIZE]
                saved = await save_candles_batch(chunk)
                total_saved += saved

            # ── Walk backwards: next end = oldest epoch - 1 ──────────────
            oldest_epoch = min(c["epoch"] for c in candles)
            if end_epoch and oldest_epoch >= end_epoch:
                break    # No progress — avoid infinite loop

            end_epoch = oldest_epoch - 1

            # Rate limiting
            await asyncio.sleep(RATE_LIMIT_DELAY)

            # Log progress
            if total_saved % 5000 == 0 and total_saved > 0:
                agent_log("PRELOAD",
                    f"  {symbol}/{granularity}s: {total_saved:,}/{target:,} velas"
                )

        return total_saved


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

import json

def json_loads_safe(raw: str) -> Dict:
    try:
        return json.loads(raw)
    except Exception:
        return {}

def json_dumps(obj) -> str:
    return json.dumps(obj)
