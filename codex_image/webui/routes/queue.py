from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from codex_image.webui.context import WebUIContext
from codex_image.webui.events import (
    event_key,
    event_snapshot,
    queue_snapshot,
    queued_or_running_task_ids,
    sse_comment,
    sse_message,
    task_events_for_finished_ids,
)
from codex_image.webui.object_storage import owner_id_for_session
from codex_image.webui.omni_session import SESSION_COOKIE_NAME

EVENT_STREAM_CHECK_INTERVAL_SECONDS = 1.0


def register_queue_routes(app: FastAPI, ctx: WebUIContext) -> None:
    h = ctx.route_helpers

    @app.get("/api/queue")
    async def get_queue(request: Request) -> dict[str, Any]:
        h["ensure_queue_worker_running"]()
        return queue_snapshot(ctx, owner_id=_require_omni_owner_id(ctx, request))

    @app.get("/api/events", response_model=None)
    async def events(request: Request, stream: bool = False) -> StreamingResponse:
        h["ensure_queue_worker_running"]()
        should_stream = stream
        owner_id = _require_omni_owner_id(ctx, request)

        async def stream_events():
            h["ensure_queue_worker_running"]()
            snapshot = event_snapshot(ctx, owner_id=owner_id)
            yield sse_message(snapshot)
            if not should_stream:
                return

            previous_queue = snapshot["queue"]
            previous_queue_key = event_key(previous_queue)
            previous_task_ids = queued_or_running_task_ids(previous_queue)
            while True:
                await asyncio.sleep(EVENT_STREAM_CHECK_INTERVAL_SECONDS)
                if await request.is_disconnected():
                    return
                h["ensure_queue_worker_running"]()
                queue = queue_snapshot(ctx, owner_id=owner_id)
                queue_key = event_key(queue)
                if queue_key == previous_queue_key:
                    yield sse_comment("heartbeat")
                    continue

                yield sse_message({"type": "queue", "queue": queue})
                current_task_ids = queued_or_running_task_ids(queue)
                for task_payload in task_events_for_finished_ids(ctx, previous_task_ids - current_task_ids, owner_id=owner_id):
                    yield sse_message(task_payload)
                previous_queue_key = queue_key
                previous_task_ids = current_task_ids

        return StreamingResponse(stream_events(), media_type="text/event-stream")

    @app.patch("/api/queue/reorder")
    def reorder_queue(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        owner_id = _require_omni_owner_id(ctx, request)
        task_ids = [str(item) for item in payload.get("task_ids", [])]
        if owner_id is not None:
            state = ctx.queue_storage.read_state()
            owner_waiting = [task_id for task_id in state["waiting"] if _task_belongs_to_owner(ctx, task_id, owner_id)]
            if task_ids != owner_waiting:
                raise HTTPException(status_code=400, detail="Reorder list must match current user queue")
            reordered = iter(task_ids)
            state["waiting"] = [next(reordered) if _task_belongs_to_owner(ctx, task_id, owner_id) else task_id for task_id in state["waiting"]]
            ctx.queue_storage.write_state(state)
            return queue_snapshot(ctx, owner_id=owner_id)
        try:
            ctx.queue_storage.reorder(task_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return queue_snapshot(ctx, owner_id=owner_id)

    @app.post("/api/queue/{task_id}/promote")
    def promote_queue_task(task_id: str, request: Request) -> dict[str, Any]:
        owner_id = _require_omni_owner_id(ctx, request)
        if not _task_belongs_to_owner(ctx, task_id, owner_id):
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            ctx.queue_storage.promote(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return queue_snapshot(ctx, owner_id=owner_id)

    @app.delete("/api/queue/{task_id}")
    async def delete_queue_task(task_id: str, request: Request) -> dict[str, Any]:
        owner_id = _require_omni_owner_id(ctx, request)
        if not _task_belongs_to_owner(ctx, task_id, owner_id):
            raise HTTPException(status_code=404, detail="Task not found")
        state = ctx.queue_storage.read_state()
        if task_id in state["waiting"]:
            ctx.queue_storage.remove_waiting(task_id)
            ctx.storage.delete_task(task_id)
            return {"ok": True, "task_id": task_id, "cancelled": False}
        running_channel_id = h["running_channel_for_task"](task_id)
        if running_channel_id is None:
            raise HTTPException(status_code=409, detail="Only waiting or running tasks can be cancelled from queue")
        try:
            h["mark_task_cancelled"](task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
        ctx.queue_storage.clear_running(running_channel_id)
        ctx.active_task_ids.discard(task_id)
        return {"ok": True, "task_id": task_id, "cancelled": True}


def _require_omni_owner_id(ctx: WebUIContext, request: Request) -> str | None:
    config = ctx.route_helpers.get("omni_poc_config")
    session_store = ctx.route_helpers.get("omni_session_store")
    if config is None or session_store is None or not getattr(config, "enabled", False):
        return None
    session = session_store.get_session(str(request.cookies.get(SESSION_COOKIE_NAME) or ""))
    if session is None:
        raise HTTPException(status_code=401, detail="请先登录 OmniAPI 后再查看图片。")
    return owner_id_for_session(session)


def _task_belongs_to_owner(ctx: WebUIContext, task_id: str, owner_id: str | None) -> bool:
    if owner_id is None:
        return True
    try:
        metadata = ctx.storage.read_metadata(task_id)
    except (FileNotFoundError, ValueError):
        return False
    return _metadata_owner_id(metadata) == owner_id


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
