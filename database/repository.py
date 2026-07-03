"""
NEXUS QUANTUM ULTRA — Database Repository
Async CRUD layer using SQLAlchemy + aiosqlite.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Sequence

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update, delete, func, and_, desc

from database.models import (
    Base, Candle, Trade, AgentDecision, Strategy,
    NeuralSnapshot, CouncilLog, BlockedPattern, DailyStats
)
from utils.config import DB_URL
from utils.logger import agent_log


def _now() -> datetime:
    """datetime timezone-aware em UTC (substitui datetime.utcnow())."""
    return datetime.now(timezone.utc)


# ── Engine & Session ───────────────────────────────────────────────────────
engine = create_async_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    """Create all tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    agent_log("SYSTEM", "Banco de dados inicializado.")


# ── Candles ────────────────────────────────────────────────────────────────
async def upsert_candles(candles: List[Dict]) -> int:
    """Insert or ignore candles (bulk). Returns count inserted."""
    inserted = 0
    async with AsyncSessionLocal() as session:
        for c in candles:
            exists = await session.execute(
                select(Candle).where(
                    Candle.symbol      == c["symbol"],
                    Candle.granularity == c["granularity"],
                    Candle.epoch       == c["epoch"],
                )
            )
            if exists.scalar_one_or_none() is None:
                session.add(Candle(**c))
                inserted += 1
        await session.commit()
    return inserted


async def get_candles(
    symbol: str,
    granularity: int,
    limit: int = 5000,
) -> List[Dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Candle)
            .where(Candle.symbol == symbol, Candle.granularity == granularity)
            .order_by(desc(Candle.epoch))
            .limit(limit)
        )
        rows = list(result.scalars().all())
        return [r.to_dict() for r in reversed(rows)]


async def get_latest_candle_epoch(symbol: str, granularity: int) -> Optional[int]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.max(Candle.epoch))
            .where(Candle.symbol == symbol, Candle.granularity == granularity)
        )
        return result.scalar_one_or_none()


# ── Trades ─────────────────────────────────────────────────────────────────
async def save_trade(trade_data: Dict) -> Trade:
    async with AsyncSessionLocal() as session:
        trade = Trade(**trade_data)
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        agent_log("MEMORY", f"Trade salvo: {trade.trade_id} | {trade.symbol} | {trade.contract_type}")
        return trade


async def update_trade_outcome(
    trade_id: str,
    outcome: str,
    profit: float,
    exit_price: float,
    payout: float,
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Trade)
            .where(Trade.trade_id == trade_id)
            .values(
                outcome=outcome,
                profit=profit,
                exit_price=exit_price,
                payout=payout,
                closed_at=_now(),
            )
        )
        await session.commit()


async def get_recent_trades(limit: int = 50) -> List[Trade]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Trade)
            .order_by(desc(Trade.opened_at))
            .limit(limit)
        )
        return list(result.scalars().all())


async def get_trade_stats(symbol: Optional[str] = None) -> Dict:
    async with AsyncSessionLocal() as session:
        q = select(
            func.count(Trade.id).label("total"),
            func.sum(Trade.profit).label("net_profit"),
        )
        if symbol:
            q = q.where(Trade.symbol == symbol)
        result = await session.execute(q)
        row = result.one()

        wins_q = select(func.count(Trade.id)).where(Trade.outcome == "WIN")
        if symbol:
            wins_q = wins_q.where(Trade.symbol == symbol)
        wins = (await session.execute(wins_q)).scalar_one() or 0


        total     = int(row.total or 0)
        net_pnl   = float(row.net_profit or 0.0)
        return {
            "total":      total,
            "wins":       wins,
            "losses":     total - wins,
            "win_rate":   round(wins / total * 100, 2) if total > 0 else 0.0,
            "net_profit": round(net_pnl, 2),
        }


# ── Strategies ─────────────────────────────────────────────────────────────
async def save_strategy(data: Dict) -> Strategy:
    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(Strategy).where(Strategy.name == data["name"])
        )
        strat = existing.scalar_one_or_none()
        if strat:
            for k, v in data.items():
                setattr(strat, k, v)
            strat.last_updated_at = _now()  # type: ignore[assignment]
        else:
            strat = Strategy(**data)
            session.add(strat)
        await session.commit()
        await session.refresh(strat)
        return strat


async def get_active_strategies() -> List[Strategy]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Strategy)
            .where(Strategy.is_active == True, Strategy.is_blocked == False)
            .order_by(desc(Strategy.win_rate))
        )
        return list(result.scalars().all())


async def block_strategy(name: str, reason: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Strategy)
            .where(Strategy.name == name)
            .values(is_blocked=True, block_reason=reason, last_updated_at=_now())
        )
        await session.commit()
        agent_log("AUDITOR", f"Estratégia bloqueada: {name} — {reason}")


