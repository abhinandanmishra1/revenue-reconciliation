import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Explanation

from .prompts import PROMPT_VERSION


def _key(import_id: uuid.UUID, item_id: uuid.UUID | None, model: str):
    item_cond = Explanation.item_id.is_(None) if item_id is None else Explanation.item_id == item_id
    return (Explanation.import_id == import_id, item_cond, Explanation.prompt_version == PROMPT_VERSION, Explanation.model == model)


def get_cached(db: Session, import_id: uuid.UUID, item_id: uuid.UUID | None, model: str) -> Explanation | None:
    return db.scalar(select(Explanation).where(*_key(import_id, item_id, model)))


def store(db: Session, import_id: uuid.UUID, item_id: uuid.UUID | None, model: str, output: dict) -> Explanation:
    """Insert once; concurrent callers all read back the single winning row."""
    db.execute(
        pg_insert(Explanation)
        .values(id=uuid.uuid4(), import_id=import_id, item_id=item_id, prompt_version=PROMPT_VERSION, model=model, output=output)
        .on_conflict_do_nothing(constraint="uq_explanations_cache")
    )
    db.commit()
    return get_cached(db, import_id, item_id, model)


def explanation_out(row: Explanation | None, *, cached: bool = True) -> dict | None:
    if row is None:
        return None
    return {
        "output": row.output, "source": "llm", "cached": cached, "model": row.model,
        "prompt_version": row.prompt_version, "created_at": row.created_at.isoformat(),
    }
