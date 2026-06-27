from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_omni_studio_r2 import StudioImageRow, archive_key_for_row, migrate_rows, owner_id_for_row


class FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.puts: list[tuple[str, bytes, str]] = []
        self.deletes: list[str] = []

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    async def put(self, key: str, data: bytes, content_type: str):
        self.puts.append((key, data, content_type))
        self.objects[key] = data

    async def delete(self, key: str) -> None:
        self.deletes.append(key)


def sample_work(**overrides):
    values = {
        "source_table": "image_works",
        "source_id": "a0f4b9a4-0000-4000-8000-000000000001",
        "sub2api_user_id": 123,
        "client_id": "browser-client-abc",
        "title": "黑神话李清照",
        "storage_driver": "r2",
        "storage_key": "works/browser-client-abc/a0f4b9a4-0000-4000-8000-000000000001.png",
        "mime_type": "image/png",
        "bytes": 1024,
        "created_at": "2026-06-26T15:00:00Z",
        "expires_at": "2026-07-26T15:00:00Z",
    }
    values.update(overrides)
    return StudioImageRow(**values)


class OmniStudioR2MigrationTests(unittest.TestCase):
    def test_archive_key_uses_user_date_time_and_safe_title(self) -> None:
        row = sample_work()

        self.assertEqual(owner_id_for_row(row), "user_123")
        self.assertEqual(
            archive_key_for_row(row),
            "legacy/omni-image-studio/users/user_123/2026/0626/150000-a0f4b9a4-0000-4000-8000-000000000001/01-黑神话李清照.png",
        )

    def test_archive_key_falls_back_to_legacy_client_owner(self) -> None:
        row = sample_work(sub2api_user_id=None, client_id="browser client/abc", title="")

        self.assertEqual(owner_id_for_row(row), "legacy_client_browser_client_abc")
        self.assertIn(
            "legacy/omni-image-studio/users/legacy_client_browser_client_abc/2026/0626/150000-",
            archive_key_for_row(row),
        )
        self.assertTrue(archive_key_for_row(row).endswith("/01-image.png"))

    def test_dry_run_writes_manifest_without_copying_or_deleting(self) -> None:
        row = sample_work()
        source = FakeStorage({row.storage_key: b"old"})
        target = FakeStorage()

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.jsonl"
            records = migrate_rows(
                [row],
                source_storage=source,
                target_storage=target,
                source_bucket="omni-image-studio",
                target_bucket="omni-image-studio",
                manifest_path=manifest,
                dry_run=True,
            )
            lines = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(records[0]["action"], "copy")
        self.assertEqual(lines, records)
        self.assertEqual(target.puts, [])
        self.assertEqual(source.deletes, [])

    def test_copy_mode_copies_archive_key_and_keeps_old_object(self) -> None:
        row = sample_work()
        source = FakeStorage({row.storage_key: b"old-image"})
        target = FakeStorage()

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.jsonl"
            records = migrate_rows(
                [row],
                source_storage=source,
                target_storage=target,
                source_bucket="omni-image-studio",
                target_bucket="omni-image-studio",
                manifest_path=manifest,
                dry_run=False,
            )

        self.assertEqual(records[0]["action"], "copied")
        self.assertEqual(target.puts[0][0], archive_key_for_row(row))
        self.assertEqual(target.puts[0][1], b"old-image")
        self.assertEqual(target.puts[0][2], "image/png")
        self.assertEqual(source.deletes, [])

    def test_copy_mode_records_missing_and_continues(self) -> None:
        row = sample_work(storage_key="missing.png")
        source = FakeStorage()
        target = FakeStorage()

        with tempfile.TemporaryDirectory() as tmp:
            records = migrate_rows(
                [row],
                source_storage=source,
                target_storage=target,
                source_bucket="omni-image-studio",
                target_bucket="omni-image-studio",
                manifest_path=Path(tmp) / "manifest.jsonl",
                dry_run=False,
            )

        self.assertEqual(records[0]["action"], "missing")
        self.assertEqual(target.puts, [])


if __name__ == "__main__":
    unittest.main()
