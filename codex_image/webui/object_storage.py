from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class StoredObject:
    driver: str
    key: str
    size: int
    content_type: str


@dataclass(frozen=True)
class ObjectStorageConfig:
    driver: str
    account_id: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = ""
    region: str = "auto"
    temp_image_ttl_hours: int = 720
    saved_image_ttl_days: int = 30
    cleanup_interval_seconds: int = 3600
    max_bytes_per_user: int | None = None
    request_timeout_seconds: float = 60.0
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 0.5


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


def owner_id_for_session(session: Any) -> str:
    return f"user_{int(session.sub2api_user_id)}"


def owner_id_from_params(params: Mapping[str, Any]) -> str:
    raw_user_id = params.get("sub2api_user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise ValueError("Sub2API user id is required") from None
    if user_id <= 0:
        raise ValueError("Sub2API user id is required")
    return f"user_{user_id}"


def output_object_key(*, owner_id: str, task_id: str, title: str, index: int, ext: str) -> str:
    clean_task_id = str(task_id or "").strip()
    date = _task_date(clean_task_id)
    compact_day = date[5:7] + date[8:10]
    time_part = clean_task_id[8:14] if len(clean_task_id) >= 14 else "000000"
    safe_ext = _safe_ext(ext)
    safe_title = _safe_title(title, fallback="output")
    return f"users/{_safe_owner(owner_id)}/images/{date[:4]}/{compact_day}/{time_part}-{clean_task_id}/outputs/{int(index):02d}-{safe_title}.{safe_ext}"


def input_object_key(*, owner_id: str, task_id: str, filename: str, index: int, ext: str) -> str:
    clean_task_id = str(task_id or "").strip()
    date = _task_date(clean_task_id)
    compact_day = date[5:7] + date[8:10]
    time_part = clean_task_id[8:14] if len(clean_task_id) >= 14 else "000000"
    safe_ext = _safe_ext(ext)
    safe_title = _safe_title(Path(str(filename or "")).stem, fallback="input")
    return f"users/{_safe_owner(owner_id)}/images/{date[:4]}/{compact_day}/{time_part}-{clean_task_id}/inputs/{int(index):02d}-{safe_title}.{safe_ext}"


def content_type_for_format(output_format: str) -> str:
    suffix = _safe_ext(output_format)
    if suffix == "jpg":
        suffix = "jpeg"
    if suffix in {"png", "jpeg", "webp", "gif"}:
        return f"image/{suffix}"
    return "application/octet-stream"


def retention_expires_at(created_at: str, *, policy: str, config: ObjectStorageConfig | None = None) -> str:
    storage_config = config or load_object_storage_config()
    base = _parse_datetime(created_at) or datetime.now(UTC)
    expires_at = base + timedelta(days=storage_config.saved_image_ttl_days)
    return expires_at.isoformat().replace("+00:00", "Z")


def load_object_storage_config(env: Mapping[str, str] | None = None) -> ObjectStorageConfig:
    payload = os.environ if env is None else env
    driver = str(payload.get("OMNI_OBJECT_STORAGE_DRIVER") or "local").strip().lower() or "local"
    return ObjectStorageConfig(
        driver=driver,
        account_id=str(payload.get("R2_ACCOUNT_ID") or "").strip(),
        access_key_id=str(payload.get("R2_ACCESS_KEY_ID") or "").strip(),
        secret_access_key=str(payload.get("R2_SECRET_ACCESS_KEY") or "").strip(),
        bucket=str(payload.get("R2_BUCKET") or "").strip(),
        region=str(payload.get("R2_REGION") or "auto").strip() or "auto",
        temp_image_ttl_hours=_int_env(payload.get("OMNI_TEMP_IMAGE_TTL_HOURS"), 720),
        saved_image_ttl_days=_int_env(payload.get("OMNI_SAVED_IMAGE_TTL_DAYS"), 30),
        cleanup_interval_seconds=_int_env(payload.get("OMNI_R2_CLEANUP_INTERVAL_SECONDS"), 3600),
        max_bytes_per_user=_optional_int_env(payload.get("OMNI_R2_MAX_BYTES_PER_USER")),
        request_timeout_seconds=_float_env(payload.get("OMNI_R2_REQUEST_TIMEOUT_SECONDS"), 60.0),
        retry_attempts=_int_env(payload.get("OMNI_R2_RETRY_ATTEMPTS"), 3),
        retry_base_delay_seconds=_float_env(payload.get("OMNI_R2_RETRY_BASE_DELAY_SECONDS"), 0.5),
    )


def object_storage_from_env(env: Mapping[str, str] | None = None) -> ObjectStorage | None:
    config = load_object_storage_config(env)
    if config.driver != "r2":
        return None
    if not (config.account_id and config.access_key_id and config.secret_access_key and config.bucket):
        return None
    return R2ObjectStorage(config)


class R2ObjectStorage:
    def __init__(self, config: ObjectStorageConfig) -> None:
        self.config = config
        self.endpoint = f"https://{config.account_id}.r2.cloudflarestorage.com"

    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        headers = {"content-type": content_type}
        response = await self._request_with_retries("PUT", key, data, headers)
        response.raise_for_status()
        return StoredObject(driver="r2", key=key, size=len(data), content_type=content_type)

    async def get(self, key: str) -> bytes:
        response = await self._request_with_retries("GET", key, b"", {})
        response.raise_for_status()
        return response.content

    async def delete(self, key: str) -> None:
        response = await self._request_with_retries("DELETE", key, b"", {})
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def _request_with_retries(
        self,
        method: str,
        key: str,
        body: bytes,
        headers: dict[str, str],
    ) -> httpx.Response:
        attempts = max(1, int(self.config.retry_attempts))
        timeout = float(max(1.0, self.config.request_timeout_seconds))
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    signed_headers = self._signed_headers(method, key, body, headers)
                    if method == "PUT":
                        response = await client.put(self._url(key), content=body, headers=signed_headers)
                    elif method == "GET":
                        response = await client.get(self._url(key), headers=signed_headers)
                    elif method == "DELETE":
                        response = await client.delete(self._url(key), headers=signed_headers)
                    else:  # pragma: no cover - private helper only receives known methods
                        raise ValueError(f"Unsupported R2 method: {method}")
                if _is_retryable_status(response.status_code) and attempt < attempts - 1:
                    await self._sleep_before_retry(attempt)
                    continue
                return response
            except httpx.TransportError:
                if attempt >= attempts - 1:
                    raise
                await self._sleep_before_retry(attempt)
        raise RuntimeError("R2 retry loop exhausted")

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = max(0.0, float(self.config.retry_base_delay_seconds)) * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    def _url(self, key: str) -> str:
        return f"{self.endpoint}/{quote(self.config.bucket, safe='')}/{quote(key, safe='/')}"

    def _signed_headers(self, method: str, key: str, body: bytes, headers: dict[str, str]) -> dict[str, str]:
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        host = f"{self.config.account_id}.r2.cloudflarestorage.com"
        payload_hash = hashlib.sha256(body).hexdigest()
        signed = {k.lower(): v for k, v in headers.items()}
        signed.update({"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date})
        signed_header_names = ";".join(sorted(signed))
        canonical_headers = "".join(f"{name}:{signed[name].strip()}\n" for name in sorted(signed))
        canonical_uri = f"/{quote(self.config.bucket, safe='')}/{quote(key, safe='/')}"
        canonical_request = "\n".join(
            [method, canonical_uri, "", canonical_headers, signed_header_names, payload_hash]
        )
        credential_scope = f"{date_stamp}/{self.config.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _sigv4_key(self.config.secret_access_key, date_stamp, self.config.region, "s3")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.config.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_header_names}, Signature={signature}"
        )
        signed["authorization"] = authorization
        return signed


def _sigv4_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = hmac.new(("AWS4" + secret).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    key_region = hmac.new(key_date, region.encode("utf-8"), hashlib.sha256).digest()
    key_service = hmac.new(key_region, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()


def _task_date(task_id: str) -> str:
    if len(task_id) >= 8 and task_id[:8].isdigit():
        return f"{task_id[:4]}-{task_id[4:6]}-{task_id[6:8]}"
    return "0000-00-00"


def _safe_owner(owner_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "", str(owner_id or "").strip())
    if not clean:
        raise ValueError("Object storage owner id is required")
    return clean


def _safe_title(title: str, *, fallback: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(title or ""))
    text = re.sub(r"\s+", "", text)
    text = text.strip(".-_ ")
    return text[:80] or fallback


def _safe_ext(ext: str) -> str:
    value = str(ext or "png").strip().lower().lstrip(".")
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value or "png"


def _int_env(value: Any, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_int_env(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_env(value: Any, default: float) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 429, 500, 502, 503, 504}


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
