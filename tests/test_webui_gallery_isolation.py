from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from codex_image.webui.app import create_app
from codex_image.webui.omni_session import SESSION_COOKIE_NAME


class WebUIGalleryIsolationTests(TestCase):
    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="gallery-isolation-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_path, ignore_errors=True))
        self.old_env = os.environ.copy()
        os.environ.update(
            {
                "OMNI_POC_MODE": "1",
                "OMNI_POC_SECRET_KEY": Fernet.generate_key().decode("ascii"),
                "OMNI_BASE_URL": "http://127.0.0.1:8080/v1",
                "OMNI_POC_DB_PATH": str(self.tmp_path / "omni-poc.db"),
                "OMNI_POC_LOGIN_URL": "https://portal.example.test/image-generator",
            }
        )
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self.old_env))
        self.app = create_app(
            output_root=self.tmp_path / "tasks",
            gallery_root=self.tmp_path / "gallery",
            auth_checker=lambda: True,
            auto_start_queue=False,
        )
        self.client = TestClient(self.app)

    def cookie_for_user(self, user_id: int) -> dict[str, str]:
        session_store = self.app.state.ctx.route_helpers["omni_session_store"]
        session = session_store.create_session(
            {"id": user_id, "email": f"user{user_id}@example.test", "username": f"user{user_id}", "balance": 1},
            f"token-{user_id}",
        )
        return {SESSION_COOKIE_NAME: session.id}

    def test_gallery_items_are_private_to_current_omni_user(self) -> None:
        user_1 = self.cookie_for_user(1)
        user_9 = self.cookie_for_user(9)

        created = self.client.post(
            "/api/gallery",
            data={"name": "管理员素材", "category": "portrait"},
            files={"image": ("portrait.png", b"admin-gallery-bytes", "image/png")},
            cookies=user_1,
        )
        item = created.json()["item"]

        user_9_list = self.client.get("/api/gallery", cookies=user_9)
        user_9_image = self.client.get(item["image_url"], cookies=user_9)
        user_1_list = self.client.get("/api/gallery", cookies=user_1)

        self.assertEqual(created.status_code, 200)
        self.assertEqual(user_9_list.status_code, 200)
        self.assertEqual(user_9_list.json()["items"], [])
        self.assertEqual(user_9_image.status_code, 404)
        self.assertEqual([listed["id"] for listed in user_1_list.json()["items"]], [item["id"]])

    def test_gallery_routes_require_omni_session_in_poc_mode(self) -> None:
        response = self.client.get("/api/gallery")

        self.assertEqual(response.status_code, 401)
        self.assertIn("请先登录 OmniAPI", response.json()["detail"])

    def test_reference_assets_are_private_to_current_omni_user(self) -> None:
        user_1 = self.cookie_for_user(1)
        user_9 = self.cookie_for_user(9)
        storage = self.app.state.ctx.reference_asset_storage
        asset = storage.scoped("user_1").create_or_touch("private.png", b"user-1-reference", "image/png")
        image_url = f"/api/reference-assets/{asset['id']}/image"

        user_1_recent = self.client.get("/api/reference-assets/recent", cookies=user_1)
        user_9_recent = self.client.get("/api/reference-assets/recent", cookies=user_9)
        user_9_image = self.client.get(image_url, cookies=user_9)
        user_9_delete = self.client.delete(f"/api/reference-assets/{asset['id']}", cookies=user_9)
        user_1_image_after_user_9_delete = self.client.get(image_url, cookies=user_1)

        self.assertEqual(user_1_recent.status_code, 200)
        self.assertEqual([item["id"] for item in user_1_recent.json()["items"]], [asset["id"]])
        self.assertEqual(user_9_recent.status_code, 200)
        self.assertEqual(user_9_recent.json()["items"], [])
        self.assertEqual(user_9_image.status_code, 404)
        self.assertEqual(user_9_delete.status_code, 404)
        self.assertEqual(user_1_image_after_user_9_delete.status_code, 200)
        self.assertEqual(user_1_image_after_user_9_delete.content, b"user-1-reference")
