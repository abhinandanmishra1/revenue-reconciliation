"""CSV ingestion: (file bytes) -> records + validation report.

Pure functions: no database, HTTP or clock access. Raises FileRejected for
file-level problems; row-level problems end up in the report instead.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .models import (
    CURRENCIES,
    MAX_FILE_BYTES,
    ORDER_STATUSES,
    PAYMENT_STATUSES,
    PAYMENT_TYPES,
    FileKind,
    FileRejected,
    IdConflict,
    OrderRecord,
    ParsedFile,
    PaymentRecord,
    RejectedRow,
)

ORDER_REQUIRED = ("order_id", "currency", "net_amount", "status")
ORDER_OPTIONAL = ("order_date", "customer_email", "gross_amount", "discount")
PAYMENT_REQUIRED = ("transaction_ref", "currency", "amount", "type", "status")
PAYMENT_OPTIONAL = ("processed_at", "order_reference", "fee", "net_settled")

ORDER_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
# Fixed day-first format: some values have day > 12, so guessing per value
# would be ambiguous for the rest.
PAYMENT_DATE_FORMAT = "%d/%m/%Y %H:%M"

_CENT = Decimal("0.01")


class _FieldError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason


def normalise_key(value: str) -> str:
    return value.strip().upper()


# ---------------------------------------------------------------- file level


def _decode(kind: FileKind, data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise FileRejected(kind, "file_too_large", f"The {kind} file is larger than 2 MB.")
    if not data.strip():
        raise FileRejected(kind, "empty_file", f"The {kind} file is empty.")
    try:
        # utf-8-sig strips a leading BOM if present.
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileRejected(kind, "not_utf8", f"The {kind} file is not UTF-8 encoded.") from None


def _read_rows(kind: FileKind, text: str, required: tuple[str, ...], other_required: tuple[str, ...]):
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise FileRejected(kind, "empty_file", f"The {kind} file is empty.") from None
    except csv.Error as exc:
        raise FileRejected(kind, "malformed_csv", f"The {kind} file is not valid CSV: {exc}.") from None

    headers = tuple(h.strip().lower() for h in header)
    if len(headers) == 1 and any(sep in headers[0] for sep in (";", "\t", "|")):
        raise FileRejected(kind, "not_comma_delimited", f"The {kind} file must be comma-delimited.")
    if len(set(headers)) != len(headers):
        raise FileRejected(kind, "duplicate_headers", f"The {kind} file has duplicate column names.")

    missing = [h for h in required if h not in headers]
    if missing:
        if all(h in headers for h in other_required):
            other = "payments" if kind == "orders" else "orders"
            raise FileRejected(
                kind,
                "wrong_file",
                f"The {kind} slot received what looks like a {other} file. Check that the files are not swapped.",
            )
        raise FileRejected(
            kind, "missing_headers", f"The {kind} file is missing required columns: {', '.join(missing)}."
        )

    rows: list[tuple[int, list[str]]] = []
    try:
        for values in reader:
            if not values or all(not v.strip() for v in values):
                continue  # blank line
            rows.append((reader.line_num, values))
    except csv.Error as exc:
        raise FileRejected(kind, "malformed_csv", f"The {kind} file is not valid CSV: {exc}.") from None
    return headers, rows


# --------------------------------------------------------------- field level


def _required(raw: dict[str, str], name: str) -> str:
    value = raw.get(name, "").strip()
    if not value:
        raise _FieldError(name, f"{name} is required")
    return value


def _money(name: str, value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise _FieldError(name, f"{name} is not a number: {value!r}") from None
    if not amount.is_finite():
        raise _FieldError(name, f"{name} is not a number: {value!r}")
    if amount < 0:
        raise _FieldError(name, f"{name} must not be negative: {value!r}")
    if amount != amount.quantize(_CENT):
        raise _FieldError(name, f"{name} has more than 2 decimal places: {value!r}")
    return amount.quantize(_CENT)


def _optional_money(raw: dict[str, str], name: str) -> Decimal | None:
    value = raw.get(name, "").strip()
    return _money(name, value) if value else None


def _currency(raw: dict[str, str]) -> str:
    value = _required(raw, "currency").upper()
    if value not in CURRENCIES:
        raise _FieldError("currency", f"currency is not supported: {value!r}")
    return value


def _choice(raw: dict[str, str], name: str, allowed: frozenset[str]) -> str:
    value = _required(raw, name).lower()
    if value not in allowed:
        raise _FieldError(name, f"{name} must be one of {', '.join(sorted(allowed))}: {value!r}")
    return value


def _date(raw: dict[str, str], name: str, fmt: str, human: str) -> datetime | None:
    value = raw.get(name, "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, fmt)
    except ValueError:
        raise _FieldError(name, f"{name} must be {human}: {value!r}") from None


def _collect(raw: dict[str, str], steps: dict[str, Callable[[], object]]) -> tuple[dict, list[_FieldError]]:
    """Run every field parser so a row reports all of its problems at once."""
    values: dict[str, object] = {}
    errors: list[_FieldError] = []
    for name, step in steps.items():
        try:
            values[name] = step()
        except _FieldError as err:
            errors.append(err)
    return values, errors


def _parse_order(row_number: int, raw: dict[str, str]) -> OrderRecord:
    values, errors = _collect(
        raw,
        {
            "order_id": lambda: _required(raw, "order_id"),
            "currency": lambda: _currency(raw),
            "net_amount": lambda: _money("net_amount", _required(raw, "net_amount")),
            "status": lambda: _choice(raw, "status", ORDER_STATUSES),
            "order_date": lambda: _date(raw, "order_date", ORDER_DATE_FORMAT, "YYYY-MM-DD HH:MM:SS"),
            "gross_amount": lambda: _optional_money(raw, "gross_amount"),
            "discount": lambda: _optional_money(raw, "discount"),
        },
    )
    if errors:
        raise _RowRejected(errors)

    flags: set[str] = set()
    order_id_raw = raw["order_id"]
    order_key = normalise_key(order_id_raw)
    if order_key != order_id_raw:
        flags.add("reference_normalised")
    if values["order_date"] is None:
        flags.add("missing_order_date")
    email = raw.get("customer_email", "").strip() or None
    if email is None:
        flags.add("missing_customer_email")

    gross, discount, net = values["gross_amount"], values["discount"], values["net_amount"]
    if gross is None:
        flags.add("missing_gross_amount")
    if discount is None:
        if gross is not None and gross == net:
            discount = Decimal("0.00")
        else:
            flags.add("missing_discount")
    if gross is not None and discount is not None and gross - discount != net:
        flags.add("gross_net_mismatch")

    return OrderRecord(
        row_numbers=(row_number,),
        order_id_raw=order_id_raw,
        order_key=order_key,
        order_date=values["order_date"],
        customer_email=email,
        currency=values["currency"],
        gross_amount=gross,
        discount=discount,
        net_amount=net,
        status=values["status"],
        raw=raw,
        dq_flags=tuple(sorted(flags)),
    )


def _parse_payment(row_number: int, raw: dict[str, str]) -> PaymentRecord:
    values, errors = _collect(
        raw,
        {
            "transaction_ref": lambda: _required(raw, "transaction_ref"),
            "currency": lambda: _currency(raw),
            "amount": lambda: _money("amount", _required(raw, "amount")),
            "type": lambda: _choice(raw, "type", PAYMENT_TYPES),
            "status": lambda: _choice(raw, "status", PAYMENT_STATUSES),
            "processed_at": lambda: _date(raw, "processed_at", PAYMENT_DATE_FORMAT, "DD/MM/YYYY HH:MM"),
            "fee": lambda: _optional_money(raw, "fee"),
            "net_settled": lambda: _optional_money(raw, "net_settled"),
        },
    )
    if errors:
        raise _RowRejected(errors)

    flags: set[str] = set()
    txn_raw = raw["transaction_ref"]
    txn_ref = normalise_key(txn_raw)
    ref_raw = raw.get("order_reference")
    order_key = normalise_key(ref_raw) if ref_raw and ref_raw.strip() else None
    if txn_ref != txn_raw or (order_key is not None and order_key != ref_raw):
        flags.add("reference_normalised")
    if order_key is None:
        flags.add("missing_order_reference")
    if values["processed_at"] is None:
        flags.add("missing_processed_at")

    amount, fee, net_settled = values["amount"], values["fee"], values["net_settled"]
    if fee is None:
        flags.add("missing_fee")
    if net_settled is None:
        flags.add("missing_net_settled")
    if fee is not None and net_settled is not None and amount - fee != net_settled:
        flags.add("fee_mismatch")

    return PaymentRecord(
        row_numbers=(row_number,),
        transaction_ref=txn_ref,
        order_reference_raw=ref_raw if ref_raw is not None else None,
        order_key=order_key,
        processed_at=values["processed_at"],
        currency=values["currency"],
        amount=amount,
        fee=fee,
        net_settled=net_settled,
        type=values["type"],
        status=values["status"],
        raw=raw,
        dq_flags=tuple(sorted(flags)),
    )


class _RowRejected(Exception):
    def __init__(self, errors: list[_FieldError]) -> None:
        self.errors = errors


# ------------------------------------------------------------- de-duplication


def _content(record) -> tuple:
    """Parsed values that define 'the same record'. 0 and 0.0 compare equal."""
    if isinstance(record, OrderRecord):
        return (
            record.order_key, record.order_date, record.customer_email, record.currency,
            record.gross_amount, record.discount, record.net_amount, record.status,
        )
    return (
        record.transaction_ref, record.order_key, record.processed_at, record.currency,
        record.amount, record.fee, record.net_settled, record.type, record.status,
    )


def _dedupe(records: list, key_of: Callable, order_keys_of: Callable):
    groups: dict[str, list] = defaultdict(list)
    for record in records:
        groups[key_of(record)].append(record)

    kept, conflicts, collapsed = [], [], 0
    for key in sorted(groups):
        group = groups[key]
        rows = tuple(sorted(n for r in group for n in r.row_numbers))
        if len(group) == 1:
            kept.append(group[0])
        elif len({_content(r) for r in group}) == 1:
            first = min(group, key=lambda r: r.row_numbers)
            flags = tuple(sorted(set().union(*(r.dq_flags for r in group)) | {"duplicate_row"}))
            kept.append(replace(first, row_numbers=rows, dq_flags=flags))
            collapsed += len(group) - 1
        else:
            order_keys = tuple(sorted({k for r in group for k in order_keys_of(r) if k}))
            conflicts.append(IdConflict(key=key, row_numbers=rows, order_keys=order_keys))
    return kept, conflicts, collapsed


# ----------------------------------------------------------------- public API


def _parse(
    kind: FileKind,
    data: bytes,
    required: tuple[str, ...],
    other_required: tuple[str, ...],
    parse_row: Callable,
    link_field: str,
    key_of: Callable,
    order_keys_of: Callable,
) -> ParsedFile:
    text = _decode(kind, data)
    headers, rows = _read_rows(kind, text, required, other_required)

    records, rejected = [], []
    for row_number, values in rows:
        raw = {h: v for h, v in zip(headers, values, strict=False)}
        if len(values) != len(headers):
            rejected.append(
                RejectedRow(
                    row_number=row_number,
                    reasons=(f"expected {len(headers)} columns, found {len(values)}",),
                    fields=(),
                    linked_order_key=None,  # column shift: no field can be trusted
                    raw=raw,
                )
            )
            continue
        try:
            records.append(parse_row(row_number, raw))
        except _RowRejected as rej:
            fields = tuple(e.field for e in rej.errors)
            link_value = raw.get(link_field, "").strip()
            # Link only when the key itself is readable and was not the problem.
            linked = normalise_key(link_value) if link_value and link_field not in fields else None
            rejected.append(
                RejectedRow(
                    row_number=row_number,
                    reasons=tuple(e.reason for e in rej.errors),
                    fields=fields,
                    linked_order_key=linked,
                    raw=raw,
                )
            )

    kept, conflicts, collapsed = _dedupe(records, key_of, order_keys_of)
    return ParsedFile(
        kind=kind,
        headers=headers,
        row_count=len(rows),
        records=tuple(kept),
        rejected=tuple(rejected),
        conflicts=tuple(conflicts),
        duplicate_rows_collapsed=collapsed,
    )


def parse_orders(data: bytes) -> ParsedFile:
    return _parse(
        "orders", data, ORDER_REQUIRED, PAYMENT_REQUIRED, _parse_order,
        link_field="order_id",
        key_of=lambda r: r.order_key, order_keys_of=lambda r: (r.order_key,),
    )


def parse_payments(data: bytes) -> ParsedFile:
    return _parse(
        "payments", data, PAYMENT_REQUIRED, ORDER_REQUIRED, _parse_payment,
        link_field="order_reference",
        key_of=lambda r: r.transaction_ref, order_keys_of=lambda r: (r.order_key,),
    )
