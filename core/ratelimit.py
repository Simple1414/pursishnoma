"""Small cache-backed fixed-window rate limiter."""
from __future__ import annotations

import time

from django.core.cache import cache

from .audit import client_ip, hash_ip


def parse_rate(rate: str) -> tuple[int, int]:
    count, seconds = rate.split("/")
    return int(count), int(seconds)


def is_rate_limited(request, scope: str, rate: str, key: str | None = None, increment: bool = True) -> bool:
    """Return True when the caller exceeded ``rate`` for ``scope``."""
    limit, window = parse_rate(rate)
    ident = key or hash_ip(client_ip(request)) or "anon"
    bucket = int(time.time() // window)
    cache_key = f"rl:{scope}:{ident}:{bucket}"
    if increment:
        added = cache.add(cache_key, 1, timeout=window)
        current = 1 if added else cache.incr(cache_key)
    else:
        current = cache.get(cache_key, 0)
    return current > limit
