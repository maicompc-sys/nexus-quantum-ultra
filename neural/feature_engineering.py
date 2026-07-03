"""
NEXUS QUANTUM ULTRA — Feature Engineering
Converts raw candles into normalized feature tensors for the LSTM.
"""

import numpy as np
from typing import List, Dict, Optional
from utils.indicators import rsi, ema, macd, atr, bollinger_bands, stochastic
from utils.config import NN_LOOKBACK


FEATURE_NAMES = [
    "close_norm", "high_norm", "low_norm", "volume_norm",
    "rsi_norm", "macd_norm", "macd_hist_norm",
    "bb_position", "atr_norm",
    "stoch_k_norm", "ema_cross", "candle_body",
]
INPUT_SIZE = len(FEATURE_NAMES)


def _normalize(arr: np.ndarray) -> np.ndarray:
    mn, mx = arr.min(), arr.max()
    if mx == mn:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def candles_to_features(candles: List[Dict]) -> Optional[np.ndarray]:
    """
    Converts a list of candle dicts into a (seq_len, INPUT_SIZE) array.
    Returns None if insufficient data.
    """
    if len(candles) < NN_LOOKBACK + 30:
        return None

    closes  = np.array([c["close"]  for c in candles], dtype=float)
    highs   = np.array([c["high"]   for c in candles], dtype=float)
    lows    = np.array([c["low"]    for c in candles], dtype=float)
    volumes = np.array([c.get("volume", 0.0) for c in candles], dtype=float)

    _rsi    = rsi(closes, 14)
    _macd   = macd(closes)
    _bb     = bollinger_bands(closes)
    _atr    = atr(highs, lows, closes)
    _stoch  = stochastic(highs, lows, closes)
    _ema9   = ema(closes, 9)
    _ema21  = ema(closes, 21)

    n = len(closes)
    features = np.zeros((n, INPUT_SIZE), dtype=np.float32)

    # Normalize price features relative to rolling window
    features[:, 0] = _normalize(closes)
    features[:, 1] = _normalize(highs)
    features[:, 2] = _normalize(lows)
    features[:, 3] = _normalize(volumes)

    # RSI → 0..1
    features[:, 4] = np.nan_to_num(_rsi / 100.0)

    # MACD
    features[:, 5] = _normalize(np.nan_to_num(_macd["macd"]))
    features[:, 6] = _normalize(np.nan_to_num(_macd["histogram"]))

    # Bollinger position (0=at lower, 0.5=at mid, 1=at upper)
    bb_range = _bb["upper"] - _bb["lower"]
    bb_pos   = np.where(
        bb_range > 0,
        (closes - _bb["lower"]) / bb_range,
        0.5
    )
    features[:, 7] = np.clip(bb_pos, 0, 1)

    # ATR normalized
    features[:, 8] = _normalize(np.nan_to_num(_atr))

    # Stochastic K
    features[:, 9] = np.nan_to_num(_stoch["k"] / 100.0)

    # EMA cross: 1=bullish, 0=bearish
    features[:, 10] = (_ema9 > _ema21).astype(float)

    # Candle body size normalized
    body = np.abs(closes - np.array([c["open"] for c in candles], dtype=float))
    features[:, 11] = _normalize(body)

    return features


def make_sequences(
    features: np.ndarray,
    labels:   np.ndarray,
    lookback: int = NN_LOOKBACK,
) -> tuple:
    """Create (X, y) sequences for LSTM training."""
    X, y = [], []
    for i in range(lookback, len(features)):
        X.append(features[i - lookback:i])
        y.append(labels[i])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int64)


def label_candles(candles: List[Dict], future_bars: int = 3) -> np.ndarray:
    """
    Generate labels based on future price movement.
    WIN threshold: 0.02% move (adjusted for synthetic indices).
    """
    closes = np.array([c["close"] for c in candles], dtype=float)
    labels = np.zeros(len(closes), dtype=int)   # 0=HOLD

    for i in range(len(closes) - future_bars):
        future_return = (closes[i + future_bars] - closes[i]) / closes[i]
        if future_return > 0.0002:
            labels[i] = 1   # CALL
        elif future_return < -0.0002:
            labels[i] = 2   # PUT

    return labels
