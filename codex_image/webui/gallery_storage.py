from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import DEFAULT_WEBUI_GALLERY_ROOT
from .storage_utils import _guess_mime_type, _safe_filename, utc_now


DEFAULT_GALLERY_CATEGORIES = [
    {"id": "portrait", "name": "人像", "prompt_role": "人像参考", "order": 10, "locked": False},
    {"id": "character", "name": "角色", "prompt_role": "角色参考", "order": 20, "locked": False},
    {"id": "product", "name": "产品", "prompt_role": "产品参考", "order": 30, "locked": False},
]
GALLERY_CATEGORIES = {category["id"] for category in DEFAULT_GALLERY_CATEGORIES}


class GalleryStorage:
    def __init__(self, root: Path | str = DEFAULT_WEBUI_GALLERY_ROOT) -> None:
        self.root = Path(root)

    def scoped(self, owner_id: str) -> "GalleryStorage":
        return GalleryStorage(self.root / "users" / _clean_gallery_owner_id(owner_id) / "gallery")

    def list_categories(self) -> list[dict[str, Any]]:
        categories = self._read_categories()
        return sorted(categories, key=lambda category: (int(category.get("order", 0)), str(category.get("name", ""))))

    def create_category(self, name: str, *, prompt_role: str | None = None, order: int | None = None) -> dict[str, Any]:
        categories = self._read_categories()
        clean_name = _clean_gallery_category_name(name)
        category_id = self._new_category_id(categories)
        now = utc_now()
        next_order = order if order is not None else (max([int(category.get("order", 0)) for category in categories] or [0]) + 10)
        category = {
            "id": category_id,
            "name": clean_name,
            "prompt_role": _clean_gallery_prompt_role(prompt_role, fallback=clean_name),
            "order": int(next_order),
            "locked": False,
            "created_at": now,
            "updated_at": now,
        }
        categories.append(category)
        self._write_categories(categories)
        return category

    def reorder_categories(self, category_ids: list[str]) -> list[dict[str, Any]]:
        categories = self._read_categories()
        current_ids = [str(category.get("id") or "") for category in self.list_categories()]
        reordered_ids = _clean_reorder_ids(category_ids, current_ids, clean_id=_clean_gallery_category_id, label="Gallery category")
        categories_by_id = {str(category.get("id") or ""): dict(category) for category in categories}
        updated: list[dict[str, Any]] = []
        now = utc_now()
        for index, category_id in enumerate(reordered_ids, start=1):
            category = categories_by_id[category_id]
            category["order"] = index * 10
            category["updated_at"] = now
            updated.append(category)
        self._write_categories(updated)
        return self.list_categories()

    def update_category(
        self,
        category_id: str,
        *,
        name: str | None = None,
        prompt_role: str | None = None,
        order: int | None = None,
    ) -> dict[str, Any]:
        clean_id = _clean_gallery_category_id(category_id)
        categories = self._read_categories()
        for category in categories:
            if category["id"] != clean_id:
                continue
            if name is not None:
                category["name"] = _clean_gallery_category_name(name)
            if prompt_role is not None:
                category["prompt_role"] = _clean_gallery_prompt_role(prompt_role, fallback=str(category.get("name") or "参考图"))
            if order is not None:
                category["order"] = int(order)
            category["updated_at"] = utc_now()
            self._write_categories(categories)
            return dict(category)
        raise FileNotFoundError(category_id)

    def delete_category(self, category_id: str, *, move_to: str | None = None) -> None:
        clean_id = _clean_gallery_category_id(category_id)
        categories = self._read_categories()
        if not any(category["id"] == clean_id for category in categories):
            raise FileNotFoundError(category_id)
        target_id = _clean_gallery_category_id(move_to) if move_to is not None else None
        if target_id == clean_id:
            raise ValueError("Move target must be different from deleted category")
        if target_id is not None and not any(category["id"] == target_id for category in categories):
            raise ValueError("Move target category does not exist")

        items = [item for item in self.list_items() if item.get("category") == clean_id]
        if items and target_id is None:
            raise ValueError("Category is not empty")
        if len(categories) <= 1:
            raise ValueError("At least one gallery category is required")

        if target_id is not None:
            for item in items:
                self.update_item(str(item["id"]), category=target_id)
        self._write_categories([category for category in categories if category["id"] != clean_id])

    def create_item(
        self,
        name: str,
        category: str,
        filename: str,
        data: bytes,
        content_type: str | None = None,
        prompt_note: str | None = None,
        order: int | None = None,
    ) -> dict[str, Any]:
        clean_name = _clean_gallery_name(name)
        clean_category = self._clean_category(category)
        self._ensure_unique_name(clean_name)
        item_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        item_path = self._item_path(item_id)
        item_path.mkdir(parents=True, exist_ok=False)
        safe_name = _safe_filename(filename)
        image_path = item_path / safe_name
        image_path.write_bytes(data)
        now = utc_now()
        metadata = {
            "id": item_id,
            "name": clean_name,
            "name_key": _gallery_name_key(clean_name),
            "category": clean_category,
            "filename": safe_name,
            "mime_type": content_type or _guess_mime_type(safe_name),
            "prompt_note": _clean_gallery_prompt_note(prompt_note),
            "order": _clean_gallery_item_order(order, fallback=self._next_item_order(clean_category)),
            "created_at": now,
            "updated_at": now,
        }
        self._write_item_metadata(item_id, metadata)
        return self._normalize_item_metadata(metadata)

    def list_items(self, category: str | None = None) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        clean_category = self._clean_category(category) if category else None
        category_map = {item["id"]: item for item in self.list_categories()}
        items: list[dict[str, Any]] = []
        for metadata_path in self.root.glob("*/metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            metadata = self._normalize_item_metadata(metadata, category_map=category_map)
            if clean_category and metadata.get("category") != clean_category:
                continue
            items.append(metadata)
        items.sort(key=lambda item: str(item.get("name", "")))
        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        items.sort(
            key=lambda item: (
                int(category_map.get(str(item.get("category") or ""), {}).get("order") or 0),
                0 if int(item.get("order") or 0) > 0 else 1,
                int(item.get("order") or 0) if int(item.get("order") or 0) > 0 else 0,
            )
        )
        return items

    def read_item(self, item_id: str) -> dict[str, Any]:
        path = self._item_path(item_id) / "metadata.json"
        return self._normalize_item_metadata(json.loads(path.read_text(encoding="utf-8")))

    def update_item(
        self,
        item_id: str,
        *,
        name: str | None = None,
        category: str | None = None,
        prompt_note: str | None = None,
        order: int | None = None,
    ) -> dict[str, Any]:
        metadata = self.read_item(item_id)
        original_category = str(metadata.get("category") or "")
        target_category = original_category
        if name is not None:
            clean_name = _clean_gallery_name(name)
            if _gallery_name_key(clean_name) != metadata.get("name_key"):
                self._ensure_unique_name(clean_name, ignore_id=item_id)
            metadata["name"] = clean_name
            metadata["name_key"] = _gallery_name_key(clean_name)
        if category is not None:
            target_category = self._clean_category(category)
            metadata["category"] = target_category
        if prompt_note is not None:
            metadata["prompt_note"] = _clean_gallery_prompt_note(prompt_note)
        if order is not None:
            metadata["order"] = _clean_gallery_item_order(order, fallback=int(metadata.get("order") or 0))
        elif category is not None and target_category != original_category:
            metadata["order"] = self._next_item_order(target_category)
        metadata["updated_at"] = utc_now()
        metadata.pop("category_name", None)
        metadata.pop("category_prompt_role", None)
        self._write_item_metadata(item_id, metadata)
        if category is not None and target_category != original_category:
            self._compact_category_item_orders(original_category)
        return self._normalize_item_metadata(metadata)

    def reorder_items(self, category: str, item_ids: list[str]) -> list[dict[str, Any]]:
        clean_category = self._clean_category(category)
        current_items = self.list_items(category=clean_category)
        current_ids = [str(item.get("id") or "") for item in current_items]
        reordered_ids = _clean_reorder_ids(item_ids, current_ids, clean_id=_clean_gallery_item_id, label="Gallery item")
        now = utc_now()
        for index, item_id in enumerate(reordered_ids, start=1):
            metadata = self.read_item(item_id)
            metadata["order"] = index * 10
            metadata["updated_at"] = now
            metadata.pop("category_name", None)
            metadata.pop("category_prompt_role", None)
            self._write_item_metadata(item_id, metadata)
        return self.list_items(category=clean_category)

    def replace_item_image(
        self,
        item_id: str,
        *,
        filename: str,
        data: bytes,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        if not data:
            raise ValueError("Image is required")
        metadata = self.read_item(item_id)
        item_path = self._item_path(item_id)
        old_path = item_path / str(metadata.get("filename", ""))
        safe_name = _safe_filename(filename)
        image_path = item_path / safe_name
        image_path.write_bytes(data)
        metadata["filename"] = safe_name
        metadata["mime_type"] = content_type or _guess_mime_type(safe_name)
        metadata["updated_at"] = utc_now()
        metadata.pop("category_name", None)
        metadata.pop("category_prompt_role", None)
        self._write_item_metadata(item_id, metadata)
        if old_path != image_path and old_path.exists() and old_path.parent == item_path:
            old_path.unlink()
        return self._normalize_item_metadata(metadata)

    def delete_item(self, item_id: str) -> None:
        item_path = self._item_path(item_id)
        if not item_path.exists():
            raise FileNotFoundError(item_id)
        shutil.rmtree(item_path)

    def image_path(self, item_id: str) -> Path:
        metadata = self.read_item(item_id)
        path = self._item_path(item_id) / str(metadata.get("filename", ""))
        if not path.exists():
            raise FileNotFoundError(item_id)
        return path

    def _write_item_metadata(self, item_id: str, metadata: dict[str, Any]) -> Path:
        path = self._item_path(item_id) / "metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _ensure_unique_name(self, name: str, *, ignore_id: str | None = None) -> None:
        name_key = _gallery_name_key(name)
        for item in self.list_items():
            if ignore_id and item.get("id") == ignore_id:
                continue
            if item.get("name_key") == name_key:
                raise FileExistsError(name)

    def _item_path(self, item_id: str) -> Path:
        if not item_id or "/" in item_id or "\\" in item_id:
            raise ValueError("Invalid gallery item id")
        return self.root / item_id

    def _categories_path(self) -> Path:
        return self.root / "categories.json"

    def _read_categories(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self._categories_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return [_normalize_gallery_category(category) for category in DEFAULT_GALLERY_CATEGORIES]
        except (OSError, json.JSONDecodeError):
            return [_normalize_gallery_category(category) for category in DEFAULT_GALLERY_CATEGORIES]
        if not isinstance(payload, list):
            return [_normalize_gallery_category(category) for category in DEFAULT_GALLERY_CATEGORIES]
        categories: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            try:
                category = _normalize_gallery_category(raw)
            except ValueError:
                continue
            if category["id"] in seen:
                continue
            seen.add(category["id"])
            categories.append(category)
        return categories or [_normalize_gallery_category(category) for category in DEFAULT_GALLERY_CATEGORIES]

    def _write_categories(self, categories: list[dict[str, Any]]) -> None:
        clean = [_normalize_gallery_category(category) for category in categories]
        self.root.mkdir(parents=True, exist_ok=True)
        self._categories_path().write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")

    def _new_category_id(self, categories: list[dict[str, Any]]) -> str:
        existing = {str(category.get("id") or "") for category in categories}
        while True:
            category_id = f"cat-{uuid.uuid4().hex[:10]}"
            if category_id not in existing:
                return category_id

    def _next_item_order(self, category: str) -> int:
        items = self._ensure_category_item_orders(category)
        current = [int(item.get("order") or 0) for item in items if int(item.get("order") or 0) > 0]
        return (max(current) if current else 0) + 10

    def _ensure_category_item_orders(self, category: str) -> list[dict[str, Any]]:
        items = self.list_items(category=category)
        if not any(int(item.get("order") or 0) <= 0 for item in items):
            return items
        category_map = {item["id"]: item for item in self.list_categories()}
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            metadata = self.read_item(str(item.get("id") or ""))
            metadata["order"] = index * 10
            metadata.pop("category_name", None)
            metadata.pop("category_prompt_role", None)
            self._write_item_metadata(str(item.get("id") or ""), metadata)
            normalized.append(self._normalize_item_metadata(metadata, category_map=category_map))
        return normalized

    def _compact_category_item_orders(self, category: str) -> list[dict[str, Any]]:
        items = self.list_items(category=category)
        category_map = {item["id"]: item for item in self.list_categories()}
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            metadata = self.read_item(str(item.get("id") or ""))
            metadata["order"] = index * 10
            metadata.pop("category_name", None)
            metadata.pop("category_prompt_role", None)
            self._write_item_metadata(str(item.get("id") or ""), metadata)
            normalized.append(self._normalize_item_metadata(metadata, category_map=category_map))
        return normalized

    def _clean_category(self, category: str | None) -> str:
        clean = _clean_gallery_category_id(category)
        if clean not in {item["id"] for item in self._read_categories()}:
            raise ValueError("Invalid gallery category")
        return clean

    def _normalize_item_metadata(
        self,
        metadata: dict[str, Any],
        *,
        category_map: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        item = dict(metadata)
        category_id = str(item.get("category") or "")
        categories = category_map if category_map is not None else {category["id"]: category for category in self.list_categories()}
        category = categories.get(category_id)
        item["order"] = _clean_gallery_item_order(item.get("order"), fallback=0)
        item["prompt_note"] = _clean_gallery_prompt_note(item.get("prompt_note"))
        item["category_name"] = str(category.get("name") or category_id) if category else category_id
        item["category_prompt_role"] = str(category.get("prompt_role") or item["category_name"] or "参考图") if category else "参考图"
        return item


def _clean_gallery_name(name: str) -> str:
    clean = " ".join(str(name or "").strip().split())
    if not clean:
        raise ValueError("Gallery name is required")
    if len(clean) > 64:
        raise ValueError("Gallery name is too long")
    return clean


def _gallery_name_key(name: str) -> str:
    return _clean_gallery_name(name).casefold()


def _clean_gallery_category(category: str | None) -> str:
    clean = str(category or "").strip()
    if clean not in GALLERY_CATEGORIES:
        raise ValueError("Invalid gallery category")
    return clean


def _clean_gallery_category_id(category: str | None) -> str:
    clean = str(category or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", clean):
        raise ValueError("Invalid gallery category")
    return clean


def _clean_gallery_category_name(name: str) -> str:
    clean = " ".join(str(name or "").strip().split())
    if not clean:
        raise ValueError("Gallery category name is required")
    if len(clean) > 32:
        raise ValueError("Gallery category name is too long")
    return clean


def _clean_gallery_prompt_role(value: str | None, *, fallback: str = "参考图") -> str:
    clean = " ".join(str(value or "").strip().split())
    if not clean:
        clean = fallback
    if len(clean) > 48:
        raise ValueError("Gallery category prompt role is too long")
    return clean


def _clean_gallery_prompt_note(value: Any) -> str:
    clean = " ".join(str(value or "").strip().split())
    if len(clean) > 160:
        raise ValueError("Gallery prompt note is too long")
    return clean


def _clean_gallery_item_id(item_id: Any) -> str:
    clean = str(item_id or "").strip()
    if not clean or "/" in clean or "\\" in clean:
        raise ValueError("Invalid gallery item id")
    return clean


def _clean_gallery_item_order(value: Any, *, fallback: int = 0) -> int:
    try:
        order = int(value)
    except (TypeError, ValueError):
        return int(fallback)
    return order if order > 0 else int(fallback)


def _clean_gallery_owner_id(owner_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "", str(owner_id or "").strip())
    if not clean:
        raise ValueError("Invalid gallery owner id")
    return clean


def _clean_reorder_ids(
    values: Any,
    expected_ids: list[str],
    *,
    clean_id,
    label: str,
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{label} reorder list must be an array")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item_id = clean_id(raw)
        if item_id in seen:
            raise ValueError(f"{label} reorder list contains duplicates")
        seen.add(item_id)
        cleaned.append(item_id)
    if cleaned != expected_ids and (len(cleaned) != len(expected_ids) or set(cleaned) != set(expected_ids)):
        raise ValueError(f"{label} reorder list must match current items")
    return cleaned


def _normalize_gallery_category(category: dict[str, Any]) -> dict[str, Any]:
    clean_id = _clean_gallery_category_id(str(category.get("id") or ""))
    clean_name = _clean_gallery_category_name(str(category.get("name") or clean_id))
    try:
        order = int(category.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return {
        "id": clean_id,
        "name": clean_name,
        "prompt_role": _clean_gallery_prompt_role(category.get("prompt_role"), fallback=clean_name),
        "order": order,
        "locked": bool(category.get("locked", False)),
        "created_at": str(category.get("created_at") or ""),
        "updated_at": str(category.get("updated_at") or ""),
    }
