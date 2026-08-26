"""只读客户端错误：权限不足时降级，不崩溃。"""

from __future__ import annotations

from typing import Any


class PermissionDeniedError(RuntimeError):
    """CAM 未授权或密钥无对应接口权限。"""

    def __init__(self, service: str, message: str) -> None:
        self.service = service
        super().__init__(message)


class CollectCancelled(RuntimeError):
    """用户点了停止拉取，或进程收到 SIGINT。"""


def check_cancel(cancel: Any | None) -> None:
    if cancel is not None and getattr(cancel, "is_set", lambda: False)():
        raise CollectCancelled("已停止拉取")


class TransientApiError(RuntimeError):
    """限流或短暂故障。"""



def is_permission_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "unauthorized",
        "authfailure",
        "accessdenied",
        "accesdenied",
        "forbidden",
        "permissiondenied",
        "cam",
        "notauthorized",
        "invalidsecretid",
        "signaturedoesnotmatch",
        "failedoperation.unauthorized",
    )
    if any(n in text.replace(" ", "") or n in text for n in needles):
        return True
    code = getattr(exc, "get_error_code", None)
    if callable(code):
        return is_permission_error(RuntimeError(str(code())))
    status = getattr(exc, "get_status_code", None)
    if callable(status):
        try:
            return int(status()) in {401, 403}
        except (TypeError, ValueError):
            return False
    return False
