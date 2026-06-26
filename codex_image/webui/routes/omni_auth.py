from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response

from codex_image.webui.context import WebUIContext
from codex_image.webui.omni_session import (
    DEFAULT_TITLE_MODEL,
    SESSION_COOKIE_NAME,
    session_dto,
    usable_key_dtos,
    verify_sub2api_token,
)

ALLOWED_AUTH_ORIGINS = {
    "https://api.brislouise.online",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}


def _set_auth_cors(request: Request, response: Response) -> None:
    origin = request.headers.get("origin")
    if origin in ALLOWED_AUTH_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"


def _cookie_value(request: Request) -> str:
    return str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()


def _session_cookie(session_id: str, *, max_age: int = 168 * 60 * 60) -> str:
    return f"{SESSION_COOKIE_NAME}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"


def register_omni_auth_routes(app: FastAPI, ctx: WebUIContext) -> None:
    @app.options("/api/auth/sub2api/exchange")
    async def exchange_options(request: Request, response: Response) -> Response:
        _set_auth_cors(request, response)
        response.status_code = 204
        return response

    @app.post("/api/auth/sub2api/exchange")
    async def exchange(request: Request, response: Response) -> dict[str, object]:
        _set_auth_cors(request, response)
        config = ctx.route_helpers.get("omni_poc_config")
        session_store = ctx.route_helpers.get("omni_session_store")
        if config is None or session_store is None or not getattr(config, "enabled", False):
            raise HTTPException(status_code=404, detail="Omni mode is disabled")
        payload = await request.json()
        access_token = str(payload.get("accessToken") or payload.get("access_token") or "").strip()
        if not access_token:
            raise HTTPException(status_code=400, detail="accessToken is required")
        try:
            user = await verify_sub2api_token(config, access_token)
            session = session_store.create_session(user, access_token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response.headers["Set-Cookie"] = _session_cookie(session.id)
        return session_dto(session)

    @app.get("/api/auth/session")
    async def session(request: Request) -> dict[str, object]:
        session_store = ctx.route_helpers.get("omni_session_store")
        if session_store is None:
            return {"authenticated": False, "user": None}
        return session_dto(session_store.get_session(_cookie_value(request)))

    @app.delete("/api/auth/session")
    async def delete_session(request: Request, response: Response) -> dict[str, object]:
        session_store = ctx.route_helpers.get("omni_session_store")
        if session_store is not None:
            session_store.delete_session(_cookie_value(request))
        response.headers["Set-Cookie"] = _session_cookie("", max_age=0)
        return {"ok": True}

    @app.get("/api/omni/keys")
    async def keys(request: Request) -> dict[str, object]:
        config = ctx.route_helpers.get("omni_poc_config")
        session_store = ctx.route_helpers.get("omni_session_store")
        if config is None or session_store is None or not getattr(config, "enabled", False):
            raise HTTPException(status_code=404, detail="Omni mode is disabled")
        current = session_store.get_session(_cookie_value(request))
        if current is None:
            raise HTTPException(status_code=401, detail="请先登录 OmniAPI 后再使用生图功能。")
        token = session_store.decrypt_token(current)
        return {
            "keys": await usable_key_dtos(config, token),
            "model": config.image_model,
            "title_model": DEFAULT_TITLE_MODEL,
        }
