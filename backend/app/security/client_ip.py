from starlette.requests import Request


def client_ip(request: Request, trusted_hops: int) -> str:
    """Client address, counting `trusted_hops` X-Forwarded-For entries from the right.

    Each trusted proxy appends the address it received the request from, so the
    entry `trusted_hops` from the right is the one our nearest untrusted peer
    could not forge. Anything further left is client-controlled and ignored.
    """
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    header = request.headers.get("x-forwarded-for", "")
    entries = [e.strip() for e in header.split(",") if e.strip()]
    if len(entries) < trusted_hops:
        return peer
    return entries[-trusted_hops]
