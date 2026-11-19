import threading
import time

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Import, Order, Payment, ReconciliationItem, User
from app.services import imports as import_service
from app.services.imports import UploadedFile, create_import
from tests.conftest import signup

ORDERS = b"""order_id,order_date,customer_email,currency,gross_amount,discount,net_amount,status
A-1,2024-03-01 10:00:00,a@x.io,USD,100,0,100,completed
B-2,2024-03-01 10:00:00,b@x.io,USD,92.81,0,92.81,completed
C-3,2024-03-01 10:00:00,c@x.io,EUR,50,0,50,completed
D-4,2024-03-01 10:00:00,,USD,10,0,10,cancelled
E-5,2024-03-01 10:00:00,e@x.io,USD,bad,0,10,completed
"""
PAYMENTS = b"""transaction_ref,order_reference,processed_at,currency,amount,fee,net_settled,type,status
T1,A-1,01/03/2024 10:05,USD,100,3,97,charge,settled
T2,B-2,01/03/2024 10:05,USD,117.81,3,114.81,charge,settled
T3,ZZ-9,01/03/2024 10:05,USD,15,0,15,charge,settled
T4,,01/03/2024 10:05,USD,7,0,7,charge,settled
"""


def upload(client, orders=ORDERS, payments=PAYMENTS, names=("orders.csv", "payments.csv")):
    return client.post(
        "/api/imports",
        files={"orders": (names[0], orders, "text/csv"), "payments": (names[1], payments, "text/csv")},
    )


def counts(engine) -> dict:
    with Session(engine) as s:
        return {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in (Import, Order, Payment, ReconciliationItem)}


def test_upload_stores_everything_and_returns_summary(user_client, db):
    r = upload(user_client)
    assert r.status_code == 201
    body = r.json()
    assert body["already_imported"] is False
    summary = body["import"]["summary"]
    assert summary["validation"]["orders"]["rejected_count"] == 1
    assert summary["metrics"]["by_currency"]["USD"]["at_risk"]["over_collected"] == "47.00"  # 25 + 15 + 7
    assert counts(db) == {"imports": 1, "orders": 4, "payments": 4, "reconciliation_items": 7}


def test_same_upload_twice_returns_existing_import(user_client, db):
    first = upload(user_client).json()["import"]["id"]
    r = upload(user_client, names=("renamed.csv", "other.csv"))
    assert r.status_code == 200 and r.json()["already_imported"] is True
    assert r.json()["import"]["id"] == first
    assert counts(db)["imports"] == 1 and counts(db)["orders"] == 4


def test_same_files_for_two_users_are_separate_imports(client, db):
    signup(client, "one@example.com")
    upload(client)
    client.cookies.clear()
    signup(client, "two@example.com")
    assert upload(client).status_code == 201
    assert counts(db)["imports"] == 2


