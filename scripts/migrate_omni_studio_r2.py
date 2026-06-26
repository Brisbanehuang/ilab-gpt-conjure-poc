#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from codex_image.webui.object_storage import ObjectStorageConfig, R2ObjectStorage


@dataclass(frozen=True)
class StudioImageRow:
    source_table: str
    source_id: str
    client_id: str
    sub2api_user_id: int | None
    title: str
    storage_driver: str
    storage_key: str
    mime_type: str
    bytes: int | None
    created_at: str
    expires_at: str


def archive_key_for_row(row: StudioImageRow) -> str:
    owner_id = owner_id_for_row(row)
    created = _parse_datetime(row.created_at) or datetime.now(UTC)
    safe_id = _safe_segment(row.source_id, fallback="item")
    title = _safe_title(row.title, fallback="image")
    ext = _extension_for_row(row)
    return (
        f"legacy/omni-image-studio/users/{owner_id}/"
        f"{created:%Y/%m/%d}/{created:%H%M%S}-{safe_id}/01-{title}.{ext}"
    )


def owner_id_for_row(row: StudioImageRow) -> str:
    if row.sub2api_user_id is not None and row.sub2api_user_id > 0:
        return f"user_{row.sub2api_user_id}"
    return f"legacy_client_{_safe_segment(row.client_id, fallback='unknown')}"


def manifest_record(row: StudioImageRow, *, source_bucket: str, target_bucket: str, action: str = "copy") -> dict[str, Any]:
    return {
        "source_table": row.source_table,
        "source_id": row.source_id,
        "old_driver": row.storage_driver,
        "old_bucket": source_bucket,
        "old_key": row.storage_key,
        "archive_driver": "r2",
        "archive_bucket": target_bucket,
        "archive_key": archive_key_for_row(row),
        "owner_id": owner_id_for_row(row),
        "bytes": row.bytes,
        "mime_type": row.mime_type,
        "expires_at": row.expires_at,
        "action": action,
    }


def migrate_rows(
    rows: Iterable[StudioImageRow],
    *,
    source_storage: Any,
    target_storage: Any,
    source_bucket: str,
    target_bucket: str,
    manifest_path: Path,
    dry_run: bool,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if row.storage_driver != "r2" or not row.storage_key:
                continue
            record = manifest_record(row, source_bucket=source_bucket, target_bucket=target_bucket)
            if not dry_run:
                try:
                    data = source_storage.get(row.storage_key)
                except Exception:
                    record["action"] = "missing"
                else:
                    target_storage.put(record["archive_key"], data, row.mime_type or "application/octet-stream")
                    record["bytes"] = len(data)
                    record["action"] = "copied"
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return records


def load_rows(database_url: str) -> list[StudioImageRow]:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("psycopg is required for DATABASE_URL migration reads; install the webui requirements first.") from exc

    query = """
        SELECT 'image_assets' AS source_table,
               a.id::text AS source_id,
               a.client_id,
               a.sub2api_user_id,
               COALESCE(j.title, j.prompt, '') AS title,
               a.storage_driver,
               a.storage_key,
               a.mime_type,
               a.bytes,
               a.created_at::text AS created_at,
               a.expires_at::text AS expires_at
          FROM image_assets a
          LEFT JOIN image_generation_jobs j ON j.id = a.job_id
         WHERE a.storage_driver = 'r2' AND COALESCE(a.storage_key, '') <> ''
        UNION ALL
        SELECT 'image_works' AS source_table,
               w.id::text AS source_id,
               w.client_id,
               w.sub2api_user_id,
               COALESCE(w.title, w.prompt, '') AS title,
               w.storage_driver,
               w.storage_key,
               w.mime_type,
               w.bytes,
               w.created_at::text AS created_at,
               w.expires_at::text AS expires_at
          FROM image_works w
         WHERE w.storage_driver = 'r2' AND COALESCE(w.storage_key, '') <> ''
         ORDER BY created_at ASC, source_table ASC, source_id ASC
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return [_row_from_mapping(dict(zip([desc.name for desc in cur.description], item))) for item in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive legacy Omni Image Studio R2 objects.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--source-bucket", required=True)
    parser.add_argument("--target-bucket", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source_storage = _r2_storage_from_env(prefix="", bucket=args.source_bucket)
    target_storage = _r2_storage_from_env(prefix="TARGET_", bucket=args.target_bucket)
    rows = load_rows(args.database_url)
    records = migrate_rows(
        rows,
        source_storage=source_storage,
        target_storage=target_storage,
        source_bucket=args.source_bucket,
        target_bucket=args.target_bucket,
        manifest_path=args.manifest,
        dry_run=args.dry_run,
    )
    copied = sum(1 for item in records if item.get("action") == "copied")
    missing = sum(1 for item in records if item.get("action") == "missing")
    print(
        f"rows={len(records)} copied={copied} missing={missing} "
        f"dry_run={str(bool(args.dry_run)).lower()} manifest={args.manifest}"
    )
    return 1 if missing else 0


def _row_from_mapping(row: dict[str, Any]) -> StudioImageRow:
    return StudioImageRow(
        source_table=str(row.get("source_table") or ""),
        source_id=str(row.get("source_id") or ""),
        client_id=str(row.get("client_id") or ""),
        sub2api_user_id=_optional_int(row.get("sub2api_user_id")),
        title=str(row.get("title") or ""),
        storage_driver=str(row.get("storage_driver") or ""),
        storage_key=str(row.get("storage_key") or ""),
        mime_type=str(row.get("mime_type") or "image/png"),
        bytes=_optional_int(row.get("bytes")),
        created_at=str(row.get("created_at") or ""),
        expires_at=str(row.get("expires_at") or ""),
    )


def _r2_storage_from_env(*, prefix: str, bucket: str) -> R2ObjectStorage:
    account_id = os.environ.get(f"{prefix}R2_ACCOUNT_ID") or os.environ.get("R2_ACCOUNT_ID") or ""
    access_key_id = os.environ.get(f"{prefix}R2_ACCESS_KEY_ID") or os.environ.get("R2_ACCESS_KEY_ID") or ""
    secret_access_key = os.environ.get(f"{prefix}R2_SECRET_ACCESS_KEY") or os.environ.get("R2_SECRET_ACCESS_KEY") or ""
    region = os.environ.get(f"{prefix}R2_REGION") or os.environ.get("R2_REGION") or "auto"
    if not (account_id and access_key_id and secret_access_key and bucket):
        raise RuntimeError("R2 migration credentials are incomplete")
    return R2ObjectStorage(
        ObjectStorageConfig(
            driver="r2",
            account_id=account_id,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket=bucket,
            region=region,
        )
    )


def _extension_for_row(row: StudioImageRow) -> str:
    mime_ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(row.mime_type.lower())
    if mime_ext:
        return mime_ext
    suffix = Path(row.storage_key).suffix.lower().lstrip(".")
    return re.sub(r"[^a-z0-9]+", "", suffix) or "png"


def _safe_segment(value: str, *, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip())
    text = text.strip("_-")
    return text[:96] or fallback


def _safe_title(value: str, *, fallback: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "", str(value or ""))
    text = re.sub(r"\s+", "", text)
    text = text.strip(".-_ ")
    return text[:80] or fallback


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _parse_datetime(value: str) -> datetime | None:
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


if __name__ == "__main__":
    raise SystemExit(main())
