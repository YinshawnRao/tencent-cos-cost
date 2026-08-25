"""凭证加载与脱敏。SecretKey 绝不写入日志或缓存。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

SECRET_ENV_KEYS = ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_TOKEN")
_SECRET_NAME_RE = re.compile(
    r"(secret[_-]?key|secretkey|cos_secret_key|token|sessiontoken)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Credentials:
    secret_id: str
    secret_key: str
    token: str | None = None

    def __repr__(self) -> str:
        return (
            f"Credentials(secret_id={redact_secret_id(self.secret_id)!r}, "
            f"secret_key='***', token={'***' if self.token else None})"
        )


def load_env_files(project_root: Path | None = None) -> None:
    """加载本地 .env（不覆盖已有环境变量）。"""
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / ".env")
    candidates.append(Path.cwd() / ".env")
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            return


def load_credentials() -> Credentials:
    secret_id = (os.environ.get("COS_SECRET_ID") or "").strip()
    secret_key = (os.environ.get("COS_SECRET_KEY") or "").strip()
    token = (os.environ.get("COS_TOKEN") or "").strip() or None
    if not secret_id or not secret_key:
        raise MissingCredentialsError(
            "缺少 COS_SECRET_ID / COS_SECRET_KEY。"
            "请写入环境变量或本地 .env，或使用 --mock。"
        )
    return Credentials(secret_id=secret_id, secret_key=secret_key, token=token)


class MissingCredentialsError(RuntimeError):
    """现场模式缺少密钥。"""


def redact_secret_id(secret_id: str) -> str:
    if len(secret_id) <= 6:
        return "***"
    return f"{secret_id[:4]}…{secret_id[-2:]}"


def looks_like_secret_key_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name))


def assert_no_secrets(payload: Any, *, secret_key: str | None = None) -> None:
    """缓存落盘前检查：不得出现密钥字段名或 SecretKey 原文。"""
    _walk(payload, secret_key=secret_key)


def _walk(node: Any, *, secret_key: str | None, path: str = "$") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_s = str(key)
            if looks_like_secret_key_name(key_s):
                raise SecretLeakError(f"拒绝缓存疑似密钥字段: {path}.{key_s}")
            _walk(value, secret_key=secret_key, path=f"{path}.{key_s}")
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            _walk(item, secret_key=secret_key, path=f"{path}[{index}]")
        return
    if secret_key and isinstance(node, str) and secret_key and node == secret_key:
        raise SecretLeakError(f"拒绝缓存 SecretKey 原文: {path}")


class SecretLeakError(RuntimeError):
    """检测到密钥即将落入缓存或日志。"""
