from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from codex_image.webui.app import create_app
from codex_image.webui.omni_session import SESSION_COOKIE_NAME


class TempOmniAppMixin:
    def create_temp_dir(self) -> str:
        path = tempfile.mkdtemp(prefix="task-isolation-test-")
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def create_poc_app(self):
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
        app = create_app(output_root=tmp / "output", auto_start_queue=False)
        return app, tmp

    def create_session_cookie(self, app, user_id: int) -> dict[str, str]:
        session_store = app.state.ctx.route_helpers["omni_session_store"]
        session = session_store.create_session(
            {"id": user_id, "email": f"user{user_id}@example.test", "username": f"user{user_id}", "balance": 1},
            f"sub2api-token-{user_id}",
        )
        return {SESSION_COOKIE_NAME: session.id}


class WebUITaskIsolationTests(TempOmniAppMixin, TestCase):
    def test_recent_tasks_and_history_are_scoped_to_current_omni_user(self) -> None:
        app, _ = self.create_poc_app()
        storage = app.state.ctx.storage
        storage.write_metadata(
            "20260627142100-admin",
            {
                "task_id": "20260627142100-admin",
                "created_at": "2026-06-27T14:21:00Z",
                "updated_at": "2026-06-27T14:22:00Z",
                "completed_at": "2026-06-27T14:22:00Z",
                "status": "completed",
                "prompt": "admin image",
                "owner_id": "user_1",
                "params": {"omni_poc": True, "sub2api_user_id": 1, "n": 1},
            },
        )
        storage.write_metadata(
            "20260627143000-user9",
            {
                "task_id": "20260627143000-user9",
                "created_at": "2026-06-27T14:30:00Z",
                "updated_at": "2026-06-27T14:31:00Z",
                "completed_at": "2026-06-27T14:31:00Z",
                "status": "completed",
                "prompt": "user9 image",
                "owner_id": "user_9",
                "params": {"omni_poc": True, "sub2api_user_id": 9, "n": 1},
            },
        )

        client = TestClient(app)
        user_9_cookie = self.create_session_cookie(app, 9)
        recent = client.get("/api/tasks/recent", cookies=user_9_cookie)
        history = client.get("/api/task-history/tasks", cookies=user_9_cookie)
        summary = client.get("/api/task-history/summary", cookies=user_9_cookie)

        self.assertEqual(recent.status_code, 200)
        self.assertEqual([task["task_id"] for task in recent.json()["tasks"]], ["20260627143000-user9"])
        self.assertEqual(history.status_code, 200)
        self.assertEqual([task["task_id"] for task in history.json()["tasks"]], ["20260627143000-user9"])
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["total"], 1)

    def test_task_detail_output_and_mutations_reject_other_omni_owner(self) -> None:
        app, tmp = self.create_poc_app()
        storage = app.state.ctx.storage
        output_file = storage.write_output("20260627142100-admin", b"admin-image", "png", index=1)
        input_file = storage.write_input("20260627142100-admin", "reference.png", b"admin-input", index=1)
        storage.write_metadata(
            "20260627142100-admin",
            {
                "task_id": "20260627142100-admin",
                "created_at": "2026-06-27T14:21:00Z",
                "updated_at": "2026-06-27T14:22:00Z",
                "completed_at": "2026-06-27T14:22:00Z",
                "status": "completed",
                "prompt": "admin image",
                "owner_id": "user_1",
                "params": {"omni_poc": True, "sub2api_user_id": 1, "n": 1},
                "input_files": [input_file.name],
                "outputs": [
                    {
                        "index": 1,
                        "status": "completed",
                        "file": storage.output_file(output_file),
                        "url": "/api/tasks/20260627142100-admin/outputs/1",
                    }
                ],
                "output_files": [storage.output_file(output_file)],
            },
        )

        client = TestClient(app)
        user_9_cookie = self.create_session_cookie(app, 9)

        self.assertEqual(client.get("/api/tasks/20260627142100-admin", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(client.get("/api/tasks/20260627142100-admin/outputs/1", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(client.get("/api/tasks/20260627142100-admin/outputs.zip", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(client.get(f"/inputs/{input_file.name}", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(client.get("/api/tasks/20260627142100-admin/inputs/1/thumbnail", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(client.get(f"/outputs/{storage.output_file(output_file)}", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(client.patch("/api/tasks/20260627142100-admin/viewed", cookies=user_9_cookie).status_code, 404)
        self.assertEqual(
            client.patch("/api/tasks/20260627142100-admin/archive", json={"archived": True}, cookies=user_9_cookie).status_code,
            404,
        )
        self.assertEqual(client.delete("/api/tasks/20260627142100-admin", cookies=user_9_cookie).status_code, 404)
        self.assertTrue((tmp / "output" / storage.output_file(output_file)).exists())

    def test_omni_task_detail_rewrites_local_output_urls_to_owner_checked_routes(self) -> None:
        app, _ = self.create_poc_app()
        storage = app.state.ctx.storage
        output_file = storage.write_output("20260627143000-user9", b"user9-image", "png", index=1)
        storage.write_metadata(
            "20260627143000-user9",
            {
                "task_id": "20260627143000-user9",
                "created_at": "2026-06-27T14:30:00Z",
                "updated_at": "2026-06-27T14:31:00Z",
                "status": "completed",
                "prompt": "user9 image",
                "owner_id": "user_9",
                "params": {"omni_poc": True, "sub2api_user_id": 9, "n": 1},
                "output_files": [storage.output_file(output_file)],
                "output_urls": [f"/outputs/{storage.output_file(output_file)}"],
                "outputs": [
                    {
                        "index": 1,
                        "status": "completed",
                        "file": storage.output_file(output_file),
                        "url": f"/outputs/{storage.output_file(output_file)}",
                    }
                ],
            },
        )

        response = TestClient(app).get("/api/tasks/20260627143000-user9", cookies=self.create_session_cookie(app, 9))

        self.assertEqual(response.status_code, 200)
        task = response.json()["task"]
        self.assertEqual(task["output_url"], "/api/tasks/20260627143000-user9/outputs/1")
        self.assertEqual(task["output_urls"], ["/api/tasks/20260627143000-user9/outputs/1"])
        self.assertEqual(task["outputs"][0]["url"], "/api/tasks/20260627143000-user9/outputs/1")

    def test_private_task_apis_require_omni_session_in_poc_mode(self) -> None:
        app, _ = self.create_poc_app()
        response = TestClient(app).get("/api/tasks/recent")

        self.assertEqual(response.status_code, 401)
        self.assertIn("请先登录 OmniAPI", response.json()["detail"])

    def test_queue_events_snapshot_is_scoped_to_current_omni_user(self) -> None:
        app, _ = self.create_poc_app()
        storage = app.state.ctx.storage
        storage.write_metadata(
            "20260627142100-admin",
            {
                "task_id": "20260627142100-admin",
                "created_at": "2026-06-27T14:21:00Z",
                "updated_at": "2026-06-27T14:22:00Z",
                "status": "completed",
                "prompt": "admin image",
                "owner_id": "user_1",
                "params": {"omni_poc": True, "sub2api_user_id": 1, "n": 1},
            },
        )
        storage.write_metadata(
            "20260627143000-user9",
            {
                "task_id": "20260627143000-user9",
                "created_at": "2026-06-27T14:30:00Z",
                "updated_at": "2026-06-27T14:31:00Z",
                "status": "completed",
                "prompt": "user9 image",
                "owner_id": "user_9",
                "params": {"omni_poc": True, "sub2api_user_id": 9, "n": 1},
            },
        )

        client = TestClient(app)
        response = client.get("/api/events", cookies=self.create_session_cookie(app, 9))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.text.removeprefix("data: ").strip())
        self.assertEqual([task["task_id"] for task in payload["tasks"]], ["20260627143000-user9"])

    def test_queue_mutations_reject_other_omni_owner(self) -> None:
        app, _ = self.create_poc_app()
        storage = app.state.ctx.storage
        storage.write_metadata(
            "20260627142100-admin",
            {
                "task_id": "20260627142100-admin",
                "created_at": "2026-06-27T14:21:00Z",
                "updated_at": "2026-06-27T14:22:00Z",
                "status": "queued",
                "prompt": "admin queued image",
                "owner_id": "user_1",
                "params": {"omni_poc": True, "sub2api_user_id": 1, "n": 1},
            },
        )
        app.state.ctx.queue_storage.enqueue("20260627142100-admin")
        client = TestClient(app)
        user_9_cookie = self.create_session_cookie(app, 9)

        promote = client.post("/api/queue/20260627142100-admin/promote", cookies=user_9_cookie)
        delete = client.delete("/api/queue/20260627142100-admin", cookies=user_9_cookie)

        self.assertEqual(promote.status_code, 404)
        self.assertEqual(delete.status_code, 404)
        self.assertIn("20260627142100-admin", app.state.ctx.queue_storage.read_state()["waiting"])
