from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile

from codex_image.client import DEFAULT_MAIN_MODEL, image_model_supports_input_fidelity
from codex_image.webui.context import WebUIContext
from codex_image.webui.executor import (
    _file_to_data_url,
    _instructions_for_transport,
    _normalize_compression,
    _normalize_prompt_fidelity,
    _prompt_for_transport,
    _resolve_gallery_refs,
    _resolve_reference_assets,
)
from codex_image.webui.omni_poc_limits import validate_upload_limits
from codex_image.webui.object_storage import owner_id_for_session
from codex_image.webui.omni_session import SESSION_COOKIE_NAME, OmniSession, resolve_omni_image_key
from codex_image.webui.prompt_ratio import append_ratio_prompt_instruction
from codex_image.webui.storage import utc_now
from codex_image.webui.submit_dedupe import submit_fingerprint
from codex_image.webui.task_metadata import _dedupe_preserve_order, _params, _with_file_urls, _write_queued_metadata
from codex_image.webui.title_generation import generate_task_title

DEFAULT_PROMPT_FIDELITY = "strict"


def register_generation_routes(app: FastAPI, ctx: WebUIContext) -> None:
    h = ctx.route_helpers

    def omni_session_from_request(request: Request):
        config = h.get("omni_poc_config")
        if not config or not getattr(config, "enabled", False):
            return None
        session_store = h.get("omni_session_store")
        if session_store is None:
            raise HTTPException(status_code=500, detail="Omni session store is not configured")
        session = session_store.get_session(str(request.cookies.get(SESSION_COOKIE_NAME) or ""))
        if session is None:
            raise HTTPException(status_code=401, detail="请先登录 OmniAPI 后再使用生图功能。")
        limiter = h.get("omni_submit_limiter")
        client_ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
        if limiter is not None and not limiter.allow(str(client_ip)):
            raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
        queue_state = ctx.queue_storage.read_state()
        if len(queue_state.get("waiting", [])) >= 100:
            raise HTTPException(status_code=429, detail="当前排队任务过多，请稍后再试")
        return session

    def owner_id_from_request_session(session: OmniSession | None) -> str | None:
        return owner_id_for_session(session) if session is not None else None

    def scoped_gallery_storage(owner_id: str | None):
        return ctx.gallery_storage if owner_id is None else ctx.gallery_storage.scoped(owner_id)

    def scoped_reference_asset_storage(owner_id: str | None):
        return ctx.reference_asset_storage if owner_id is None else ctx.reference_asset_storage.scoped(owner_id)

    async def omni_key_from_session(session: OmniSession | None, sub2api_key_id: str | None) -> dict[str, Any] | None:
        if session is None:
            return None
        config = h.get("omni_poc_config")
        session_store = h.get("omni_session_store")
        try:
            return await resolve_omni_image_key(config, session_store, session, str(sub2api_key_id or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def omni_auth_values(omni_key: dict[str, Any] | None, api_provider_id: str | None, api_mode: str | None, codex_mode: str | None, web_search: bool = False) -> tuple[str, str | None, str | None, str | None, str | None, int]:
        if omni_key is not None:
            return "api", "omni-poc", "Omni API Key", "responses" if web_search else "images", None, 1
        auth_source = ctx.auth_settings.read_source() if not h["client_factory_overridden"] else "codex"
        effective_api_provider_id = h["request_api_provider_id"](auth_source, api_provider_id)
        effective_api_provider_name = h["request_api_provider_name"](auth_source, effective_api_provider_id)
        effective_api_mode = h["request_api_mode"](auth_source, api_mode, effective_api_provider_id)
        effective_codex_mode = h["request_codex_mode"](auth_source, codex_mode)
        effective_api_images_concurrency = h["request_api_images_concurrency"](auth_source, effective_api_provider_id)
        return auth_source, effective_api_provider_id, effective_api_provider_name, effective_api_mode, effective_codex_mode, effective_api_images_concurrency

    def put_omni_task_key(task_id: str, omni_key: dict[str, Any] | None) -> None:
        if omni_key is None:
            return
        store = h.get("omni_task_secret_store")
        if store is None:
            raise HTTPException(status_code=500, detail="Omni POC secret store is not configured")
        store.put_task_key(task_id, str(omni_key.get("key") or ""))

    def omni_session_params(request: Request) -> dict[str, Any]:
        session_store = h.get("omni_session_store")
        session = session_store.get_session(str(request.cookies.get(SESSION_COOKIE_NAME) or "")) if session_store is not None else None
        return {"sub2api_user_id": session.sub2api_user_id} if session is not None else {}

    async def omni_task_title(omni_key: dict[str, Any] | None, prompt: str) -> str | None:
        if omni_key is None:
            return None
        config = h.get("omni_poc_config")
        if config is None:
            return None
        return await generate_task_title(config, omni_key, prompt)

    @app.post("/api/generate")
    async def generate(
        request: Request,
        prompt: str = Form(...),
        main_model: str = Form(DEFAULT_MAIN_MODEL),
        model: str = Form("gpt-image-2"),
        size: str = Form("auto"),
        resolution: str | None = Form(None),
        ratio: str | None = Form(None),
        orientation: str | None = Form(None),
        quality: str = Form("low"),
        background: str | None = Form(None),
        output_format: str = Form("png"),
        moderation: str | None = Form(None),
        output_compression: str | None = Form(None),
        n: int = Form(1, ge=1, le=4),
        web_search: bool = Form(False),
        codex_mode: str | None = Form(None),
        api_mode: str | None = Form(None),
        api_provider_id: str | None = Form(None),
        sub2api_key_id: str | None = Form(None),
        prompt_for_model: str | None = Form(None),
        prompt_fidelity: str = Form(DEFAULT_PROMPT_FIDELITY),
        gallery_image_ids: list[str] | None = Form(None),
        reference_asset_ids: list[str] | None = Form(None),
        reference_images: list[UploadFile] | None = File(None),
    ) -> dict[str, Any]:
        session = omni_session_from_request(request)
        omni_key = await omni_key_from_session(session, sub2api_key_id)
        owner_id = owner_id_from_request_session(session)
        gallery_storage = scoped_gallery_storage(owner_id)
        reference_asset_storage = scoped_reference_asset_storage(owner_id)
        if omni_key is not None:
            h["check_omni_storage_quota"](omni_session_params(request))
            try:
                validate_upload_limits(reference_images or [], max_files=4, max_bytes_each=8 * 1024 * 1024)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if omni_key is None and not ctx.auth_checker():
            raise HTTPException(status_code=401, detail="Codex auth is not available")

        gallery_refs, gallery_data_urls = _resolve_gallery_refs(gallery_storage, gallery_image_ids or [])
        uploaded_assets = await h["save_reference_assets"](reference_images or [], storage_override=reference_asset_storage)
        selected_assets, _ = _resolve_reference_assets(reference_asset_storage, reference_asset_ids or [])
        reference_assets = h["dedupe_reference_assets"](uploaded_assets + selected_assets)
        created_at = utc_now()
        input_files: list[Path] = []
        reference_data_urls = [
            _file_to_data_url(reference_asset_storage.image_path(str(item["id"])), mime_type=str(item.get("mime_type") or ""))
            for item in reference_assets
        ]
        all_reference_data_urls = reference_data_urls + gallery_data_urls
        compression = _normalize_compression(output_format, output_compression)
        fidelity = _normalize_prompt_fidelity(prompt_fidelity)
        model_prompt = append_ratio_prompt_instruction(h["model_prompt_for_fidelity"](prompt, prompt_for_model, fidelity), ratio)
        prompt_constraints, guard_instructions = h["prompt_guard_context"](prompt, fidelity)
        (
            auth_source,
            effective_api_provider_id,
            effective_api_provider_name,
            effective_api_mode,
            effective_codex_mode,
            effective_api_images_concurrency,
        ) = omni_auth_values(omni_key, api_provider_id, api_mode, codex_mode, bool(web_search))
        requested_backend = h["backend_for_submit"](auth_source, effective_api_mode, effective_codex_mode)
        transport_mode = effective_api_mode or effective_codex_mode
        web_search_enabled = bool(web_search) and requested_backend.endswith("_responses")
        request_model_prompt = _prompt_for_transport(
            model_prompt,
            auth_source=auth_source,
            api_mode=transport_mode,
            prompt_fidelity=fidelity,
            instructions=guard_instructions,
        )
        request_instructions = _instructions_for_transport(
            auth_source=auth_source,
            api_mode=transport_mode,
            instructions=guard_instructions,
        )

        request_kwargs: dict[str, Any] = dict(
            auth_source=auth_source,
            api_mode=effective_api_mode,
            codex_mode=effective_codex_mode,
            prompt=request_model_prompt,
            main_model=main_model,
            model=model,
            input_images=all_reference_data_urls,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            moderation=moderation,
            output_compression=compression,
        )
        if request_instructions:
            request_kwargs["instructions"] = request_instructions
        if web_search_enabled:
            request_kwargs["web_search"] = True
        request_payload = h["build_image_request_payload"](**request_kwargs)
        stored_request_payload = h["slim_request_payload"](
            request_payload,
            input_files=[path.name for path in input_files],
            gallery_refs=gallery_refs,
            reference_assets=reference_assets,
        )
        stored_request_payload["webui_requested_backend"] = requested_backend
        if effective_api_provider_id is not None:
            stored_request_payload["webui_api_provider_id"] = effective_api_provider_id
        if effective_api_provider_name:
            stored_request_payload["webui_api_provider_name"] = effective_api_provider_name
        if auth_source == "api" and effective_api_mode == "images":
            stored_request_payload["webui_api_images_concurrency"] = effective_api_images_concurrency
        params = _params(main_model, model, size, quality, background, output_format, moderation, compression, n)
        if resolution:
            params["resolution"] = resolution
        if ratio:
            params["ratio"] = ratio
        if orientation:
            params["orientation"] = orientation
        params["prompt_fidelity"] = fidelity
        if web_search_enabled:
            params["web_search"] = True
        if effective_codex_mode is not None:
            params["codex_mode"] = effective_codex_mode
        if effective_api_mode is not None:
            params["api_mode"] = effective_api_mode
        if effective_api_provider_id is not None:
            params["api_provider_id"] = effective_api_provider_id
        if effective_api_provider_name:
            params["api_provider_name"] = effective_api_provider_name
        if auth_source == "api" and effective_api_mode == "images":
            params["api_images_concurrency"] = effective_api_images_concurrency
        if omni_key is not None:
            params["omni_poc"] = True
            params["sub2api_api_key_id"] = str(omni_key.get("id") or sub2api_key_id or "")
            params.update(omni_session_params(request))
        owner_id_for_dedupe = owner_id_from_request_session(session) or "local"
        dedupe_key = submit_fingerprint(
            {
                "owner_id": owner_id_for_dedupe,
                "mode": "generate",
                "prompt": prompt,
                "prompt_for_model": model_prompt,
                "params": params,
                "gallery_refs": [str(ref.get("id") or "") for ref in gallery_refs if isinstance(ref, dict)],
                "reference_assets": [str(item.get("id") or "") for item in reference_assets if isinstance(item, dict)],
            }
        )
        dedupe_cache = getattr(ctx.app.state, "submit_dedupe_cache", None)
        if dedupe_cache is not None:
            existing_task_id = dedupe_cache.get(dedupe_key)
            if existing_task_id:
                try:
                    existing_metadata = ctx.storage.read_metadata(existing_task_id)
                    existing_payload = _with_file_urls(existing_metadata, ctx.active_task_ids, gallery_storage, reference_asset_storage)
                    existing_payload["deduplicated"] = True
                    return {"task": existing_payload, "request": stored_request_payload}
                except FileNotFoundError:
                    pass
        task = ctx.storage.create_task("generate")
        ctx.storage.write_request(task.task_id, stored_request_payload)
        title = await omni_task_title(omni_key, prompt)
        metadata = _write_queued_metadata(
            ctx.storage,
            task.task_id,
            created_at=created_at,
            mode="generate",
            prompt=prompt,
            prompt_for_model=model_prompt,
            params=params,
            input_files=[path.name for path in input_files],
            mask_file=None,
            gallery_refs=gallery_refs,
            reference_assets=reference_assets,
            prompt_constraints=prompt_constraints,
            requested_backend=requested_backend,
            max_attempts=ctx.queue_manager.max_attempts if ctx.queue_manager is not None else 1,
            title=title,
        )
        if dedupe_cache is not None:
            existing_task_id = dedupe_cache.put_if_absent(dedupe_key, task.task_id)
            if existing_task_id:
                try:
                    existing_metadata = ctx.storage.read_metadata(existing_task_id)
                    existing_payload = _with_file_urls(existing_metadata, ctx.active_task_ids, gallery_storage, reference_asset_storage)
                    existing_payload["deduplicated"] = True
                    try:
                        ctx.storage.delete_task(task.task_id)
                    except FileNotFoundError:
                        pass
                    return {"task": existing_payload, "request": stored_request_payload}
                except FileNotFoundError:
                    dedupe_cache.put(dedupe_key, task.task_id)
        put_omni_task_key(task.task_id, omni_key)
        ctx.queue_storage.enqueue(task.task_id)
        h["ensure_queue_worker_running"]()
        return {
            "task": _with_file_urls(metadata, ctx.active_task_ids, gallery_storage, reference_asset_storage),
            "request": stored_request_payload,
        }

    @app.post("/api/edit")
    async def edit(
        request: Request,
        prompt: str = Form(...),
        main_model: str = Form(DEFAULT_MAIN_MODEL),
        model: str = Form("gpt-image-2"),
        size: str = Form("auto"),
        resolution: str | None = Form(None),
        ratio: str | None = Form(None),
        orientation: str | None = Form(None),
        quality: str = Form("low"),
        background: str | None = Form(None),
        output_format: str = Form("png"),
        input_fidelity: str | None = Form(None),
        moderation: str | None = Form(None),
        output_compression: str | None = Form(None),
        n: int = Form(1, ge=1, le=4),
        web_search: bool = Form(False),
        codex_mode: str | None = Form(None),
        api_mode: str | None = Form(None),
        api_provider_id: str | None = Form(None),
        sub2api_key_id: str | None = Form(None),
        prompt_for_model: str | None = Form(None),
        prompt_fidelity: str = Form(DEFAULT_PROMPT_FIDELITY),
        gallery_image_ids: list[str] | None = Form(None),
        reference_asset_ids: list[str] | None = Form(None),
        images: list[UploadFile] | None = File(None),
        mask: UploadFile | None = File(None),
    ) -> dict[str, Any]:
        session = omni_session_from_request(request)
        omni_key = await omni_key_from_session(session, sub2api_key_id)
        owner_id = owner_id_from_request_session(session)
        gallery_storage = scoped_gallery_storage(owner_id)
        reference_asset_storage = scoped_reference_asset_storage(owner_id)
        if omni_key is not None:
            h["check_omni_storage_quota"](omni_session_params(request))
            upload_items = list(images or [])
            if mask is not None:
                upload_items.append(mask)
            try:
                validate_upload_limits(upload_items, max_files=5, max_bytes_each=8 * 1024 * 1024)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if omni_key is None and not ctx.auth_checker():
            raise HTTPException(status_code=401, detail="Codex auth is not available")

        if not images and not _dedupe_preserve_order(gallery_image_ids or []) and not _dedupe_preserve_order(reference_asset_ids or []):
            raise HTTPException(status_code=400, detail="At least one image is required")
        gallery_refs, gallery_data_urls = _resolve_gallery_refs(gallery_storage, gallery_image_ids or [])
        uploaded_assets = await h["save_reference_assets"](images or [], storage_override=reference_asset_storage)
        selected_assets, _ = _resolve_reference_assets(reference_asset_storage, reference_asset_ids or [])
        reference_assets = h["dedupe_reference_assets"](uploaded_assets + selected_assets)
        task = ctx.storage.create_task("edit")
        created_at = utc_now()
        input_files: list[Path] = []
        if not reference_assets and not gallery_data_urls:
            raise HTTPException(status_code=400, detail="At least one image is required")
        mask_files = await h["save_uploads"](task.task_id, [mask] if mask is not None else [], kind="mask")
        image_data_urls = [
            _file_to_data_url(reference_asset_storage.image_path(str(item["id"])), mime_type=str(item.get("mime_type") or ""))
            for item in reference_assets
        ]
        all_image_data_urls = image_data_urls + gallery_data_urls
        mask_data_url = _file_to_data_url(mask_files[0]) if mask_files else None
        compression = _normalize_compression(output_format, output_compression)
        fidelity = _normalize_prompt_fidelity(prompt_fidelity)
        model_prompt = append_ratio_prompt_instruction(h["model_prompt_for_fidelity"](prompt, prompt_for_model, fidelity), ratio)
        prompt_constraints, guard_instructions = h["prompt_guard_context"](prompt, fidelity)
        effective_input_fidelity = input_fidelity if image_model_supports_input_fidelity(model) else None
        (
            auth_source,
            effective_api_provider_id,
            effective_api_provider_name,
            effective_api_mode,
            effective_codex_mode,
            effective_api_images_concurrency,
        ) = omni_auth_values(omni_key, api_provider_id, api_mode, codex_mode, bool(web_search))
        requested_backend = h["backend_for_submit"](auth_source, effective_api_mode, effective_codex_mode)
        transport_mode = effective_api_mode or effective_codex_mode
        web_search_enabled = bool(web_search) and requested_backend.endswith("_responses")
        request_model_prompt = _prompt_for_transport(
            model_prompt,
            auth_source=auth_source,
            api_mode=transport_mode,
            prompt_fidelity=fidelity,
            instructions=guard_instructions,
        )
        request_instructions = _instructions_for_transport(
            auth_source=auth_source,
            api_mode=transport_mode,
            instructions=guard_instructions,
        )

        request_kwargs = dict(
            auth_source=auth_source,
            api_mode=effective_api_mode,
            codex_mode=effective_codex_mode,
            prompt=request_model_prompt,
            action="edit",
            main_model=main_model,
            model=model,
            input_images=all_image_data_urls,
            mask_image=mask_data_url,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            input_fidelity=effective_input_fidelity,
            moderation=moderation,
            output_compression=compression,
        )
        if request_instructions:
            request_kwargs["instructions"] = request_instructions
        if web_search_enabled:
            request_kwargs["web_search"] = True
        request_payload = h["build_image_request_payload"](**request_kwargs)
        image_input_names = [path.name for path in input_files]
        mask_file = mask_files[0].name if mask_files else None
        stored_request_payload = h["slim_request_payload"](
            request_payload,
            input_files=image_input_names,
            gallery_refs=gallery_refs,
            reference_assets=reference_assets,
            mask_file=mask_file,
        )
        stored_request_payload["webui_requested_backend"] = requested_backend
        if effective_api_provider_id is not None:
            stored_request_payload["webui_api_provider_id"] = effective_api_provider_id
        if effective_api_provider_name:
            stored_request_payload["webui_api_provider_name"] = effective_api_provider_name
        if auth_source == "api" and effective_api_mode == "images":
            stored_request_payload["webui_api_images_concurrency"] = effective_api_images_concurrency
        ctx.storage.write_request(task.task_id, stored_request_payload)
        params = _params(main_model, model, size, quality, background, output_format, moderation, compression, n)
        if resolution:
            params["resolution"] = resolution
        if ratio:
            params["ratio"] = ratio
        if orientation:
            params["orientation"] = orientation
        params["prompt_fidelity"] = fidelity
        if effective_input_fidelity:
            params["input_fidelity"] = effective_input_fidelity
        if web_search_enabled:
            params["web_search"] = True
        if effective_codex_mode is not None:
            params["codex_mode"] = effective_codex_mode
        if effective_api_mode is not None:
            params["api_mode"] = effective_api_mode
        if effective_api_provider_id is not None:
            params["api_provider_id"] = effective_api_provider_id
        if effective_api_provider_name:
            params["api_provider_name"] = effective_api_provider_name
        if auth_source == "api" and effective_api_mode == "images":
            params["api_images_concurrency"] = effective_api_images_concurrency
        if omni_key is not None:
            params["omni_poc"] = True
            params["sub2api_api_key_id"] = str(omni_key.get("id") or sub2api_key_id or "")
            params.update(omni_session_params(request))
        title = await omni_task_title(omni_key, prompt)
        metadata = _write_queued_metadata(
            ctx.storage,
            task.task_id,
            created_at=created_at,
            mode="edit",
            prompt=prompt,
            prompt_for_model=model_prompt,
            params=params,
            input_files=image_input_names,
            mask_file=mask_file,
            gallery_refs=gallery_refs,
            reference_assets=reference_assets,
            prompt_constraints=prompt_constraints,
            requested_backend=requested_backend,
            max_attempts=ctx.queue_manager.max_attempts if ctx.queue_manager is not None else 1,
            title=title,
        )
        put_omni_task_key(task.task_id, omni_key)
        ctx.queue_storage.enqueue(task.task_id)
        h["ensure_queue_worker_running"]()
        return {
            "task": _with_file_urls(metadata, ctx.active_task_ids, gallery_storage, reference_asset_storage),
            "request": stored_request_payload,
        }
