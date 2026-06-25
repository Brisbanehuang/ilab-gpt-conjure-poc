from __future__ import annotations

from fastapi import FastAPI

from codex_image.webui.context import WebUIContext

from .gallery import register_gallery_routes
from .generation import register_generation_routes
from .omni_poc import register_omni_poc_routes
from .queue import register_queue_routes
from .settings import register_settings_routes
from .tasks import register_task_routes


def register_webui_routes(app: FastAPI, ctx: WebUIContext) -> None:
    register_settings_routes(app, ctx)
    register_task_routes(app, ctx)
    register_queue_routes(app, ctx)
    register_gallery_routes(app, ctx)
    register_omni_poc_routes(app, ctx)
    register_generation_routes(app, ctx)
