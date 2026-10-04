"""In-process sliding-window rate limiter.

NOTE: state is per worker process. The nginx `limit_req` zones in frontend/nginx.conf are the
authoritative limit across workers; this limiter is defence in depth (and protects dev setups).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.config import get_settings
from app.errors import TooManyRequests

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)
_last_gc = 0.0


def reset() -> None:
    with _lock:
        _hits.clear()


def hit(key: str, limit: int, window: float = 60.0) -> None:
    if not get_settings().rate_limit_enabled:
        return
    global _last_gc
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(window - (now - q[0])) + 1)
            raise TooManyRequests("Too many requests. Please wait a moment and try again.", headers={"Retry-After": str(retry)})
        q.append(now)
        if now - _last_gc > 300:  # opportunistic garbage collection
            _last_gc = now
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 600]:
                del _hits[k]
