from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any

from .schemas import DEFAULT_WEBUI_INPUT_ROOT, DEFAULT_WEBUI_REFERENCE_ASSET_SUBDIR
from .storage_utils import _guess_mime_type, _safe_filename, utc_now

REFERENCE_ASSET_SUFFIXES = {".png", ".jpg", ".webp", ".gif"}
MAX_REFERENCE_ASSETS = 50


class ReferenceAssetStorage:
    def __init__(
        self,
        root: Path | str = DEFAULT_WEBUI_INPUT_ROOT / DEFAULT_WEBUI_REFERENCE_ASSET_SUBDIR,
        *,
        max_items: int = MAX_REFERENCE_ASSETS,
    ) -> None:
        self.root = Path(root)
        self.max_items = max(1, int(max_items))
        self._lock = threading.RLock()

    def scoped(self, owner_id: str) -> "ReferenceAssetStorage":
        return ReferenceAssetStorage(self.root / "users" / _clean_reference_asset_owner_id(owner_id) / "reference-assets", max_items=self.max_items)

    def create_or_touch(
        self,
        filename: str,
        data: bytes,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        asset_id = hashlib.sha256(data).hexdigest()
        with self._lock:
            existing = self._read_valid_item(asset_id)
            if existing is not None:
                touched = self._touch_metadata(existing)
                self._prune_to_limit()
                return touched

            suffix = _reference_asset_suffix(filename, content_type)
            stored_filename = f"{asset_id}{suffix}"
            shard_path = self._shard_path(asset_id)
            image_path = shard_path / stored_filename
            now = utc_now()
            metadata = {
                "id": asset_id,
                "sha256": asset_id,
                "filename": _safe_filename(filename),
                "stored_filename": stored_filename,
                "mime_type": content_type or _guess_mime_type(stored_filename),
                "size_bytes": len(data),
                "created_at": now,
                "last_used_at": now,
                "used_count": 1,
            }
            shard_path.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(data)
            self._write_item_metadata(asset_id, metadata)
            self._prune_to_limit()
            return metadata

    def touch(self, asset_id: str) -> dict[str, Any]:
        with self._lock:
            touched = self._touch_metadata(self.read_item(asset_id))
            self._prune_to_limit()
            return touched

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        items: list[dict[str, Any]] = []
        for metadata_path in self.root.glob("*/*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            asset_id = str(metadata.get("id") or "")
            try:
                self._validate_asset_id(asset_id)
            except ValueError:
                continue
            if metadata.get("sha256") != asset_id:
                continue
            if self._stored_image_path(asset_id, metadata) is None:
                continue
            items.append(metadata)
        return sorted(items, key=lambda item: str(item.get("last_used_at", "")), reverse=True)[: max(0, limit)]

    def read_item(self, asset_id: str) -> dict[str, Any]:
        self._validate_asset_id(asset_id)
        return json.loads(self._metadata_path(asset_id).read_text(encoding="utf-8"))

    def delete_item(self, asset_id: str) -> None:
        self._validate_asset_id(asset_id)
        with self._lock:
            metadata = self.read_item(asset_id)
            self._delete_item_files(asset_id, metadata)

    def image_path(self, asset_id: str) -> Path:
        metadata = self.read_item(asset_id)
        path = self._stored_image_path(asset_id, metadata)
        if path is None:
            raise FileNotFoundError(asset_id)
        return path

    def _touch_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(metadata.get("id") or "")
        self._validate_asset_id(asset_id)
        metadata = dict(metadata)
        metadata["last_used_at"] = utc_now()
        try:
            used_count = int(metadata.get("used_count", 0))
        except (TypeError, ValueError):
            used_count = 0
        metadata["used_count"] = used_count + 1
        self._write_item_metadata(asset_id, metadata)
        return metadata

    def _read_valid_item(self, asset_id: str) -> dict[str, Any] | None:
        try:
            metadata = self.read_item(asset_id)
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            return None
        if not isinstance(metadata, dict):
            return None
        if metadata.get("id") != asset_id or metadata.get("sha256") != asset_id:
            return None
        if self._stored_image_path(asset_id, metadata) is None:
            return None
        return metadata

    def _prune_to_limit(self) -> None:
        items = self.list_recent(limit=10_000)
        for metadata in sorted(
            items[self.max_items :],
            key=lambda item: (str(item.get("last_used_at", "")), str(item.get("id", ""))),
        ):
            asset_id = str(metadata.get("id") or "")
            try:
                self._delete_item_files(asset_id, metadata)
            except (OSError, ValueError):
                continue

    def _delete_item_files(self, asset_id: str, metadata: dict[str, Any]) -> None:
        self._validate_asset_id(asset_id)
        image_path = self._stored_image_path(asset_id, metadata)
        metadata_path = self._metadata_path(asset_id)
        if image_path is not None:
            image_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        shard_path = self._shard_path(asset_id)
        try:
            next(shard_path.iterdir())
        except StopIteration:
            shard_path.rmdir()
        except FileNotFoundError:
            pass

    def _stored_image_path(self, asset_id: str, metadata: Any) -> Path | None:
        if not isinstance(metadata, dict):
            return None
        stored_filename = metadata.get("stored_filename")
        if not isinstance(stored_filename, str):
            return None
        if "/" in stored_filename or "\\" in stored_filename:
            return None
        suffix = Path(stored_filename).suffix.lower()
        if suffix not in REFERENCE_ASSET_SUFFIXES:
            return None
        if stored_filename != f"{asset_id}{suffix}":
            return None
        path = self._shard_path(asset_id) / stored_filename
        if not path.is_file():
            return None
        return path

    def _write_item_metadata(self, asset_id: str, metadata: dict[str, Any]) -> Path:
        path = self._metadata_path(asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _metadata_path(self, asset_id: str) -> Path:
        self._validate_asset_id(asset_id)
        return self._shard_path(asset_id) / f"{asset_id}.json"

    def _shard_path(self, asset_id: str) -> Path:
        self._validate_asset_id(asset_id)
        return self.root / asset_id[:2]

    @staticmethod
    def _validate_asset_id(asset_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", asset_id or ""):
            raise ValueError("Invalid reference asset id")


def _reference_asset_suffix(filename: str, content_type: str | None = None) -> str:
    suffix = Path(_safe_filename(filename)).suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    if suffix in REFERENCE_ASSET_SUFFIXES:
        return suffix

    guessed = mimetypes.guess_extension(content_type or "")
    guessed = (guessed or "").lower()
    if guessed in {".jpe", ".jpeg"}:
        return ".jpg"
    if guessed in REFERENCE_ASSET_SUFFIXES:
        return guessed
    return ".png"


def _clean_reference_asset_owner_id(owner_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "", str(owner_id or "").strip())
    if not clean:
        raise ValueError("Invalid reference asset owner id")
    return clean
