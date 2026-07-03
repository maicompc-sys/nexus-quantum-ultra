"""
Diagnóstico completo da conexão Deriv.
Testa múltiplas combinações de app_id + token e mostra
a resposta RAW do servidor para análise.
"""
import asyncio
import json
import websockets

TOKEN = "pat_3191c5d022b3ed2da2beab09a941f303dcc4c80056290836ef162aaa928ddce2"

APP_IDS = [1089, 16303, 36544]

async def test_one(app_id: int, token: str):
    url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
    print(f"\n─── App ID: {app_id} ───────────────────────────")
    print(f"URL: {url}")
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            print("✅ WebSocket conectado")
            await ws.send(json.dumps({"authorize": token, "req_id": 1}))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(raw)
            if "error" in resp:
                print(f"❌ Auth error: [{resp['error'].get('code')}] {resp['error'].get('message')}")
            elif "authorize" in resp:
                a = resp["authorize"]
                print(f"✅ Auth OK!")
                print(f"   loginid:    {a.get('loginid')}")
                print(f"   balance:    {a.get('balance')} {a.get('currency')}")
                print(f"   is_virtual: {a.get('is_virtual')}")
                print(f"   scopes:     {a.get('scopes')}")
            else:
                print(f"? Resposta inesperada: {raw[:300]}")
    except Exception as e:
        print(f"❌ Conexão falhou: {type(e).__name__}: {e}")


async def main():
    print("=" * 55)
    print("NEXUS QUANTUM — Diagnóstico de Credenciais Deriv")
    print("=" * 55)
    print(f"Token: {TOKEN[:10]}...{TOKEN[-6:]}")

    for app_id in APP_IDS:
        await test_one(app_id, TOKEN)
        await asyncio.sleep(1)

    print("\n" + "=" * 55)
    print("Diagnóstico concluído.")

asyncio.run(main())
