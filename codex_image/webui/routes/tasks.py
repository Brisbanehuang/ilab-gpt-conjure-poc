from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
import sys
from typing import Any
import zipfile

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from codex_image.webui.context import WebUIContext
from codex_image.webui.object_storage import object_storage_from_env
from codex_image.webui.storage import utc_now
from codex_image.webui.task_metadata import (
    _accept_partial_task_successes,
    _delete_unselected_task_outputs,
    _downloadable_output_paths,
    _output_record_filename,
    _output_thumbnail_fields,
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
    def list_tasks() -> dict[str, Any]:
        active_ids = h["visible_running_task_ids"]()
        return {
            "tasks": [
                _with_file_urls(task, active_ids, ctx.gallery_storage, ctx.reference_asset_storage, include_request=False)
                for task in ctx.storage.list_tasks()
            ]
        }

    @app.get("/api/tasks/recent")
    def list_recent_tasks(limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
        tasks = ctx.storage.list_recent_task_cards(limit=limit)
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
                task = ctx.storage.task_sidebar_card(task_id)
            except (FileNotFoundError, ValueError):
                continue
            tasks_by_id[task_id] = task
            tasks.append(task)
        return {"tasks": tasks}

    @app.get("/api/task-history/summary")
    def task_history_summary() -> dict[str, Any]:
        return ctx.storage.task_history_summary()

    @app.get("/api/task-history/tasks")
    def task_history_tasks(
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
        )

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        try:
            metadata = h["with_stored_request_payload"](task_id, ctx.storage.read_metadata(task_id))
            return {
                "task": _with_file_urls(
                    metadata,
                    h["visible_running_task_ids"](),
                    ctx.gallery_storage,
                    ctx.reference_asset_storage,
                )
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.patch("/api/tasks/{task_id}/viewed")
    def mark_task_viewed(task_id: str) -> dict[str, Any]:
        try:
            metadata = ctx.storage.read_metadata(task_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        metadata["viewed_at"] = utc_now()
        ctx.storage.write_metadata(task_id, metadata)
        return {
            "task": _with_file_urls(
                metadata,
                h["visible_running_task_ids"](),
                ctx.gallery_storage,
                ctx.reference_asset_storage,
                include_request=False,
            )
        }

    @app.get("/api/tasks/{task_id}/outputs.zip")
    def download_task_outputs_zip(task_id: str, selected: bool = Query(False)) -> StreamingResponse:
        try:
            metadata = ctx.storage.read_metadata(task_id)
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
            metadata = ctx.storage.read_metadata(task_id)
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
    def get_task_input_thumbnail(task_id: str, input_index: int) -> FileResponse:
        try:
            metadata = ctx.storage.read_metadata(task_id)
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

    @app.get("/api/tasks/{task_id}/outputs/{output_index}", response_model=None)
    def get_task_output(task_id: str, output_index: int):
        try:
            metadata = ctx.storage.read_metadata(task_id)
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
                data = object_storage.get(str(record["storage_key"]))
            except Exception as exc:
                raise HTTPException(status_code=404, detail="Output not found") from exc
            return StreamingResponse(BytesIO(data), media_type=str(record.get("content_type") or "application/octet-stream"))

        output_path = _safe_output_path(ctx.storage, task_id, _output_record_filename(record))
        if output_path is None or not output_path.is_file():
            raise HTTPException(status_code=404, detail="Output not found")
        return FileResponse(output_path)

    @app.get("/api/tasks/{task_id}/outputs/{output_index}/thumbnail")
    def get_task_output_thumbnail(task_id: str, output_index: int) -> FileResponse:
        try:
            metadata = ctx.storage.read_metadata(task_id)
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
    def update_task_output_selection(task_id: str, output_index: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            metadata = ctx.storage.read_metadata(task_id)
            _ensure_outputs_mutable(task_id, metadata)
            metadata = _set_task_output_selected(ctx.storage, task_id, metadata, output_index, bool(payload.get("selected")))
            return {
                "task": _with_file_urls(
                    metadata,
                    h["visible_running_task_ids"](),
                    ctx.gallery_storage,
                    ctx.reference_asset_storage,
                )
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/outputs/delete-unselected")
    def delete_unselected_task_outputs(task_id: str) -> dict[str, Any]:
        try:
            metadata = ctx.storage.read_metadata(task_id)
            _ensure_outputs_mutable(task_id, metadata)
            metadata = _delete_unselected_task_outputs(ctx.storage, task_id, metadata)
            return {
                "task": _with_file_urls(
                    metadata,
                    h["visible_running_task_ids"](),
                    ctx.gallery_storage,
                    ctx.reference_asset_storage,
                )
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/api/tasks/{task_id}/archive")
    def update_task_archive(task_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            metadata = h["set_task_archived"](task_id, bool(payload.get("archived")))
            return {
                "task": _with_file_urls(
                    metadata,
                    h["visible_running_task_ids"](),
                    ctx.gallery_storage,
                    ctx.reference_asset_storage,
                )
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.post("/api/tasks/{task_id}/retry-failed")
    def retry_failed_task(task_id: str, payload: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
        try:
            metadata = ctx.storage.read_metadata(task_id)
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
        h["apply_retry_api_provider"](task_id, metadata, str((payload or {}).get("api_provider_id") or "").strip() or None)
        ctx.storage.write_metadata(task_id, metadata)
        if ctx.queue_manager is not None:
            ctx.queue_manager.attempts.pop(task_id, None)
            ctx.queue_manager.failed_channels.pop(task_id, None)
        ctx.queue_storage.enqueue(task_id)
        h["ensure_queue_worker_running"]()
        return {
            "task": _with_file_urls(
                metadata,
                h["visible_running_task_ids"](),
                ctx.gallery_storage,
                ctx.reference_asset_storage,
            )
        }

    @app.post("/api/tasks/{task_id}/accept-successes")
    def accept_task_successes(task_id: str) -> dict[str, Any]:
        try:
            metadata = ctx.storage.read_metadata(task_id)
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
            "task": _with_file_urls(
                metadata,
                h["visible_running_task_ids"](),
                ctx.gallery_storage,
                ctx.reference_asset_storage,
            )
        }

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: str) -> dict[str, Any]:
        if task_id in ctx.active_task_ids or h["queue_has_running_task"](task_id):
            raise HTTPException(status_code=409, detail="Running task cannot be deleted")
        try:
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


def _open_path_in_file_manager(path: Path) -> None:
    target = path.resolve(strict=False)
    if sys.platform == "darwin":
        command = ["open", str(target)]
    elif sys.platform.startswith("win"):
        command = ["explorer", str(target)]
    else:
        command = ["xdg-open", str(target)]
    subprocess.Popen(command)
