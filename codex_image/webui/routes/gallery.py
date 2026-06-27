from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from codex_image.webui.context import WebUIContext
from codex_image.webui.gallery_storage import GalleryStorage
from codex_image.webui.object_storage import owner_id_for_session
from codex_image.webui.omni_session import SESSION_COOKIE_NAME
from codex_image.webui.storage import _guess_mime_type
from codex_image.webui.task_metadata import _gallery_category_response, _gallery_item_response, _reference_asset_response


def register_gallery_routes(app: FastAPI, ctx: WebUIContext) -> None:
    @app.get("/api/gallery")
    def list_gallery(request: Request, category: str | None = None) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            items = gallery_storage.list_items(category=category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "items": [_gallery_item_response(item) for item in items],
            "categories": [_gallery_category_response(category) for category in gallery_storage.list_categories()],
        }

    @app.get("/api/gallery/categories")
    def list_gallery_categories(request: Request) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        return {"categories": [_gallery_category_response(category) for category in gallery_storage.list_categories()]}

    @app.post("/api/gallery/categories")
    def create_gallery_category(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            category = gallery_storage.create_category(
                name=str(payload["name"]),
                prompt_role=str(payload["prompt_role"]) if "prompt_role" in payload else None,
                order=int(payload["order"]) if "order" in payload and payload["order"] is not None else None,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"category": _gallery_category_response(category)}

    @app.patch("/api/gallery/categories/reorder")
    def reorder_gallery_categories(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            categories = gallery_storage.reorder_categories(list(payload["category_ids"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"categories": [_gallery_category_response(category) for category in categories]}

    @app.patch("/api/gallery/categories/{category_id}")
    def update_gallery_category(category_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            category = gallery_storage.update_category(
                category_id,
                name=str(payload["name"]) if "name" in payload else None,
                prompt_role=str(payload["prompt_role"]) if "prompt_role" in payload else None,
                order=int(payload["order"]) if "order" in payload and payload["order"] is not None else None,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Gallery category not found") from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"category": _gallery_category_response(category)}

    @app.delete("/api/gallery/categories/{category_id}")
    def delete_gallery_category(category_id: str, request: Request, move_to: str | None = None) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            gallery_storage.delete_category(category_id, move_to=move_to)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Gallery category not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "id": category_id}

    @app.post("/api/gallery")
    async def create_gallery_item(
        request: Request,
        name: str = Form(...),
        category: str = Form(...),
        prompt_note: str | None = Form(None),
        image: UploadFile = File(...),
    ) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail="Image is required")
        if image.content_type and not image.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {image.content_type}")
        try:
            item = gallery_storage.create_item(
                name=name,
                category=category,
                filename=image.filename or "image.png",
                data=data,
                content_type=image.content_type,
                prompt_note=prompt_note,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="Gallery name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item": _gallery_item_response(item)}

    @app.patch("/api/gallery/reorder")
    def reorder_gallery_items(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            items = gallery_storage.reorder_items(str(payload["category"]), list(payload["item_ids"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": [_gallery_item_response(item) for item in items]}

    @app.patch("/api/gallery/{item_id}")
    def update_gallery_item(item_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            item = gallery_storage.update_item(
                item_id,
                name=str(payload["name"]) if "name" in payload else None,
                category=str(payload["category"]) if "category" in payload else None,
                prompt_note=str(payload["prompt_note"]) if "prompt_note" in payload else None,
                order=int(payload["order"]) if "order" in payload and payload["order"] is not None else None,
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="Gallery name already exists") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Gallery item not found") from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item": _gallery_item_response(item)}

    @app.put("/api/gallery/{item_id}/image")
    async def replace_gallery_item_image(item_id: str, request: Request, image: UploadFile = File(...)) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail="Image is required")
        if image.content_type and not image.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {image.content_type}")
        try:
            item = gallery_storage.replace_item_image(
                item_id,
                filename=image.filename or "image.png",
                data=data,
                content_type=image.content_type,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Gallery item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"item": _gallery_item_response(item)}

    @app.delete("/api/gallery/{item_id}")
    def delete_gallery_item(item_id: str, request: Request) -> dict[str, Any]:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            gallery_storage.delete_item(item_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Gallery item not found") from exc
        return {"ok": True, "id": item_id}

    @app.get("/api/gallery/{item_id}/image")
    def get_gallery_image(item_id: str, request: Request) -> Response:
        gallery_storage = _gallery_storage_for_request(ctx, request)
        try:
            item = gallery_storage.read_item(item_id)
            path = gallery_storage.image_path(item_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Gallery item not found") from exc
        return FileResponse(
            path,
            media_type=str(item.get("mime_type") or "application/octet-stream"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/reference-assets/recent")
    def list_reference_assets(limit: int = 20) -> dict[str, Any]:
        clean_limit = max(0, min(int(limit), 50))
        return {"items": [_reference_asset_response(item) for item in ctx.reference_asset_storage.list_recent(limit=clean_limit)]}

    @app.delete("/api/reference-assets/{asset_id}")
    def delete_reference_asset(asset_id: str) -> dict[str, bool]:
        try:
            ctx.reference_asset_storage.delete_item(asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid reference asset id") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Reference asset not found: {asset_id}") from exc
        return {"ok": True}

    @app.get("/api/reference-assets/{asset_id}/image")
    def get_reference_asset_image(asset_id: str) -> Response:
        try:
            item = ctx.reference_asset_storage.read_item(asset_id)
            path = ctx.reference_asset_storage.image_path(asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid reference asset id") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Reference asset not found: {asset_id}") from exc
        return FileResponse(path, media_type=str(item.get("mime_type") or _guess_mime_type(path.name)))


def _gallery_storage_for_request(ctx: WebUIContext, request: Request) -> GalleryStorage:
    owner_id = _require_omni_owner_id(ctx, request)
    return ctx.gallery_storage if owner_id is None else ctx.gallery_storage.scoped(owner_id)


def _require_omni_owner_id(ctx: WebUIContext, request: Request) -> str | None:
    config = ctx.route_helpers.get("omni_poc_config")
    session_store = ctx.route_helpers.get("omni_session_store")
    if config is None or session_store is None or not getattr(config, "enabled", False):
        return None
    session = session_store.get_session(str(request.cookies.get(SESSION_COOKIE_NAME) or ""))
    if session is None:
        raise HTTPException(status_code=401, detail="请先登录 OmniAPI 后再管理素材库。")
    return owner_id_for_session(session)
