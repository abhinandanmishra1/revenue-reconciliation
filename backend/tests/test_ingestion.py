from datetime import datetime
from decimal import Decimal

import pytest

from app.ingestion import FileRejected, parse_orders, parse_payments

ORDER_HEADER = "order_id,order_date,customer_email,currency,gross_amount,discount,net_amount,status\n"
PAYMENT_HEADER = "transaction_ref,order_reference,processed_at,currency,amount,fee,net_settled,type,status\n"


def orders(*rows: str) -> bytes:
    return (ORDER_HEADER + "".join(r + "\n" for r in rows)).encode()


def payments(*rows: str) -> bytes:
    return (PAYMENT_HEADER + "".join(r + "\n" for r in rows)).encode()


# ------------------------------------------------------------------ file rules


def test_bom_is_tolerated_and_headers_ignore_case_and_spaces() -> None:
    data = "﻿ Order_ID , ORDER_DATE,customer_email,Currency,gross_amount,discount,net_amount,status\n"
    data += "O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed\n"
    parsed = parse_orders(data.encode("utf-8"))
    assert len(parsed.records) == 1


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty_file"),
        (b"   \n", "empty_file"),
        ("order_id;currency\n".encode(), "not_comma_delimited"),
        ("order_id,net_amount,status\n".encode(), "missing_headers"),
        (PAYMENT_HEADER.encode(), "wrong_file"),
        ("\xff\xfeo".encode("latin-1"), "not_utf8"),
        (b"a" * (2 * 1024 * 1024 + 1), "file_too_large"),
    ],
)
def test_file_level_rejections(data: bytes, code: str) -> None:
    with pytest.raises(FileRejected) as exc:
        parse_orders(data)
    assert exc.value.code == code


def test_swapped_payments_slot_is_detected() -> None:
    with pytest.raises(FileRejected) as exc:
        parse_payments(ORDER_HEADER.encode())
    assert exc.value.code == "wrong_file"


def test_header_only_file_is_valid_with_no_records() -> None:
    parsed = parse_orders(ORDER_HEADER.encode())
    assert parsed.records == () and parsed.row_count == 0


def test_extra_columns_are_kept_in_raw() -> None:
    data = ORDER_HEADER.strip() + ",channel\nO1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed,web\n"
    assert parse_orders(data.encode()).records[0].raw["channel"] == "web"


# ------------------------------------------------------------------ dates


def test_payment_dates_are_day_first() -> None:
    parsed = parse_payments(payments("T1,O1,13/02/2024 09:05,USD,10,0.5,9.5,charge,settled"))
    assert parsed.records[0].processed_at == datetime(2024, 2, 13, 9, 5)


def test_month_first_payment_date_is_rejected_not_guessed() -> None:
    parsed = parse_payments(payments("T1,O1,02/13/2024 09:05,USD,10,0.5,9.5,charge,settled"))
    assert parsed.records == ()
    assert "processed_at must be DD/MM/YYYY HH:MM" in parsed.rejected[0].reasons[0]


def test_order_date_format() -> None:
    parsed = parse_orders(orders("O1,01/02/2024,a@x.io,USD,10,0,10,completed"))
    assert parsed.rejected and parsed.rejected[0].fields == ("order_date",)


def test_blank_timestamps_are_flagged() -> None:
    o = parse_orders(orders("O1,,a@x.io,USD,10,0,10,completed")).records[0]
    p = parse_payments(payments("T1,O1,,USD,10,0.5,9.5,charge,settled")).records[0]
    assert o.order_date is None and "missing_order_date" in o.dq_flags
    assert p.processed_at is None and "missing_processed_at" in p.dq_flags


# ------------------------------------------------------------------ discount


def test_blank_discount_is_zero_when_gross_equals_net() -> None:
    o = parse_orders(orders("O1,2024-01-01 10:00:00,a@x.io,USD,10,,10,completed")).records[0]
    assert o.discount == Decimal("0.00") and "missing_discount" not in o.dq_flags


def test_blank_discount_is_flagged_when_gross_differs() -> None:
    o = parse_orders(orders("O1,2024-01-01 10:00:00,a@x.io,USD,12,,10,completed")).records[0]
    assert o.discount is None and "missing_discount" in o.dq_flags


def test_consistency_mismatches_are_flagged_not_rejected() -> None:
    o = parse_orders(orders("O1,2024-01-01 10:00:00,a@x.io,USD,12,1,10,completed")).records[0]
    p = parse_payments(payments("T1,O1,01/01/2024 10:00,USD,10,0.5,9,charge,settled")).records[0]
    assert "gross_net_mismatch" in o.dq_flags
    assert "fee_mismatch" in p.dq_flags


# ------------------------------------------------------------------ normalisation


def test_identifiers_are_trimmed_and_uppercased_and_flagged() -> None:
    o = parse_orders(orders(" ord-7 ,2024-01-01 10:00:00,a@x.io,usd,10,0,10,Completed")).records[0]
    p = parse_payments(payments("t1, ord-7 ,01/01/2024 10:00,USD,10,0,10,CHARGE,Settled")).records[0]
    assert (o.order_key, o.order_id_raw, o.currency, o.status) == ("ORD-7", " ord-7 ", "USD", "completed")
    assert "reference_normalised" in o.dq_flags
    assert (p.order_key, p.transaction_ref, p.type, p.status) == ("ORD-7", "T1", "charge", "settled")


