"""
Teste New API 2026 - fluxo completo: REST OTP -> WebSocket
"""
import asyncio
import json
import os
import sys
import aiohttp
import websockets
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

DERIV_APP_ID     = os.getenv("DERIV_APP_ID",     "")
DERIV_API_TOKEN  = os.getenv("DERIV_API_TOKEN",  "")
DERIV_ACCOUNT_ID = os.getenv("DERIV_ACCOUNT_ID", "")

REST_BASE = "https://api.derivws.com"

print("=" * 60)
print("NEXUS QUANTUM - Teste New API 2026")
print("=" * 60)
print(f"App ID     : {DERIV_APP_ID}")
print(f"Token      : {DERIV_API_TOKEN[:12]}...{DERIV_API_TOKEN[-6:]}")
print(f"Account ID : {DERIV_ACCOUNT_ID}")
print("-" * 60)


async def get_otp_url() -> str | None:
    url = f"{REST_BASE}/trading/v1/options/accounts/{DERIV_ACCOUNT_ID}/otp"
    headers = {
        "Deriv-App-ID":  DERIV_APP_ID,
        "Authorization": f"Bearer {DERIV_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    print(f"\n[1] POST {url}")
    print(f"    Headers: Deriv-App-ID={DERIV_APP_ID[:10]}...")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            body = await resp.text()
            print(f"    Status : {resp.status}")
            print(f"    Body   : {body[:300]}")

            if resp.status == 200:
                data = json.loads(body)
                # Response real: {"data": {"url": "wss://..."}, "meta": {...}}
                inner  = data.get("data", data)   # entra no objeto data se existir
                ws_url = (
                    inner.get("url")
                    or inner.get("ws_url")
                    or inner.get("websocket_url")
                    or data.get("url")
                )
                if not ws_url:
                    otp = inner.get("otp") or inner.get("token")
                    if otp:
                        ws_url = f"wss://api.derivws.com/trading/v1/options/ws/demo?otp={otp}"
                return ws_url
            return None


async def test_websocket(ws_url: str):
    print(f"\n[2] Conectando WS: {ws_url[:80]}...")
    try:
        ws = await websockets.connect(
            ws_url,
            additional_headers={"Deriv-App-ID": DERIV_APP_ID},
            open_timeout=10,
        )
        print("    WS conectado!")

        # Tenta buscar balance
        await ws.send(json.dumps({"balance": 1, "req_id": 1}))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(raw)
        print(f"    Resposta: {json.dumps(resp, indent=2)[:400]}")
        await ws.close()
    except Exception as e:
        print(f"    WS erro: {type(e).__name__}: {e}")


async def main():
    try:
        ws_url = await get_otp_url()
    except Exception as e:
        print(f"\nErro REST: {type(e).__name__}: {e}")
        return

    if ws_url:
        await test_websocket(ws_url)
    else:
        print("\nOTP URL nao obtida.")

    print("\n" + "=" * 60)


asyncio.run(main())
