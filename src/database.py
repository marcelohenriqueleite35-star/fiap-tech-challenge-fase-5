"""Camada de acesso ao PostgreSQL da aplicação (SQLAlchemy 2.x).

Tabelas:
- bandit_state: contadores de sucessos/falhas por (contexto, braço). A coluna `segment`
  guarda a chave do contexto, "segmento|mês" (ex.: "senior|may").
- recommendation_logs: cada recomendação servida pela API e o feedback recebido.
"""

from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from src.config import ARM_NAMES, CONTEXTS, DATABASE_URL

INIT_LOCK_KEY = 20260926


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class BanditState(Base):
    __tablename__ = "bandit_state"

    segment: Mapped[str] = mapped_column(String(32), primary_key=True)
    arm: Mapped[str] = mapped_column(String(64), primary_key=True)
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationLog(Base):
    __tablename__ = "recommendation_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(32), nullable=False)
    recommended_offer: Mapped[str] = mapped_column(String(64), nullable=False)
    sampled_score: Mapped[float] = mapped_column(Float, nullable=False)
    reward: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL = sem feedback ainda
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # pool_pre_ping evita erros com conexões ociosas derrubadas pelo servidor gerenciado.
    return create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def init_db() -> None:
    """Cria as tabelas (idempotente) e garante uma linha zerada para cada (contexto, braço)."""
    rows = [{"segment": c, "arm": a, "successes": 0, "failures": 0} for c in CONTEXTS for a in ARM_NAMES]
    with get_engine().begin() as conn:
        # Vários workers/tasks sobem ao mesmo tempo: o advisory lock serializa o DDL.
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INIT_LOCK_KEY})
        Base.metadata.create_all(conn)
        conn.execute(insert(BanditState).values(rows).on_conflict_do_nothing())


def ping() -> bool:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


if __name__ == "__main__":
    init_db()
    print("Tabelas bandit_state e recommendation_logs prontas.")
