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
