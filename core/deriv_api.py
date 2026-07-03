"""
NEXUS QUANTUM ULTRA — Deriv WebSocket Client (New API)
Usa OAuth 2.0 — App registrado em developers.deriv.com
WebSocket: wss://ws.derivws.com/websockets/v3?app_id=<APP_ID>

Diferenças New API vs Legacy:
  - App ID alfanumérico (mas WS URL ainda usa o ID numérico do OAuth app)
  - proposal usa "symbol" (igual Legacy no WS)
  - buy/sell igual
  - balance: sem multi-account
  - ticks_history: adjust_start_time obrigatório
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from core.event_bus import BUS, Events
from utils.config   import DERIV_WS_URL, DERIV_API_TOKEN, DERIV_APP_ID, SYMBOLS
from utils.logger   import agent_log


RECONNECT_DELAYS   = [2, 5, 10, 20, 40, 60]
HEARTBEAT_INTERVAL = 20
REQUEST_TIMEOUT    = 20.0
PROPOSAL_TIMEOUT   = 12.0
BUY_TIMEOUT        = 10.0


class DerivClient:
    """
    Deriv WebSocket Client — New API (OAuth app).

    Auth flow:
      1. Connect to wss://ws.derivws.com/websockets/v3?app_id=<ID>
      2. Send {"authorize": "<API_TOKEN>"}
      3. Token gerado em: app.deriv.com → API tokens
         Scopes: Read + Trade

    New API app criado em:
      developers.deriv.com → Dashboard → Register application
    """

    def __init__(self, executor_agent=None):
        self._ws:            Optional[websockets.WebSocketClientProtocol] = None
        self._running        = False
        self._authorized     = False
        self._req_id         = 1
        self._pending:       Dict[int, asyncio.Future] = {}
        self._executor       = executor_agent
        self._send_lock      = asyncio.Lock()
        self._account_info:  Dict = {}
        self._heartbeat_task: Optional[asyncio.Task] = None

        self._tick_subs:   List[str] = list(SYMBOLS)
        self._candle_subs: List[Dict] = []

    # ══════════════════════════════════════════════════════════════
    #  Connection
    # ══════════════════════════════════════════════════════════════

    async def connect(self) -> bool:
        for attempt, delay in enumerate(RECONNECT_DELAYS):
            try:
                agent_log("DERIV", f"Conectando... tentativa {attempt + 1}")

                self._ws = await websockets.connect(
                    DERIV_WS_URL,
                    ping_interval = 25,
                    ping_timeout  = 15,
                    close_timeout = 10,
                    max_size      = 2 ** 21,
                    extra_headers = {"User-Agent": "NexusQuantumUltra/2.0"},
                )

                asyncio.create_task(self._listen(), name="deriv_listener")

                if not await self._authorize():
                    await self._ws.close()
                    raise ConnectionError("Autorização falhou")

                await self._subscribe_balance()
                await self._resubscribe_ticks()

                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(), name="deriv_heartbeat"
                )

                agent_log("DERIV",
                    f"✅ Conectado | {self._account_info.get('loginid','?')} | "
                    f"{'DEMO' if self._account_info.get('is_virtual') else 'REAL'}"
                )
                await BUS.emit(Events.AGENT_STATUS, {"agent": "DERIV", "status": "running"})
                return True

            except ConnectionError as e:
                agent_log("DERIV", f"❌ {e}", logging.ERROR)
            except Exception as e:
                agent_log("DERIV", f"Falha: {type(e).__name__}: {e}", logging.ERROR)

            if attempt < len(RECONNECT_DELAYS) - 1:
                agent_log("DERIV", f"Aguardando {delay}s...")
                await asyncio.sleep(delay)

        agent_log("DERIV", "Todas tentativas falharam.", logging.CRITICAL)
        return False

    async def _authorize(self) -> bool:
        if not DERIV_API_TOKEN:
            agent_log("DERIV", "DERIV_API_TOKEN ausente no .env", logging.CRITICAL)
            return False

        resp = await self._request({"authorize": DERIV_API_TOKEN}, timeout=15.0)

        if not resp:
            agent_log("DERIV", "Timeout na autorização", logging.ERROR)
            return False

        if "error" in resp:
            code = resp["error"].get("code",    "")
            msg  = resp["error"].get("message", "")
            agent_log("DERIV", f"Auth error [{code}]: {msg}", logging.CRITICAL)

            hints = {
                "InvalidAppID":   "Verifique DERIV_APP_ID no .env",
                "InvalidToken":   "Gere novo token: app.deriv.com → API tokens",
                "RateLimit":      "Aguarde e tente novamente",
                "DisabledClient": "Conta desabilitada",
            }
            if code in hints:
                agent_log("DERIV", f"→ {hints[code]}", logging.CRITICAL)
            return False

        auth = resp.get("authorize", {})
        self._account_info = {
            "loginid":    auth.get("loginid",    "?"),
            "balance":    float(auth.get("balance", 0)),
            "currency":   auth.get("currency",   "USD"),
            "is_virtual": bool(auth.get("is_virtual", 1)),
            "scopes":     auth.get("scopes",     []),
        }

        # Validate required scopes
        scopes = self._account_info["scopes"]
        missing = [s for s in ["read", "trade"] if s not in scopes]
        if missing:
            agent_log("DERIV",
                f"Token sem scopes: {missing}. "
                f"Regenere com: Read + Trade",
                logging.CRITICAL
            )
            return False

        self._authorized = True
        await BUS.emit(Events.BALANCE_UPDATE, {
            "balance":  self._account_info["balance"],
            "currency": self._account_info["currency"],
        })
        return True

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
        except (ConnectionClosed, ConnectionClosedOK, ConnectionClosedError) as e:
            agent_log("DERIV", f"WS fechado: {e}", logging.WARNING)
        except Exception as e:
            agent_log("DERIV", f"Listener error: {e}", logging.ERROR)
        finally:
            self._authorized = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("WS desconectado"))
            self._pending.clear()
            if self._running:
                await asyncio.sleep(3)
                asyncio.create_task(self.connect(), name="deriv_reconnect")

    async def _heartbeat_loop(self) -> None:
        while self._running and self._authorized:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                resp = await self._request({"ping": 1}, timeout=8.0)
                if not resp or resp.get("ping") != "pong":
                    agent_log("DERIV", "Heartbeat falhou", logging.WARNING)
                    if self._ws:
                        await self._ws.close()
                    break
            except Exception:
                break

    # ══════════════════════════════════════════════════════════════
    #  Dispatcher
    # ══════════════════════════════════════════════════════════════

    async def _dispatch(self, msg: Dict) -> None:
        msg_type = msg.get("msg_type", "")
        req_id   = msg.get("req_id")

        # Resolve pending future
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
            err  = msg["error"].get("message", "unknown")
            agent_log("DERIV", f"Buy stream error [{code}]: {err}", logging.ERROR)
            await BUS.emit(Events.TRADE_ERROR, {"reason": err, "code": code})

        elif "error" in msg:
            code = msg["error"].get("code",    "")
            err  = msg["error"].get("message", "")
            if code not in ("MarketIsClosed", "TooManyRequests", "AlreadySubscribed"):
                agent_log("DERIV", f"API error [{code}]: {err}", logging.WARNING)

    # ══════════════════════════════════════════════════════════════
    #  Request
    # ══════════════════════════════════════════════════════════════

    async def _request(
        self,
        payload: Dict,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Optional[Dict]:
        if not self._ws:
            return None

        async with self._send_lock:
            rid            = self._req_id
            self._req_id  += 1

        fut                = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        payload["req_id"]  = rid

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            agent_log("DERIV", f"Timeout req={rid} {list(payload.keys())}", logging.WARNING)
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
            resp = await self._request({"ticks": symbol, "subscribe": 1}, timeout=10.0)
            if resp and "error" in resp:
                agent_log("DERIV",
                    f"Tick sub error {symbol}: {resp['error'].get('message')}",
                    logging.WARNING
                )
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
            "adjust_start_time": 1,      # ← obrigatório New API
        }, timeout=12.0)
        ok = resp is not None and "error" not in resp
        if ok:
            entry = {"symbol": symbol, "granularity": granularity}
            if entry not in self._candle_subs:
                self._candle_subs.append(entry)
        return ok

    # ══════════════════════════════════════════════════════════════
    #  Data Fetching
    # ══════════════════════════════════════════════════════════════

    async def fetch_candles(
        self,
        symbol:      str,
        granularity: int,
        count:       int = 500,
        end:         Optional[int] = None,
    ) -> Optional[List[Dict]]:
        payload = {
            "ticks_history":     symbol,
            "style":             "candles",
            "granularity":       granularity,
            "count":             min(count, 5000),
            "end":               str(end) if end else "latest",
            "adjust_start_time": 1,          # ← obrigatório New API
        }
        resp = await self._request(payload, timeout=30.0)
        if not resp or "error" in resp:
            err = resp.get("error", {}).get("message", "timeout") if resp else "timeout"
            agent_log("DERIV", f"fetch_candles {symbol}: {err}", logging.WARNING)
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
    #  Trading — New API
    # ══════════════════════════════════════════════════════════════

    async def proposal(
        self,
        symbol:        str,
        contract_type: str,
        stake:         float,
        duration:      int   = 5,
        duration_unit: str   = "t",
        currency:      str   = "USD",
        basis:         str   = "stake",
    ) -> Optional[Dict]:
        """
        New API WebSocket usa "symbol" igual à Legacy.
        "underlying_symbol" existe apenas na REST API nova.
        """
        return await self._request({
            "proposal":      1,
            "amount":        round(float(stake), 2),
            "basis":         basis,
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
            code = resp["error"].get("code",    "")
            msg  = resp["error"].get("message", "")
            agent_log("DERIV", f"Buy rejeitado [{code}]: {msg}", logging.ERROR)
            return resp
        if "buy" not in resp:
            agent_log("DERIV", "Resposta sem objeto 'buy'", logging.ERROR)
            return None

        b = resp["buy"]
        agent_log("DERIV",
            f"✅ Buy OK | contract={b.get('contract_id')} | "
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
        """Proposal + Buy em uma chamada."""
        prop = await self.proposal(
            symbol=symbol, contract_type=contract_type,
            stake=stake, duration=duration, duration_unit=duration_unit,
        )
        if not prop or "error" in prop:
            err = prop.get("error", {}).get("message", "timeout") if prop else "timeout"
            agent_log("DERIV", f"Proposal falhou: {err}", logging.ERROR)
            return None

        p = prop.get("proposal", {})
        return await self.buy(p.get("id"), float(p.get("ask_price", stake)))

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
        return self._ws is not None and self._authorized

    def get_account_info(self) -> Dict:
        return dict(self._account_info)

    async def run(self) -> None:
        self._running = True
        agent_log("DERIV", "DerivClient (New API) iniciando...")
        if not await self.connect():
            agent_log("DERIV",
                "FALHA CRÍTICA.\n"
                "Verifique:\n"
                "  1. DERIV_APP_ID = ID numérico do app registrado\n"
                "  2. DERIV_API_TOKEN = token com scopes Read + Trade\n"
                "  3. Conexão com internet",
                logging.CRITICAL
            )
            await BUS.emit(Events.SYSTEM_STOP, {"reason": "deriv_connection_failed"})
            return
        while self._running:
            await asyncio.sleep(1)

    def stop(self) -> None:
        self._running    = False
        self._authorized = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws:
            asyncio.create_task(self._ws.close())
