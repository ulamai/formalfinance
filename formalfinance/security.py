from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class CIDRAllowlist:
    networks: tuple[str, ...]

    def allows(self, ip: str) -> bool:
        if not self.networks:
            return True
        candidate = ip_address(ip)
        for raw in self.networks:
            net = ip_network(raw, strict=False)
            if candidate in net:
                return True
        return False


class InMemoryRateLimiter:
    def __init__(self, rate_per_minute: int = 120, burst: int | None = None) -> None:
        self.rate_per_minute = max(1, int(rate_per_minute))
        self.rate_per_second = self.rate_per_minute / 60.0
        self.capacity = float(max(1, int(burst if burst is not None else self.rate_per_minute)))
        self._lock = Lock()
        self._state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = monotonic()
        with self._lock:
            tokens, updated_at = self._state.get(key, (self.capacity, now))
            elapsed = max(0.0, now - updated_at)
            tokens = min(self.capacity, tokens + elapsed * self.rate_per_second)
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False
            tokens -= 1.0
            self._state[key] = (tokens, now)
            return True
