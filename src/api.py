"""API de recomendação em tempo real.

POST /recommend  -> busca features do cliente no Redis (Feast Online Store), monta o
                    contexto (segmento do cliente x mês da campanha), escolhe a oferta
                    via Thompson Sampling e registra o log.
POST /feedback   -> recebe a conversão (1/0) de uma recomendação e atualiza
                    os contadores do braço no PostgreSQL.

Uso local: uvicorn src.api:app --host 0.0.0.0 --port 8000
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from feast import FeatureStore
from feast.errors import FeastObjectNotFoundException
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import select

from src.bandit import PostgresArmStore, ThompsonSamplingBandit, posterior_mean
from src.config import (
    ARMS,
    CONTEXTS,
    FEAST_REPO_PATH,
    FEATURE_VIEW_NAME,
    MONTHS,
    REDIS_CONNECTION_STRING,
    SEGMENTS,
    context_key,
    month_of,
    parse_redis_connection_string,
)
from src.database import RecommendationLog, get_session_factory, init_db, ping, utcnow

log = logging.getLogger("uvicorn.error")

ONLINE_FEATURES = [
    "age",
    "job",
    "education",
    "housing",
    "loan",
    "previous",
    "days_since_last_contact",
    "poutcome",
    "customer_segment",
]


Month = Literal[tuple(MONTHS)]


class RecommendRequest(BaseModel):
    client_id: int = Field(..., examples=[900003])
    campaign_month: Month | None = Field(
        None, description="Mês da campanha (jan..dec). Se omitido, usa o mês atual.", examples=["may"]
    )


class RecommendResponse(BaseModel):
    recommendation_id: int
    client_id: int
    segment: str
    campaign_month: str
    context: str
    recommended_offer: str
    offer_description: str
    sampled_scores: dict[str, float]
    features: dict
    feature_latency_ms: float


class FeedbackRequest(BaseModel):
    recommendation_id: int
    reward: Literal[0, 1] = Field(..., description="1 = cliente converteu, 0 = não converteu")


class FeedbackResponse(BaseModel):
    recommendation_id: int
    context: str
    offer: str
    reward: int
    successes: int
    failures: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.feature_store = FeatureStore(repo_path=FEAST_REPO_PATH)
    app.state.arm_store = PostgresArmStore(get_session_factory())
    app.state.bandit = ThompsonSamplingBandit(app.state.arm_store)
    log.info("API pronta: Feature Store e bandit inicializados")
    yield


app = FastAPI(
    title="MAB Offer Recommender",
    description="Seleção adaptativa de canal/oferta com Thompson Sampling + Feast (Redis/Postgres)",
    version="1.0.0",
    lifespan=lifespan,
)


def fetch_online_features(client_id: int) -> tuple[dict, float]:
    start = time.perf_counter()
    try:
        response = app.state.feature_store.get_online_features(
            features=[f"{FEATURE_VIEW_NAME}:{name}" for name in ONLINE_FEATURES],
            entity_rows=[{"client_id": client_id}],
        ).to_dict()
    except FeastObjectNotFoundException as exc:
        raise HTTPException(
            status_code=503,
            detail="Feature Store não inicializada: rode feature_store/materialize.py",
        ) from exc
    latency_ms = (time.perf_counter() - start) * 1000
    return {name: response[name][0] for name in ONLINE_FEATURES}, latency_ms


def ping_redis() -> None:
    Redis(**parse_redis_connection_string(REDIS_CONNECTION_STRING), socket_timeout=2).ping()


@app.get("/health")
def health() -> dict:
    try:
        ping()
        ping_redis()
    except Exception as exc:  # noqa: BLE001 - health check reporta qualquer falha de dependência
        raise HTTPException(status_code=503, detail=f"dependência indisponível: {exc}") from exc
    return {"status": "ok"}


@app.post("/recommend", response_model=RecommendResponse)
def recommend(request: RecommendRequest) -> RecommendResponse:
    features, latency_ms = fetch_online_features(request.client_id)
    segment = features.get("customer_segment")
    if segment not in SEGMENTS:
        raise HTTPException(
            status_code=404,
            detail=f"cliente {request.client_id} não encontrado na Online Store (rode a materialização)",
        )

    month = request.campaign_month or month_of(utcnow())
    context = context_key(segment, month)
    arm, scores = app.state.bandit.select_arm(context)

    with get_session_factory().begin() as session:
        entry = RecommendationLog(
            client_id=request.client_id,
            segment=context,
            recommended_offer=arm,
            sampled_score=scores[arm],
        )
        session.add(entry)
        session.flush()
        recommendation_id = entry.id

    return RecommendResponse(
        recommendation_id=recommendation_id,
        client_id=request.client_id,
        segment=segment,
        campaign_month=month,
        context=context,
        recommended_offer=arm,
        offer_description=ARMS[arm],
        sampled_scores={a: round(s, 4) for a, s in scores.items()},
        features=features,
        feature_latency_ms=round(latency_ms, 3),
    )


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(request: FeedbackRequest) -> FeedbackResponse:
    with get_session_factory().begin() as session:
        # FOR UPDATE garante que dois feedbacks simultâneos não contem a mesma recomendação duas vezes.
        entry = session.execute(
            select(RecommendationLog).where(RecommendationLog.id == request.recommendation_id).with_for_update()
        ).scalar_one_or_none()
        if entry is None:
            raise HTTPException(status_code=404, detail="recomendação não encontrada")
        if entry.reward is not None:
            raise HTTPException(status_code=409, detail="feedback já registrado para esta recomendação")
        if entry.segment not in CONTEXTS:
            raise HTTPException(
                status_code=409,
                detail=f"recomendação feita por uma versão anterior do modelo (contexto '{entry.segment}')",
            )

        entry.reward = request.reward
        entry.feedback_at = utcnow()
        # entry.segment guarda o contexto em que a recomendação foi feita.
        app.state.arm_store.increment(entry.segment, entry.recommended_offer, request.reward, session=session)
        context, offer = entry.segment, entry.recommended_offer

    successes, failures = app.state.arm_store.get_counts(context)[offer]
    return FeedbackResponse(
        recommendation_id=request.recommendation_id,
        context=context,
        offer=offer,
        reward=request.reward,
        successes=successes,
        failures=failures,
    )


@app.get("/arms")
def arms_state(month: Month | None = None) -> dict:
    """Estado atual do bandit por contexto (segmento|mês): contadores e conversão esperada.
    Com ?month=may, mostra só os contextos desse mês."""
    output = {}
    for context in CONTEXTS:
        if month and not context.endswith(f"|{month}"):
            continue
        counts = app.state.arm_store.get_counts(context)
        output[context] = {
            arm: {"successes": s, "failures": f, "expected_conversion": round(posterior_mean(s, f), 4)}
            for arm, (s, f) in counts.items()
        }
    return output
