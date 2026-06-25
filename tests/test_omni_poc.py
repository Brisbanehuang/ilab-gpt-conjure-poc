from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from codex_image.webui.app import create_app
from codex_image.webui.omni_poc import (
    OmniPOCConfig,
    OmniTaskSecretStore,
    mask_api_key,
    omni_poc_enabled,
)


class TempDirMixin:
    def create_temp_dir(self) -> str:
        path = tempfile.mkdtemp(prefix="omni-poc-test-")
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path


class OmniPOCTests(TempDirMixin, TestCase):
    def test_mask_api_key_preserves_edges(self) -> None:
        self.assertEqual(mask_api_key("sk-abcdefghijklmnopqrstuvwxyz"), "sk-...wxyz")
        self.assertEqual(mask_api_key("short"), "********")
        self.assertEqual(mask_api_key(""), "")

    def test_poc_enabled_from_env(self) -> None:
        self.assertFalse(omni_poc_enabled({}))
        self.assertTrue(omni_poc_enabled({"OMNI_POC_MODE": "1"}))
        self.assertTrue(omni_poc_enabled({"OMNI_POC_MODE": "true"}))
        self.assertFalse(omni_poc_enabled({"OMNI_POC_MODE": "0"}))

    def test_secret_store_encrypts_and_clears_task_key(self) -> None:
        tmp = Path(self.create_temp_dir())
        config = OmniPOCConfig(
            enabled=True,
            base_url="http://127.0.0.1:8080/v1",
            image_model="gpt-image-2",
            secret_key=Fernet.generate_key().decode("ascii"),
            db_path=tmp / "omni-poc.db",
            source_url="https://example.test/source",
        )
        store = OmniTaskSecretStore(config)
        store.put_task_key("task-1", "sk-test-secret")

        raw = config.db_path.read_bytes()
        self.assertNotIn(b"sk-test-secret", raw)
        self.assertEqual(store.get_task_key("task-1"), "sk-test-secret")

        store.clear_task_key("task-1")
        self.assertIsNone(store.get_task_key("task-1"))

    def test_secret_store_rejects_blank_key(self) -> None:
        tmp = Path(self.create_temp_dir())
        config = OmniPOCConfig(
            enabled=True,
            base_url="http://127.0.0.1:8080/v1",
            image_model="gpt-image-2",
            secret_key=Fernet.generate_key().decode("ascii"),
            db_path=tmp / "omni-poc.db",
            source_url="https://example.test/source",
        )
        store = OmniTaskSecretStore(config)
        with self.assertRaises(ValueError):
            store.put_task_key("task-1", "   ")


class OmniPOCRouteTests(TempDirMixin, TestCase):
    def test_health_reports_omni_poc_mode(self) -> None:
        tmp = Path(self.create_temp_dir())
        old = os.environ.copy()
        os.environ.update(
            {
                "OMNI_POC_MODE": "1",
                "OMNI_POC_SECRET_KEY": Fernet.generate_key().decode("ascii"),
                "OMNI_BASE_URL": "http://127.0.0.1:8080/v1",
                "OMNI_POC_DB_PATH": str(tmp / "omni-poc.db"),
                "OMNI_POC_SOURCE_URL": "https://example.test/source",
            }
        )
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(old))
        app = create_app(output_root=tmp / "output", auto_start_queue=False)
        response = TestClient(app).get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["omni_poc"]["enabled"], True)
        self.assertEqual(payload["omni_poc"]["base_url"], "http://127.0.0.1:8080/v1")
        self.assertEqual(payload["omni_poc"]["image_model"], "gpt-image-2")
        self.assertEqual(payload["omni_poc"]["source_url"], "https://example.test/source")


class OmniPOCGenerationTests(TempDirMixin, TestCase):
    def create_poc_app(self) -> tuple[object, Path]:
        tmp = Path(self.create_temp_dir())
        old = os.environ.copy()
        os.environ.update(
            {
                "OMNI_POC_MODE": "1",
                "OMNI_POC_SECRET_KEY": Fernet.generate_key().decode("ascii"),
                "OMNI_BASE_URL": "http://127.0.0.1:8080/v1",
                "OMNI_POC_DB_PATH": str(tmp / "omni-poc.db"),
            }
        )
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(old))
        return create_app(output_root=tmp / "output", auto_start_queue=False), tmp

    def test_generate_requires_omni_key_in_poc_mode(self) -> None:
        app, _ = self.create_poc_app()
        response = TestClient(app).post(
            "/api/generate",
            data={"prompt": "test image", "model": "gpt-image-2"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Omni API Key", response.json()["detail"])

    def test_generate_stores_task_scoped_omni_key_without_metadata_leak(self) -> None:
        app, _ = self.create_poc_app()
        response = TestClient(app).post(
            "/api/generate",
            headers={"X-Omni-API-Key": "sk-task-secret"},
            data={"prompt": "test image", "model": "gpt-image-2"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        task_id = payload["task"]["task_id"]
        metadata = app.state.ctx.storage.read_metadata(task_id)
        self.assertEqual(metadata["params"]["omni_poc"], True)
        self.assertEqual(metadata["params"]["api_provider_id"], "omni-poc")
        self.assertEqual(metadata["params"]["api_mode"], "images")
        self.assertEqual(app.state.ctx.route_helpers["omni_task_secret_store"].get_task_key(task_id), "sk-task-secret")
        self.assertNotIn("sk-task-secret", str(metadata))
