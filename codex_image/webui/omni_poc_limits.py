from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Protocol


class SizedUpload(Protocol):
    filename: str | None
    size: int | None


@dataclass
class FixedWindowRateLimiter:
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= self.window_seconds:
            start, count = now, 0
        if count >= self.limit:
            self._buckets[key] = (start, count)
            return False
        self._buckets[key] = (start, count + 1)
        return True


def validate_upload_limits(files: Iterable[SizedUpload], *, max_files: int, max_bytes_each: int) -> None:
    items = [item for item in files if item is not None]
    if len(items) > max_files:
        raise ValueError(f"最多只能上传 {max_files} 张参考图")
    for item in items:
        size = getattr(item, "size", None)
        if size is not None and int(size) > max_bytes_each:
            raise ValueError(f"{getattr(item, 'filename', '上传文件')} 超过单文件大小限制")
