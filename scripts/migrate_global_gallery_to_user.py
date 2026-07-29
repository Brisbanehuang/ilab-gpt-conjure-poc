#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def migrate_gallery(gallery_root: Path, owner_id: str, *, dry_run: bool) -> dict[str, int]:
    source = gallery_root
    target = gallery_root / "users" / owner_id / "gallery"
    result = {"copied_items": 0, "copied_categories": 0, "skipped": 0}
    if not source.exists():
        return result
    if dry_run:
        print(f"dry-run: source={source}")
        print(f"dry-run: target={target}")
    else:
        target.mkdir(parents=True, exist_ok=True)

    categories_path = source / "categories.json"
    target_categories = target / "categories.json"
    if categories_path.is_file():
        if target_categories.exists():
            result["skipped"] += 1
        else:
            if not dry_run:
                target_categories.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(categories_path, target_categories)
            result["copied_categories"] += 1

    for item_dir in sorted(source.iterdir()):
        if not item_dir.is_dir() or item_dir.name == "users":
            continue
        if not (item_dir / "metadata.json").is_file():
            result["skipped"] += 1
            continue
        target_dir = target / item_dir.name
        if target_dir.exists():
            result["skipped"] += 1
            continue
        if not dry_run:
            shutil.copytree(item_dir, target_dir)
        result["copied_items"] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy legacy global gallery data into a user-scoped gallery.")
    parser.add_argument("--gallery-root", type=Path, required=True)
    parser.add_argument("--owner-id", default="user_1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate_gallery(args.gallery_root, args.owner_id, dry_run=args.dry_run)
    print(
        "copied_items={copied_items} copied_categories={copied_categories} skipped={skipped}".format(
            **result,
        )
    )


if __name__ == "__main__":
    main()
