from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from codex_image.webui.omni_poc import OmniPOCConfig, mask_api_key


DEFAULT_TITLE_MODEL = "gpt-5.4-mini"
SESSION_COOKIE_NAME = "omni_lens_session"
SESSION_TTL_HOURS = 168


@dataclass(frozen=True)
class OmniSession:
    id: str
    sub2api_user_id: int
    email: str
    username: str
    balance: float
    token_cipher: str
    expires_at: str


def utc_iso(hours_from_now: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).isoformat()


def sub2api_root(config: OmniPOCConfig) -> str:
    base = config.base_url.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def unwrap_sub2api_response(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


class OmniSessionStore:
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
                create table if not exists omni_sessions (
                    id text primary key,
                    sub2api_user_id integer not null,
                    email text not null default '',
                    username text not null default '',
                    balance real not null default 0,
                    token_cipher text not null,
                    expires_at text not null,
                    created_at text not null default current_timestamp
                )
                """
            )
            connection.commit()

    def create_session(self, user: dict[str, Any], access_token: str) -> OmniSession:
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("accessToken is required")
        session_id = str(uuid.uuid4())
        expires_at = utc_iso(SESSION_TTL_HOURS)
        encrypted = self._fernet.encrypt(token.encode("utf-8")).decode("ascii")
        session = OmniSession(
            id=session_id,
            sub2api_user_id=int(user.get("id") or user.get("sub2api_user_id") or 0),
            email=str(user.get("email") or ""),
            username=str(user.get("username") or ""),
            balance=float(user.get("balance") or 0),
            token_cipher=encrypted,
            expires_at=expires_at,
        )
        if session.sub2api_user_id <= 0:
            raise ValueError("Sub2API user id is required")
        with self._connect() as connection:
            connection.execute(
                """
                insert or replace into omni_sessions
                    (id, sub2api_user_id, email, username, balance, token_cipher, expires_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.sub2api_user_id,
                    session.email,
                    session.username,
                    session.balance,
                    session.token_cipher,
                    session.expires_at,
                ),
            )
            connection.commit()
        return session

    def get_session(self, session_id: str | None) -> OmniSession | None:
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                select id, sub2api_user_id, email, username, balance, token_cipher, expires_at
                from omni_sessions
                where id = ? and expires_at > ?
                """,
                (clean_id, datetime.now(timezone.utc).isoformat()),
            ).fetchone()
        if row is None:
            return None
        return OmniSession(
            id=str(row[0]),
            sub2api_user_id=int(row[1]),
            email=str(row[2] or ""),
            username=str(row[3] or ""),
            balance=float(row[4] or 0),
            token_cipher=str(row[5]),
            expires_at=str(row[6]),
        )

    def delete_session(self, session_id: str | None) -> None:
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return
        with self._connect() as connection:
            connection.execute("delete from omni_sessions where id = ?", (clean_id,))
            connection.commit()

    def decrypt_token(self, session: OmniSession) -> str:
        try:
            return self._fernet.decrypt(session.token_cipher.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Stored Omni session token cannot be decrypted") from exc


def session_dto(session: OmniSession | None) -> dict[str, Any]:
    if session is None:
        return {"authenticated": False, "user": None}
    return {
        "authenticated": True,
        "user": {
            "id": session.sub2api_user_id,
            "email": session.email,
            "username": session.username,
            "balance": session.balance,
        },
    }


async def sub2api_json(config: OmniPOCConfig, pathname: str, token: str) -> Any:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{sub2api_root(config)}{pathname}",
            headers={"Authorization": f"Bearer {token}"},
        )
    payload: Any
    try:
        payload = response.json()
    except Exception:
        payload = {"message": response.text}
    if response.status_code < 200 or response.status_code >= 300:
        message = ""
        if isinstance(payload, dict):
            error = payload.get("error")
            message = str((error or {}).get("message") if isinstance(error, dict) else payload.get("message") or payload.get("reason") or "")
        raise ValueError(message or f"Sub2API HTTP {response.status_code}")
    return unwrap_sub2api_response(payload)


async def verify_sub2api_token(config: OmniPOCConfig, access_token: str) -> dict[str, Any]:
    data = await sub2api_json(config, "/api/v1/auth/me", access_token)
    if not isinstance(data, dict):
        raise ValueError("Sub2API user payload is invalid")
    return data


async def list_omni_image_keys(config: OmniPOCConfig, token: str) -> list[dict[str, Any]]:
    data = await sub2api_json(config, "/api/v1/keys?page=1&page_size=100&status=active", token)
    items = data.get("items") if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict)]


def is_openai_image_key_candidate(key: dict[str, Any]) -> bool:
    group = key.get("group") if isinstance(key.get("group"), dict) else {}
    return (
        key.get("status") == "active"
        and group.get("status") == "active"
        and group.get("platform") == "openai"
        and group.get("allow_image_generation") is True
    )


def is_openai_text_key_candidate(key: dict[str, Any]) -> bool:
    group = key.get("group") if isinstance(key.get("group"), dict) else {}
    return key.get("status") == "active" and group.get("status") == "active" and group.get("platform") == "openai"


async def key_supports_model(config: OmniPOCConfig, api_key: str, model_id: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{config.base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        payload = response.json()
        models = payload.get("data") if isinstance(payload, dict) else []
        return any(isinstance(item, dict) and item.get("id") == model_id for item in models)
    except Exception:
        return False


def key_dto(key: dict[str, Any], *, supports_image_model: bool, supports_title_model: bool) -> dict[str, Any]:
    group = key.get("group") if isinstance(key.get("group"), dict) else {}
    return {
        "id": str(key.get("id") or ""),
        "name": str(key.get("name") or ""),
        "group_id": key.get("group_id"),
        "group_name": str(group.get("name") or ""),
        "masked_key": mask_api_key(str(key.get("key") or "")),
        "supports_image_model": supports_image_model,
        "supports_title_model": supports_title_model,
    }


async def usable_key_dtos(config: OmniPOCConfig, token: str) -> list[dict[str, Any]]:
    rows = await list_omni_image_keys(config, token)
    result: list[dict[str, Any]] = []
    for key in rows:
        api_key = str(key.get("key") or "").strip()
        if not api_key or not is_openai_image_key_candidate(key):
            continue
        supports_image = await key_supports_model(config, api_key, config.image_model)
        if not supports_image:
            continue
        supports_title = await key_supports_model(config, api_key, DEFAULT_TITLE_MODEL) if is_openai_text_key_candidate(key) else False
        result.append(key_dto(key, supports_image_model=True, supports_title_model=supports_title))
    return result


async def resolve_omni_image_key(config: OmniPOCConfig, session_store: OmniSessionStore, session: OmniSession, key_id: str) -> dict[str, Any]:
    clean_key_id = str(key_id or "").strip()
    if not clean_key_id:
        raise ValueError("请选择 Omni API Key")
    token = session_store.decrypt_token(session)
    rows = await list_omni_image_keys(config, token)
    for key in rows:
        if str(key.get("id") or "") != clean_key_id:
            continue
        api_key = str(key.get("key") or "").strip()
        if not api_key or not is_openai_image_key_candidate(key):
            break
        if not await key_supports_model(config, api_key, config.image_model):
            break
        key = dict(key)
        key["supports_title_model"] = await key_supports_model(config, api_key, DEFAULT_TITLE_MODEL) if is_openai_text_key_candidate(key) else False
        return key
    raise ValueError("选择的 Omni API Key 不可用或不属于当前用户")
