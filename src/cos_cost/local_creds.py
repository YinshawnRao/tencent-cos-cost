"""本机密钥文件：仅供本地测试。不进 git、不进 cache、不进前端。"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cos_cost.secrets import Credentials

LOCAL_CREDS_NAME = ".local-creds.json"


def default_local_creds_path() -> Path:
    return Path.cwd() / LOCAL_CREDS_NAME


@dataclass
class StoredLocalCreds:
    credentials: Credentials
    month: str | None = None
    model_api_key: str | None = None


def load_local_creds(path: Path | None = None) -> StoredLocalCreds | None:
    target = path or default_local_creds_path()
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    secret_id = str(raw.get("secret_id") or "").strip()
    secret_key = str(raw.get("secret_key") or "").strip()
    if not secret_id or not secret_key:
        return None
    token = str(raw.get("token") or "").strip() or None
    month = str(raw.get("month") or "").strip() or None
    model_key = str(raw.get("model_api_key") or "").strip() or None
    return StoredLocalCreds(
        credentials=Credentials(secret_id=secret_id, secret_key=secret_key, token=token),
        month=month,
        model_api_key=model_key,
    )


def save_local_creds(
    creds: Credentials,
    path: Path | None = None,
    *,
    month: str | None = None,
    model_api_key: str | None = None,
) -> Path:
    target = path or default_local_creds_path()
    payload = {
        "secret_id": creds.secret_id,
        "secret_key": creds.secret_key,
        "token": creds.token or "",
        "month": month or "",
        "model_api_key": model_api_key or "",
        "_comment": "本机测试专用。chmod 600。不要提交、不要把 serve 暴露到公网。",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return target


def clear_local_creds(path: Path | None = None) -> None:
    target = path or default_local_creds_path()
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        pass
    leftover = target.with_suffix(".tmp")
    try:
        if leftover.is_file():
            leftover.unlink()
    except OSError:
        pass
