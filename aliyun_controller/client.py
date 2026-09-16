"""阿里云 API 公共层：统一的错误类型、错误格式化与 client 配置构造。"""
from alibabacloud_tea_openapi import models as open_api_models

DEFAULT_REGION = "cn-hangzhou"


class AliyunApiError(RuntimeError):
    """阿里云 API 调用失败（已格式化为可展示文案）。"""


def format_api_error(e: Exception, operation: str) -> str:
    """将阿里云 SDK 异常格式化为统一的中文错误信息。"""
    data = getattr(e, "data", None) or {}
    code = data.get("Code") or getattr(e, "code", "Unknown")
    message = data.get("Message", str(e))
    status_code = data.get("statusCode", "")
    status_part = f" (HTTP {status_code})" if status_code else ""
    return f"{operation}时出错: {code}{status_part}\n{message}"


def build_open_api_config(
    access_key_id: str,
    access_key_secret: str,
    *,
    region_id: str | None = None,
    endpoint: str | None = None,
) -> open_api_models.Config:
    """构造 open_api_models.Config，按需附加 region_id / endpoint。"""
    kwargs: dict = {
        "access_key_id": access_key_id,
        "access_key_secret": access_key_secret,
    }
    if region_id:
        kwargs["region_id"] = region_id
    if endpoint:
        kwargs["endpoint"] = endpoint
    return open_api_models.Config(**kwargs)
