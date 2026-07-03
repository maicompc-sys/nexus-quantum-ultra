"""
NEXUS QUANTUM ULTRA — Technical Indicators
Pure NumPy implementations — no external TA lib dependency at runtime.

NOTA: bollinger_bands() retorna TUPLA (upper, mid, lower) para compatibilidade
com quant_agent.py. compute_all() usa dict internamente.
"""

import numpy as np
from typing import List, Dict, Tuple, Optional


def _ensure_float64(arr: np.ndarray) -> np.ndarray:
    """Garante dtype float64 para evitar erros de ufunc com object arrays."""
    if arr.dtype != np.float64:
        return arr.astype(np.float64)
    return arr


def ema(prices: np.ndarray, period: int) -> np.ndarray:
    prices = _ensure_float64(prices)
    result = np.zeros(len(prices), dtype=np.float64)
    k = 2.0 / (period + 1)
    result[0] = prices[0]
    for i in range(1, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
    return result


def sma(prices: np.ndarray, period: int) -> np.ndarray:
    prices = _ensure_float64(prices)
    result = np.full(len(prices), np.nan, dtype=np.float64)
    for i in range(period - 1, len(prices)):
        result[i] = np.mean(prices[i - period + 1:i + 1])
    return result


def rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    prices = _ensure_float64(prices)
    deltas = np.diff(prices)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.zeros(len(prices), dtype=np.float64)
    avg_loss = np.zeros(len(prices), dtype=np.float64)

    if len(gains) >= period:
        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])

    for i in range(period + 1, len(prices)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

    with np.errstate(divide='ignore', invalid='ignore'):
        rs = np.where(avg_loss == 0, 100.0, avg_gain / avg_loss)
    result = 100.0 - (100.0 / (1.0 + rs))
    result[:period] = np.nan
    return result


def macd(
    prices: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Dict[str, np.ndarray]:
    prices      = _ensure_float64(prices)
    ema_fast    = ema(prices, fast)
    ema_slow    = ema(prices, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def bollinger_bands(
    prices: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Retorna TUPLA (upper, mid, lower) para uso direto no quant_agent:
        bb_upper, bb_mid, bb_lower = bollinger_bands(closes, 20, 2.0)
    """
    prices = _ensure_float64(prices)
    mid    = sma(prices, period)
    std    = np.array([
        np.std(prices[max(0, i - period + 1):i + 1])
        for i in range(len(prices))
    ], dtype=np.float64)
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    highs  = _ensure_float64(highs)
    lows   = _ensure_float64(lows)
    closes = _ensure_float64(closes)
    tr = np.zeros(len(closes), dtype=np.float64)
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    result = np.zeros(len(tr), dtype=np.float64)
    if len(tr) > period:
        result[period] = np.mean(tr[1:period + 1])
        for i in range(period + 1, len(tr)):
            result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def stochastic(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    k_period: int = 14,
    d_period: int = 3,
) -> Dict[str, np.ndarray]:
    highs  = _ensure_float64(highs)
    lows   = _ensure_float64(lows)
    closes = _ensure_float64(closes)
    k = np.zeros(len(closes), dtype=np.float64)
    for i in range(k_period - 1, len(closes)):
        lo = np.min(lows[i - k_period + 1:i + 1])
        hi = np.max(highs[i - k_period + 1:i + 1])
        k[i] = ((closes[i] - lo) / (hi - lo) * 100.0) if hi != lo else 50.0
    d = sma(k, d_period)
    return {"k": k, "d": d}


def williams_r(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    highs  = _ensure_float64(highs)
    lows   = _ensure_float64(lows)
    closes = _ensure_float64(closes)
    result = np.zeros(len(closes), dtype=np.float64)
    for i in range(period - 1, len(closes)):
        hi = np.max(highs[i - period + 1:i + 1])
        lo = np.min(lows[i - period + 1:i + 1])
        result[i] = ((hi - closes[i]) / (hi - lo) * -100.0) if hi != lo else -50.0
    return result


def detect_trend(closes: np.ndarray, period: int = 20) -> str:
    closes = _ensure_float64(closes)
    if len(closes) < period:
        return "neutral"
    recent = closes[-period:]
    slope  = np.polyfit(range(period), recent, 1)[0]
    if slope > 0.0001:
        return "bullish"
    elif slope < -0.0001:
        return "bearish"
    return "neutral"


def detect_regime(closes: np.ndarray, period: int = 50) -> str:
    """trending | ranging | volatile"""
    closes = _ensure_float64(closes)
    if len(closes) < period:
        return "unknown"
    c    = closes[-period:]
    std  = np.std(c)
    mean = np.mean(c)
    cv   = float(std / mean) if mean != 0 else 0.0

    ema_s = ema(c, 10)
    ema_l = ema(c, 30)
    diff  = abs(float(ema_s[-1]) - float(ema_l[-1])) / float(mean) if mean != 0 else 0.0

    if cv > 0.02:
        return "volatile"
    if diff > 0.005:
        return "trending"
    return "ranging"


def compute_all(candles: List[Dict]) -> Dict:
    """Compute full indicator suite from candle list."""
    if len(candles) < 30:
        return {}

    closes = np.array([c["close"] for c in candles], dtype=np.float64)
    highs  = np.array([c["high"]  for c in candles], dtype=np.float64)
    lows   = np.array([c["low"]   for c in candles], dtype=np.float64)

    _rsi          = rsi(closes)
    _macd         = macd(closes)
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes)
    _atr          = atr(highs, lows, closes)
    _stoch        = stochastic(highs, lows, closes)
    _ema9         = ema(closes, 9)
    _ema21        = ema(closes, 21)
    _ema50        = ema(closes, 50)

    last = -1
    return {
        "rsi":          round(float(_rsi[last]),   2),
        "macd":         round(float(_macd["macd"][last]),      5),
        "macd_signal":  round(float(_macd["signal"][last]),    5),
        "macd_hist":    round(float(_macd["histogram"][last]), 5),
        "bb_upper":     round(float(bb_upper[last]), 5),
        "bb_mid":       round(float(bb_mid[last]),   5),
        "bb_lower":     round(float(bb_lower[last]), 5),
        "bb_width":     round(float(bb_upper[last] - bb_lower[last]), 5),
        "atr":          round(float(_atr[last]),    5),
        "stoch_k":      round(float(_stoch["k"][last]), 2),
        "stoch_d":      round(float(_stoch["d"][last]), 2),
        "ema_9":        round(float(_ema9[last]),   5),
        "ema_21":       round(float(_ema21[last]),  5),
        "ema_50":       round(float(_ema50[last]),  5),
        "ema_cross":    "bullish" if _ema9[last] > _ema21[last] else "bearish",
        "close":        round(float(closes[last]),  5),
        "trend":        detect_trend(closes),
        "regime":       detect_regime(closes),
        "price_vs_bb":  (
            "above" if closes[last] > bb_upper[last] else
            "below" if closes[last] < bb_lower[last] else "inside"
        ),
    }
