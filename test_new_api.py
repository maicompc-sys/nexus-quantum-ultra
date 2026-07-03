"""
Diagnóstico COMPLETO da New API 2026 — mostra TUDO.
Roda com: python test_new_api.py
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

print("=" * 65)
print("NEXUS QUANTUM — Diagnóstico New API 2026")
print("=" * 65)
print(f"App ID     : {DERIV_APP_ID}")
print(f"Token      : {DERIV_API_TOKEN[:12]}...{DERIV_API_TOKEN[-6:]}")
print(f"Account ID : {DERIV_ACCOUNT_ID}")
print("-" * 65)


async def step1_health():
    """Verifica se a API está no ar."""
    print("\n[STEP 1] Health check...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{REST_BASE}/v1/health",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                body = await r.text()
                print(f"  Status: {r.status}")
                print(f"  Body  : {body[:200]}")
                return r.status == 200
    except Exception as e:
        print(f"  ERRO: {e}")
        return False


async def step2_accounts_list():
    """Tenta listar contas para descobrir o accountId correto."""
    print("\n[STEP 2] GET /trading/v1/options/accounts (lista contas)...")
    headers = {
        "Deriv-App-ID":  DERIV_APP_ID,
        "Authorization": f"Bearer {DERIV_API_TOKEN}",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{REST_BASE}/trading/v1/options/accounts",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                body = await r.text()
                print(f"  Status: {r.status}")
                print(f"  Body  : {body[:500]}")
                if r.status == 200:
                    data = json.loads(body)
                    accounts = data.get("data", data)
                    if isinstance(accounts, list) and accounts:
                        ids = [a.get("account_id") or a.get("id") or a.get("accountId") for a in accounts]
                        print(f"  ✅ Account IDs encontrados: {ids}")
                        return ids
    except Exception as e:
        print(f"  ERRO: {e}")
    return []


async def step3_otp(account_id: str) -> str | None:
    """Solicita OTP para o accountId dado."""
    url = f"{REST_BASE}/trading/v1/options/accounts/{account_id}/otp"
    headers = {
        "Deriv-App-ID":  DERIV_APP_ID,
        "Authorization": f"Bearer {DERIV_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    print(f"\n[STEP 3] POST {url}")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                body = await r.text()
                print(f"  Status : {r.status}")
                print(f"  Body   : {body[:600]}")

                if r.status == 200:
                    data = json.loads(body)
                    inner  = data.get("data", data)
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
                    if ws_url:
                        print(f"  ✅ WS URL: {ws_url[:80]}...")
                        return ws_url
                    print("  ❌ Sem URL no body")
                elif r.status == 401:
                    print("  ❌ 401 — Token ou App ID inválido")
                elif r.status == 404:
                    print("  ❌ 404 — Account ID não encontrado")
    except Exception as e:
        print(f"  ERRO: {e}")
    return None


async def step4_websocket(ws_url: str):
    """Conecta no WS e testa balance + ticks."""
    print(f"\n[STEP 4] Conectando WS...")
    try:
        ws = await websockets.connect(
            ws_url,
            open_timeout=12,
            ping_interval=None,
        )
        print("  ✅ WS conectado!")

        for payload, label in [
            ({"balance": 1, "req_id": 1}, "balance"),
            ({"ticks": "R_50", "req_id": 2}, "tick R_50"),
        ]:
            await ws.send(json.dumps(payload))
            try:
                raw  = await asyncio.wait_for(ws.recv(), timeout=10)
                resp = json.loads(raw)
                if "error" in resp:
                    print(f"  ❌ {label}: [{resp['error'].get('code')}] {resp['error'].get('message')}")
                else:
                    print(f"  ✅ {label}: OK — {json.dumps(resp)[:180]}")
            except asyncio.TimeoutError:
                print(f"  ⏱ {label}: timeout")

        await ws.close()
        print("  WS fechado.")
    except Exception as e:
        print(f"  ❌ WS erro: {type(e).__name__}: {e}")


async def main():
    await step1_health()
    accounts = await step2_accounts_list()

    # Tenta o account_id do .env primeiro, depois os descobertos
    candidates = [DERIV_ACCOUNT_ID]
    for a in accounts:
        if a and a not in candidates:
            candidates.append(a)

    ws_url = None
    for acc in candidates:
        if acc:
            ws_url = await step3_otp(acc)
            if ws_url:
                print(f"  ✅ Account ID que funcionou: {acc}")
                # Atualiza sugestão no .env se diferente
                if acc != DERIV_ACCOUNT_ID:
                    print(f"\n  ⚠️  SEU DERIV_ACCOUNT_ID no .env está ERRADO!")
                    print(f"     Mude para: DERIV_ACCOUNT_ID={acc}")
                break

    if ws_url:
        await step4_websocket(ws_url)
    else:
        print("\n❌ OTP falhou em todos os account IDs tentados.")
        print("   Verifique: DERIV_APP_ID, DERIV_API_TOKEN e DERIV_ACCOUNT_ID no .env")

    print("\n" + "=" * 65)
    print("Diagnóstico concluído.")


asyncio.run(main())
