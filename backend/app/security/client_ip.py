from starlette.requests import Request


def client_ip(request: Request, trusted_hops: int, header: str = "") -> str:
    """Client address used to key per-IP rate limits.

    1. If `header` is configured and present, use it. The front proxy (Vercel)
       sets it to the browser's IP; this is needed because the next proxy
       (Railway) rewrites X-Forwarded-For to [Vercel's egress IP, edge IP].
    2. Otherwise count `trusted_hops` X-Forwarded-For entries from the right:
       each trusted proxy appends the address it received the request from, so
       anything further left is client-controlled and ignored.
    3. With neither, use the TCP peer.
    """
    if header:
        value = request.headers.get(header, "").split(",")[0].strip()
        if value:
            return value
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    entries = [e.strip() for e in forwarded.split(",") if e.strip()]
    if len(entries) < trusted_hops:
        return peer
    return entries[-trusted_hops]
