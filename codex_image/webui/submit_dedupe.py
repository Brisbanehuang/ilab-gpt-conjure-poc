from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

SUBMIT_DEDUPE_WINDOW_SECONDS = 8.0


class SubmitDedupeCache:
    def __init__(self, window_seconds: float = SUBMIT_DEDUPE_WINDOW_SECONDS) -> None:
        self.window_seconds = window_seconds
        self._lock = threading.RLock()
        self._entries: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._entries.get(key)
            if entry is None:
                return None
            return entry[0]

    def put(self, key: str, task_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._entries[key] = (task_id, now)

    def put_if_absent(self, key: str, task_id: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._entries.get(key)
            if entry is not None:
                return entry[0]
            self._entries[key] = (task_id, now)
            return None

    def _prune(self, now: float) -> None:
        expired = [key for key, (_, created_at) in self._entries.items() if now - created_at > self.window_seconds]
        for key in expired:
            self._entries.pop(key, None)


def submit_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
