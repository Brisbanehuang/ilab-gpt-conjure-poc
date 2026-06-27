from __future__ import annotations

import os
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from codex_image.webui.app import create_app
from codex_image.webui.omni_poc import (
    OmniPOCConfig,
    OmniTaskSecretStore,
    mask_api_key,
    omni_poc_enabled,
)
from codex_image.webui.omni_session import OmniSessionStore


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
        self.assertIn("请先登录 OmniAPI", response.json()["detail"])

    def test_generate_stores_task_scoped_selected_key_without_metadata_leak(self) -> None:
        app, _ = self.create_poc_app()
        session_store = app.state.ctx.route_helpers["omni_session_store"]
        session = session_store.create_session(
            {"id": 123, "email": "user@example.test", "username": "user", "balance": 12.5},
            "sub2api-token",
        )
        with patch(
            "codex_image.webui.routes.generation.resolve_omni_image_key",
            return_value={"id": "456", "key": "sk-task-secret", "name": "image key", "group": {"name": "default"}},
        ):
            response = TestClient(app).post(
                "/api/generate",
                cookies={"omni_lens_session": session.id},
                data={"prompt": "test image", "model": "gpt-image-2", "sub2api_key_id": "456"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        task_id = payload["task"]["task_id"]
        metadata = app.state.ctx.storage.read_metadata(task_id)
        self.assertEqual(metadata["params"]["omni_poc"], True)
        self.assertEqual(metadata["params"]["api_provider_id"], "omni-poc")
        self.assertEqual(metadata["params"]["api_mode"], "images")
        self.assertEqual(metadata["params"]["sub2api_api_key_id"], "456")
        self.assertEqual(metadata["params"]["sub2api_user_id"], 123)
        self.assertEqual(app.state.ctx.route_helpers["omni_task_secret_store"].get_task_key(task_id), "sk-task-secret")
        self.assertNotIn("sk-task-secret", str(metadata))

    def test_generate_with_web_search_uses_omni_responses_backend(self) -> None:
        app, _ = self.create_poc_app()
        session_store = app.state.ctx.route_helpers["omni_session_store"]
        session = session_store.create_session(
            {"id": 123, "email": "user@example.test", "username": "user", "balance": 12.5},
            "sub2api-token",
        )
        with patch(
            "codex_image.webui.routes.generation.resolve_omni_image_key",
            return_value={"id": "456", "key": "sk-task-secret", "name": "image key", "group": {"name": "default"}},
        ):
            response = TestClient(app).post(
                "/api/generate",
                cookies={"omni_lens_session": session.id},
                data={"prompt": "test image with search", "model": "gpt-image-2", "sub2api_key_id": "456", "web_search": "true"},
            )
        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task"]["task_id"]
        metadata = app.state.ctx.storage.read_metadata(task_id)
        request = json.loads(app.state.ctx.storage.request_path(task_id).read_text(encoding="utf-8"))
        self.assertEqual(metadata["requested_backend"], "openai_responses")
        self.assertEqual(metadata["params"]["api_mode"], "responses")
        self.assertTrue(metadata["params"]["web_search"])
        self.assertEqual(metadata["params"]["sub2api_api_key_id"], "456")
        self.assertEqual(request["endpoint"], "/responses")
        self.assertEqual(request["tools"][0]["type"], "web_search")

    def test_retry_failed_omni_web_search_rebinds_task_key_and_preserves_responses_mode(self) -> None:
        app, _ = self.create_poc_app()
        storage = app.state.ctx.storage
        session_store = app.state.ctx.route_helpers["omni_session_store"]
        session = session_store.create_session(
            {"id": 123, "email": "user@example.test", "username": "user", "balance": 12.5},
            "sub2api-token",
        )
        task_id = "20260627134442-2623bc89"
        storage.write_metadata(
            task_id,
            {
                "task_id": task_id,
                "created_at": "2026-06-27T13:44:42+00:00",
                "updated_at": "2026-06-27T13:45:00+00:00",
                "mode": "generate",
                "status": "failed",
                "prompt": "联网搜索生成图片",
                "owner_id": "user_123",
                "params": {
                    "omni_poc": True,
                    "api_provider_id": "omni-poc",
                    "api_mode": "images",
                    "web_search": True,
                    "sub2api_user_id": 123,
                    "sub2api_api_key_id": "456",
                    "model": "gpt-image-2",
                    "n": 1,
                },
                "outputs": [{"index": 1, "status": "failed", "error": "temporary responses parse error"}],
                "error": "temporary responses parse error",
                "last_error": "temporary responses parse error",
                "total_count": 1,
                "failed_count": 1,
            },
        )
        storage.write_request(
            task_id,
            {
                "endpoint": "/responses",
                "tools": [{"type": "web_search"}, {"type": "image_generation"}],
            },
        )
        self.assertIsNone(app.state.ctx.route_helpers["omni_task_secret_store"].get_task_key(task_id))

        with patch(
            "codex_image.webui.omni_session.list_omni_image_keys",
            return_value=[
                {
                    "id": "456",
                    "key": "sk-retry-secret",
                    "status": "active",
                    "group": {"status": "active", "platform": "openai", "allow_image_generation": True},
                }
            ],
        ), patch(
            "codex_image.webui.omni_session._key_supported_models",
            return_value=frozenset({"gpt-image-2", "gpt-5.4-mini"}),
        ):
            response = TestClient(app).post(
                f"/api/tasks/{task_id}/retry-failed",
                cookies={"omni_lens_session": session.id},
            )

        self.assertEqual(response.status_code, 200, response.text)
        metadata = storage.read_metadata(task_id)
        self.assertEqual(metadata["status"], "queued")
        self.assertEqual(metadata["params"]["api_provider_id"], "omni-poc")
        self.assertEqual(metadata["params"]["api_mode"], "responses")
        self.assertTrue(metadata["params"]["web_search"])
        self.assertEqual(metadata["requested_backend"], "openai_responses")
        self.assertEqual(app.state.ctx.route_helpers["omni_task_secret_store"].get_task_key(task_id), "sk-retry-secret")

    def test_generate_rejects_when_auto_select_has_no_usable_key(self) -> None:
        app, _ = self.create_poc_app()
        session_store = app.state.ctx.route_helpers["omni_session_store"]
        session = session_store.create_session(
            {"id": 123, "email": "user@example.test", "username": "user", "balance": 12.5},
            "sub2api-token",
        )
        with patch("codex_image.webui.omni_session.list_omni_image_keys", return_value=[]):
            response = TestClient(app).post(
                "/api/generate",
                cookies={"omni_lens_session": session.id},
                data={"prompt": "test image", "model": "gpt-image-2"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("没有检测到可调用 gpt-image-2 的 API Key", response.json()["detail"])


class OmniPOCQueueRuntimeTests(TempDirMixin, TestCase):
    def test_queue_client_uses_task_secret(self) -> None:
        from codex_image.webui.queue import QueueChannel
        from codex_image.webui.queue_runtime import _client_for_queue_channel
        from codex_image.client import OpenAIImagesImageClient

        app, _ = OmniPOCGenerationTests.create_poc_app(self)
        ctx = app.state.ctx
        ctx.route_helpers["omni_task_secret_store"].put_task_key("task-1", "sk-task")

        client = _client_for_queue_channel(
            ctx,
            QueueChannel(channel_id="api:default:1", auth_source="api", account_id=None),
            {"task_id": "task-1", "params": {"omni_poc": True}},
        )

        self.assertEqual(client.api_key, "sk-task")
        self.assertEqual(client.base_url, "http://127.0.0.1:8080/v1")
        self.assertIsInstance(client, OpenAIImagesImageClient)

    def test_queue_client_uses_responses_client_for_omni_web_search_task(self) -> None:
        from codex_image.webui.queue import QueueChannel
        from codex_image.webui.queue_runtime import _client_for_queue_channel
        from codex_image.client import OpenAIResponsesImageClient

        app, _ = OmniPOCGenerationTests.create_poc_app(self)
        ctx = app.state.ctx
        ctx.route_helpers["omni_task_secret_store"].put_task_key("task-1", "sk-task")

        client = _client_for_queue_channel(
            ctx,
            QueueChannel(channel_id="api:default:1", auth_source="api", account_id=None),
            {"task_id": "task-1", "params": {"omni_poc": True, "api_mode": "responses"}},
        )

        self.assertEqual(client.api_key, "sk-task")
        self.assertEqual(client.responses_url, "http://127.0.0.1:8080/v1/responses")
        self.assertIsInstance(client, OpenAIResponsesImageClient)

    def test_poc_mode_starts_api_queue_channels(self) -> None:
        app, _ = OmniPOCGenerationTests.create_poc_app(self)
        channels = app.state.ctx.queue_manager.channels
        self.assertTrue(channels)
        self.assertTrue(all(channel.auth_source == "api" for channel in channels))


class OmniPOCValidationEndpointTests(TempDirMixin, TestCase):
    def test_auth_session_exchange_and_key_listing(self) -> None:
        app, _ = OmniPOCGenerationTests.create_poc_app(self)
        client = TestClient(app)

        async def fake_verify(config, access_token):
            self.assertEqual(access_token, "sub2api-token")
            return {"id": 123, "email": "user@example.test", "username": "user", "balance": 12.5}

        async def fake_key_dtos(config, token):
            self.assertEqual(token, "sub2api-token")
            return [
                {
                    "id": "456",
                    "name": "image key",
                    "group_id": "g1",
                    "group_name": "default",
                    "masked_key": "sk-...cret",
                    "supports_image_model": True,
                    "supports_title_model": True,
                }
            ]

        with (
            patch("codex_image.webui.routes.omni_auth.verify_sub2api_token", fake_verify),
            patch("codex_image.webui.routes.omni_auth.usable_key_dtos", fake_key_dtos),
        ):
            exchange = client.post("/api/auth/sub2api/exchange", json={"accessToken": "sub2api-token"})
            self.assertEqual(exchange.status_code, 200)
            self.assertTrue(exchange.json()["authenticated"])
            self.assertEqual(exchange.json()["user"]["id"], 123)

            session = client.get("/api/auth/session")
            self.assertEqual(session.status_code, 200)
            self.assertTrue(session.json()["authenticated"])

            keys = client.get("/api/omni/keys")
            self.assertEqual(keys.status_code, 200)
            payload = keys.json()
            self.assertEqual(payload["model"], "gpt-image-2")
            self.assertEqual(payload["title_model"], "gpt-5.4-mini")
            self.assertEqual(payload["keys"][0]["id"], "456")
            self.assertTrue(payload["keys"][0]["supports_title_model"])
            self.assertNotIn("sk-image-secret", str(payload))

    def test_key_listing_requires_session(self) -> None:
        app, _ = OmniPOCGenerationTests.create_poc_app(self)
        response = TestClient(app).get("/api/omni/keys")
        self.assertEqual(response.status_code, 401)
        self.assertIn("请先登录 OmniAPI", response.json()["detail"])

    def test_usable_key_listing_checks_models_once_per_key(self) -> None:
        from codex_image.webui.omni_session import usable_key_dtos

        config = OmniPOCConfig(
            enabled=True,
            base_url="http://127.0.0.1:8080/v1",
            image_model="gpt-image-2",
            secret_key=Fernet.generate_key().decode("ascii"),
            db_path=Path(self.create_temp_dir()) / "omni-poc.db",
            source_url="https://example.test/source",
        )
        rows = [
            {
                "id": "456",
                "name": "image key",
                "key": "sk-image-secret",
                "status": "active",
                "group": {"status": "active", "platform": "openai", "allow_image_generation": True, "name": "default"},
            }
        ]
        calls: list[str] = []

        async def fake_list_keys(_config, token):
            self.assertEqual(token, "sub2api-token")
            return rows

        async def fake_supported_models(_config, api_key):
            calls.append(api_key)
            return frozenset({"gpt-image-2", "gpt-5.4-mini"})

        with (
            patch("codex_image.webui.omni_session.list_omni_image_keys", fake_list_keys),
            patch("codex_image.webui.omni_session._key_supported_models", fake_supported_models),
        ):
            payload = asyncio.run(usable_key_dtos(config, "sub2api-token"))

        self.assertEqual(calls, ["sk-image-secret"])
        self.assertEqual(payload[0]["id"], "456")
        self.assertTrue(payload[0]["supports_title_model"])

    def test_resolve_omni_image_key_auto_selects_first_usable_full_key(self) -> None:
        from codex_image.webui.omni_session import resolve_omni_image_key

        config = OmniPOCConfig(
            enabled=True,
            base_url="http://127.0.0.1:8080/v1",
            image_model="gpt-image-2",
            secret_key=Fernet.generate_key().decode("ascii"),
            db_path=Path(self.create_temp_dir()) / "omni-poc.db",
            source_url="https://example.test/source",
        )
        store = OmniSessionStore(config)
        session = store.create_session(
            {"id": 123, "email": "user@example.test", "username": "user", "balance": 12.5},
            "sub2api-token",
        )
        rows = [
            {
                "id": "bad",
                "name": "bad key",
                "key": "sk-bad",
                "status": "active",
                "group": {"status": "active", "platform": "openai", "allow_image_generation": True, "name": "default"},
            },
            {
                "id": "456",
                "name": "image key",
                "key": "sk-image-secret",
                "status": "active",
                "group": {"status": "active", "platform": "openai", "allow_image_generation": True, "name": "default"},
            },
        ]
        calls: list[str] = []

        async def fake_list_keys(_config, token):
            self.assertEqual(token, "sub2api-token")
            return rows

        async def fake_supported_models(_config, api_key):
            calls.append(api_key)
            if api_key == "sk-image-secret":
                return frozenset({"gpt-image-2", "gpt-5.4-mini"})
            return frozenset({"gpt-5.4-mini"})

        with (
            patch("codex_image.webui.omni_session.list_omni_image_keys", fake_list_keys),
            patch("codex_image.webui.omni_session._key_supported_models", fake_supported_models),
        ):
            key = asyncio.run(resolve_omni_image_key(config, store, session, ""))

        self.assertEqual(calls, ["sk-bad", "sk-image-secret"])
        self.assertEqual(key["id"], "456")
        self.assertEqual(key["key"], "sk-image-secret")
        self.assertTrue(key["supports_title_model"])

    def test_session_store_encrypts_sub2api_token(self) -> None:
        tmp = Path(self.create_temp_dir())
        config = OmniPOCConfig(
            enabled=True,
            base_url="http://127.0.0.1:8080/v1",
            image_model="gpt-image-2",
            secret_key=Fernet.generate_key().decode("ascii"),
            db_path=tmp / "omni-poc.db",
            source_url="https://example.test/source",
        )
        store = OmniSessionStore(config)
        session = store.create_session({"id": 123, "email": "u@example.test", "username": "u", "balance": 1}, "sub2api-token")
        self.assertEqual(store.decrypt_token(session), "sub2api-token")
        self.assertNotIn(b"sub2api-token", config.db_path.read_bytes())


class OmniPOCLimitTests(TestCase):
    def test_rate_limiter_rejects_after_limit(self) -> None:
        from codex_image.webui.omni_poc_limits import FixedWindowRateLimiter

        limiter = FixedWindowRateLimiter(limit=2, window_seconds=60)
        self.assertTrue(limiter.allow("127.0.0.1"))
        self.assertTrue(limiter.allow("127.0.0.1"))
        self.assertFalse(limiter.allow("127.0.0.1"))

    def test_upload_limits_count_and_size(self) -> None:
        from codex_image.webui.omni_poc_limits import validate_upload_limits

        files = [
            Mock(size=1024, filename="a.png"),
            Mock(size=1024, filename="b.png"),
        ]
        validate_upload_limits(files, max_files=2, max_bytes_each=2048)
        with self.assertRaises(ValueError):
            validate_upload_limits(files, max_files=1, max_bytes_each=2048)
        with self.assertRaises(ValueError):
            validate_upload_limits(files, max_files=2, max_bytes_each=512)
