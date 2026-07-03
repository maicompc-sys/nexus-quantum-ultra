"""
NEXUS QUANTUM ULTRA — Deriv Client (New API 2026)
Fluxo: REST OTP -> WebSocket autenticado
Docs: developers.deriv.com/docs/intro/api-overview
"""

import asyncio
import json
import logging
import time
import aiohttp
import websockets
from typing import Dict, List, Optional
from websockets.exceptions import ConnectionClosed

from core.event_bus import BUS, Events
from utils.config   import (
    DERIV_APP_ID, DERIV_API_TOKEN,
    DERIV_ACCOUNT_ID, DERIV_ACCOUNT_TYPE, SYMBOLS
)
from utils.logger   import agent_log


# ── Endpoints ──────────────────────────────────────────────────────────────
REST_BASE    = "https://api.derivws.com"
WS_PUBLIC    = "wss://api.derivws.com/trading/v1/options/ws/public"
WS_DEMO_BASE = "wss://api.derivws.com/trading/v1/options/ws/demo"
WS_REAL_BASE = "wss://api.derivws.com/trading/v1/options/ws/real"

RECONNECT_BASE_DELAY  = 2       # segundos iniciais entre tentativas
RECONNECT_MAX_DELAY   = 60      # cap máximo de backoff
HEARTBEAT_INTERVAL    = 20
REQUEST_TIMEOUT       = 20.0
PROPOSAL_TIMEOUT      = 12.0
BUY_TIMEOUT           = 10.0
OTP_REFRESH_MARGIN    = 60      # renova OTP 60s antes de expirar


