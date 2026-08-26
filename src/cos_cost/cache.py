"""文件系统 JSON 缓存。键：(account_key, month, data_kind)。不含凭证。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cos_cost.secrets import SecretLeakError, assert_no_secrets

BUCKET_LIST_TTL = timedelta(hours=1)
MONITOR_TTL = timedelta(minutes=30)
ESTIMATED_BILL_TTL = timedelta(hours=1)
CONFIG_TTL = timedelta(hours=1)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CacheRecord:
    account_key: str
    month: str | None
    data_kind: str
    fetched_at: str
    ready: int | None
    immutable: bool
    payload: Any

    def fetched_dt(self) -> datetime:
        return datetime.fromisoformat(self.fetched_at)


class FileCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, account_key: str, data_kind: str, month: str | None) -> Path:
        safe_account = _safe_segment(account_key)
        safe_kind = _safe_segment(data_kind)
        if month:
            name = f"{safe_kind}_{_safe_segment(month)}.json"
        else:
            name = f"{safe_kind}.json"
        return self.root / safe_account / name

    def get(
        self,
        account_key: str,
        data_kind: str,
        month: str | None,
        *,
        ttl: timedelta | None,
        immutable_if_ready: bool = False,
    ) -> CacheRecord | None:
        path = self.path_for(account_key, data_kind, month)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        record = CacheRecord(
            account_key=str(raw.get("account_key") or account_key),
            month=raw.get("month"),
            data_kind=str(raw.get("data_kind") or data_kind),
            fetched_at=str(raw.get("fetched_at") or ""),
            ready=_as_optional_int(raw.get("ready")),
            immutable=bool(raw.get("immutable")),
            payload=raw.get("payload"),
        )
        if record.immutable and immutable_if_ready:
            return record
        if immutable_if_ready and record.ready == 1:
            return record
        if ttl is None:
            return record
        try:
            fetched = record.fetched_dt()
        except ValueError:
            return None
        if utcnow() - fetched > ttl:
            return None
        return record

    def put(
        self,
        account_key: str,
        data_kind: str,
        month: str | None,
        payload: Any,
        *,
        ready: int | None = None,
        immutable: bool = False,
        secret_key: str | None = None,
    ) -> CacheRecord:
        record = {
            "account_key": account_key,
            "month": month,
            "data_kind": data_kind,
            "fetched_at": utcnow().isoformat(),
            "ready": ready,
            "immutable": immutable,
            "payload": payload,
        }
        try:
            assert_no_secrets(record, secret_key=secret_key)
        except SecretLeakError:
            raise
        path = self.path_for(account_key, data_kind, month)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return CacheRecord(
            account_key=account_key,
            month=month,
            data_kind=data_kind,
            fetched_at=str(record["fetched_at"]),
            ready=ready,
            immutable=immutable,
            payload=payload,
        )


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)
    return cleaned or "unknown"


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
