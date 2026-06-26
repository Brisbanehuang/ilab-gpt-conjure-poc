from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .object_storage import object_storage_from_env
from .schemas import DEFAULT_WEBUI_SOURCE_DATA_SUBDIR
from .storage import TaskStorage


@dataclass
class CleanupResult:
    scanned_tasks: int = 0
    deleted_objects: int = 0
    deleted_metadata: int = 0
    deleted_local_files: int = 0
    errors: int = 0
    dry_run: bool = False


def cleanup_expired_storage(output_root: Path, *, dry_run: bool = False, now: datetime | None = None) -> CleanupResult:
    cutoff = now or datetime.now(UTC)
    storage = TaskStorage(output_root=output_root, source_data_root=output_root / DEFAULT_WEBUI_SOURCE_DATA_SUBDIR)
    object_storage = object_storage_from_env()
    result = CleanupResult(dry_run=dry_run)
    for metadata_path in storage.iter_metadata_paths():
        result.scanned_tasks += 1
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result.errors += 1
            continue
        if not isinstance(metadata, dict):
            continue
        changed = False
        for collection_key in ("outputs", "input_sources"):
            records = metadata.get(collection_key)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict) or record.get("deleted"):
                    continue
                expires_at = _parse_datetime(record.get("expires_at") or metadata.get("expires_at"))
                if expires_at is None or expires_at > cutoff:
                    continue
                if str(record.get("storage_driver") or "") == "r2" and record.get("storage_key"):
                    if object_storage is None:
                        result.errors += 1
                        continue
                    try:
                        if not dry_run:
                            object_storage.delete(str(record["storage_key"]))
                        result.deleted_objects += 1
                    except Exception:
                        result.errors += 1
                        continue
                local_file = _safe_local_file(storage, metadata, collection_key, record)
                if local_file is not None and local_file.exists():
                    if not dry_run:
                        local_file.unlink()
                        storage._prune_empty_output_dir(local_file.parent)
                    result.deleted_local_files += 1
                record["deleted"] = True
                record["status"] = "deleted"
                record["deleted_at"] = cutoff.isoformat().replace("+00:00", "Z")
                changed = True
        task_expires_at = _parse_datetime(metadata.get("expires_at"))
        if task_expires_at is not None and task_expires_at <= cutoff:
            live_records = _live_storage_records(metadata)
            if not live_records:
                metadata["storage_expired_at"] = cutoff.isoformat().replace("+00:00", "Z")
                result.deleted_metadata += 1
                changed = True
        if changed and not dry_run:
            task_id = str(metadata.get("task_id") or metadata_path.name.removesuffix(".metadata.json"))
            storage.write_metadata(task_id, metadata)
    return result


def _live_storage_records(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for collection_key in ("outputs", "input_sources"):
        items = metadata.get(collection_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("storage_key") and not item.get("deleted"):
                records.append(item)
    return records


def _safe_local_file(storage: TaskStorage, metadata: dict[str, Any], collection_key: str, record: dict[str, Any]) -> Path | None:
    task_id = str(metadata.get("task_id") or "")
    filename = str(record.get("file") or "")
    if not filename:
        return None
    if collection_key == "input_sources":
        path = storage.input_path(filename)
    else:
        path = storage.output_path(filename)
        if task_id and not path.name.startswith(f"{task_id}-image-"):
            return None
    try:
        resolved = path.resolve(strict=False)
        source_data_root = storage.source_data_root.resolve(strict=False)
        try:
            resolved.relative_to(source_data_root)
            return None
        except ValueError:
            pass
        if resolved.suffix.lower() in {".sqlite", ".db"}:
            return None
        root = storage.input_root if collection_key == "input_sources" else storage.output_root
        resolved.relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return path


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean expired Omni image storage objects.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = cleanup_expired_storage(args.output_root, dry_run=args.dry_run)
    print(
        " ".join(
            [
                f"scanned_tasks={result.scanned_tasks}",
                f"deleted_objects={result.deleted_objects}",
                f"deleted_metadata={result.deleted_metadata}",
                f"deleted_local_files={result.deleted_local_files}",
                f"errors={result.errors}",
                f"dry_run={str(result.dry_run).lower()}",
            ]
        )
    )
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
