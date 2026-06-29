from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path


class WebUIStorageCleanupTests(unittest.TestCase):
    def test_omni_metadata_gets_thirty_day_retention_fields(self) -> None:
        from codex_image.webui.storage import TaskStorage
        from codex_image.webui.task_metadata import _write_queued_metadata

        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(
            "os.environ",
            {
                "OMNI_OBJECT_STORAGE_DRIVER": "r2",
                "OMNI_SAVED_IMAGE_TTL_DAYS": "30",
            },
        ):
            root = Path(tmp)
            storage = TaskStorage(input_root=root / "inputs", output_root=root / "outputs", source_data_root=root / "outputs" / "source-data")
            task_id = "20260626150000-a63df6c2"
            storage._task_source_data_dir(task_id).mkdir(parents=True, exist_ok=True)

            metadata = _write_queued_metadata(
                storage,
                task_id,
                created_at="2026-06-26T15:00:00+00:00",
                mode="generate",
                prompt="猫",
                prompt_for_model="猫",
                params={"omni_poc": True, "sub2api_user_id": 123, "output_format": "png", "n": 1},
                input_files=[],
                mask_file=None,
                gallery_refs=[],
            )

        self.assertEqual(metadata["owner_id"], "user_123")
        self.assertEqual(metadata["storage_driver"], "r2")
        self.assertEqual(metadata["retention_policy"], "30_days")
        self.assertEqual(metadata["expires_at"], "2026-07-26T15:00:00Z")

    def test_r2_output_put_removes_only_transient_local_output(self) -> None:
        from codex_image.client import ImageResult
        from codex_image.webui.object_storage import StoredObject
        from codex_image.webui.storage import TaskStorage
        from codex_image.webui.task_metadata import _complete_task, _write_queued_metadata
        from PIL import Image

        class FakeObjectStorage:
            async def delete(self, key: str) -> None:
                raise AssertionError(key)

            async def get(self, key: str) -> bytes:
                raise AssertionError(key)

            async def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
                return StoredObject(driver="r2", key=key, size=len(data), content_type=content_type)

        png = BytesIO()
        Image.new("RGB", (1024, 1024), (120, 180, 160)).save(png, format="PNG")

        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict("os.environ", {"OMNI_OBJECT_STORAGE_DRIVER": "r2"}):
            root = Path(tmp)
            storage = TaskStorage(input_root=root / "inputs", output_root=root / "outputs", source_data_root=root / "outputs" / "source-data")
            task_id = "20260626150000-a63df6c2"
            storage._task_source_data_dir(task_id).mkdir(parents=True, exist_ok=True)
            _write_queued_metadata(
                storage,
                task_id,
                created_at="2026-06-26T15:00:00+00:00",
                mode="generate",
                prompt="猫",
                prompt_for_model="猫",
                params={"omni_poc": True, "sub2api_user_id": 123, "output_format": "png", "n": 1},
                input_files=[],
                mask_file=None,
                gallery_refs=[],
            )
            with unittest.mock.patch("codex_image.webui.task_outputs.object_storage_from_env", return_value=FakeObjectStorage()):
                metadata = _complete_task(
                    storage,
                    task_id,
                    "2026-06-26T15:00:00+00:00",
                    "generate",
                    "猫",
                    "猫",
                    ImageResult(png.getvalue(), "revised", "png", "1024x1024", "auto", "low", {}),
                    [],
                    [],
                    None,
                    {},
                    {"omni_poc": True, "sub2api_user_id": 123, "output_format": "png", "n": 1},
                )
                local_output = storage.output_path(metadata["output_files"][0])
                thumbnail_path = storage.output_thumbnail_path(task_id, 1)
                metadata_path = storage.metadata_path(task_id)
                sqlite_path = storage.source_data_root / "webui-task-index.db"

            self.assertFalse(local_output.exists())
            self.assertTrue(thumbnail_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertTrue(sqlite_path.exists())
            self.assertEqual(metadata["outputs"][0]["expires_at"], "2026-07-26T15:00:00Z")

    def test_cleanup_expired_storage_dry_run_and_delete(self) -> None:
        from codex_image.webui.storage import TaskStorage
        from codex_image.webui.storage_cleanup import cleanup_expired_storage

        class FakeObjectStorage:
            def __init__(self) -> None:
                self.deleted: list[str] = []

            async def put(self, key: str, data: bytes, content_type: str):
                raise AssertionError(key)

            async def get(self, key: str) -> bytes:
                raise AssertionError(key)

            async def delete(self, key: str) -> None:
                self.deleted.append(key)

        fake = FakeObjectStorage()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = TaskStorage(input_root=root / "inputs", output_root=root / "outputs", source_data_root=root / "outputs" / "source-data")
            task_id = "20260625120000-a63df6c2"
            local_output = storage.write_output(task_id, b"png", "png", index=1)
            storage._task_source_data_dir(task_id).mkdir(parents=True, exist_ok=True)
            storage.write_metadata(
                task_id,
                {
                    "task_id": task_id,
                    "owner_id": "user_123",
                    "storage_driver": "r2",
                    "retention_policy": "temporary",
                    "expires_at": "2026-06-26T12:00:00Z",
                    "outputs": [
                        {
                            "index": 1,
                            "status": "completed",
                            "file": storage.output_file(local_output),
                            "storage_driver": "r2",
                            "storage_key": "users/user_123/images/old.png",
                            "bytes": 3,
                            "expires_at": "2026-06-26T12:00:00Z",
                        }
                    ],
                },
            )

            with unittest.mock.patch("codex_image.webui.storage_cleanup.object_storage_from_env", return_value=fake):
                dry = cleanup_expired_storage(root / "outputs", dry_run=True, now=datetime(2026, 6, 27, tzinfo=UTC))
                after_dry = json.loads(storage.metadata_path(task_id).read_text(encoding="utf-8"))
                actual = cleanup_expired_storage(root / "outputs", dry_run=False, now=datetime(2026, 6, 27, tzinfo=UTC))
                after_delete = storage.read_metadata(task_id)

        self.assertEqual(dry.deleted_objects, 1)
        self.assertEqual(fake.deleted, ["users/user_123/images/old.png"])
        self.assertFalse(after_dry["outputs"][0].get("deleted", False))
        self.assertEqual(actual.deleted_objects, 1)
        self.assertFalse(local_output.exists())
        self.assertTrue(after_delete["outputs"][0]["deleted"])
        self.assertEqual(after_delete["storage_expired_at"], "2026-06-27T00:00:00Z")

    def test_stored_bytes_for_owner_counts_only_live_r2_records(self) -> None:
        from codex_image.webui.storage import TaskStorage

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = TaskStorage(input_root=root / "inputs", output_root=root / "outputs", source_data_root=root / "outputs" / "source-data")
            first = storage.create_task("generate")
            second = storage.create_task("generate")
            storage.write_metadata(
                first.task_id,
                {
                    "task_id": first.task_id,
                    "owner_id": "user_123",
                    "expires_at": "2026-06-28T00:00:00Z",
                    "outputs": [{"storage_driver": "r2", "storage_key": "live", "bytes": 20}],
                    "input_sources": [{"storage_driver": "r2", "storage_key": "input", "bytes": 5}],
                },
            )
            storage.write_metadata(
                second.task_id,
                {
                    "task_id": second.task_id,
                    "owner_id": "user_123",
                    "expires_at": "2026-06-26T00:00:00Z",
                    "outputs": [{"storage_driver": "r2", "storage_key": "expired", "bytes": 99}],
                },
            )

            total = storage.stored_bytes_for_owner("user_123", now=datetime(2026, 6, 27, tzinfo=UTC))

        self.assertEqual(total, 25)


if __name__ == "__main__":
    unittest.main()
