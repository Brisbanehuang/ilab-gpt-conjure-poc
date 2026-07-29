from __future__ import annotations

import os
import socket
import ssl
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib import error, request

DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _request_timeout_seconds(value: float | None = None) -> float:
    if value is not None:
        return float(value)
    raw = os.getenv("CODEX_IMAGE_REQUEST_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    return parsed if parsed > 0 else DEFAULT_REQUEST_TIMEOUT_SECONDS


def _format_elapsed_seconds(seconds: float) -> str:
    return f"{max(0.0, seconds):.2f}".rstrip("0").rstrip(".")


@lru_cache(maxsize=1)
def _https_ssl_context() -> ssl.SSLContext | None:
    if os.getenv("SSL_CERT_FILE") or os.getenv("SSL_CERT_DIR"):
        return ssl.create_default_context()

    try:
        import certifi  # type: ignore[import-not-found]
    except Exception:
        return None

    ca_file = Path(certifi.where())
    if not ca_file.is_file():
        return None
    return ssl.create_default_context(cafile=str(ca_file))


@dataclass
class HTTPResponse:
    status: int
    body: bytes
    headers: dict[str, str]


class Transport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HTTPResponse: ...


class UrllibTransport:
    def __init__(self, *, timeout: float | None = None) -> None:
        self.timeout = _request_timeout_seconds(timeout)

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
    ) -> HTTPResponse:
        request_headers = dict(headers)
        if not any(key.lower() == "user-agent" for key in request_headers):
            request_headers["User-Agent"] = DEFAULT_USER_AGENT
        req = request.Request(url=url, data=body, headers=request_headers, method=method)
        started_at = time.monotonic()
        try:
            context = _https_ssl_context() if url.lower().startswith("https://") else None
            with request.urlopen(req, timeout=self.timeout, context=context) as response:
                return HTTPResponse(
                    status=getattr(response, "status", response.getcode()),
                    body=response.read(),
                    headers=dict(response.headers.items()),
                )
        except error.HTTPError as exc:
            return HTTPResponse(
                status=exc.code,
                body=exc.read(),
                headers=dict(exc.headers.items()),
            )
        except socket.timeout as exc:
            elapsed = _format_elapsed_seconds(time.monotonic() - started_at)
            raise TimeoutError(f"HTTP request timed out after {elapsed}s (timeout limit {self.timeout:g}s)") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                elapsed = _format_elapsed_seconds(time.monotonic() - started_at)
                raise TimeoutError(
                    f"HTTP request timed out after {elapsed}s (timeout limit {self.timeout:g}s): {exc.reason}"
                ) from exc
            raise
