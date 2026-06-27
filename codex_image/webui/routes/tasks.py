from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import sys
from typing import Any
import zipfile

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from codex_image.webui.auth_routing import _backend_for_api_mode
from codex_image.webui.context import WebUIContext
from codex_image.webui.object_storage import object_storage_from_env, owner_id_for_session
from codex_image.webui.omni_session import SESSION_COOKIE_NAME, OmniSession, resolve_omni_image_key
from codex_image.webui.storage import utc_now
from codex_image.webui.task_metadata import (
    _accept_partial_task_successes,
    _delete_unselected_task_outputs,
    _downloadable_output_paths,
    _output_record_filename,
    _output_thumbnail_fields,
    _positive_int,
    _retryable_failed_output_indexes,
    _safe_output_path,
    _set_task_output_selected,
    _visible_completed_output_records,
    _with_file_urls,
)
from codex_image.webui.thumbnails import create_image_thumbnail, thumbnail_needs_refresh


def register_task_routes(app: FastAPI, ctx: WebUIContext) -> None:
    h = ctx.route_helpers

    @app.get("/api/tasks")
    def list_tasks(request: Request) -> dict[str, Any]:
        owner_id = _require_omni_owner_id(ctx, request)
        active_ids = h["visible_running_task_ids"]()
        return {
            "tasks": [
                _with_owned_file_urls(ctx, task, owner_id, active_ids=active_ids, include_request=False)
                for task in ctx.storage.list_tasks(owner_id=owner_id)
            ]
        }

    @app.get("/api/tasks/recent")
    def list_recent_tasks(request: Request, limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
        owner_id = _require_omni_owner_id(ctx, request)
        tasks = ctx.storage.list_recent_task_cards(limit=limit, owner_id=owner_id)
        tasks_by_id = {str(task.get("task_id") or ""): task for task in tasks}
        queue_state = ctx.queue_storage.read_state()
        active_ids = [
            *[str(task_id) for task_id in queue_state.get("waiting", []) if task_id],
            *[str(item.get("task_id")) for item in queue_state.get("running", {}).values() if isinstance(item, dict) and item.get("task_id")],
        ]
        for task_id in active_ids:
            if task_id in tasks_by_id:
                continue
            try:
                task = _read_owned_task_card(ctx, task_id, owner_id)
            except (FileNotFoundError, ValueError):
                continue
            tasks_by_id[task_id] = task
            tasks.append(task)
        return {"tasks": tasks}

    @app.get("/api/task-history/summary")
    def task_history_summary(request: Request) -> dict[str, Any]:
        return ctx.storage.task_history_summary(owner_id=_require_omni_owner_id(ctx, request))

    @app.get("/api/task-history/tasks")
    def task_history_tasks(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None),
        q: str = Query(""),
        month: str = Query(""),
        status: str = Query(""),
        prompt_mode: str = Query(""),
        size: str = Query(""),
        quality: str = Query(""),
        ratio: str = Query(""),
        orientation: str = Query(""),
        backend: str = Query(""),
        provider: str = Query(""),
        archived: bool | None = Query(None),
        sort: str = Query("newest"),
        direction: str = Query("next"),
    ) -> dict[str, Any]:
        return ctx.storage.query_task_history(
            limit=limit,
            cursor=cursor,
            q=q,
            month=month,
            status=status,
            prompt_mode=prompt_mode,
            size=size,
            quality=quality,
            ratio=ratio,
            orientation=orientation,
            backend=backend,
            provider=provider,
            archived=archived,
            sort=sort,
            direction=direction,
            owner_id=_require_omni_owner_id(ctx, request),
        )

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, request: Request) -> dict[str, Any]:
        try:
            metadata = h["with_stored_request_payload"](task_id, _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request)))
            return {
                "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"]())
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.patch("/api/tasks/{task_id}/viewed")
    def mark_task_viewed(task_id: str, request: Request) -> dict[str, Any]:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        metadata["viewed_at"] = utc_now()
        ctx.storage.write_metadata(task_id, metadata)
        return {
            "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"](), include_request=False)
        }

    @app.get("/api/tasks/{task_id}/outputs.zip")
    def download_task_outputs_zip(task_id: str, request: Request, selected: bool = Query(False)) -> StreamingResponse:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
            output_paths = _downloadable_output_paths(ctx.storage, metadata, selected_only=selected)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if len(output_paths) < 2:
            detail = "Task has fewer than two selected outputs" if selected else "Task has fewer than two downloadable outputs"
            raise HTTPException(status_code=400, detail=detail)

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            used_names: set[str] = set()
            for index, path in enumerate(output_paths, start=1):
                archive_name = path.name
                if archive_name in used_names:
                    archive_name = f"{task_id}-image-{index}{path.suffix}"
                used_names.add(archive_name)
                archive.write(path, archive_name)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{task_id}-images.zip"'},
        )

    @app.post("/api/tasks/{task_id}/reveal-output")
    def reveal_task_output_directory(task_id: str, request: Request) -> dict[str, Any]:
        if request.headers.get("x-requested-with") != "codex-image-webui":
            raise HTTPException(status_code=403, detail="WebUI request header required")
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
            output_paths = _downloadable_output_paths(ctx.storage, metadata, selected_only=False)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if not output_paths:
            raise HTTPException(status_code=409, detail="Task has no local output files")
        output_directory = output_paths[0].parent
        try:
            _open_path_in_file_manager(output_directory)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Could not open output directory") from exc
        return {"ok": True, "path": str(output_directory)}

    @app.get("/api/tasks/{task_id}/inputs/{input_index}/thumbnail")
    def get_task_input_thumbnail(task_id: str, input_index: int, request: Request) -> FileResponse:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if input_index < 1:
            raise HTTPException(status_code=404, detail="Input not found")

        input_files = metadata.get("input_files") if isinstance(metadata.get("input_files"), list) else []
        if input_index > len(input_files):
            raise HTTPException(status_code=404, detail="Input not found")
        input_path = ctx.storage.input_path(str(input_files[input_index - 1]))
        if not input_path.is_file():
            raise HTTPException(status_code=404, detail="Input not found")

        thumbnail_path = ctx.storage.input_thumbnail_path(task_id, input_index)
        if thumbnail_needs_refresh(input_path, thumbnail_path):
            create_image_thumbnail(input_path, thumbnail_path)
        if not thumbnail_path.exists():
            raise HTTPException(status_code=404, detail="Thumbnail unavailable")
        return FileResponse(
            thumbnail_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/api/tasks/{task_id}/inputs/{input_index}", response_model=None)
    async def get_task_input(task_id: str, input_index: int, request: Request):
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if input_index < 1:
            raise HTTPException(status_code=404, detail="Input not found")

        input_sources = metadata.get("input_sources") if isinstance(metadata.get("input_sources"), list) else []
        record = input_sources[input_index - 1] if input_index <= len(input_sources) and isinstance(input_sources[input_index - 1], dict) else None
        if record is not None and str(record.get("storage_driver") or "") == "r2" and record.get("storage_key"):
            object_storage = object_storage_from_env()
            if object_storage is None:
                raise HTTPException(status_code=404, detail="Object storage is not configured")
            try:
                data = await object_storage.get(str(record["storage_key"]))
            except Exception as exc:
                raise HTTPException(status_code=404, detail="Input not found") from exc
            return StreamingResponse(BytesIO(data), media_type=str(record.get("content_type") or record.get("mime_type") or "application/octet-stream"))

        input_files = metadata.get("input_files") if isinstance(metadata.get("input_files"), list) else []
        if input_index > len(input_files):
            raise HTTPException(status_code=404, detail="Input not found")
        input_path = ctx.storage.input_path(str(input_files[input_index - 1]))
        if not input_path.is_file():
            raise HTTPException(status_code=404, detail="Input not found")
        return FileResponse(input_path)

    @app.get("/api/tasks/{task_id}/outputs/{output_index}", response_model=None)
    async def get_task_output(task_id: str, output_index: int, request: Request):
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if output_index < 1:
            raise HTTPException(status_code=404, detail="Output not found")

        records = _visible_completed_output_records(metadata)
        record = next((item for item in records if item.get("index") == output_index), None)
        if record is None:
            raise HTTPException(status_code=404, detail="Output not found")
        if str(record.get("storage_driver") or "") == "r2" and record.get("storage_key"):
            object_storage = object_storage_from_env()
            if object_storage is None:
                raise HTTPException(status_code=404, detail="Object storage is not configured")
            try:
                data = await object_storage.get(str(record["storage_key"]))
            except Exception as exc:
                raise HTTPException(status_code=404, detail="Output not found") from exc
            return StreamingResponse(BytesIO(data), media_type=str(record.get("content_type") or "application/octet-stream"))

        output_path = _safe_output_path(ctx.storage, task_id, _output_record_filename(record))
        if output_path is None or not output_path.is_file():
            raise HTTPException(status_code=404, detail="Output not found")
        return FileResponse(output_path)

    @app.get("/api/tasks/{task_id}/outputs/{output_index}/thumbnail")
    def get_task_output_thumbnail(task_id: str, output_index: int, request: Request) -> FileResponse:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if output_index < 1:
            raise HTTPException(status_code=404, detail="Output not found")

        records = _visible_completed_output_records(metadata)
        record = next((item for item in records if item.get("index") == output_index), None)
        if record is None:
            raise HTTPException(status_code=404, detail="Output not found")
        output_path = _safe_output_path(ctx.storage, task_id, _output_record_filename(record))
        if output_path is None or not output_path.is_file():
            raise HTTPException(status_code=404, detail="Output not found")

        fields = _output_thumbnail_fields(ctx.storage, task_id, output_index, output_path)
        thumbnail_file = fields.get("thumbnail_file")
        if not thumbnail_file:
            raise HTTPException(status_code=404, detail="Thumbnail unavailable")
        thumbnail_path = ctx.storage.output_path(thumbnail_file)
        return FileResponse(
            thumbnail_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.patch("/api/tasks/{task_id}/outputs/{output_index}/selected")
    def update_task_output_selection(task_id: str, output_index: int, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
            _ensure_outputs_mutable(task_id, metadata)
            metadata = _set_task_output_selected(ctx.storage, task_id, metadata, output_index, bool(payload.get("selected")))
            return {
                "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"]())
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/outputs/delete-unselected")
    async def delete_unselected_task_outputs(task_id: str, request: Request) -> dict[str, Any]:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
            _ensure_outputs_mutable(task_id, metadata)
            removed_keys = _storage_keys_for_unselected_outputs(metadata)
            await _delete_r2_objects(removed_keys)
            metadata = _delete_unselected_task_outputs(ctx.storage, task_id, metadata)
            return {
                "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"]())
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/api/tasks/{task_id}/archive")
    def update_task_archive(task_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
            metadata = h["set_task_archived"](task_id, bool(payload.get("archived")))
            return {
                "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"]())
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.post("/api/tasks/{task_id}/retry-failed")
    async def retry_failed_task(task_id: str, request: Request, payload: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if h["queue_has_running_task"](task_id) or task_id in ctx.active_task_ids:
            raise HTTPException(status_code=409, detail="Running task cannot be retried")
        if task_id in ctx.queue_storage.read_state()["waiting"]:
            raise HTTPException(status_code=409, detail="Task is already queued")
        metadata = h["materialize_orphaned_running_failure"](task_id, metadata)
        if metadata.get("status") not in {"failed", "partial_failed"}:
            raise HTTPException(status_code=409, detail="Only failed tasks can retry failed image slots")

        retry_slots = _retryable_failed_output_indexes(metadata)
        if not retry_slots:
            raise HTTPException(status_code=409, detail="No retryable failed image slots")

        now = utc_now()
        metadata["status"] = "queued"
        metadata["queued_at"] = now
        metadata["updated_at"] = now
        metadata["attempts"] = 0
        metadata["max_attempts"] = ctx.queue_manager.max_attempts if ctx.queue_manager is not None else 1
        metadata["retrying_failed_slots"] = retry_slots
        metadata["retry_failed_slots"] = retry_slots
        metadata["retry_requested_at"] = now
        metadata["error"] = ""
        if _is_omni_poc_task(metadata):
            await _prepare_omni_retry(ctx, request, task_id, metadata)
        else:
            h["apply_retry_api_provider"](task_id, metadata, str((payload or {}).get("api_provider_id") or "").strip() or None)
        ctx.storage.write_metadata(task_id, metadata)
        if ctx.queue_manager is not None:
            ctx.queue_manager.attempts.pop(task_id, None)
            ctx.queue_manager.failed_channels.pop(task_id, None)
        ctx.queue_storage.enqueue(task_id)
        h["ensure_queue_worker_running"]()
        return {
            "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"]())
        }

    @app.post("/api/tasks/{task_id}/accept-successes")
    def accept_task_successes(task_id: str, request: Request) -> dict[str, Any]:
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        if h["queue_has_running_task"](task_id) or task_id in ctx.active_task_ids:
            raise HTTPException(status_code=409, detail="Running task cannot be accepted")
        if task_id in ctx.queue_storage.read_state()["waiting"]:
            raise HTTPException(status_code=409, detail="Queued task cannot be accepted")
        metadata = h["materialize_orphaned_running_failure"](task_id, metadata)
        if metadata.get("status") not in {"failed", "partial_failed"}:
            raise HTTPException(status_code=409, detail="Only failed tasks can accept successful outputs")

        try:
            metadata = _accept_partial_task_successes(ctx.storage, task_id, metadata)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "task": _with_owned_file_urls(ctx, metadata, _metadata_owner_id(metadata), active_ids=h["visible_running_task_ids"]())
        }

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, request: Request) -> dict[str, Any]:
        if task_id in ctx.active_task_ids or h["queue_has_running_task"](task_id):
            raise HTTPException(status_code=409, detail="Running task cannot be deleted")
        try:
            metadata = _read_owned_metadata(ctx, task_id, _require_omni_owner_id(ctx, request))
            await _delete_r2_objects(_storage_keys_for_metadata(metadata))
            ctx.queue_storage.remove_waiting(task_id)
            ctx.storage.delete_task(task_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        return {"ok": True, "task_id": task_id}

    def _ensure_outputs_mutable(task_id: str, metadata: dict[str, Any]) -> None:
        if task_id in ctx.active_task_ids or h["queue_has_running_task"](task_id):
            raise ValueError("Running task outputs cannot be changed")
        if task_id in ctx.queue_storage.read_state()["waiting"]:
            raise ValueError("Queued task outputs cannot be changed")
        if metadata.get("status") in {"running", "submitting", "queued"}:
            raise ValueError("Unfinished task outputs cannot be changed")


def _storage_keys_for_metadata(metadata: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for collection_key in ("outputs", "input_sources"):
        records = metadata.get(collection_key)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict) and str(record.get("storage_driver") or "") == "r2" and record.get("storage_key"):
                keys.append(str(record["storage_key"]))
    return _dedupe_keys(keys)


def _require_omni_owner_id(ctx: WebUIContext, request: Request) -> str | None:
    session = _omni_session_for_request(ctx, request)
    if session is None:
        return None
    return owner_id_for_session(session)


def _omni_session_for_request(ctx: WebUIContext, request: Request) -> OmniSession | None:
    config = ctx.route_helpers.get("omni_poc_config")
    session_store = ctx.route_helpers.get("omni_session_store")
    if config is None or session_store is None or not getattr(config, "enabled", False):
        return None
    session = session_store.get_session(str(request.cookies.get(SESSION_COOKIE_NAME) or ""))
    if session is None:
        raise HTTPException(status_code=401, detail="请先登录 OmniAPI 后再查看图片。")
    return session


async def _prepare_omni_retry(ctx: WebUIContext, request: Request, task_id: str, metadata: dict[str, Any]) -> None:
    config = ctx.route_helpers.get("omni_poc_config")
    session_store = ctx.route_helpers.get("omni_session_store")
    secret_store = ctx.route_helpers.get("omni_task_secret_store")
    if config is None or session_store is None or secret_store is None or not getattr(config, "enabled", False):
        raise HTTPException(status_code=500, detail="Omni POC is not configured")
    session = _omni_session_for_request(ctx, request)
    if session is None:
        raise HTTPException(status_code=401, detail="请先登录 OmniAPI 后再查看图片。")
    params = dict(metadata.get("params") or {})
    key_id = str(params.get("sub2api_api_key_id") or "").strip()
    try:
        omni_key = await resolve_omni_image_key(config, session_store, session, key_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    secret_store.put_task_key(task_id, str(omni_key.get("key") or ""))

    api_mode = "responses" if bool(params.get("web_search")) else str(params.get("api_mode") or "images")
    if api_mode not in {"images", "responses"}:
        api_mode = "images"
    params["api_provider_id"] = "omni-poc"
    params["api_provider_name"] = "Omni API Key"
    params["api_mode"] = api_mode
    params["sub2api_api_key_id"] = str(omni_key.get("id") or key_id)
    params["sub2api_user_id"] = session.sub2api_user_id
    if api_mode == "responses":
        params.pop("api_images_concurrency", None)
    metadata["params"] = params
    metadata["requested_backend"] = _backend_for_api_mode(api_mode)
    metadata["api_provider_id"] = "omni-poc"
    metadata["api_provider_name"] = "Omni API Key"


def _is_omni_poc_task(metadata: dict[str, Any]) -> bool:
    params = metadata.get("params") if isinstance(metadata.get("params"), dict) else {}
    return bool(params.get("omni_poc"))


def _read_owned_task_card(ctx: WebUIContext, task_id: str, owner_id: str | None) -> dict[str, Any]:
    metadata = _read_owned_metadata(ctx, task_id, owner_id)
    return ctx.storage.task_sidebar_card(task_id) if owner_id is None else _sidebar_card_from_metadata(metadata)


def _read_owned_metadata(ctx: WebUIContext, task_id: str, owner_id: str | None) -> dict[str, Any]:
    metadata = ctx.storage.read_metadata(task_id)
    if owner_id is not None and _metadata_owner_id(metadata) != owner_id:
        raise FileNotFoundError(task_id)
    return metadata


def _metadata_owner_id(metadata: dict[str, Any]) -> str:
    owner_id = str(metadata.get("owner_id") or "").strip()
    if owner_id:
        return owner_id
    params = metadata.get("params") if isinstance(metadata.get("params"), dict) else {}
    try:
        user_id = int(params.get("sub2api_user_id"))
    except (TypeError, ValueError):
        return ""
    return f"user_{user_id}" if user_id > 0 else ""


def _with_owned_file_urls(
    ctx: WebUIContext,
    metadata: dict[str, Any],
    owner_id: str | None,
    *,
    active_ids: set[str] | None = None,
    include_request: bool = True,
) -> dict[str, Any]:
    gallery_storage = ctx.gallery_storage if not owner_id else ctx.gallery_storage.scoped(owner_id)
    reference_asset_storage = ctx.reference_asset_storage if not owner_id else ctx.reference_asset_storage.scoped(owner_id)
    return _with_file_urls(
        metadata,
        active_ids,
        gallery_storage,
        reference_asset_storage,
        include_request=include_request,
    )


def _sidebar_card_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in (
            "task_id",
            "created_at",
            "updated_at",
            "viewed_at",
            "queued_at",
            "started_at",
            "mode",
            "status",
            "title",
            "display_title",
            "prompt",
            "owner_id",
            "params",
            "generated_count",
            "failed_count",
            "total_count",
            "thumbnail_urls",
            "output_size",
            "archived_at",
        )
        if key in metadata
    } | {"summary_only": True}


def _storage_keys_for_unselected_outputs(metadata: dict[str, Any]) -> list[str]:
    selected_indexes = set(_positive_int(index) for index in metadata.get("selected_output_indexes", []) if _positive_int(index) is not None)
    if not selected_indexes:
        return []
    keys: list[str] = []
    for record in _visible_completed_output_records(metadata):
        index = _positive_int(record.get("index"))
        if index is None or index in selected_indexes:
            continue
        if str(record.get("storage_driver") or "") == "r2" and record.get("storage_key"):
            keys.append(str(record["storage_key"]))
    return _dedupe_keys(keys)


def _dedupe_keys(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


async def _delete_r2_objects(keys: list[str]) -> None:
    if not keys:
        return
    object_storage = object_storage_from_env()
    if object_storage is None:
        raise HTTPException(status_code=503, detail="Object storage is not configured")
    errors: list[Exception] = []
    for key in keys:
        try:
            await object_storage.delete(key)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise HTTPException(status_code=502, detail="Could not delete stored image") from errors[0]


def _open_path_in_file_manager(path: Path) -> None:
    target = path.resolve(strict=False)
    if sys.platform == "darwin":
        command = ["open", str(target)]
    elif sys.platform.startswith("win"):
        command = ["explorer", str(target)]
    else:
        command = ["xdg-open", str(target)]
    subprocess.Popen(command)
