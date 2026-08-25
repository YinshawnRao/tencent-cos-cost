"""只读客户端错误：权限不足时降级，不崩溃。"""

from __future__ import annotations


class PermissionDeniedError(RuntimeError):
    """CAM 未授权或密钥无对应接口权限。"""

    def __init__(self, service: str, message: str) -> None:
        self.service = service
        super().__init__(message)


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
