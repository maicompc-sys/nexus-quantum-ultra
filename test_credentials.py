"""
Teste de conexao Deriv - New API (OAuth App ID alfanumerico)
"""
import asyncio
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

DERIV_APP_ID    = os.getenv("DERIV_APP_ID", "")
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")
WS_URL          = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

print(f"App ID : {DERIV_APP_ID}")
print(f"Token  : {DERIV_API_TOKEN[:12]}...{DERIV_API_TOKEN[-6:]}")
print(f"URL    : {WS_URL}")
print("-" * 60)

async def test_deriv():
    try:
        import websockets
    except ImportError:
        print("ERRO: websockets nao instalado. Rode: pip install websockets")
        return

    try:
        print(f"Conectando...")
        ws = await websockets.connect(WS_URL, open_timeout=10)
        print("WebSocket conectado OK")

        await ws.send(json.dumps({"authorize": DERIV_API_TOKEN, "req_id": 1}))
        raw  = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(raw)

        if "error" in resp:
            code = resp["error"].get("code", "?")
            msg  = resp["error"].get("message", "?")
            print(f"Auth FALHOU: [{code}] {msg}")
        elif "authorize" in resp:
            a = resp["authorize"]
            print(f"Auth OK!")
            print(f"  loginid    : {a.get('loginid')}")
            print(f"  balance    : {a.get('balance')} {a.get('currency')}")
            print(f"  is_virtual : {a.get('is_virtual')}")
            print(f"  scopes     : {a.get('scopes')}")
        else:
            print(f"Resposta inesperada: {raw[:200]}")

        await ws.close()

    except Exception as e:
        print(f"ERRO de conexao: {type(e).__name__}: {e}")


def test_groq():
    print()
    print("-" * 60)
    print("Testando Groq...")
    try:
        from groq import Groq
    except ImportError:
        print("ERRO: groq nao instalado. Rode: pip install groq")
        return

    keys = [
        os.getenv("GROQ_KEY_1", ""),
        os.getenv("GROQ_KEY_2", ""),
        os.getenv("GROQ_KEY_3", ""),
    ]
    for i, key in enumerate(keys, 1):
        if not key:
            print(f"  Key {i}: nao configurada")
            continue
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            print(f"  Key {i}: OK -> {resp.choices[0].message.content}")
        except Exception as e:
            print(f"  Key {i}: FALHOU -> {e}")


async def main():
    await test_deriv()
    test_groq()
    print()
    print("-" * 60)
    print("Diagnostico concluido.")


if __name__ == "__main__":
    # Forcar stdout UTF-8 no Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(main())
