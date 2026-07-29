from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codex_image.version import APP_VERSION, APP_VERSION_TAG

UPDATE_NOTICE_FILENAME = "update-notice.json"
RELEASES_URL = "https://github.com/Brisbanehuang/ilab-gpt-conjure-poc/tree/share/v0.2.25"


def _parse_semver(value: str | None) -> tuple[int, int, int] | None:
    match = re.match(r"^[vV]?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _normalize_version(value: str | None) -> str:
    parts = _parse_semver(value)
    if not parts:
        return APP_VERSION
    return ".".join(str(part) for part in parts)


def _version_tag(value: str | None) -> str:
    return f"v{_normalize_version(value)}"


def _portable_bundle_dir() -> Path | None:
    raw = os.environ.get("ILAB_CONJURE_BUNDLE_DIR")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() else None


def _data_dir_from_output_root(output_root: Path) -> Path | None:
    raw = os.environ.get("ILAB_CONJURE_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    if output_root.name == "webui-outputs":
        return output_root.parent
    return None


def _portable_version(bundle_dir: Path | None) -> str:
    if not bundle_dir:
        return APP_VERSION
    version_file = bundle_dir / "portable-version.txt"
    try:
        raw = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return APP_VERSION
    return _normalize_version(raw)


def _read_update_notice(data_dir: Path | None, current_version: str) -> dict[str, Any] | None:
    if not data_dir:
        return None
    notice_path = data_dir / UPDATE_NOTICE_FILENAME
    try:
        payload = json.loads(notice_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    latest_version = _normalize_version(str(payload.get("latest_version") or payload.get("latest") or ""))
    current_parts = _parse_semver(current_version)
    latest_parts = _parse_semver(latest_version)
    if not current_parts or not latest_parts or latest_parts <= current_parts:
        return None
    return {
        "latest_version": latest_version,
        "latest_version_label": _version_tag(latest_version),
        "checked_at": payload.get("checked_at"),
        "release_url": payload.get("release_url") or RELEASES_URL,
    }


def _updater_script(bundle_dir: Path | None) -> Path | None:
    if not bundle_dir:
        return None
    system = platform.system().lower()
    if system == "darwin":
        candidate = bundle_dir / "Update WebUI Portable.command"
    elif system == "windows":
        candidate = bundle_dir / "Update WebUI Portable.bat"
    else:
        candidate = bundle_dir / "Update WebUI Portable.command"
    return candidate if candidate.exists() else None


def app_version_payload(output_root: Path) -> dict[str, Any]:
    bundle_dir = _portable_bundle_dir()
    data_dir = _data_dir_from_output_root(output_root)
    current_version = _portable_version(bundle_dir)
    notice = _read_update_notice(data_dir, current_version)
    updater = _updater_script(bundle_dir)
    portable = bundle_dir is not None
    latest_version = notice["latest_version"] if notice else current_version
    return {
        "current_version": current_version,
        "current_version_label": _version_tag(current_version),
        "source": "portable" if portable else "source",
        "portable": portable,
        "update_available": bool(notice),
        "latest_version": latest_version,
        "latest_version_label": _version_tag(latest_version),
        "checked_at": notice.get("checked_at") if notice else None,
        "release_url": notice.get("release_url") if notice else RELEASES_URL,
        "updater_available": updater is not None,
        "updater_label": updater.name if updater else None,
        "server_time": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "app_version": APP_VERSION,
        "app_version_label": APP_VERSION_TAG,
    }


def open_portable_updater(output_root: Path) -> dict[str, Any]:
    bundle_dir = _portable_bundle_dir()
    updater = _updater_script(bundle_dir)
    if not bundle_dir or not updater:
        raise FileNotFoundError("Portable updater script is not available")
    system = platform.system().lower()
    if system == "darwin":
        subprocess.Popen(["open", str(updater)])
    elif system == "windows":
        if hasattr(os, "startfile"):
            os.startfile(str(updater))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["cmd", "/c", "start", "", str(updater)])
    else:
        subprocess.Popen([str(updater)])
    return app_version_payload(output_root) | {"started": True}