def test_concurrent_identical_uploads_create_one_import(client, db, monkeypatch):
    signup(client)
    with Session(db) as s:
        user_id = s.scalar(select(User.id))

    real = import_service.insert_children

    def slow_insert(*args, **kwargs):
        time.sleep(0.3)  # hold the first transaction open while the second arrives
        return real(*args, **kwargs)

    monkeypatch.setattr(import_service, "insert_children", slow_insert)
    results, barrier = [], threading.Barrier(2)

    def worker():
        with Session(db) as s:
            barrier.wait()
            results.append(create_import(s, user_id, UploadedFile("o.csv", ORDERS), UploadedFile("p.csv", PAYMENTS)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r.created for r in results) == [False, True]
    assert results[0].import_id == results[1].import_id
    assert counts(db) == {"imports": 1, "orders": 4, "payments": 4, "reconciliation_items": 7}


def test_mid_import_failure_leaves_no_rows(user_client, db, monkeypatch):
    real_bulk = import_service._bulk

    def failing_bulk(session, model, rows):
        if model is ReconciliationItem:
            raise RuntimeError("simulated crash after orders and payments were inserted")
        return real_bulk(session, model, rows)

    monkeypatch.setattr(import_service, "_bulk", failing_bulk)
    r = upload(user_client)
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Something went wrong."}}
    assert counts(db) == {"imports": 0, "orders": 0, "payments": 0, "reconciliation_items": 0}
    monkeypatch.setattr(import_service, "_bulk", real_bulk)
    assert upload(user_client).status_code == 201  # a retry succeeds cleanly


def test_concurrent_duplicate_signup_creates_one_user(client, db):
    from app.main import app
    from fastapi.testclient import TestClient

    codes, barrier = [], threading.Barrier(2)

    def worker():
        with TestClient(app, headers={"Origin": "http://localhost:3000"}) as c:
            barrier.wait()
            codes.append(c.post("/api/auth/signup", json={"email": "race@example.com", "password": "password1"}).status_code)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [201, 409]
    with Session(db) as s:
        assert s.scalar(select(func.count()).select_from(User)) == 1


# ------------------------------------------------------------------ file errors


def test_swapped_files_are_422_and_nothing_is_saved(user_client, db):
    r = upload(user_client, orders=PAYMENTS, payments=ORDERS)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "wrong_file" and r.json()["error"]["details"] == {"file": "orders"}
    assert counts(db)["imports"] == 0


def test_oversize_file_is_413(user_client, db):
    big = ORDERS + b"x" * (2 * 1024 * 1024)
    r = upload(user_client, orders=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_missing_file_part_is_422(user_client):
    r = user_client.post("/api/imports", files={"orders": ("o.csv", ORDERS, "text/csv")})
    assert r.status_code == 422


def test_header_only_files_import_with_no_records(user_client):
    r = upload(user_client, orders=ORDERS.splitlines(keepends=True)[0], payments=PAYMENTS.splitlines(keepends=True)[0])
    assert r.status_code == 201
    assert r.json()["import"]["summary"]["metrics"]["totals"]["items"] == 0


# ------------------------------------------------------------------ isolation


def test_other_users_data_is_404(client, db):
    signup(client, "owner@example.com")
    import_id = upload(client).json()["import"]["id"]
    item_id = client.get(f"/api/imports/{import_id}/items").json()["items"][0]["id"]
    client.cookies.clear()
    signup(client, "intruder@example.com")
    assert client.get("/api/imports").json() == {"imports": []}
    for path in (f"/api/imports/{import_id}", f"/api/imports/{import_id}/items", f"/api/imports/{import_id}/items/{item_id}"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_item_from_another_import_is_404(user_client, db):
    first = upload(user_client).json()["import"]["id"]
    second = upload(user_client, orders=ORDERS.replace(b"A-1", b"A-9")).json()["import"]["id"]
    item_id = user_client.get(f"/api/imports/{first}/items").json()["items"][0]["id"]
    assert user_client.get(f"/api/imports/{second}/items/{item_id}").status_code == 404


def test_unauthenticated_routes_are_401(client):
    for path in ("/api/imports", "/api/auth/me"):
        assert client.get(path).status_code == 401


# ------------------------------------------------------------------ dashboard queries


def test_history_is_newest_first_without_raw_files(user_client, db):
    upload(user_client)
    upload(user_client, orders=ORDERS.replace(b"A-1", b"A-9"))
    with db.begin() as conn:
        conn.execute(text("UPDATE imports SET created_at = created_at - interval '1 hour' WHERE orders_filename = 'orders.csv' AND id = (SELECT id FROM imports ORDER BY created_at LIMIT 1)"))
    rows = user_client.get("/api/imports").json()["imports"]
    assert len(rows) == 2 and rows[0]["created_at"] > rows[1]["created_at"]
    assert "orders_raw" not in rows[0] and rows[0]["rejected_rows"] == 1


def test_items_default_hides_matched_and_sorts_by_priority(user_client):
    import_id = upload(user_client).json()["import"]["id"]
    body = user_client.get(f"/api/imports/{import_id}/items").json()
    categories = [i["category"] for i in body["items"]]
    assert "MATCHED" not in categories and body["total"] == len(categories) == 5
    # All high severity, so amount at risk breaks the tie; NEEDS_REVIEW has 0 at risk.
    assert categories[:3] == ["MISSING_PAYMENT", "OVERCHARGED", "NEEDS_REVIEW"]
    matched = user_client.get(f"/api/imports/{import_id}/items", params={"category": "MATCHED"}).json()
    assert [i["order_key"] for i in matched["items"]] == ["A-1", "D-4"]


def test_items_filters_search_and_sort(user_client):
    import_id = upload(user_client).json()["import"]["id"]
    get = lambda **p: user_client.get(f"/api/imports/{import_id}/items", params=p).json()  # noqa: E731
    assert [i["order_key"] for i in get(direction="over_collected", sort="amount_at_risk")["items"]] == ["B-2", "ZZ-9", None]
    assert [i["category"] for i in get(category=["OVERCHARGED", "MATCHED"])["items"]] == ["OVERCHARGED", "MATCHED", "MATCHED"]
    assert [i["order_key"] for i in get(q="b@x")["items"]] == ["B-2"]
    assert [i["item_key"] for i in get(q="t4")["items"]] == ["T:T4"]
    assert [i["order_key"] for i in get(q="t2")["items"]] == ["B-2"]
    assert get(q="%")["total"] == 0
    assert get(currency="EUR")["items"][0]["category"] == "MISSING_PAYMENT"
    assert get(has_dq=True)["total"] >= 1
    assert get(page_size=1, page=2)["items"][0]["order_key"] is not None


def test_unknown_filter_value_is_422(user_client):
    import_id = upload(user_client).json()["import"]["id"]
    r = user_client.get(f"/api/imports/{import_id}/items", params={"category": "NOPE"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_item_detail_includes_evidence(user_client):
    import_id = upload(user_client).json()["import"]["id"]
    item = user_client.get(f"/api/imports/{import_id}/items", params={"q": "B-2"}).json()["items"][0]
    detail = user_client.get(f"/api/imports/{import_id}/items/{item['id']}").json()
    evidence = detail["item"]["evidence"]
    assert evidence["order"]["row_numbers"] == [3]
    assert evidence["events"][0]["transaction_ref"] == "T2"
    assert detail["item"]["amount_at_risk"] == "25.00" and detail["item"]["disputed_value"] == "92.81"
