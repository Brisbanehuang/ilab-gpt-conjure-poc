from __future__ import annotations

import json
from typing import Any

from .context import WebUIContext
from .task_metadata import _gallery_item_response, _with_file_urls


def queue_snapshot(ctx: WebUIContext, *, owner_id: str | None = None) -> dict[str, Any]:
    state = ctx.queue_storage.read_state()
    active_ids = ctx.route_helpers["visible_running_task_ids"]()
    waiting = [
        _with_file_urls(task, active_ids, ctx.gallery_storage, ctx.reference_asset_storage, include_request=False)
        for task in (ctx.storage.read_metadata(task_id) for task_id in state["waiting"] if ctx.storage.metadata_path(task_id).exists())
        if _metadata_owner_id(task) == owner_id or owner_id is None
    ]
    running = []
    for channel_id, item in state["running"].items():
        metadata_path = ctx.storage.metadata_path(str(item.get("task_id") or "")) if isinstance(item, dict) else None
        if metadata_path is None or not metadata_path.exists():
            continue
        metadata = ctx.storage.read_metadata(str(item["task_id"]))
        if owner_id is not None and _metadata_owner_id(metadata) != owner_id:
            continue
        task = _with_file_urls(
            metadata,
            active_ids,
            ctx.gallery_storage,
            ctx.reference_asset_storage,
            include_request=False,
        )
        task["channel_id"] = channel_id
        task["account_id"] = item.get("account_id")
        running.append(task)
    channels = ctx.queue_manager.channels if ctx.queue_manager is not None else []
    queue_channel_available = ctx.route_helpers["queue_channel_available"]
    return {
        "waiting": waiting,
        "running": running,
        "summary": {
            "waiting_count": len(waiting),
            "running_count": len(running),
            "channel_count": len(channels),
            "usable_channel_count": sum(1 for channel in channels if queue_channel_available(channel)),
        },
    }


def event_snapshot(ctx: WebUIContext, *, owner_id: str | None = None) -> dict[str, Any]:
    gallery_storage = ctx.gallery_storage if owner_id is None else ctx.gallery_storage.scoped(owner_id)
    return {
        "type": "snapshot",
        "tasks": ctx.storage.list_recent_task_cards(limit=200, owner_id=owner_id),
        "queue": queue_snapshot(ctx, owner_id=owner_id),
        "gallery": [_gallery_item_response(item) for item in gallery_storage.list_items()],
        "auth": ctx.route_helpers["auth_event_payload"](),
    }


def sse_message(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_comment(comment: str) -> str:
    clean_comment = str(comment).replace("\n", " ")
    return f": {clean_comment}\n\n"


def event_key(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def queued_or_running_task_ids(queue: dict[str, Any]) -> set[str]:
    return {
        str(task.get("task_id"))
        for task in list(queue.get("waiting") or []) + list(queue.get("running") or [])
        if isinstance(task, dict) and task.get("task_id")
    }


def task_event(ctx: WebUIContext, task_id: str, *, owner_id: str | None = None) -> dict[str, Any] | None:
    if not ctx.storage.metadata_path(task_id).exists():
        return None
    metadata = ctx.storage.read_metadata(task_id)
    if owner_id is not None and _metadata_owner_id(metadata) != owner_id:
        return None
    return {
        "type": "task",
        "task": _with_file_urls(
            metadata,
            ctx.route_helpers["visible_running_task_ids"](),
            ctx.gallery_storage,
            ctx.reference_asset_storage,
            include_request=False,
        ),
    }


def task_events_for_finished_ids(ctx: WebUIContext, task_ids: set[str], *, owner_id: str | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for task_id in sorted(task_ids):
        task_payload = task_event(ctx, task_id, owner_id=owner_id)
        if task_payload is not None:
            events.append(task_payload)
    return events


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
