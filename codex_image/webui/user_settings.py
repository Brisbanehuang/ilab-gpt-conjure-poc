from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from .context import WebUIContext
from .object_storage import owner_id_for_session
from .omni_session import SESSION_COOKIE_NAME
from .settings_store import PromptSnippetSettings, PromptTemplateSettings


def omni_owner_id_for_request(ctx: WebUIContext, request: Request, *, detail: str) -> str | None:
    config = ctx.route_helpers.get("omni_poc_config")
    session_store = ctx.route_helpers.get("omni_session_store")
    if config is None or session_store is None or not getattr(config, "enabled", False):
        return None
    session = session_store.get_session(str(request.cookies.get(SESSION_COOKIE_NAME) or ""))
    if session is None:
        raise HTTPException(status_code=401, detail=detail)
    return owner_id_for_session(session)


def prompt_snippet_settings_for_request(ctx: WebUIContext, request: Request) -> PromptSnippetSettings:
    owner_id = omni_owner_id_for_request(ctx, request, detail="请先登录 OmniAPI 后再管理提示词片段。")
    if owner_id is None:
        return ctx.prompt_snippet_settings
    return PromptSnippetSettings(_owner_settings_dir(ctx, owner_id) / "webui-prompt-snippets.json")


def prompt_template_settings_for_request(ctx: WebUIContext, request: Request) -> PromptTemplateSettings:
    owner_id = omni_owner_id_for_request(ctx, request, detail="请先登录 OmniAPI 后再管理提示词模板。")
    if owner_id is None:
        return ctx.prompt_template_settings
    return PromptTemplateSettings(_owner_settings_dir(ctx, owner_id) / "webui-prompt-templates.json")


def _owner_settings_dir(ctx: WebUIContext, owner_id: str) -> Path:
    return ctx.source_data_root / "users" / owner_id / "settings"
