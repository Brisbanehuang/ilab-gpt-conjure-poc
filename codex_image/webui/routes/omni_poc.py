from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from codex_image.webui.context import WebUIContext
from codex_image.webui.omni_poc import validate_omni_api_key


def register_omni_poc_routes(app: FastAPI, ctx: WebUIContext) -> None:
    @app.post("/api/omni/validate")
    async def validate(request: Request) -> dict[str, object]:
        config = ctx.route_helpers.get("omni_poc_config")
        if config is None or not getattr(config, "enabled", False):
            raise HTTPException(status_code=404, detail="Omni POC mode is disabled")
        api_key = str(request.headers.get("x-omni-api-key") or "").strip()
        if not api_key:
            raise HTTPException(status_code=401, detail="Omni API Key is required")
        try:
            return await validate_omni_api_key(config, api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
