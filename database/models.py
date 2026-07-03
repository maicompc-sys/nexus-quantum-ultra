"""
NEXUS QUANTUM ULTRA — SQLAlchemy ORM Models
Persistent storage for candles, trades, agents, strategies, neural weights.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Boolean,
    DateTime, Text, JSON, ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Candles ────────────────────────────────────────────────────────────────
class Candle(Base):
    __tablename__ = "candles"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    symbol       = Column(String(16),  nullable=False, index=True)
    granularity  = Column(Integer,     nullable=False, index=True)  # seconds
    epoch        = Column(Integer,     nullable=False, index=True)
    open         = Column(Float,       nullable=False)
    high         = Column(Float,       nullable=False)
    low          = Column(Float,       nullable=False)
    close        = Column(Float,       nullable=False)
    volume       = Column(Float,       default=0.0)
    created_at   = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        Index("ix_candle_symbol_gran_epoch", "symbol", "granularity", "epoch", unique=True),
    )

    def to_dict(self) -> dict:
        return {
            "epoch": self.epoch, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": self.volume,
        }


# ── Trades ─────────────────────────────────────────────────────────────────
class Trade(Base):
    __tablename__ = "trades"

    id              = Column(Integer,   primary_key=True, autoincrement=True)
    trade_id        = Column(String(64),unique=True, nullable=False)
    symbol          = Column(String(16),nullable=False, index=True)
    contract_type   = Column(String(8), nullable=False)   # CALL | PUT
    stake           = Column(Float,     nullable=False)
    payout          = Column(Float,     default=0.0)
    profit          = Column(Float,     default=0.0)
    duration        = Column(Integer,   nullable=False)   # seconds
    entry_price     = Column(Float,     default=0.0)
    exit_price      = Column(Float,     default=0.0)
    outcome         = Column(String(8), default="OPEN")   # WIN | LOSS | OPEN
    confidence      = Column(Float,     default=0.0)
    martingale_lvl  = Column(Integer,   default=0)
    strategy_name   = Column(String(64),default="")
    agent_votes     = Column(JSON,      default=dict)
    council_summary = Column(Text,      default="")
    indicators      = Column(JSON,      default=dict)
    opened_at       = Column(DateTime,  default=datetime.utcnow)
    closed_at       = Column(DateTime,  nullable=True)
    account_type    = Column(String(8), default="demo")

    __table_args__ = (
        Index("ix_trade_symbol_outcome", "symbol", "outcome"),
    )


# ── Agent Decisions ────────────────────────────────────────────────────────
class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id          = Column(Integer,   primary_key=True, autoincrement=True)
    cycle_id    = Column(String(36),nullable=False, index=True)   # UUID do ciclo
    agent_name  = Column(String(32),nullable=False)
    symbol      = Column(String(16),nullable=False)
    signal      = Column(String(8), nullable=False)   # CALL | PUT | HOLD
    confidence  = Column(Float,     default=0.0)
    reasoning   = Column(Text,      default="")
    latency_ms  = Column(Integer,   default=0)
    created_at  = Column(DateTime,  default=datetime.utcnow)


# ── Strategies ─────────────────────────────────────────────────────────────
class Strategy(Base):
    __tablename__ = "strategies"

    id              = Column(Integer,    primary_key=True, autoincrement=True)
    name            = Column(String(64), nullable=False, unique=True)
    description     = Column(Text,       default="")
    rules           = Column(JSON,       default=dict)   # condições da estratégia
    symbols         = Column(JSON,       default=list)   # símbolos válidos
    granularities   = Column(JSON,       default=list)
    win_rate        = Column(Float,      default=0.0)
    total_trades    = Column(Integer,    default=0)
    total_wins      = Column(Integer,    default=0)
    total_profit    = Column(Float,      default=0.0)
    is_active       = Column(Boolean,    default=True)
    is_blocked      = Column(Boolean,    default=False)
    block_reason    = Column(Text,       default="")
    created_by      = Column(String(32), default="COUNCIL")   # COUNCIL | MANUAL
    created_at      = Column(DateTime,   default=datetime.utcnow)
    last_used_at    = Column(DateTime,   nullable=True)
    last_updated_at = Column(DateTime,   default=datetime.utcnow)


# ── Neural Network Snapshots ───────────────────────────────────────────────
class NeuralSnapshot(Base):
    __tablename__ = "neural_snapshots"

    id              = Column(Integer,    primary_key=True, autoincrement=True)
    version         = Column(Integer,    nullable=False)
    accuracy        = Column(Float,      default=0.0)
    val_accuracy    = Column(Float,      default=0.0)
    loss            = Column(Float,      default=0.0)
    trades_trained  = Column(Integer,    default=0)
    model_path      = Column(String(256),default="")
    hyperparams     = Column(JSON,       default=dict)
    created_at      = Column(DateTime,   default=datetime.utcnow)


# ── Groq Council Logs ──────────────────────────────────────────────────────
class CouncilLog(Base):
    __tablename__ = "council_logs"

    id           = Column(Integer,    primary_key=True, autoincrement=True)
    cycle_id     = Column(String(36), nullable=False, index=True)
    symbol       = Column(String(16), nullable=False)
    model_a_out  = Column(Text,       default="")
    model_b_out  = Column(Text,       default="")
    model_c_out  = Column(Text,       default="")
    final_signal = Column(String(8),  default="HOLD")
    confidence   = Column(Float,      default=0.0)
    strategy_ref = Column(String(64), default="")
    tokens_used  = Column(Integer,    default=0)
    latency_ms   = Column(Integer,    default=0)
    created_at   = Column(DateTime,   default=datetime.utcnow)


# ── Blocked Patterns (Obsidian Memory) ────────────────────────────────────
class BlockedPattern(Base):
    __tablename__ = "blocked_patterns"

    id           = Column(Integer,    primary_key=True, autoincrement=True)
    pattern_hash = Column(String(64), nullable=False, unique=True)
    symbol       = Column(String(16), nullable=False)
    description  = Column(Text,       default="")
    loss_streak  = Column(Integer,    default=0)
    total_loss   = Column(Float,      default=0.0)
    indicators   = Column(JSON,       default=dict)
    blocked_at   = Column(DateTime,   default=datetime.utcnow)
    expires_at   = Column(DateTime,   nullable=True)


# ── Performance Stats (daily rollup) ──────────────────────────────────────
class DailyStats(Base):
    __tablename__ = "daily_stats"

    id              = Column(Integer,    primary_key=True, autoincrement=True)
    date            = Column(String(10), nullable=False, unique=True)  # YYYY-MM-DD
    total_trades    = Column(Integer,    default=0)
    wins            = Column(Integer,    default=0)
    losses          = Column(Integer,    default=0)
    win_rate        = Column(Float,      default=0.0)
    gross_profit    = Column(Float,      default=0.0)
    gross_loss      = Column(Float,      default=0.0)
    net_profit      = Column(Float,      default=0.0)
    max_drawdown    = Column(Float,      default=0.0)
    best_symbol     = Column(String(16), default="")
    worst_symbol    = Column(String(16), default="")
    best_strategy   = Column(String(64), default="")
    created_at      = Column(DateTime,   default=datetime.utcnow)
