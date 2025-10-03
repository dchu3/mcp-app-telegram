from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional


class RateLimitExceeded(RuntimeError):
    """Raised when rate limits cannot be satisfied within a timeout."""


@dataclass(slots=True)
class TokenBucket:
    """Token bucket limiter supporting asynchronous acquisition."""

    rate_per_second: float
    capacity: float
    jitter_ratio: float = 0.1
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0, *, timeout: Optional[float] = None) -> float:
        """Acquire a number of tokens, waiting if necessary.

        Returns the amount of time spent waiting for capacity.
        """

        start = time.monotonic()
        total_wait = 0.0
        while True:
            async with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return total_wait
                wait_seconds = (tokens - self._tokens) / self.rate_per_second
                wait_seconds *= 1.0 + random.uniform(-self.jitter_ratio, self.jitter_ratio)
                wait_seconds = max(0.0, wait_seconds)
            now = time.monotonic()
            if timeout is not None and (now - start + wait_seconds) > timeout:
                raise RateLimitExceeded("Token bucket timeout exceeded")
            if wait_seconds <= 0:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(wait_seconds)
            total_wait += wait_seconds

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_refill)
        if elapsed <= 0:
            return
        refill = elapsed * self.rate_per_second
        self._tokens = min(self.capacity, self._tokens + refill)
        self._last_refill = now


class RequestRateLimiter:
    """Combined global/per-host rate limiter employing token buckets."""

    def __init__(
        self,
        *,
        global_rate_per_min: int,
        per_host_rate_per_min: Mapping[str, int],
        burst_ratio: float = 0.2,
        jitter_ratio: float = 0.1,
    ) -> None:
        if global_rate_per_min <= 0:
            raise ValueError("global_rate_per_min must be positive")
        self._global_bucket = TokenBucket(
            rate_per_second=global_rate_per_min / 60.0,
            capacity=global_rate_per_min * (1.0 + burst_ratio),
            jitter_ratio=jitter_ratio,
        )
        self._host_buckets: Dict[str, TokenBucket] = {
            host: TokenBucket(
                rate_per_second=max(1, rate // 60) if rate >= 60 else rate / 60.0,
                capacity=rate * (1.0 + burst_ratio),
                jitter_ratio=jitter_ratio,
            )
            for host, rate in per_host_rate_per_min.items()
        }
        self._jitter_ratio = jitter_ratio

    async def acquire(self, host: Optional[str], *, tokens: float = 1.0, timeout: Optional[float] = None) -> float:
        waited = await self._global_bucket.acquire(tokens, timeout=timeout)
        if host and host in self._host_buckets:
            waited += await self._host_buckets[host].acquire(tokens, timeout=timeout)
        return waited

    def register_host(self, host: str, rate_per_min: int) -> None:
        self._host_buckets[host] = TokenBucket(
            rate_per_second=rate_per_min / 60.0,
            capacity=rate_per_min * 1.2,
            jitter_ratio=self._jitter_ratio,
        )
