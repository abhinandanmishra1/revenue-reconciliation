from types import SimpleNamespace

from app.security.client_ip import client_ip
from app.security.origin import request_origin
from app.security.ratelimit import Limits, SlidingWindow


def req(xff=None, peer="10.0.0.1"):
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_client_ip_uses_peer_without_trusted_hops():
    assert client_ip(req("1.1.1.1"), 0) == "10.0.0.1"


def test_client_ip_counts_from_the_right_and_ignores_forged_entries():
    assert client_ip(req("6.6.6.6, 1.1.1.1"), 1) == "1.1.1.1"
    assert client_ip(req("6.6.6.6, 1.1.1.1, 172.16.0.1"), 2) == "1.1.1.1"


def test_client_ip_falls_back_when_chain_is_short():
    assert client_ip(req("1.1.1.1"), 2) == "10.0.0.1"


def test_origin_prefers_origin_then_referer():
    assert request_origin({"origin": "https://a.io", "referer": "https://b.io/x"}) == "https://a.io"
    assert request_origin({"referer": "https://b.io/x?y=1"}) == "https://b.io"
    assert request_origin({}) is None


def test_sliding_window_expires_hits():
    now = [0.0]
    w = SlidingWindow(2, 60, lambda: now[0])
    assert w.take("k") is None and w.take("k") is None
    assert w.take("k") == 60
    now[0] = 60.0
    assert w.take("k") is None


def test_email_delay_doubles_and_caps():
    now = [0.0]
    limits = Limits(clock=lambda: now[0], sleep=lambda s: None)
    delays = []
    for _ in range(15):
        delays.append(limits.email_delay("a@x.io"))
        limits.email_failures.hit("a@x.io")
    assert delays[:10] == [0.0] * 10
    assert delays[10:] == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_client_ip_header_takes_precedence_when_present():
    request = req("54.1.1.1, 152.2.2.2")
    request.headers["x-vercel-forwarded-for"] = "203.0.113.7"
    assert client_ip(request, 2, "x-vercel-forwarded-for") == "203.0.113.7"


def test_client_ip_header_falls_back_to_forwarded_for_when_absent_or_blank():
    assert client_ip(req("1.1.1.1, 152.2.2.2"), 2, "x-vercel-forwarded-for") == "1.1.1.1"
    request = req("1.1.1.1, 152.2.2.2")
    request.headers["x-vercel-forwarded-for"] = "  "
    assert client_ip(request, 2, "x-vercel-forwarded-for") == "1.1.1.1"
