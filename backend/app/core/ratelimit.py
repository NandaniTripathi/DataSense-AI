"""Tiny in-memory sliding-window rate limiter (per client key).

Good enough for a single-process deployment. For multiple workers, swap the
store for Redis; the interface stays the same.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import get_settings

_hits: dict[str, deque[float]] = defaultdict(deque)


def reset() -> None:
    _hits.clear()


def check_chat_rate_limit(request: Request) -> None:
    limit = get_settings().chat_rate_limit_per_min
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _hits[key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Too many questions. Limit is {limit} per minute; try again shortly.",
        )
    window.append(now)
