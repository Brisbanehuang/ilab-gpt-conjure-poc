from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import httpx
from cryptography.fernet import Fernet, InvalidToken

from codex_image.client import OpenAIImagesImageClient, OpenAIResponsesImageClient


TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_OMNI_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_OMNI_IMAGE_MODEL = "gpt-image-2"


@dataclass(frozen=True)
class OmniPOCConfig:
    enabled: bool
    base_url: str
    image_model: str
    secret_key: str
    db_path: Path
    source_url: str


def omni_poc_enabled(env: Mapping[str, str] | None = None) -> bool:
    payload = os.environ if env is None else env
    return str(payload.get("OMNI_POC_MODE", "")).strip().lower() in TRUE_VALUES


def load_omni_poc_config(output_root: Path, env: Mapping[str, str] | None = None) -> OmniPOCConfig:
    payload = os.environ if env is None else env
    enabled = omni_poc_enabled(payload)
    base_url = str(payload.get("OMNI_BASE_URL") or DEFAULT_OMNI_BASE_URL).strip().rstrip("/")
    image_model = str(payload.get("OMNI_IMAGE_MODEL") or DEFAULT_OMNI_IMAGE_MODEL).strip() or DEFAULT_OMNI_IMAGE_MODEL
    secret_key = str(payload.get("OMNI_POC_SECRET_KEY") or "").strip()
    db_path = Path(payload.get("OMNI_POC_DB_PATH") or (output_root / "source-data" / "omni-poc-secrets.db"))
    source_url = str(payload.get("OMNI_POC_SOURCE_URL") or "https://github.com/Brisbanehuang/ilab-gpt-conjure-poc").strip()
    if enabled and not secret_key:
        raise RuntimeError("OMNI_POC_SECRET_KEY is required when OMNI_POC_MODE is enabled")
    if enabled and base_url != DEFAULT_OMNI_BASE_URL:
        raise RuntimeError("OMNI_BASE_URL must remain http://127.0.0.1:8080/v1 for this POC")
    return OmniPOCConfig(
        enabled=enabled,
        base_url=base_url,
        image_model=image_model,
        secret_key=secret_key,
        db_path=db_path,
        source_url=source_url,
    )


def mask_api_key(api_key: str) -> str:
    clean = str(api_key or "").strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "********"
    return f"{clean[:3]}...{clean[-4:]}"


class OmniTaskSecretStore:
    def __init__(self, config: OmniPOCConfig) -> None:
        self.config = config
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(config.secret_key.encode("ascii"))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.config.db_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists omni_task_keys (
                    task_id text primary key,
                    encrypted_key text not null,
                    created_at text not null default current_timestamp
                )
                """
            )
            connection.commit()

    def put_task_key(self, task_id: str, api_key: str) -> None:
        clean_task_id = str(task_id or "").strip()
        clean_key = str(api_key or "").strip()
        if not clean_task_id:
            raise ValueError("task_id is required")
        if not clean_key:
            raise ValueError("Omni API Key is required")
        encrypted = self._fernet.encrypt(clean_key.encode("utf-8")).decode("ascii")
        with self._connect() as connection:
            connection.execute(
                "insert or replace into omni_task_keys(task_id, encrypted_key) values(?, ?)",
                (clean_task_id, encrypted),
            )
            connection.commit()

    def get_task_key(self, task_id: str) -> str | None:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "select encrypted_key from omni_task_keys where task_id = ?",
                (clean_task_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return self._fernet.decrypt(str(row[0]).encode("ascii")).decode("utf-8")
        except InvalidToken:
            return None

    def clear_task_key(self, task_id: str) -> None:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            return
        with self._connect() as connection:
            connection.execute("delete from omni_task_keys where task_id = ?", (clean_task_id,))
            connection.commit()


async def validate_omni_api_key(config: OmniPOCConfig, api_key: str) -> dict[str, object]:
    clean_key = str(api_key or "").strip()
    if not clean_key:
        raise ValueError("Omni API Key is required")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{config.base_url}/models",
            headers={"Authorization": f"Bearer {clean_key}"},
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"Omni API Key validation failed with HTTP {response.status_code}")
    payload = response.json()
    models = payload.get("data") if isinstance(payload, dict) else []
    model_ids = {str(item.get("id") or "") for item in models if isinstance(item, dict)}
    if config.image_model not in model_ids:
        raise ValueError(f"Omni API Key cannot access {config.image_model}")
    return {"ok": True, "masked_key": mask_api_key(clean_key), "model": config.image_model}


def client_for_task(config: OmniPOCConfig, store: OmniTaskSecretStore, task_id: str, *, api_mode: str = "images") -> OpenAIImagesImageClient | OpenAIResponsesImageClient:
    api_key = store.get_task_key(task_id)
    if not api_key:
        raise RuntimeError("Omni API Key is missing for this task")
    if str(api_mode or "").strip() == "responses":
        return OpenAIResponsesImageClient(api_key=api_key, base_url=config.base_url, image_model=config.image_model)
    return OpenAIImagesImageClient(api_key=api_key, base_url=config.base_url, image_model=config.image_model)