def test_blank_order_reference_has_no_key() -> None:
    p = parse_payments(payments("T1,,01/01/2024 10:00,USD,10,0,10,charge,settled")).records[0]
    assert p.order_key is None and "missing_order_reference" in p.dq_flags


# ------------------------------------------------------------------ invalid values


@pytest.mark.parametrize(
    ("row", "field"),
    [
        ("O1,2024-01-01 10:00:00,a@x.io,USD,10,0,-1,completed", "net_amount"),
        ("O1,2024-01-01 10:00:00,a@x.io,USD,10,0,1.234,completed", "net_amount"),
        ("O1,2024-01-01 10:00:00,a@x.io,USD,10,0,abc,completed", "net_amount"),
        ("O1,2024-01-01 10:00:00,a@x.io,USD,10,0,NaN,completed", "net_amount"),
        ("O1,2024-01-01 10:00:00,a@x.io,USD,10,0,,completed", "net_amount"),
        ("O1,2024-01-01 10:00:00,a@x.io,JPY,10,0,10,completed", "currency"),
        ("O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,shipped", "status"),
    ],
)
def test_invalid_values_are_rejected_never_coerced(row: str, field: str) -> None:
    parsed = parse_orders(orders(row))
    assert parsed.records == ()
    assert field in parsed.rejected[0].fields
    assert parsed.rejected[0].row_number == 2


def test_trailing_zeros_count_as_two_decimals() -> None:
    o = parse_orders(orders("O1,2024-01-01 10:00:00,a@x.io,USD,10.500,0,10.500,completed")).records[0]
    assert o.net_amount == Decimal("10.50")


def test_invalid_payment_type_and_status() -> None:
    parsed = parse_payments(payments("T1,O1,01/01/2024 10:00,USD,10,0,10,chargeback,done"))
    assert set(parsed.rejected[0].fields) == {"type", "status"}


# ------------------------------------------------------------------ duplicates vs conflicts


def test_exact_duplicates_compare_parsed_values() -> None:
    parsed = parse_orders(
        orders(
            "O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed",
            "o1,2024-01-01 10:00:00,a@x.io,usd,10.0,0.00,10.00,completed",
        )
    )
    (record,) = parsed.records
    assert record.row_numbers == (2, 3)
    assert "duplicate_row" in record.dq_flags
    assert parsed.duplicate_rows_collapsed == 1 and parsed.conflicts == ()


def test_same_order_id_with_different_content_is_a_conflict() -> None:
    parsed = parse_orders(
        orders(
            "O1,2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed",
            "O1,2024-01-01 10:00:00,a@x.io,USD,20,0,20,completed",
            "O2,2024-01-01 10:00:00,b@x.io,USD,5,0,5,completed",
        )
    )
    assert [r.order_key for r in parsed.records] == ["O2"]
    (conflict,) = parsed.conflicts
    assert conflict.key == "O1" and conflict.row_numbers == (2, 3) and conflict.order_keys == ("O1",)


def test_payment_duplicates_and_conflicts() -> None:
    parsed = parse_payments(
        payments(
            "T1,O1,01/01/2024 10:00,USD,10,0,10,charge,settled",
            "T1,O1,01/01/2024 10:00,USD,10.00,0,10,charge,settled",
            "T2,O2,01/01/2024 10:00,USD,10,0,10,charge,settled",
            "T2,O3,01/01/2024 10:00,USD,11,0,11,charge,settled",
        )
    )
    assert [(r.transaction_ref, r.row_numbers) for r in parsed.records] == [("T1", (2, 3))]
    assert parsed.conflicts[0].order_keys == ("O2", "O3")


# ------------------------------------------------------------------ rejected-row linking


def test_rejected_row_with_readable_key_is_linked() -> None:
    parsed = parse_payments(payments("T1,O1,01/01/2024 10:00,USD,ten,0,10,charge,settled"))
    assert parsed.rejected[0].linked_order_key == "O1"


def test_rejected_row_with_blank_key_stays_in_report_only() -> None:
    parsed = parse_payments(payments("T1,,01/01/2024 10:00,USD,ten,0,10,charge,settled"))
    assert parsed.rejected[0].linked_order_key is None


def test_rejected_row_with_wrong_column_count_is_not_linked() -> None:
    parsed = parse_payments(payments("T1,O1,01/01/2024 10:00,USD,10,0,10,charge"))
    rejected = parsed.rejected[0]
    assert rejected.linked_order_key is None and "expected 9 columns" in rejected.reasons[0]


def test_order_rejected_because_of_its_key_is_not_linked() -> None:
    parsed = parse_orders(orders(",2024-01-01 10:00:00,a@x.io,USD,10,0,10,completed"))
    assert parsed.rejected[0].linked_order_key is None
