"""
NEXUS QUANTUM ULTRA — Preloader (New API 2026)
Pré-carrega histórico via OTP WebSocket.
Modo incremental: só baixa o que falta no DB.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from core.event_bus       import BUS, Events
from database.repository  import (
    save_candles_batch,
    get_latest_candle_epoch,
    get_candle_count,
)
from utils.config  import (
    DERIV_APP_ID, DERIV_API_TOKEN, DERIV_ACCOUNT_ID,
    SYMBOLS, PRELOAD_GRANULARITIES, PRELOAD_TARGET,
)
from utils.logger  import agent_log


BATCH_SIZE       = 5000
RATE_LIMIT_DELAY = 0.4
SAVE_CHUNK_SIZE  = 1000
REST_BASE        = "https://api.derivws.com"


# ─────────────────────────────────────────────────────────────────────────────

class PreloadSession:
    """Sessão WS isolada para preload — não interfere com o DerivClient principal."""

    def __init__(self):
        self._ws       = None
        self._req_id   = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._lock     = asyncio.Lock()
        self._alive    = False

    # ── OTP ──────────────────────────────────────────────────────────────────

    async def _get_otp_url(self) -> Optional[str]:
        """REST POST /otp → URL WebSocket autenticada."""
        if not DERIV_ACCOUNT_ID:
            agent_log("PRELOAD",
                "DERIV_ACCOUNT_ID não configurado!\n"
                "→ Adicione no .env: DERIV_ACCOUNT_ID=DOT93171699",
                logging.CRITICAL
            )
            return None

        url = f"{REST_BASE}/trading/v1/options/accounts/{DERIV_ACCOUNT_ID}/otp"
        headers = {
            "Deriv-App-ID":  DERIV_APP_ID,
            "Authorization": f"Bearer {DERIV_API_TOKEN}",
            "Content-Type":  "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:

                    if resp.status == 200:
                        raw_data = await resp.json()
                        data = raw_data.get("data", raw_data)
                        ws_url = (
                            data.get("url") or
                            data.get("ws_url") or
                            data.get("websocket_url")
                        )
                        if not ws_url:
                            otp = data.get("otp") or data.get("token")
                            if otp:
                                ws_url = (
                                    f"wss://api.derivws.com"
                                    f"/trading/v1/options/ws/demo?otp={otp}"
                                )
                        if ws_url:
                            agent_log("PRELOAD", "OTP obtido para sessão de preload")
                            return ws_url
                        agent_log("PRELOAD", f"OTP response inesperado: {data}", logging.ERROR)
                        return None

                    elif resp.status == 401:
                        body = await resp.text()
                        agent_log("PRELOAD",
                            f"401 Unauthorized no preload OTP.\n"
                            f"Verifique DERIV_APP_ID e DERIV_API_TOKEN.\n"
                            f"Body: {body[:200]}",
                            logging.ERROR
                        )
                        return None

                    elif resp.status == 404:
                        agent_log("PRELOAD",
                            f"404 — DERIV_ACCOUNT_ID '{DERIV_ACCOUNT_ID}' inválido.",
                            logging.ERROR
                        )
                        return None

                    else:
                        body = await resp.text()
                        agent_log("PRELOAD", f"OTP HTTP {resp.status}: {body[:200]}", logging.ERROR)
                        return None

        except aiohttp.ClientError as e:
            agent_log("PRELOAD", f"OTP network error: {e}", logging.ERROR)
            return None
        except Exception as e:
            agent_log("PRELOAD", f"OTP exception: {e}", logging.ERROR)
            return None

    # ── Connect ───────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        try:
            ws_url = await self._get_otp_url()
            if not ws_url:
                agent_log("PRELOAD", "Sessão de pré-carga falhou — OTP não obtido", logging.ERROR)
                return False

            self._ws = await websockets.connect(
                ws_url,
                ping_interval = 30,
                ping_timeout  = 20,
                close_timeout = 10,
                max_size      = 2 ** 22,    # 4MB para batches grandes
            )

            self._alive = True
            asyncio.create_task(self._listen(), name="preload_listener")
            await asyncio.sleep(0.3)
            agent_log("PRELOAD", "✅ Sessão de preload conectada")
            return True

        except Exception as e:
            agent_log("PRELOAD", f"Conexão falhou: {type(e).__name__}: {e}", logging.ERROR)
            return False

    # ── Listener ──────────────────────────────────────────────────────────────

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg    = json.loads(raw)
                    req_id = msg.get("req_id")
                    if req_id and req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(msg)
                except Exception:
                    pass
        except (ConnectionClosed, Exception):
            pass
        finally:
            self._alive = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Preload WS fechado"))
            self._pending.clear()

    # ── Request ───────────────────────────────────────────────────────────────

    async def _request(self, payload: Dict, timeout: float = 40.0) -> Optional[Dict]:
        if not self._ws or not self._alive:
            return None

        async with self._lock:
            rid           = self._req_id
            self._req_id += 1

        fut                = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        payload["req_id"]  = rid

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            agent_log("PRELOAD", f"Timeout req={rid}", logging.WARNING)
            return None
        except Exception as e:
            self._pending.pop(rid, None)
            agent_log("PRELOAD", f"Request error: {e}", logging.WARNING)
            return None

    # ── Fetch Candles ─────────────────────────────────────────────────────────

    async def fetch_candle_batch(
        self,
        symbol:      str,
        granularity: int,
        count:       int,
        end_epoch:   Optional[int] = None,
    ) -> Optional[List[Dict]]:

        resp = await self._request({
            "ticks_history":     symbol,
            "style":             "candles",
            "granularity":       granularity,
            "count":             min(count, BATCH_SIZE),
            "end":               str(end_epoch) if end_epoch else "latest",
            "adjust_start_time": 1,
        }, timeout=45.0)

        if not resp:
            return None

        if "error" in resp:
            code = resp["error"].get("code",    "")
            msg  = resp["error"].get("message", "")
            if code in ("NoDataFound", "MarketIsClosed", "InvalidSymbol"):
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
        self._alive = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────

class Preloader:

    def __init__(self):
        self._session: Optional[PreloadSession] = None

    async def run(self, incremental: bool = True) -> Dict:
        agent_log("PRELOAD", "=" * 50)
        agent_log("PRELOAD", "Iniciando pré-carga de histórico...")
        agent_log("PRELOAD", f"Símbolos: {SYMBOLS}")
        agent_log("PRELOAD", f"Granularidades: {PRELOAD_GRANULARITIES}")
        agent_log("PRELOAD", f"Alvo por série: {PRELOAD_TARGET:,} velas")
        agent_log("PRELOAD", "=" * 50)

        self._session = PreloadSession()
        if not await self._session.connect():
            agent_log("PRELOAD", "Erro: Sessão de pré-carga falhou", logging.ERROR)
            return {}

        total_candles = 0
        total_series  = len(SYMBOLS) * len(PRELOAD_GRANULARITIES)
        completed     = 0
        results       = {}

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
                        f"+{count:,} | Total: {total_candles:,} | {pct}%"
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

        agent_log("PRELOAD", "=" * 50)
        agent_log("PRELOAD", f"[OK] Pré-carga concluída: {total_candles:,} velas")
        agent_log("PRELOAD", "=" * 50)

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

        # ── Incremental check ──────────────────────────────────────────
        if incremental:
            existing = await get_candle_count(symbol, granularity)
            remaining = PRELOAD_TARGET - existing
            if remaining <= 0:
                agent_log("PRELOAD", f"{symbol}/{granularity}s: completo ({existing:,}) — skip")
                return 0
            target = remaining
            agent_log("PRELOAD",
                f"{symbol}/{granularity}s: tem {existing:,}, "
                f"baixando {target:,} faltantes..."
            )
        else:
            target = PRELOAD_TARGET

        # ── Fetch backwards ────────────────────────────────────────────
        total_saved = 0
        end_epoch   = None
        retries     = 0
        max_retries = 3

        while total_saved < target:
            batch_size = min(BATCH_SIZE, target - total_saved)

            candles = await self._session.fetch_candle_batch(
                symbol      = symbol,
                granularity = granularity,
                count       = batch_size,
                end_epoch   = end_epoch,
            )

            # Retry
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
                break   # Sem mais dados

            # ── Salva em chunks ────────────────────────────────────────
            for i in range(0, len(candles), SAVE_CHUNK_SIZE):
                chunk = candles[i:i + SAVE_CHUNK_SIZE]
                saved = await save_candles_batch(chunk)
                total_saved += saved

            # ── Caminha para trás ──────────────────────────────────────
            oldest = min(c["epoch"] for c in candles)
            if end_epoch and oldest >= end_epoch:
                break   # Sem progresso

            end_epoch = oldest - 1

            await asyncio.sleep(RATE_LIMIT_DELAY)

            if total_saved % 5000 == 0 and total_saved > 0:
                agent_log("PRELOAD",
                    f"  {symbol}/{granularity}s: {total_saved:,}/{target:,}"
                )

        return total_saved
