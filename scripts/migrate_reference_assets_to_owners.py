from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


def migrate_reference_assets(input_root: Path, source_data_root: Path, *, dry_run: bool = False) -> dict[str, Any]:
    reference_root = input_root / "reference-assets"
    result: dict[str, Any] = {
        "scanned_tasks": 0,
        "referenced_assets": 0,
        "copied_files": 0,
        "skipped_existing_files": 0,
        "missing_source_refs": 0,
        "owners": {},
        "dry_run": dry_run,
    }
    for metadata_path in sorted(source_data_root.glob("tasks/**/*.metadata.json")):
        metadata = _read_json(metadata_path)
        if not isinstance(metadata, dict):
            continue
        owner_id = _metadata_owner_id(metadata)
        if not owner_id:
            continue
        asset_ids = _metadata_reference_asset_ids(metadata)
        if not asset_ids:
            continue
        result["scanned_tasks"] += 1
        result["owners"][owner_id] = int(result["owners"].get(owner_id, 0)) + len(asset_ids)
        for asset_id in asset_ids:
            result["referenced_assets"] += 1
            source_dir = reference_root / asset_id[:2]
            source_json = source_dir / f"{asset_id}.json"
            if not source_json.is_file():
                result["missing_source_refs"] += 1
                continue
            source_files = [source_json, *sorted(path for path in source_dir.glob(f"{asset_id}.*") if path != source_json)]
            target_dir = reference_root / "users" / owner_id / "reference-assets" / asset_id[:2]
            for source_file in source_files:
                target_file = target_dir / source_file.name
                if target_file.exists():
                    result["skipped_existing_files"] += 1
                    continue
                if not dry_run:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, target_file)
                result["copied_files"] += 1
    return result


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _metadata_owner_id(metadata: dict[str, Any]) -> str:
    owner_id = str(metadata.get("owner_id") or "").strip()
    if _valid_owner_id(owner_id):
        return owner_id
    params = metadata.get("params") if isinstance(metadata.get("params"), dict) else {}
    try:
        user_id = int(params.get("sub2api_user_id"))
    except (TypeError, ValueError):
        return ""
    return f"user_{user_id}" if user_id > 0 else ""


def _metadata_reference_asset_ids(metadata: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for collection_key in ("reference_assets", "input_sources"):
        items = metadata.get(collection_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("id") or "").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", asset_id) or asset_id in seen:
                continue
            seen.add(asset_id)
            result.append(asset_id)
    return result


def _valid_owner_id(owner_id: str) -> bool:
    return bool(re.fullmatch(r"user_[1-9][0-9]*", owner_id))


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy legacy global reference-assets into Omni owner-scoped directories.")
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--source-data-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    result = migrate_reference_assets(args.input_root, args.source_data_root, dry_run=args.dry_run)
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
