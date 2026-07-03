"""
NEXUS QUANTUM ULTRA — LSTM Neural Network (PyTorch)
Predicts CALL / PUT / HOLD from candle sequences.
"""

import torch          # type: ignore[import]
import torch.nn as nn  # type: ignore[import]
import numpy as np    # type: ignore[import]
from typing import Tuple
from utils.config import NN_HIDDEN_SIZE, NN_LOOKBACK


class NexusLSTM(nn.Module):
    def __init__(
        self,
        input_size:  int = 12,
        hidden_size: int = NN_HIDDEN_SIZE,
        num_layers:  int = 3,
        dropout:     float = 0.25,
        num_classes: int = 3,       # 0=HOLD 1=CALL 2=PUT
    ):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers  = num_layers

        self.lstm = nn.LSTM(
            input_size   = input_size,
            hidden_size  = hidden_size,
            num_layers   = num_layers,
            batch_first  = True,
            dropout      = dropout if num_layers > 1 else 0.0,
            bidirectional= True,
        )

        self.attention = nn.Sequential(
            nn.Linear(hidden_size * 2, hidden_size),
            nn.Tanh(),
            nn.Linear(hidden_size, 1),
            nn.Softmax(dim=1),
        )

        self.classifier = nn.Sequential(
            nn.Linear(hidden_size * 2, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_classes),
        )

        self.batch_norm = nn.BatchNorm1d(hidden_size * 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, input_size)
        lstm_out, _ = self.lstm(x)
        # lstm_out: (batch, seq_len, hidden*2)

        # Attention mechanism
        attn_weights = self.attention(lstm_out)          # (batch, seq_len, 1)
        context      = (attn_weights * lstm_out).sum(1)  # (batch, hidden*2)

        context = self.batch_norm(context)
        return self.classifier(context)                  # (batch, 3)

    def predict_proba(self, x: torch.Tensor) -> np.ndarray:
        self.eval()
        with torch.no_grad():
            logits = self.forward(x)
            probs  = torch.softmax(logits, dim=-1)
            return probs.cpu().numpy()


class NexusEnsemble(nn.Module):
    """
    Ensemble of 3 LSTM models voting on the final signal.
    Increases robustness — reduces overfitting.
    """
    def __init__(self, input_size: int = 12):
        super().__init__()
        self.models = nn.ModuleList([
            NexusLSTM(input_size=input_size, hidden_size=128, num_layers=3),
            NexusLSTM(input_size=input_size, hidden_size=96,  num_layers=2),
            NexusLSTM(input_size=input_size, hidden_size=64,  num_layers=2),
        ])
        self.weights = [0.5, 0.3, 0.2]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        outputs = [
            torch.softmax(m(x), dim=-1) * w
            for m, w in zip(self.models, self.weights)
        ]
        return torch.stack(outputs).sum(dim=0)

    def predict_proba(self, x: torch.Tensor) -> np.ndarray:
        self.eval()
        with torch.no_grad():
            probs = self.forward(x)
            return probs.cpu().numpy()


LABEL_MAP   = {0: "HOLD", 1: "CALL", 2: "PUT"}
LABEL_INDEX = {"HOLD": 0, "CALL": 1, "PUT": 2}

# Constantes de arquitetura exportadas
INPUT_SIZE  = 12   # numero de features por timestep (indicadores tecnicos)
NUM_CLASSES = 3    # HOLD, CALL, PUT
