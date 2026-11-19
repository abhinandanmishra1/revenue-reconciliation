import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import Import, Order, Payment, ReconciliationItem, User
from app.db.session import get_db
from app.deps import current_user, owned_import
from app.explain import cache
from app.errors import ApiError, not_found
from app.ingestion.models import MAX_FILE_BYTES, FileRejected
from app.reconcile.rules import CATEGORIES, DIRECTIONS, SEVERITY_RANK
from app.services.imports import UploadedFile, create_import

router = APIRouter(prefix="/api/imports", tags=["imports"])

Item = ReconciliationItem


async def _read_limited(kind: str, upload: UploadFile) -> UploadedFile:
    data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(413, "file_too_large", f"The {kind} file is larger than 2 MB.", {"file": kind})
    return UploadedFile(filename=(upload.filename or f"{kind}.csv")[:255], data=data)


def _money(value) -> str | None:
    return None if value is None else f"{value:.2f}"


def _import_out(imp: Import, *, full: bool) -> dict:
    out = {
        "id": str(imp.id),
        "orders_filename": imp.orders_filename,
        "payments_filename": imp.payments_filename,
        "rules_version": imp.rules_version,
        "created_at": imp.created_at.isoformat(),
    }
    if full:
        out["summary"] = imp.summary
    return out


@router.post("")
async def upload(
    response: Response,
    orders: Annotated[UploadFile, File()],
    payments: Annotated[UploadFile, File()],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    orders_file = await _read_limited("orders", orders)
    payments_file = await _read_limited("payments", payments)
    try:
        outcome = create_import(db, user.id, orders_file, payments_file)
    except FileRejected as exc:
        status = 413 if exc.code == "file_too_large" else 422
        raise ApiError(status, exc.code, exc.message, {"file": exc.file}) from None
    response.status_code = 201 if outcome.created else 200
    imp = owned_import(db, outcome.import_id, user)
    return {"import": _import_out(imp, full=True), "already_imported": not outcome.created}


@router.get("")
def history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Import.id, Import.orders_filename, Import.payments_filename, Import.rules_version, Import.created_at,
            Import.summary["metrics"]["totals"].label("totals"),
            Import.summary["validation"]["orders"]["rejected_count"].label("orders_rejected"),
            Import.summary["validation"]["payments"]["rejected_count"].label("payments_rejected"),
        )
        .where(Import.user_id == user.id)
        .order_by(Import.created_at.desc(), Import.id)
    ).all()
    return {
        "imports": [
            {
                "id": str(r.id), "orders_filename": r.orders_filename, "payments_filename": r.payments_filename,
                "rules_version": r.rules_version, "created_at": r.created_at.isoformat(), "totals": r.totals,
                "rejected_rows": (r.orders_rejected or 0) + (r.payments_rejected or 0),
            }
            for r in rows
        ]
    }


@router.get("/{import_id}")
def detail(
    import_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    imp = owned_import(db, import_id, user)
    summary = cache.get_cached(db, imp.id, None, settings.gemini_model or "none")
    return {"import": _import_out(imp, full=True), "summary_explanation": cache.explanation_out(summary)}


def item_out(item: Item, *, evidence: bool = False) -> dict:
    out = {
        "id": str(item.id), "item_key": item.item_key, "order_key": item.order_key, "category": item.category,
        "severity": item.severity, "currency": item.currency,
        "expected_amount": _money(item.expected_amount), "actual_amount": _money(item.actual_amount),
        "difference": _money(item.difference), "reconciled_value": _money(item.reconciled_value),
        "disputed_value": _money(item.disputed_value), "amount_at_risk": _money(item.amount_at_risk),
        "risk_direction": item.risk_direction, "reason": item.reason, "dq_flags": item.dq_flags,
    }
    if evidence:
        out["evidence"] = item.evidence
    return out


SortField = Literal["priority", "amount_at_risk", "difference", "order_key", "category"]


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/{import_id}/items")
def items(
    import_id: uuid.UUID,
    category: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    direction: Annotated[list[str] | None, Query()] = None,
    currency: Annotated[list[str] | None, Query()] = None,
    has_dq: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: SortField = "priority",
    order: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    imp = owned_import(db, import_id, user)
    _check_values("category", category, CATEGORIES)
    _check_values("severity", severity, SEVERITY_RANK)
    _check_values("direction", direction, DIRECTIONS)

    conditions = [Item.import_id == imp.id]
    conditions.append(Item.category.in_(category) if category else Item.category != "MATCHED")
    if severity:
        conditions.append(Item.severity.in_(severity))
    if direction:
        conditions.append(Item.risk_direction.in_(direction))
    if currency:
        conditions.append(Item.currency.in_([c.upper() for c in currency]))
    if has_dq is not None:
        has = func.cardinality(Item.dq_flags) > 0
        conditions.append(has if has_dq else ~has)
    if q and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(
                Item.item_key.ilike(like, escape="\\"),
                exists().where(
                    Order.import_id == imp.id, Order.order_key == Item.order_key,
                    or_(Order.order_id_raw.ilike(like, escape="\\"), Order.customer_email.ilike(like, escape="\\")),
                ),
                exists().where(
                    Payment.import_id == imp.id,
                    Payment.transaction_ref.ilike(like, escape="\\"),
                    or_(Payment.order_key == Item.order_key, and_(Item.order_key.is_(None), Item.item_key == "T:" + Payment.transaction_ref)),
                ),
            )
        )

    if sort == "priority":
        ordering = [Item.severity_rank, Item.amount_at_risk.desc(), Item.order_key, Item.item_key]
    else:
        column = getattr(Item, sort)
        ordering = [column.desc().nulls_last() if order == "desc" else column.asc().nulls_last(), Item.item_key]

    total = db.scalar(select(func.count()).select_from(Item).where(*conditions))
    rows = db.scalars(
        select(Item).where(*conditions).order_by(*ordering).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {"items": [item_out(i) for i in rows], "total": total, "page": page, "page_size": page_size}


def _check_values(name: str, values: list[str] | None, allowed) -> None:
    bad = [v for v in values or [] if v not in allowed]
    if bad:
        raise ApiError(422, "validation_error", f"Unknown {name}: {', '.join(bad)}.", {"field": name})


@router.get("/{import_id}/items/{item_id}")
def item_detail(
    import_id: uuid.UUID,
    item_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    imp = owned_import(db, import_id, user)
    item = db.scalar(select(Item).where(Item.id == item_id, Item.import_id == imp.id))
    if item is None:
        raise not_found("Item")
    cached = cache.get_cached(db, imp.id, item.id, settings.gemini_model or "none")
    return {"item": item_out(item, evidence=True), "explanation": cache.explanation_out(cached)}
