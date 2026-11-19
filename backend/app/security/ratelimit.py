"""In-memory sliding-window counters (single API instance; see design 15)."""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

FAILED_LOGIN_LIMIT = 5  # per client IP + email
FAILED_LOGIN_WINDOW = 15 * 60
AUTH_IP_LIMIT = 20  # login + signup, per client IP
AUTH_IP_WINDOW = 60
EMAIL_SLOWDOWN_AFTER = 10  # failures per email across all IPs
EMAIL_SLOWDOWN_WINDOW = 15 * 60
EMAIL_SLOWDOWN_MAX = 5.0
EXPLAIN_LIMIT = 10  # per user
EXPLAIN_WINDOW = 60


class SlidingWindow:
    def __init__(self, limit: int, window: float, clock: Callable[[], float]) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        return hits

    def count(self, key: str) -> int:
        with self._lock:
            return len(self._prune(key, self.clock()))

    def retry_after(self, key: str) -> int | None:
        """Seconds until the next attempt is allowed, or None if allowed now."""
        with self._lock:
            now = self.clock()
            hits = self._prune(key, now)
            if len(hits) < self.limit:
                return None
            return max(1, math.ceil(hits[0] + self.window - now))

    def hit(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune(key, now).append(now)

    def take(self, key: str) -> int | None:
        """Record a hit if allowed; otherwise return the Retry-After seconds."""
        with self._lock:
            now = self.clock()
            hits = self._prune(key, now)
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


class Limits:
    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.clock, self.sleep = clock, sleep
        self.auth_ip = SlidingWindow(AUTH_IP_LIMIT, AUTH_IP_WINDOW, clock)
        self.failed_login = SlidingWindow(FAILED_LOGIN_LIMIT, FAILED_LOGIN_WINDOW, clock)
        self.email_failures = SlidingWindow(10**9, EMAIL_SLOWDOWN_WINDOW, clock)
        self.explain = SlidingWindow(EXPLAIN_LIMIT, EXPLAIN_WINDOW, clock)

    def email_delay(self, email: str) -> float:
        """1 s after 10 recent failures, doubling up to 5 s. Never a refusal."""
        failures = self.email_failures.count(email)
        if failures < EMAIL_SLOWDOWN_AFTER:
            return 0.0
        return min(2.0 ** (failures - EMAIL_SLOWDOWN_AFTER), EMAIL_SLOWDOWN_MAX)


limits = Limits()
