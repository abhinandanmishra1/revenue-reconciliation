import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import ReconciliationItem, User
from app.db.session import get_db
from app.deps import current_user, owned_import
from app.errors import ApiError, not_found
from app.explain import cache, prompts, service
from app.explain.provider import GeminiProvider, Provider
from app.routers.imports import item_out
from app.security.ratelimit import limits

router = APIRouter(prefix="/api/imports/{import_id}", tags=["explain"])

SUMMARY_TOP_ITEMS = 20


def get_provider(settings: Settings = Depends(get_settings)) -> Provider | None:
    if not (settings.gemini_api_key and settings.gemini_model):
        return None
    return GeminiProvider(settings.gemini_api_key, settings.gemini_model)


def _model_id(settings: Settings) -> str:
    return settings.gemini_model or "none"


def _rate_limit(user: User) -> None:
    retry = limits.explain.take(str(user.id))
    if retry is not None:
        raise ApiError(429, "rate_limited", "Too many explanation requests. Please wait a moment.",
                       headers={"Retry-After": str(retry)})


def _run(db, import_id, item_id, evidence, kind, provider, settings) -> dict:
    model = _model_id(settings)
    hit = cache.get_cached(db, import_id, item_id, model)
    if hit is not None:
        return {"explanation": cache.explanation_out(hit)}
    task, schema, fallback = kind
    result = service.explain(provider, task, evidence, schema, fallback)
    if result.source == "fallback":
        # Not cached, so a later click can try the LLM again.
        return {"explanation": {"output": result.output, "source": "fallback", "cached": False, "model": None,
                                "prompt_version": prompts.PROMPT_VERSION, "created_at": None}}
    row = cache.store(db, import_id, item_id, model, result.output)
    return {"explanation": cache.explanation_out(row, cached=False)}


@router.post("/items/{item_id}/explain")
def explain_item(
    import_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: Provider | None = Depends(get_provider),
):
    imp = owned_import(db, import_id, user)
    item = db.scalar(select(ReconciliationItem).where(ReconciliationItem.id == item_id, ReconciliationItem.import_id == imp.id))
    if item is None:
        raise not_found("Item")
    _rate_limit(user)
    evidence = prompts.item_evidence(item_out(item, evidence=True))
    return _run(db, imp.id, item.id, evidence, service.ITEM, provider, settings)


@router.post("/summary/explain")
def explain_summary(
    import_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    provider: Provider | None = Depends(get_provider),
):
    imp = owned_import(db, import_id, user)
    _rate_limit(user)
    top = db.scalars(
        select(ReconciliationItem)
        .where(ReconciliationItem.import_id == imp.id, ReconciliationItem.category != "MATCHED")
        .order_by(ReconciliationItem.severity_rank, ReconciliationItem.amount_at_risk.desc(), ReconciliationItem.item_key)
        .limit(SUMMARY_TOP_ITEMS)
    ).all()
    evidence = prompts.summary_evidence(imp.summary["metrics"], imp.summary["validation"], [item_out(i) for i in top])
    return _run(db, imp.id, None, evidence, service.SUMMARY, provider, settings)
