from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import json


@dataclass
class TraceLogger:
    path: str

    def __post_init__(self) -> None:
        self._index = 0
        self._fp = open(self.path, "w", encoding="utf-8")

    def log(self, event_type: str, **payload: Any) -> None:
        self._index += 1
        line = {
            "index": self._index,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **payload,
        }
        self._fp.write(json.dumps(line, sort_keys=True) + "\n")
        self._fp.flush()

    def close(self) -> None:
        self._fp.close()

    def __enter__(self) -> "TraceLogger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


class NoopTraceLogger:
    def log(self, event_type: str, **payload: Any) -> None:
        del event_type
        del payload
