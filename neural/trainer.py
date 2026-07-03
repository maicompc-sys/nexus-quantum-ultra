"""
NEXUS QUANTUM ULTRA — Neural Trainer
Continuous retraining loop using live trade history and candle data.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import train_test_split

from neural.model import NexusEnsemble, INPUT_SIZE, LABEL_INDEX
from neural.feature_engineering import candles_to_features, label_candles, make_sequences
from core.event_bus import BUS, Events
from database.repository import get_candles, save_neural_snapshot, get_latest_neural_snapshot
from utils.logger import agent_log
from utils.config import (
    SYMBOLS, MODELS_DIR, NN_LOOKBACK, NN_RETRAIN_EVERY
)


class NeuralTrainer:
    NAME    = "NEURAL"
    DEVICE  = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def __init__(self, adaptive_agent=None):
        self._running    = False
        self._adaptive   = adaptive_agent
        self._model: Optional[NexusEnsemble] = None
        self._version    = 0
        self._trade_count = 0

        BUS.subscribe(Events.NN_RETRAIN,  self._on_retrain_request)
        BUS.subscribe(Events.TRADE_CLOSE, self._on_trade)

    async def _on_trade(self, _event: str, _data) -> None:
        self._trade_count += 1
        if self._trade_count % NN_RETRAIN_EVERY == 0:
            await BUS.emit(Events.NN_RETRAIN, {"reason": "scheduled"})

    async def _on_retrain_request(self, _event: str, data: dict) -> None:
        agent_log(self.NAME, f"Retrain solicitado: {data.get('reason', '?')}")
        asyncio.create_task(self._train())

    def _get_lr(self) -> float:
        if self._adaptive:
            return self._adaptive.get_learning_rate()
        return 0.001

    async def _build_dataset(self) -> Optional[tuple]:
        all_X, all_y = [], []

        for symbol in SYMBOLS:
            candles = await get_candles(symbol, 60, limit=5000)
            if len(candles) < NN_LOOKBACK + 50:
                continue

            features = candles_to_features(candles)
            if features is None:
                continue

            labels = label_candles(candles)
            X, y   = make_sequences(features, labels, NN_LOOKBACK)

            all_X.append(X)
            all_y.append(y)

        if not all_X:
            return None

        X = np.concatenate(all_X, axis=0)
        y = np.concatenate(all_y, axis=0)
        return X, y

    async def _train(self) -> None:
        agent_log(self.NAME, f"Iniciando treino — device={self.DEVICE}")

        dataset = await _run_in_executor(self._build_dataset)
        if dataset is None:
            agent_log(self.NAME, "Dataset vazio — treino abortado", logging.WARNING)
            return

        X, y = await dataset
        if len(X) < 100:
            agent_log(self.NAME, f"Amostras insuficientes: {len(X)}", logging.WARNING)
            return

        X_tr, X_val, y_tr, y_val = train_test_split(X, y, test_size=0.2, shuffle=True)

        tr_ds  = TensorDataset(torch.from_numpy(X_tr), torch.from_numpy(y_tr))
        val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))
        tr_dl  = DataLoader(tr_ds,  batch_size=64, shuffle=True)
        val_dl = DataLoader(val_ds, batch_size=64)

        model = NexusEnsemble(input_size=INPUT_SIZE).to(self.DEVICE)
        optimizer = torch.optim.AdamW(model.parameters(), lr=self._get_lr(), weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=20)
        criterion = nn.CrossEntropyLoss()

        best_val_acc = 0.0
        patience     = 5
        no_improve   = 0

        for epoch in range(50):
            # ── Train ──────────────────────────────────────────────────────
            model.train()
            for xb, yb in tr_dl:
                xb, yb = xb.to(self.DEVICE), yb.to(self.DEVICE)
                optimizer.zero_grad()
                loss = criterion(model(xb), yb)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            scheduler.step()

            # ── Validate ───────────────────────────────────────────────────
            model.eval()
            correct = total = 0
            val_loss = 0.0
            with torch.no_grad():
                for xb, yb in val_dl:
                    xb, yb = xb.to(self.DEVICE), yb.to(self.DEVICE)
                    out     = model(xb)
                    val_loss += criterion(out, yb).item()
                    preds    = out.argmax(dim=1)
                    correct += (preds == yb).sum().item()
                    total   += len(yb)

            val_acc = correct / total if total > 0 else 0.0

            if (epoch + 1) % 10 == 0:
                agent_log(self.NAME, f"Epoch {epoch+1}/50 — val_acc={val_acc:.3f} loss={val_loss:.4f}")

            # Early stopping
            if val_acc > best_val_acc:
                best_val_acc = val_acc
                no_improve   = 0
                self._save_model(model)
            else:
                no_improve += 1
                if no_improve >= patience:
                    agent_log(self.NAME, f"Early stop na epoch {epoch+1}")
                    break

        self._model   = model
        self._version += 1

        # Save snapshot to DB
        snap_path = str(MODELS_DIR / f"nexus_v{self._version}.pt")
        await save_neural_snapshot({
            "version":        self._version,
            "accuracy":       best_val_acc,
            "val_accuracy":   best_val_acc,
            "trades_trained": self._trade_count,
            "model_path":     snap_path,
            "hyperparams":    {"lr": self._get_lr(), "lookback": NN_LOOKBACK},
        })

        agent_log(self.NAME, f"✅ Treino concluído: v{self._version} acc={best_val_acc:.3f}")
        await BUS.emit(Events.NN_DONE, {"accuracy": best_val_acc, "version": self._version})

    def _save_model(self, model: NexusEnsemble) -> None:
        path = MODELS_DIR / f"nexus_best.pt"
        torch.save(model.state_dict(), path)

    def load_best(self) -> bool:
        path = MODELS_DIR / "nexus_best.pt"
        if not path.exists():
            return False
        model = NexusEnsemble(input_size=INPUT_SIZE).to(self.DEVICE)
        model.load_state_dict(torch.load(path, map_location=self.DEVICE))
        self._model = model
        agent_log(self.NAME, f"Modelo carregado: {path}")
        return True

    def predict(self, candles: list) -> dict:
        """Synchronous prediction — call from async context via executor."""
        if self._model is None:
            return {"signal": "HOLD", "confidence": 0.0, "probas": []}

        features = candles_to_features(candles)
        if features is None or len(features) < NN_LOOKBACK:
            return {"signal": "HOLD", "confidence": 0.0, "probas": []}

        seq   = features[-NN_LOOKBACK:]
        x     = torch.from_numpy(seq).unsqueeze(0).to(self.DEVICE)
        proba = self._model.predict_proba(x)[0]

        idx        = int(np.argmax(proba))
        labels_map = {0: "HOLD", 1: "CALL", 2: "PUT"}
        return {
            "signal":     labels_map[idx],
            "confidence": float(proba[idx]),
            "probas":     proba.tolist(),
        }

    async def run(self) -> None:
        self._running = True
        agent_log(self.NAME, f"Neural Trainer iniciado. Device: {self.DEVICE}")
        await BUS.emit(Events.AGENT_STATUS, {"agent": self.NAME, "status": "running"})

        # Try loading existing model
        loaded = self.load_best()
        if not loaded:
            agent_log(self.NAME, "Nenhum modelo salvo — iniciando primeiro treino...")
            await BUS.emit(Events.NN_RETRAIN, {"reason": "initial"})

        while self._running:
            await asyncio.sleep(60)

    def stop(self):
        self._running = False


async def _run_in_executor(coro):
    """Helper to await a coroutine (dataset building) safely."""
    return await coro()
