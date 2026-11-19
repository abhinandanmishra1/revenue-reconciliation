import json
import threading
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Explanation
from app.explain import service
from app.explain.provider import GeminiProvider, ProviderError
from app.explain.schemas import ItemExplanation
from app.main import app
from app.routers.explain import get_provider
from tests.test_imports_api import upload

GOOD_ITEM = {
    "summary": "Customer was charged 117.81 USD for a 92.81 USD order.",
    "likely_cause": "A pricing or discount error at checkout.",
    "recommended_actions": ["Refund 25.00 USD to the customer."],
    "confidence": "medium",
}
GOOD_SUMMARY = {"headline": "Mostly reconciled.", "top_issues": ["One overcharge."], "recommended_priorities": ["Refund it."]}


class FakeProvider:
    def __init__(self, *responses, model="test-model"):
        self.model, self.responses, self.calls = model, list(responses), []

    def generate(self, system, user, schema, timeout):
        self.calls.append({"system": system, "user": user, "schema": schema, "timeout": timeout})
        response = self.responses.pop(0) if self.responses else self.responses_default
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response)

    responses_default = GOOD_ITEM


EVIDENCE = {"category": "OVERCHARGED", "reason": "Expected 92.81 USD but collected 117.81; overcharged by 25.00."}


# ------------------------------------------------------------------ service


def test_success_is_validated_llm_output():
    result = service.explain(FakeProvider(GOOD_ITEM), "task", EVIDENCE, ItemExplanation, service.item_fallback)
    assert result.source == "llm" and result.output == GOOD_ITEM


def test_timeout_then_success_retries_once():
    provider = FakeProvider(ProviderError("timeout"), GOOD_ITEM)
    result = service.explain(provider, "task", EVIDENCE, ItemExplanation, service.item_fallback)
    assert result.source == "llm" and len(provider.calls) == 2


@pytest.mark.parametrize(
    "bad",
    [
        ProviderError("timeout"),
        "{not json",
        {**GOOD_ITEM, "confidence": "certain"},  # schema violation
        {**GOOD_ITEM, "recommended_actions": []},
        {**GOOD_ITEM, "summary": "x" * 601},
        {**GOOD_ITEM, "extra": "field"},
    ],
)
def test_two_bad_responses_fall_back_deterministically(bad):
    provider = FakeProvider(bad, bad)
    result = service.explain(provider, "task", EVIDENCE, ItemExplanation, service.item_fallback)
    assert result.source == "fallback" and len(provider.calls) == 2
    assert result.output["summary"] == EVIDENCE["reason"] and result.output["confidence"] == "low"


def test_no_provider_configured_falls_back():
    assert service.explain(None, "t", EVIDENCE, ItemExplanation, service.item_fallback).source == "fallback"


def test_both_attempts_fit_in_the_25_second_budget():
    now = [0.0]

    class SlowProvider(FakeProvider):
        def generate(self, system, user, schema, timeout):
            self.calls.append({"timeout": timeout})
            now[0] += timeout  # each attempt uses its whole timeout
            raise ProviderError("timeout")

    provider = SlowProvider()
    result = service.explain(provider, "t", EVIDENCE, ItemExplanation, service.item_fallback, clock=lambda: now[0])
    assert result.source == "fallback" and len(provider.calls) == 2
    assert now[0] <= 24.0 < 25


# ------------------------------------------------------------------ REST adapter


def _gemini(handler) -> GeminiProvider:
    return GeminiProvider("test-key", "gemini-3-flash-preview", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_adapter_sends_schema_and_key_and_skips_thought_parts():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers["x-goog-api-key"], json.loads(request.content)
        parts = [{"text": "thinking...", "thought": True}, {"text": json.dumps(GOOD_ITEM)}]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}]})

    text = _gemini(handler).generate("sys", "user", {"type": "object"}, 5)
    assert json.loads(text) == GOOD_ITEM
    assert seen["url"].endswith("/models/gemini-3-flash-preview:generateContent") and seen["key"] == "test-key"
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["responseJsonSchema"] == {"type": "object"}
    assert "temperature" not in config and config["thinkingConfig"] == {"thinkingLevel": "low"}


def test_non_gemini_3_model_uses_low_temperature():
    assert GeminiProvider("k", "other-model")._generation_config({})["temperature"] == 0.2


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
        lambda r: httpx.Response(503, json={}),
        lambda r: httpx.Response(200, json={"candidates": []}),
        lambda r: httpx.Response(200, text="not json"),
    ],
)
def test_adapter_failures_raise_provider_error(handler):
    with pytest.raises(ProviderError):
        _gemini(handler).generate("s", "u", {}, 5)


# ------------------------------------------------------------------ API: cache, fallback, isolation


@pytest.fixture
def provider():
    fake = FakeProvider()
    app.dependency_overrides[get_provider] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_provider, None)


def _first_item(client, import_id, q="B-2"):
    return client.get(f"/api/imports/{import_id}/items", params={"q": q}).json()["items"][0]["id"]


def _explanations(db) -> int:
    with Session(db) as s:
        return s.scalar(select(func.count()).select_from(Explanation))