class DerivClient:
    """
    New Deriv API 2026:
      1. REST POST /otp  -> recebe WS URL com OTP
      2. Conecta na WS URL retornada
      3. Opera normalmente
    """

    def __init__(self, executor_agent=None):
        self._ws:             Optional[websockets.WebSocketClientProtocol] = None
        self._listen_task:    Optional[asyncio.Task] = None   # referência forte
        self._running         = False
        self._connected       = False
        self._req_id          = 1
        self._pending:        Dict[int, asyncio.Future] = {}
        self._executor        = executor_agent
        self._send_lock       = asyncio.Lock()
        self._account_info:   Dict = {}
        self._otp_expires_at  = 0.0
        self._ws_url:         Optional[str] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        self._tick_subs:   List[str]  = list(SYMBOLS)
        self._candle_subs: List[Dict] = []

    # ══════════════════════════════════════════════════════════════
    #  OTP — pega URL WebSocket autenticada via REST
    # ══════════════════════════════════════════════════════════════

    async def _get_otp_url(self, account_type: str = "demo") -> Optional[str]:
        """
        POST /trading/v1/options/accounts/{accountId}/otp
        Retorna URL WS com OTP embutido.
        """
        if not DERIV_ACCOUNT_ID:
            agent_log("DERIV",
                "DERIV_ACCOUNT_ID nao configurado no .env!\n"
                "-> Encontre em: app.deriv.com -> seu loginid\n"
                "   Demo:  VRTC123456  |  Real:  CR123456",
                logging.CRITICAL
            )
            return None

        url     = f"{REST_BASE}/trading/v1/options/accounts/{DERIV_ACCOUNT_ID}/otp"
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
                        body = await resp.json()
                        data = body.get("data", body)

                        ws_url = (
                            data.get("url") or
                            data.get("ws_url") or
                            data.get("websocket_url")
                        )
                        if not ws_url:
                            otp = data.get("otp") or data.get("token")
                            if otp:
                                base   = WS_DEMO_BASE if account_type == "demo" else WS_REAL_BASE
                                ws_url = f"{base}?otp={otp}"

                        if ws_url:
                            raw_expires = body.get("data", {}).get("expires_at", 0)
                            if raw_expires and raw_expires > time.time():
                                self._otp_expires_at = float(raw_expires)
                            else:
                                self._otp_expires_at = 0.0

                            agent_log("DERIV", f"OTP obtido | url={ws_url[:60]}...")
                            return ws_url

                        agent_log("DERIV", f"OTP response sem URL: {body}", logging.ERROR)
                        return None

                    elif resp.status == 401:
                        body = await resp.text()
                        agent_log("DERIV",
                            "401 Unauthorized — verifique DERIV_APP_ID e DERIV_API_TOKEN no .env",
                            logging.CRITICAL
                        )
                        return None

                    elif resp.status == 404:
                        agent_log("DERIV",
                            f"404 — Account ID nao encontrado. Verifique DERIV_ACCOUNT_ID no .env",
                            logging.CRITICAL
                        )
                        return None

                    else:
                        body = await resp.text()
                        agent_log("DERIV", f"OTP HTTP {resp.status}: {body[:200]}", logging.ERROR)
                        return None

        except aiohttp.ClientError as e:
            agent_log("DERIV", f"OTP request falhou: {e}", logging.ERROR)
            return None
        except Exception as e:
            agent_log("DERIV", f"OTP exception: {e}", logging.ERROR)
            return None

    # ══════════════════════════════════════════════════════════════
    #  Connection
    # ══════════════════════════════════════════════════════════════

    async def connect(self, account_type: str | None = None) -> bool:
        """
        Tenta conectar com backoff exponencial infinito (para 24/7).
        account_type padrão lido do .env via DERIV_ACCOUNT_TYPE.
        """
        if account_type is None:
            account_type = DERIV_ACCOUNT_TYPE

        attempt = 0
        while True:
            delay = min(RECONNECT_BASE_DELAY * (2 ** min(attempt, 5)), RECONNECT_MAX_DELAY)
            try:
                agent_log("DERIV", f"Obtendo OTP... (tentativa {attempt + 1})")

                ws_url = await self._get_otp_url(account_type)
                if not ws_url:
                    raise ConnectionError("Falha ao obter OTP URL")

                agent_log("DERIV", "Conectando ao WebSocket...")
                self._ws = await websockets.connect(
                    ws_url,
                    ping_interval = 25,
                    ping_timeout  = 15,
                    close_timeout = 10,
                    max_size      = 2 ** 21,
                )
                self._ws_url = ws_url

                # Guarda referência forte na task de listener
                if self._listen_task and not self._listen_task.done():
                    self._listen_task.cancel()
                self._listen_task = asyncio.create_task(
                    self._listen(), name="deriv_listener"
                )
                await asyncio.sleep(0.5)

                await self._subscribe_balance()
                await self._resubscribe_ticks()

                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(), name="deriv_heartbeat"
                )

                self._connected = True
                agent_log("DERIV", f"Conectado via New API 2026 | {account_type.upper()}")
                await BUS.emit(Events.AGENT_STATUS, {"agent": "DERIV", "status": "running"})
                return True

            except ConnectionError as e:
                agent_log("DERIV", f"Erro: {e}", logging.ERROR)
            except Exception as e:
                agent_log("DERIV", f"Falha: {type(e).__name__}: {e}", logging.ERROR)

            if not self._running:
                agent_log("DERIV", "Sistema parado — abortando reconexão.")
                return False

            agent_log("DERIV", f"Aguardando {delay}s antes de reconectar...")
            await asyncio.sleep(delay)
            attempt += 1

    # ══════════════════════════════════════════════════════════════
    #  Listener & Heartbeat
    # ══════════════════════════════════════════════════════════════

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    await self._dispatch(json.loads(raw))
                except Exception as e:
                    agent_log("DERIV", f"Dispatch error: {e}", logging.ERROR)
        except ConnectionClosed as e:
            agent_log("DERIV", f"WS fechado: {e}", logging.WARNING)
        except Exception as e:
            agent_log("DERIV", f"Listener error: {e}", logging.ERROR)
        finally:
            self._connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("WS desconectado"))
            self._pending.clear()
            if self._running:
                await asyncio.sleep(3)
                asyncio.create_task(self.connect(), name="deriv_reconnect")

    async def _heartbeat_loop(self) -> None:
        while self._running and self._connected:
            await asyncio.sleep(HEARTBEAT_INTERVAL)

            if self._otp_expires_at > 0:
                remaining = self._otp_expires_at - time.time()
                if remaining < OTP_REFRESH_MARGIN:
                    agent_log("DERIV", "OTP expirando — renovando conexão...")
                    if self._ws:
                        await self._ws.close()
                    return

            try:
                resp = await self._request({"ping": 1}, timeout=8.0)
                if not resp:
                    agent_log("DERIV", "Heartbeat sem resposta", logging.WARNING)
                    if self._ws:
                        await self._ws.close()
                    break
            except Exception:
                break

    # ══════════════════════════════════════════════════════════════
    #  Dispatcher
    # ══════════════════════════════════════════════════════════════

    async def _dispatch(self, msg: Dict) -> None:
        # Prioridade explícita: msg_type primeiro, type como fallback
        msg_type = msg.get("msg_type") or msg.get("type", "")
        req_id   = msg.get("req_id")

        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result(msg)
            return

        if msg_type == "tick":
            t = msg.get("tick", {})
            await BUS.emit(Events.TICK, {
                "symbol":   t.get("symbol"),
                "price":    float(t.get("quote", 0)),
                "epoch":    t.get("epoch"),
                "pip_size": t.get("pip_size"),
            })

        elif msg_type == "ohlc":
            o = msg.get("ohlc", {})
            await BUS.emit(Events.CANDLE, {
                "symbol": o.get("symbol"),
                "gran":   int(o.get("granularity", 60)),
                "open":   float(o.get("open",  0)),
                "high":   float(o.get("high",  0)),
                "low":    float(o.get("low",   0)),
                "close":  float(o.get("close", 0)),
                "epoch":  int(o.get("open_time", 0)),
            })

        elif msg_type == "balance":
            if "error" not in msg:
                b = msg.get("balance", {})
                await BUS.emit(Events.BALANCE_UPDATE, {
                    "balance":  float(b.get("balance",  0)),
                    "currency": b.get("currency", "USD"),
                })

        elif msg_type == "proposal_open_contract":
            poc = msg.get("proposal_open_contract", {})
            if poc.get("is_expired") or poc.get("is_sold"):
                if self._executor:
                    await self._executor.handle_contract_settled({
                        "contract_id": poc.get("contract_id"),
                        "profit":      float(poc.get("profit",     0)),
                        "payout":      float(poc.get("payout",     0)),
                        "exit_tick":   float(poc.get("exit_tick",  0)),
                        "sell_price":  float(poc.get("sell_price", 0)),
                        "buy_price":   float(poc.get("buy_price",  0)),
                        "status":      poc.get("status", ""),
                    })

        elif msg_type == "buy" and "error" in msg:
            code = msg["error"].get("code",    "")
            err  = msg["error"].get("message", "")
            agent_log("DERIV", f"Buy error [{code}]: {err}", logging.ERROR)
            await BUS.emit(Events.TRADE_ERROR, {"reason": err, "code": code})

        elif "error" in msg:
            code = msg["error"].get("code",    "")
            err  = msg["error"].get("message", "")
            if code not in ("MarketIsClosed", "AlreadySubscribed"):
                agent_log("DERIV", f"API error [{code}]: {err}", logging.WARNING)

    # ══════════════════════════════════════════════════════════════
    #  Request — send_lock protege TODO o envio WS
    # ══════════════════════════════════════════════════════════════

    async def _request(self, payload: Dict, timeout: float = REQUEST_TIMEOUT) -> Optional[Dict]:
        if not self._ws:
            return None

        # Lock protege incremento + envio para evitar race condition no WS
        async with self._send_lock:
            rid            = self._req_id
            self._req_id  += 1
            fut            = asyncio.get_running_loop().create_future()
            self._pending[rid] = fut
            payload["req_id"]  = rid
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as e:
                self._pending.pop(rid, None)
                agent_log("DERIV", f"Send error: {e}", logging.ERROR)
                return None

        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            agent_log("DERIV", f"Timeout req={rid}", logging.WARNING)
            return None
        except Exception as e:
            self._pending.pop(rid, None)
            agent_log("DERIV", f"Request error: {e}", logging.ERROR)
            return None

    # ══════════════════════════════════════════════════════════════
    #  Subscriptions
    # ══════════════════════════════════════════════════════════════

    async def _resubscribe_ticks(self) -> None:
        for symbol in self._tick_subs:
            await self._request({"ticks": symbol, "subscribe": 1}, timeout=10.0)
            await asyncio.sleep(0.15)

    async def _subscribe_balance(self) -> None:
        await self._request({"balance": 1, "subscribe": 1}, timeout=10.0)

    async def subscribe_candles(self, symbol: str, granularity: int) -> bool:
        resp = await self._request({
            "ticks_history":     symbol,
            "style":             "candles",
            "granularity":       granularity,
            "subscribe":         1,
            "count":             1,
            "adjust_start_time": 1,
        }, timeout=12.0)
        return resp is not None and "error" not in resp

    # ══════════════════════════════════════════════════════════════
    #  Data
    # ══════════════════════════════════════════════════════════════

    async def fetch_candles(
        self,
        symbol:      str,
        granularity: int,
        count:       int = 500,
        end:         Optional[int] = None,
    ) -> Optional[List[Dict]]:
        resp = await self._request({
            "ticks_history":     symbol,
            "style":             "candles",
            "granularity":       granularity,
            "count":             min(count, 5000),
            "end":               str(end) if end else "latest",
            "adjust_start_time": 1,
        }, timeout=30.0)
        if not resp or "error" in resp:
            return None
        return [
            {
                "epoch": int(c["epoch"]),
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"]),
            }
            for c in resp.get("candles", [])
        ]

    async def get_balance(self) -> Optional[float]:
        resp = await self._request({"balance": 1}, timeout=10.0)
        if resp and "balance" in resp and "error" not in resp:
            return float(resp["balance"].get("balance", 0))
        return None

    async def get_active_symbols(self) -> Optional[List[Dict]]:
        resp = await self._request({
            "active_symbols": "brief",
            "product_type":   "basic",
        }, timeout=15.0)
        if not resp or "error" in resp:
            return None
        return [
            s for s in resp.get("active_symbols", [])
            if s.get("market") == "synthetic_index"
        ]

    # ══════════════════════════════════════════════════════════════
    #  Trading
    # ══════════════════════════════════════════════════════════════

    async def proposal(
        self,
        symbol:        str,
        contract_type: str,
        stake:         float,
        duration:      int   = 5,
        duration_unit: str   = "t",
        currency:      str   = "USD",
    ) -> Optional[Dict]:
        return await self._request({
            "proposal":      1,
            "amount":        round(float(stake), 2),
            "basis":         "stake",
            "contract_type": contract_type,
            "currency":      currency,
            "duration":      duration,
            "duration_unit": duration_unit,
            "symbol":        symbol,
        }, timeout=PROPOSAL_TIMEOUT)

    async def buy(self, proposal_id: str, price: float) -> Optional[Dict]:
        resp = await self._request({
            "buy":   proposal_id,
            "price": round(float(price), 2),
        }, timeout=BUY_TIMEOUT)
        if not resp:
            return None
        if "error" in resp:
            agent_log("DERIV",
                f"Buy rejeitado [{resp['error'].get('code')}]: "
                f"{resp['error'].get('message')}",
                logging.ERROR
            )
            return resp
        if "buy" not in resp:
            return None
        b = resp["buy"]
        agent_log("DERIV",
            f"Buy OK | contract={b.get('contract_id')} | "
            f"price={b.get('buy_price')} | payout={b.get('payout')}"
        )
        return resp

    async def buy_contract(
        self,
        symbol:        str,
        contract_type: str,
        stake:         float,
        duration:      int = 5,
        duration_unit: str = "t",
    ) -> Optional[Dict]:
        prop = await self.proposal(
            symbol=symbol, contract_type=contract_type,
            stake=stake, duration=duration, duration_unit=duration_unit,
        )
        if not prop or "error" in prop:
            err = prop.get("error", {}).get("message", "timeout") if prop else "timeout"
            agent_log("DERIV", f"Proposal falhou: {err}", logging.ERROR)
            return None

        proposal_id = prop.get("proposal", {}).get("id")
        if not proposal_id:
            agent_log("DERIV", "Proposal retornou sem 'id' — abortando buy", logging.ERROR)
            return None

        ask_price = float(prop.get("proposal", {}).get("ask_price", stake))
        return await self.buy(proposal_id, ask_price)

    async def sell_contract(self, contract_id: int, price: float = 0) -> Optional[Dict]:
        return await self._request({"sell": contract_id, "price": price}, timeout=10.0)

    async def subscribe_contract(self, contract_id: int) -> Optional[Dict]:
        return await self._request({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe":   1,
        }, timeout=10.0)

    # ══════════════════════════════════════════════════════════════
    #  Lifecycle
    # ══════════════════════════════════════════════════════════════

    def is_connected(self) -> bool:
        return self._ws is not None and self._connected

    def get_account_info(self) -> Dict:
        return dict(self._account_info)

    async def run(self) -> None:
        self._running = True
        agent_log("DERIV", "DerivClient New API 2026 iniciando...")
        if not await self.connect():
            agent_log("DERIV",
                "FALHA CRITICA na conexão Deriv. "
                "Verifique DERIV_APP_ID, DERIV_API_TOKEN e DERIV_ACCOUNT_ID no .env",
                logging.CRITICAL
            )
            await BUS.emit(Events.SYSTEM_STOP, {"reason": "deriv_connection_failed"})
            return
        while self._running:
            await asyncio.sleep(1)

    def stop(self) -> None:
        self._running   = False
        self._connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            asyncio.create_task(self._ws.close())
