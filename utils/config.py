import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Deriv New API 2026 ──────────────────────────────────────────────────────
DERIV_APP_ID     = os.getenv("DERIV_APP_ID",     "")
DERIV_API_TOKEN  = os.getenv("DERIV_API_TOKEN",  "")
DERIV_ACCOUNT_ID = os.getenv("DERIV_ACCOUNT_ID", "")   # loginid: VRTCXXXXXX ou CRXXXXXX

# URL WS construida dinamicamente (compatibilidade com preloader Legacy)
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

# ── Símbolos e granularidades ───────────────────────────────────────────────
SYMBOLS = [
    "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V",
]

PRELOAD_GRANULARITIES = [60, 300, 900]      # 1m, 5m, 15m
PRELOAD_TARGET        = 5000                # velas por série

# ── Groq ────────────────────────────────────────────────────────────────────
GROQ_KEYS = [
    k for k in [
        os.getenv("GROQ_KEY_1", ""),
        os.getenv("GROQ_KEY_2", ""),
        os.getenv("GROQ_KEY_3", ""),
    ] if k
]

# Modelos por agente do conclave (round-robin de chaves)
GROQ_MODEL_A = os.getenv("GROQ_MODEL_A", "llama-3.3-70b-versatile")   # Estrategista
GROQ_MODEL_B = os.getenv("GROQ_MODEL_B", "mixtral-8x7b-32768")        # Analista
GROQ_MODEL_C = os.getenv("GROQ_MODEL_C", "llama-3.1-8b-instant")      # Executor rapido

# ── Risco ───────────────────────────────────────────────────────────────────
MIN_STAKE        = float(os.getenv("MIN_STAKE",   "0.35"))
MAX_STAKE        = float(os.getenv("MAX_STAKE",   "50.0"))
STOP_LOSS        = float(os.getenv("STOP_LOSS",   "20.0"))
TAKE_PROFIT      = float(os.getenv("TAKE_PROFIT", "50.0"))
MIN_CONFIDENCE   = float(os.getenv("MIN_CONFIDENCE", "0.62"))
MARTINGALE_MULT  = float(os.getenv("MARTINGALE_MULT",  "2.1"))
MARTINGALE_SAFE  = float(os.getenv("MARTINGALE_SAFE",  "1.5"))
MAX_MARTINGALE_LVL = int(os.getenv("MAX_MARTINGALE_LVL", "4"))

# ── Neural ──────────────────────────────────────────────────────────────────
NN_LOOKBACK      = int(os.getenv("NN_LOOKBACK",    "50"))
NN_HIDDEN_SIZE   = int(os.getenv("NN_HIDDEN_SIZE", "128"))
NN_RETRAIN_EVERY = int(os.getenv("NN_RETRAIN_EVERY", "50"))

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
MODELS_DIR    = BASE_DIR / "models"
MEMORY_FILE   = BASE_DIR / "data" / "memory.json"
DATABASE_URL  = f"sqlite+aiosqlite:///{BASE_DIR}/data/nexus.db"
DB_URL        = DATABASE_URL   # alias usado por database/repository.py
MODELS_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

# ── Misc ────────────────────────────────────────────────────────────────────
ANALYSIS_INTERVAL = int(os.getenv("ANALYSIS_INTERVAL", "30"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")