async def update_strategy_stats(name: str, won: bool, profit: float) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Strategy).where(Strategy.name == name)
        )
        strat = result.scalar_one_or_none()
        if not strat:
            return

        total_trades  = int(strat.total_trades or 0) + 1        # type: ignore[arg-type]
        total_wins    = int(strat.total_wins   or 0) + int(won) # type: ignore[arg-type]
        total_profit  = float(strat.total_profit or 0) + profit  # type: ignore[arg-type]
        win_rate      = round(total_wins / total_trades * 100, 2)

        strat.total_trades   = total_trades   # type: ignore[assignment]
        strat.total_wins     = total_wins     # type: ignore[assignment]
        strat.total_profit   = total_profit   # type: ignore[assignment]
        strat.win_rate       = win_rate       # type: ignore[assignment]
        strat.last_used_at   = _now()         # type: ignore[assignment]
        strat.last_updated_at = _now()        # type: ignore[assignment]

        # Auto-block se win_rate < 35% após 20+ trades
        if total_trades >= 20 and win_rate < 35.0:
            strat.is_blocked   = True  # type: ignore[assignment]
            strat.block_reason = f"Win rate abaixo de 35% ({win_rate}%) após {total_trades} trades"  # type: ignore[assignment]
            agent_log("AUDITOR", f"Auto-bloqueio: {name} — win_rate={win_rate}%")

        await session.commit()


# ── Blocked Patterns ───────────────────────────────────────────────────────
async def add_blocked_pattern(
    symbol: str,
    description: str,
    indicators: Dict,
    loss_streak: int,
    total_loss: float,
    expires_hours: int = 24,
) -> None:
    pattern_hash = hashlib.sha256(
        json.dumps(indicators, sort_keys=True).encode()
    ).hexdigest()[:32]

    async with AsyncSessionLocal() as session:
        session.add(BlockedPattern(
            pattern_hash = pattern_hash,
            symbol       = symbol,
            description  = description,
            loss_streak  = loss_streak,
            total_loss   = total_loss,
            indicators   = indicators,
            expires_at   = _now() + timedelta(hours=expires_hours),
        ))
        await session.commit()


async def is_pattern_blocked(symbol: str, indicators: Dict) -> bool:
    pattern_hash = hashlib.sha256(
        json.dumps(indicators, sort_keys=True).encode()
    ).hexdigest()[:32]
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(BlockedPattern).where(
                BlockedPattern.pattern_hash == pattern_hash,
                BlockedPattern.symbol       == symbol,
                BlockedPattern.expires_at   > _now(),
            )
        )
        return result.scalar_one_or_none() is not None


# ── Council Logs ───────────────────────────────────────────────────────────
async def save_council_log(data: Dict) -> None:
    async with AsyncSessionLocal() as session:
        session.add(CouncilLog(**data))
        await session.commit()


async def get_council_logs(limit: int = 20) -> List[CouncilLog]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CouncilLog).order_by(desc(CouncilLog.created_at)).limit(limit)
        )
        return list(result.scalars().all())


# ── Agent Decisions ────────────────────────────────────────────────────────
async def save_agent_decision(data: Dict) -> None:
    async with AsyncSessionLocal() as session:
        session.add(AgentDecision(**data))
        await session.commit()


# ── Neural Snapshots ───────────────────────────────────────────────────────
async def save_neural_snapshot(data: Dict) -> NeuralSnapshot:
    async with AsyncSessionLocal() as session:
        snap = NeuralSnapshot(**data)
        session.add(snap)
        await session.commit()
        await session.refresh(snap)
        return snap


async def get_latest_neural_snapshot() -> Optional[NeuralSnapshot]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(NeuralSnapshot).order_by(desc(NeuralSnapshot.version)).limit(1)
        )
        return result.scalar_one_or_none()


# ── Daily Stats ────────────────────────────────────────────────────────────
async def update_daily_stats() -> None:
    today = _now().strftime("%Y-%m-%d")
    stats = await get_trade_stats()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyStats).where(DailyStats.date == today)
        )
        row = result.scalar_one_or_none()
        if row:
            row.total_trades = stats["total"]    # type: ignore[assignment]
            row.wins         = stats["wins"]     # type: ignore[assignment]
            row.losses       = stats["losses"]   # type: ignore[assignment]
            row.win_rate     = stats["win_rate"] # type: ignore[assignment]
            row.net_profit   = stats["net_profit"] # type: ignore[assignment]
        else:
            session.add(DailyStats(
                date         = today,
                total_trades = stats["total"],
                wins         = stats["wins"],
                losses       = stats["losses"],
                win_rate     = stats["win_rate"],
                net_profit   = stats["net_profit"],
            ))
        await session.commit()


# ── Aliases de compatibilidade (usados pelo preloader) ─────────────────────
async def save_candles_batch(candles: List[Dict]) -> int:
    """Alias de upsert_candles — compatibilidade com preloader.py."""
    return await upsert_candles(candles)


async def get_candle_count(symbol: str, granularity: int) -> int:
    """Retorna total de velas salvas para um par symbol/granularity."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(Candle.id))
            .where(Candle.symbol == symbol, Candle.granularity == granularity)
        )
        return result.scalar_one() or 0