def test_explain_item_caches_one_row(user_client, db, provider):
    import_id = upload(user_client).json()["import"]["id"]
    item_id = _first_item(user_client, import_id)
    first = user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain").json()["explanation"]
    second = user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain").json()["explanation"]
    assert first["source"] == "llm" and first["cached"] is False and second["cached"] is True
    assert first["output"] == second["output"] == GOOD_ITEM
    assert len(provider.calls) == 1 and _explanations(db) == 1
    detail = user_client.get(f"/api/imports/{import_id}/items/{item_id}").json()
    assert detail["explanation"]["output"] == GOOD_ITEM


def test_prompt_has_no_personal_data_and_wraps_evidence_as_data(user_client, provider):
    import_id = upload(user_client).json()["import"]["id"]
    item_id = _first_item(user_client, import_id)
    user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain")
    prompt = provider.calls[0]["user"]
    assert "b@x.io" not in prompt and "@" not in prompt
    assert "<data>" in prompt and "117.81" in prompt
    assert "untrusted" in provider.calls[0]["system"]


def test_fallback_is_returned_and_not_cached(user_client, db):
    failing = FakeProvider(ProviderError("timeout"), ProviderError("timeout"))
    app.dependency_overrides[get_provider] = lambda: failing
    try:
        import_id = upload(user_client).json()["import"]["id"]
        item_id = _first_item(user_client, import_id)
        r = user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain")
    finally:
        app.dependency_overrides.pop(get_provider, None)
    assert r.status_code == 200 and r.json()["explanation"]["source"] == "fallback"
    assert _explanations(db) == 0


def test_model_change_creates_a_new_explanation(user_client, db, provider):
    import_id = upload(user_client).json()["import"]["id"]
    item_id = _first_item(user_client, import_id)
    user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain")
    other = get_settings().model_copy(update={"gemini_model": "another-model"})
    app.dependency_overrides[get_settings] = lambda: other
    try:
        r = user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain").json()
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert r["explanation"]["cached"] is False and r["explanation"]["model"] == "another-model"
    assert len(provider.calls) == 2 and _explanations(db) == 2


def test_summary_explanation_is_cached_once_and_shown_on_import(user_client, db):
    fake = FakeProvider(GOOD_SUMMARY, GOOD_SUMMARY)
    app.dependency_overrides[get_provider] = lambda: fake
    try:
        import_id = upload(user_client).json()["import"]["id"]
        a = user_client.post(f"/api/imports/{import_id}/summary/explain").json()["explanation"]
        b = user_client.post(f"/api/imports/{import_id}/summary/explain").json()["explanation"]
    finally:
        app.dependency_overrides.pop(get_provider, None)
    assert a["output"] == b["output"] == GOOD_SUMMARY and b["cached"] is True
    assert len(fake.calls) == 1 and _explanations(db) == 1  # NULLS NOT DISTINCT keeps one summary row
    assert user_client.get(f"/api/imports/{import_id}").json()["summary_explanation"]["output"] == GOOD_SUMMARY


def test_concurrent_explains_store_one_row(client, db, provider):
    from fastapi.testclient import TestClient

    from tests.conftest import signup

    signup(client)
    import_id = upload(client).json()["import"]["id"]
    item_id = _first_item(client, import_id)
    cookies, outputs, barrier = dict(client.cookies), [], threading.Barrier(3)

    def worker():
        with TestClient(app, headers={"Origin": "http://localhost:3000"}, cookies=cookies) as c:
            barrier.wait()
            outputs.append(c.post(f"/api/imports/{import_id}/items/{item_id}/explain").json()["explanation"]["output"])

    threads = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outputs == [GOOD_ITEM] * 3 and _explanations(db) == 1


def test_explain_is_rate_limited_per_user(user_client, provider):
    import_id = upload(user_client).json()["import"]["id"]
    item_id = _first_item(user_client, import_id)
    codes = [user_client.post(f"/api/imports/{import_id}/items/{item_id}/explain").status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]


def test_explain_other_users_item_is_404(client, db, provider):
    from tests.conftest import signup

    signup(client, "owner@example.com")
    import_id = upload(client).json()["import"]["id"]
    item_id = _first_item(client, import_id)
    client.cookies.clear()
    signup(client, "intruder@example.com")
    assert client.post(f"/api/imports/{import_id}/items/{item_id}/explain").status_code == 404
    assert client.post(f"/api/imports/{import_id}/summary/explain").status_code == 404
    assert provider.calls == []


def test_cross_import_explanation_is_blocked_by_foreign_key(user_client, db):
    first = upload(user_client).json()["import"]["id"]
    second = upload(user_client, orders=b"order_id,currency,net_amount,status\nQ,USD,1,completed\n").json()["import"]["id"]
    item_id = _first_item(user_client, first)
    with Session(db) as s:
        s.add(Explanation(import_id=uuid.UUID(second), item_id=uuid.UUID(item_id), prompt_version="v1", model="m", output={}))
        with pytest.raises(IntegrityError):
            s.commit()
