"""DNS 服务层：阿里云解析（Alidns）域名与解析记录管理。

只负责 API 调用、参数校验与排序，不含任何终端输出或 TUI 依赖。
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from alibabacloud_alidns20150109 import models as alidns_models
from alibabacloud_alidns20150109.client import Client as Alidns20150109Client

from .client import AliyunApiError, build_open_api_config, format_api_error
from .config import load_config

# ---------------------------------------------------------------- 记录类型元数据

@dataclass(frozen=True)
class RecordField:
    """记录值子字段（结构化输入用）。"""

    name: str
    label: str
    hint: str = ""
    kind: str = "text"  # text | choice
    options: tuple[tuple[str, str], ...] = ()
    placeholder: str = ""


@dataclass(frozen=True)
class RecordTypeSpec:
    """一种解析记录的元数据：展示名、记录值子字段、RR/参数限制。"""

    code: str
    label: str
    hint: str
    fields: tuple[RecordField, ...]
    rr_kind: str = "general"
    rr_hint: str = ""
    needs_priority: bool = False
    line_locked: bool = False
    supports_weight: bool = False
    value_max_len: int = 253


_SINGLE = (RecordField("value", "记录值"),)

RECORD_TYPES: tuple[RecordTypeSpec, ...] = (
    RecordTypeSpec(
        "A", "A（IP 地址）", "例：1.2.3.4；多值请添加多条记录", _SINGLE,
        rr_kind="no_underscore", rr_hint="不支持下划线；@ 表示主域名", supports_weight=True, value_max_len=15,
    ),
    RecordTypeSpec(
        "AAAA", "AAAA（IPv6 地址）", "例：2400:3200::1", _SINGLE,
        rr_kind="no_underscore", rr_hint="不支持下划线；@ 表示主域名", supports_weight=True, value_max_len=39,
    ),
    RecordTypeSpec(
        "CNAME", "CNAME（域名别名）", "例：www.example.com（不含协议 / 路径）", _SINGLE,
        rr_hint="例：www；@ 表示主域名", supports_weight=True,
    ),
    RecordTypeSpec(
        "MX", "MX（邮件交换）", "例：mx1.aliyun.com，优先级 10", _SINGLE,
        rr_kind="no_underscore", rr_hint="不支持下划线；@ 表示主域名", needs_priority=True,
    ),
    RecordTypeSpec(
        "TXT", "TXT（文本）", "例：v=spf1 include:_spf.example.com ~all（≤253 字符）", _SINGLE,
        rr_kind="txt", rr_hint="不支持中文；主机记录不能为 *",
    ),
    RecordTypeSpec(
        "NS", "NS（域名服务器）", "例：ns1.hichina.com", _SINGLE,
        rr_kind="ns", rr_hint="不能填 @ 与 *，仅子域委派",
    ),
    RecordTypeSpec(
        "SRV", "SRV（服务定位）", "格式：优先级 权重 端口 目标域名",
        (
            RecordField("srv_priority", "优先级", "0-65535"),
            RecordField("srv_weight", "权重", "0-65535"),
            RecordField("srv_port", "端口", "1-65535"),
            RecordField("srv_target", "目标域名", "例：www.example.com"),
        ),
        rr_kind="srv", rr_hint="_服务._协议，例：_sip._tcp", value_max_len=512,
    ),
    RecordTypeSpec(
        "CAA", "CAA（证书颁发机构）", '例：0 issue "letsencrypt.org"',
        (
            RecordField("caa_flag", "标记", "flag：0-255，一般填 0"),
            RecordField(
                "caa_tag", "标签", "issue / issuewild / iodef", kind="choice",
                options=(("issue", "issue"), ("issuewild", "issuewild"), ("iodef", "iodef")),
            ),
            RecordField("caa_value", "值", "自动加双引号，例：letsencrypt.org"),
        ),
        value_max_len=512,
    ),
    RecordTypeSpec(
        "ALIAS", "ALIAS（别名，企业版）", "仅企业旗舰版 / 尊享版；与 DNSSEC 互斥；例：www.example.com", _SINGLE,
        supports_weight=True,
    ),
    RecordTypeSpec(
        "SVCB", "SVCB（服务绑定）", "优先级 0=别名模式，非 0=服务模式",
        (
            RecordField("svcb_priority", "优先级", "0=别名模式，非 0=服务模式"),
            RecordField("svcb_target", "目标域名", '例：svc.example.com（"." 表示自身）'),
            RecordField("svcb_params", "服务参数", '例：alpn="h2" ipv4hint="223.5.5.5"'),
        ),
        value_max_len=1024,
    ),
    RecordTypeSpec(
        "HTTPS", "HTTPS（HTTPS 服务）", "格式同 SVCB",
        (
            RecordField("svcb_priority", "优先级", "0=别名模式，非 0=服务模式"),
            RecordField("svcb_target", "目标域名", '例：svc.example.com（"." 表示自身）'),
            RecordField("svcb_params", "服务参数", '例：alpn="h2" port="443"'),
        ),
        value_max_len=1024,
    ),
    RecordTypeSpec(
        "TLSA", "TLSA（证书关联）", "Usage Selector MatchingType Data",
        (
            RecordField(
                "tlsa_usage", "用途", "Usage：证书用途", kind="choice",
                options=(
                    ("0 PKIX-TA", "0"), ("1 PKIX-EE", "1"), ("2 DANE-TA", "2"), ("3 DANE-EE", "3"),
                ),
            ),
            RecordField(
                "tlsa_selector", "选择器", "Selector：选择证书字段", kind="choice",
                options=(("0 完整证书", "0"), ("1 公钥", "1"), ("2 预留", "2")),
            ),
            RecordField(
                "tlsa_matching", "匹配方式", "MatchingType：证书数据格式", kind="choice",
                options=(("0 精确匹配", "0"), ("1 SHA-256", "1"), ("2 SHA-512", "2")),
            ),
            RecordField("tlsa_data", "证书数据", "hex；MatchingType=1 需 64 个 hex，=2 需 128 个"),
        ),
        rr_kind="tlsa", rr_hint="_端口._协议[.子域]，例：_443._tcp", value_max_len=512,
    ),
    RecordTypeSpec(
        "REDIRECT_URL", "显性URL（页面重定向）",
        "301|目标URL 或 302|目标URL（≤500 字符，仅默认线路）",
        (
            RecordField(
                "url_scheme", "跳转方式", "301 永久 / 302 临时", kind="choice",
                options=(("301 永久", "301"), ("302 临时", "302")),
            ),
            RecordField("url_target", "目标 URL", "不支持中文域名与 IP；转发前域名需已备案"),
        ),
        rr_kind="url", rr_hint="不支持泛解析（*）与下划线", line_locked=True, value_max_len=500,
    ),
    RecordTypeSpec(
        "FORWARD_URL", "隐性URL（掩码转发）", "目标 URL（≤500 字符，仅默认线路）",
        (RecordField("url_target", "目标 URL", "不支持中文域名与 IP；转发前域名需已备案"),),
        rr_kind="url", rr_hint="不支持泛解析（*）与下划线", line_locked=True, value_max_len=500,
    ),
)

RECORD_TYPE_MAP: dict[str, RecordTypeSpec] = {spec.code: spec for spec in RECORD_TYPES}
RECORD_TYPE_LABELS: list[tuple[str, str]] = [(spec.label, spec.code) for spec in RECORD_TYPES]

# 向后兼容：只暴露 code 列表；新代码请优先使用 RECORD_TYPES / RECORD_TYPE_MAP
DNS_RECORD_TYPES: list[str] = [spec.code for spec in RECORD_TYPES]

# 一级线路白名单（Select 候选；以 DescribeSupportLines 返回结果为准过滤）
PRIMARY_LINES: list[tuple[str, str]] = [
    ("default", "默认"),
    ("internal", "中国内地"),
    ("oversea", "境外"),
    ("telecom", "电信"),
    ("unicom", "联通"),
    ("mobile", "移动"),
    ("edu", "教育网"),
    ("drpeng", "鹏博士"),
    ("btvn", "广电"),
    ("cstnet", "科技网"),
    ("wexchange", "驰联"),
    ("founder", "方正"),
    ("topway_video", "天威视讯"),
    ("wasu", "华数"),
    ("ocn", "东方有线"),
    ("cnix", "皓宽"),
    ("bgctv", "歌华"),
    ("search", "搜索引擎"),
    ("aliyun", "云厂商"),
]

TTL_CHOICES: list[int] = [1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 43200, 86400]

# 排序类型 / 顺序的展示文案（与服务端无关，仅用于界面）
SORT_TYPE_LABELS = ["创建时间", "二级域名", "首字母"]
SORT_ORDER_LABELS = ["逆序", "正序"]

_STRUCTURED_TYPES = {"SRV", "CAA", "TLSA", "SVCB", "HTTPS", "REDIRECT_URL", "FORWARD_URL"}
_RR_ALLOWED = re.compile(r"^[A-Za-z0-9\-_.*@\u4e00-\u9fff]+$")
_DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
_SRV_RR_RE = re.compile(r"^_[^.\s]+\._[^.\s]+(\..+)?$")
_TLSA_RR_RE = re.compile(r"^_\d+\._(?:tcp|udp|sctp)(\..+)?$", re.IGNORECASE)


def normalize_record_type(record_type: str | None) -> str:
    """统一记录类型大小写，并容忍控制台历史拼写 FORWORD_URL。"""
    code = str(record_type or "").strip().upper()
    return "FORWARD_URL" if code == "FORWORD_URL" else code


def get_record_type(record_type: str | None) -> RecordTypeSpec | None:
    return RECORD_TYPE_MAP.get(normalize_record_type(record_type))


def _norm_rr(rr) -> str:
    return str(rr or "").strip().lower()


def _norm_line(line) -> str:
    return str(line or "default").strip().lower() or "default"


def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 记录值拼装 / 反解析

def compose_record_value(record_type: str, parts: dict) -> str:
    """把结构化子字段拼成 API 需要的单个 Value 字符串。"""
    code = normalize_record_type(record_type)

    def _get(name: str) -> str:
        return str(parts.get(name, "") or "").strip()

    if code == "SRV":
        return " ".join(_get(k) for k in ("srv_priority", "srv_weight", "srv_port", "srv_target"))
    if code == "CAA":
        val = _get("caa_value")
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        return f'{_get("caa_flag")} {_get("caa_tag")} "{val}"'
    if code == "TLSA":
        return " ".join(_get(k) for k in ("tlsa_usage", "tlsa_selector", "tlsa_matching", "tlsa_data"))
    if code in ("SVCB", "HTTPS"):
        head = f"{_get('svcb_priority')} {_get('svcb_target')}"
        params = _get("svcb_params")
        return f"{head} {params}" if params else head
    if code == "REDIRECT_URL":
        return f"{_get('url_scheme') or '301'}|{_get('url_target')}"
    if code == "FORWARD_URL":
        return _get("url_target")
    return _get("value")


def parse_record_value(record_type: str, value: str) -> dict | None:
    """把已有 Value 反解析成子字段；无法解析返回 None（调用方回显原文并提示）。"""
    code = normalize_record_type(record_type)
    text = str(value or "").strip()
    if code == "SRV":
        parts = text.split(None, 3)
        if len(parts) != 4:
            return None
        keys = ("srv_priority", "srv_weight", "srv_port", "srv_target")
        return dict(zip(keys, parts, strict=True))
    if code == "CAA":
        match = re.match(r"^\s*(\d+)\s+(\S+)\s+(.*?)\s*$", text)
        if not match:
            return None
        flag, tag, val = match.groups()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        return {"caa_flag": flag, "caa_tag": tag.lower(), "caa_value": val}
    if code == "TLSA":
        parts = text.split(None, 3)
        if len(parts) != 4:
            return None
        keys = ("tlsa_usage", "tlsa_selector", "tlsa_matching", "tlsa_data")
        return dict(zip(keys, parts, strict=True))
    if code in ("SVCB", "HTTPS"):
        parts = text.split(None, 2)
        if len(parts) < 2:
            return None
        return {
            "svcb_priority": parts[0],
            "svcb_target": parts[1],
            "svcb_params": parts[2] if len(parts) > 2 else "",
        }
    if code == "REDIRECT_URL":
        if "|" in text:
            scheme, target = text.split("|", 1)
            if scheme.strip() in ("301", "302"):
                return {"url_scheme": scheme.strip(), "url_target": target.strip()}
        return {"url_scheme": "301", "url_target": text}
    if code == "FORWARD_URL":
        return {"url_target": text}
    return {"value": text}


# ---------------------------------------------------------------- 校验

def _validate_rr(rr: str, rr_kind: str) -> str | None:
    text = str(rr or "").strip()
    if not text:
        return "主机记录不能为空"
    if len(text) > 253:
        return "主机记录长度不能超过 253 个字符"
    if not _RR_ALLOWED.match(text):
        return "主机记录只能包含字母、数字、- _ . * @ 与中文"
    if text.startswith(("-", ".")) or text.endswith(("-", ".")):
        return "主机记录不能以 - 或 . 开头 / 结尾"
    if ".." in text:
        return "主机记录不能出现连续的点"
    if any(len(part) > 63 for part in text.split(".")):
        return "主机记录以 . 分隔的每段不能超过 63 个字符"
    if "*" in text and text != "*" and not text.startswith("*."):
        return "泛解析只能填写 * 或 *.example 形式"

    if rr_kind == "no_underscore" and "_" in text:
        return "该记录类型的主机记录不允许包含下划线"
    if rr_kind == "txt" and text == "*":
        return "TXT 记录的主机记录不能为 *"
    if rr_kind == "ns":
        if text in ("@", "*"):
            return "NS 记录只能用于子域委派，主机记录不能为 @ 或 *"
    elif rr_kind == "srv":
        if text == "*":
            return "SRV 记录的主机记录不能为 *"
        if not _SRV_RR_RE.match(text):
            return "SRV 记录的主机记录应形如 _服务._协议（例：_sip._tcp）"
    elif rr_kind == "tlsa":
        if text == "*":
            return "TLSA 记录的主机记录不能为 *"
        if not _TLSA_RR_RE.match(text):
            return "TLSA 记录的主机记录应形如 _端口._协议（例：_443._tcp）"
    elif rr_kind == "url":
        if "*" in text:
            return "URL 转发不支持泛解析（*）"
        if "_" in text:
            return "URL 转发的主机记录不允许包含下划线"
    return None


def _is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return "." in value


def _is_ipv6(value: str) -> bool:
    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        return False
    return ":" in value


def _is_domain(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and len(text) <= 253 and bool(_DOMAIN_RE.match(text))


def _is_url_target(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return "目标 URL 不能为空"
    if len(text) > 500:
        return "目标 URL 不能超过 500 个字符"
    match = re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?([^/?#]+)", text)
    host = match.group(1) if match else ""
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    if any("\u4e00" <= ch <= "\u9fff" for ch in host):
        return "目标 URL 不支持中文域名"
    if host and (_is_ipv4(host) or _is_ipv6(host)):
        return "目标 URL 不支持 IP 地址"
    return None


def _validate_parts(code: str, parts: dict, value: str) -> str | None:
    def _get(name: str) -> str:
        return str(parts.get(name, "") or "").strip()

    if code == "SRV":
        priority = _to_int(_get("srv_priority"))
        weight = _to_int(_get("srv_weight"))
        port = _to_int(_get("srv_port"))
        target = _get("srv_target")
        if priority is None or not 0 <= priority <= 65535:
            return "SRV 优先级必须是 0-65535 之间的整数"
        if weight is None or not 0 <= weight <= 65535:
            return "SRV 权重必须是 0-65535 之间的整数"
        if port is None or not 1 <= port <= 65535:
            return "SRV 端口必须是 1-65535 之间的整数"
        if target != "." and not _is_domain(target):
            return "SRV 目标必须是有效域名"
        return None

    if code == "CAA":
        flag = _to_int(_get("caa_flag"))
        tag = _get("caa_tag").lower()
        caa_value = _get("caa_value")
        if flag is None or not 0 <= flag <= 255:
            return "CAA 标记（flag）必须是 0-255 之间的整数"
        if tag not in ("issue", "issuewild", "iodef"):
            return "CAA 标签（tag）只能是 issue / issuewild / iodef"
        if not caa_value:
            return "CAA 的值不能为空"
        if '"' in caa_value:
            return "CAA 的值不能包含双引号"
        return None

    if code == "TLSA":
        usage = _to_int(_get("tlsa_usage"))
        selector = _to_int(_get("tlsa_selector"))
        matching = _to_int(_get("tlsa_matching"))
        data = _get("tlsa_data")
        if usage is None or not 0 <= usage <= 3:
            return "TLSA Usage 必须是 0-3"
        if selector is None or not 0 <= selector <= 2:
            return "TLSA Selector 必须是 0-2"
        if matching is None or not 0 <= matching <= 2:
            return "TLSA MatchingType 必须是 0-2"
        if not data or not re.fullmatch(r"[0-9a-fA-F]+", data):
            return "TLSA 证书数据必须是十六进制字符串"
        if len(data) % 2:
            return "TLSA 证书数据的十六进制长度必须为偶数"
        if matching == 1 and len(data) != 64:
            return "MatchingType=1（SHA-256）需要 64 个 hex 字符"
        if matching == 2 and len(data) != 128:
            return "MatchingType=2（SHA-512）需要 128 个 hex 字符"
        return None

    if code in ("SVCB", "HTTPS"):
        priority = _to_int(_get("svcb_priority"))
        target = _get("svcb_target")
        params = _get("svcb_params")
        if priority is None or not 0 <= priority <= 65535:
            return f"{code} 优先级必须是 0-65535 之间的整数"
        if target != "." and not _is_domain(target):
            return f"{code} 目标必须是有效域名（或 .）"
        if priority == 0 and params:
            return f"{code} 别名模式（优先级 0）不能填写服务参数"
        return None

    if code == "REDIRECT_URL":
        scheme = _get("url_scheme")
        target = _get("url_target")
        if scheme not in ("301", "302"):
            return "显性 URL 的跳转方式必须是 301 或 302"
        err = _is_url_target(target)
        if err:
            return err
        if len(value) > 500:
            return "显性 URL 记录值不能超过 500 个字符"
        return None

    if code == "FORWARD_URL":
        return _is_url_target(_get("url_target"))

    return None


def validate_dns_record(
    rr: str,
    record_type: str,
    value: str,
    ttl: int,
    *,
    priority: int | None = None,
    line: str = "default",
    parts: dict | None = None,
) -> str | None:
    """校验 DNS 记录参数，合法返回 None，否则返回中文错误信息。

    parts 为结构化子字段（SRV/CAA/TLSA/SVCB/HTTPS/URL）；不传时会尝试从 value 反解析。
    """
    code = normalize_record_type(record_type)
    spec = RECORD_TYPE_MAP.get(code)

    rr_error = _validate_rr(rr, spec.rr_kind if spec else "general")
    if rr_error:
        return rr_error

    ttl_value = _to_int(ttl)
    if ttl_value is None or not 1 <= ttl_value <= 86400:
        return "TTL 值必须在 1-86400 之间"

    if spec is None:
        if not str(value or "").strip():
            return "记录值不能为空"
        return None

    if spec.line_locked and _norm_line(line) != "default":
        return f"{code} 记录仅支持默认线路"

    if spec.needs_priority:
        prio = _to_int(priority)
        if prio is None:
            return "MX 记录必须填写优先级（1-50）"
        if not 1 <= prio <= 50:
            return "MX 优先级必须在 1-50 之间（越小越优先）"

    text = str(value or "").strip()
    if not text:
        return "记录值不能为空"
    if len(text) > spec.value_max_len:
        return f"{code} 记录值不能超过 {spec.value_max_len} 个字符"

    if code in _STRUCTURED_TYPES and parts is None:
        parts = parse_record_value(code, text)
        if parts is None:
            return f"{code} 记录值格式不正确"
    parts = parts or {}

    if code == "A":
        return None if _is_ipv4(text) else "A 记录的值必须是有效的 IPv4 地址"
    if code == "AAAA":
        return None if _is_ipv6(text) else "AAAA 记录的值必须是有效的 IPv6 地址"
    if code in ("CNAME", "ALIAS"):
        return None if _is_domain(text) else f"{code} 记录的值必须是域名（不含协议 / 路径）"
    if code == "MX":
        return None if _is_domain(text) else "MX 记录的值必须是邮件服务器域名"
    if code == "TXT":
        if len(text) > 253:
            return "TXT 记录值不能超过 253 个字符"
        if any(ord(ch) > 126 or ord(ch) < 32 for ch in text):
            return "TXT 记录值不支持中文与控制字符"
        return None
    if code == "NS":
        return None if _is_domain(text) else "NS 记录的值必须是权威 DNS 服务器域名"
    if code == "CAA":
        if not re.match(r'^\s*\d+\s+\S+\s+".*"\s*$', text):
            return 'CAA 记录值格式应为 flag tag "value"（value 必须带双引号）'
        return _validate_parts(code, parts, text)
    return _validate_parts(code, parts, text)


# ---------------------------------------------------------------- 冲突预检

def _svcb_mode(value: str) -> int | None:
    parts = str(value or "").strip().split(None, 1)
    return _to_int(parts[0]) if parts else None


def _conflict_reason(new_type: str, old_type: str, rr: str, value: str | None, record: dict) -> str | None:
    if new_type == old_type:
        if new_type in ("SVCB", "HTTPS"):
            new_mode = _svcb_mode(value or "")
            old_mode = _svcb_mode(str(record.get("Value", "")))
            if new_mode is not None and old_mode is not None:
                if (new_mode == 0) != (old_mode == 0):
                    return f"{new_type} 的别名模式（优先级 0）与服务模式互斥"
        if value is not None and str(value).strip() == str(record.get("Value", "")).strip():
            return "存在完全相同的记录（RR / 类型 / 值 / 线路均相同）"
        return None

    if rr == "@":
        if new_type == "CNAME":
            blocked = {"ALIAS", "A", "AAAA", "REDIRECT_URL", "FORWARD_URL", "SRV", "CAA", "SVCB", "HTTPS"}
            if old_type in blocked:
                return f"与现有 {old_type} 记录冲突（@ 下 CNAME 与多数类型互斥）"
        if old_type == "CNAME":
            blocked = {"ALIAS", "A", "AAAA", "REDIRECT_URL", "FORWARD_URL", "SRV", "CAA", "SVCB", "HTTPS"}
            if new_type in blocked:
                return "与现有 CNAME 记录冲突（@ 下 CNAME 与多数类型互斥）"
        alias_group = {"ALIAS", "REDIRECT_URL", "FORWARD_URL"}
        if new_type in alias_group and old_type in {"A", "AAAA"}:
            return f"与现有 {old_type} 记录冲突（同一主机记录下的别名 / URL 记录互斥）"
        if old_type in alias_group and new_type in {"A", "AAAA"}:
            return f"与现有 {old_type} 记录冲突（同一主机记录下的别名 / URL 记录互斥）"
        return None

    # 非 @ 主机记录
    if new_type in ("NS", "CNAME"):
        return f"与现有 {old_type} 记录冲突（NS / CNAME 与其它类型互斥）"
    if old_type in ("NS", "CNAME"):
        return f"与现有 {old_type} 记录冲突（NS / CNAME 与其它类型互斥）"

    alias_group = {"ALIAS", "REDIRECT_URL", "FORWARD_URL"}
    if new_type in {"A", "AAAA"} and old_type in alias_group:
        return f"与现有 {old_type} 记录冲突（A / AAAA 与别名 / URL 记录互斥）"
    if new_type in alias_group and old_type in {"A", "AAAA"}:
        return f"与现有 {old_type} 记录冲突（A / AAAA 与别名 / URL 记录互斥）"
    return None


def find_conflicting_records(
    records: list, rr: str, line: str, record_type: str, value: str | None = None
) -> list[dict]:
    """按阿里云冲突矩阵找出同主机记录 + 同线路的冲突项。

    返回 ``[{"record": 原记录, "reason": 中文原因}]``；调用方排除自身 RecordId 后再传入。
    """
    new_type = normalize_record_type(record_type)
    target_rr = _norm_rr(rr)
    target_line = _norm_line(line)
    conflicts = []
    for record in records:
        if _norm_rr(record.get("RR")) != target_rr:
            continue
        if _norm_line(record.get("Line")) != target_line:
            continue
        old_type = normalize_record_type(record.get("Type"))
        reason = _conflict_reason(new_type, old_type, target_rr, value, record)
        if reason:
            conflicts.append({"record": record, "reason": reason})
    return conflicts


# ---------------------------------------------------------------- 服务客户端

class AliCloudDnsQuerier:
    """DNS 查询客户端。所有 API 失败统一抛出 AliyunApiError。"""

    def __init__(self) -> None:
        config = load_config()
        self.client = Alidns20150109Client(
            build_open_api_config(
                config["access_key_id"],
                config["access_key_secret"],
                endpoint="dns.aliyuncs.com",
            )
        )
        self._support_lines: list[dict] | None = None

    def get_domains(self) -> list:
        request = alidns_models.DescribeDomainsRequest()
        try:
            response = self.client.describe_domains(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "获取域名列表")) from e
        return response.body.to_map().get("Domains", {}).get("Domain", [])

    def get_domain_records(self, domain_name: str) -> list:
        all_records: list = []
        page_number = 1
        page_size = 500
        try:
            while True:
                request = alidns_models.DescribeDomainRecordsRequest(
                    domain_name=domain_name,
                    page_number=page_number,
                    page_size=page_size,
                )
                response = self.client.describe_domain_records(request)
                response_dict = response.body.to_map()
                records = response_dict.get("DomainRecords", {}).get("Record", [])
                if not records:
                    break
                all_records.extend(records)
                if len(all_records) >= response_dict.get("TotalCount", 0):
                    break
                page_number += 1
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取域名 {domain_name} 的解析记录")) from e
        return all_records

    def add_domain_record(
        self,
        domain_name: str,
        rr: str,
        type: str,
        value: str,
        ttl: int = 600,
        *,
        line: str = "default",
        priority: int | None = None,
    ) -> str | None:
        request = alidns_models.AddDomainRecordRequest(
            domain_name=domain_name,
            rr=rr,
            type=type,
            value=value,
            ttl=ttl,
            line=line,
            priority=priority,
        )
        try:
            response = self.client.add_domain_record(request)
            return response.body.record_id
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "添加解析记录")) from e

    def update_domain_record(
        self,
        record_id: str,
        rr: str,
        type: str,
        value: str,
        ttl: int = 600,
        *,
        line: str = "default",
        priority: int | None = None,
    ) -> None:
        # UpdateDomainRecord 是整条覆盖：Line / Priority 不传会被重置为默认 / 空
        request = alidns_models.UpdateDomainRecordRequest(
            record_id=record_id,
            rr=rr,
            type=type,
            value=value,
            ttl=ttl,
            line=line,
            priority=priority,
        )
        try:
            self.client.update_domain_record(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "更新解析记录")) from e

    def delete_domain_record(self, record_id: str) -> None:
        request = alidns_models.DeleteDomainRecordRequest(record_id=record_id)
        try:
            self.client.delete_domain_record(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "删除解析记录")) from e

    def set_domain_record_status(self, record_id: str, status: str) -> None:
        """status: "Enable" / "Disable"。"""
        request = alidns_models.SetDomainRecordStatusRequest(record_id=record_id, status=status)
        try:
            self.client.set_domain_record_status(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "设置解析记录状态")) from e

    def get_support_lines(self, domain_name: str | None = None) -> list[dict]:
        """支持的解析线路；同一 client 只请求一次（失败不缓存）。"""
        if self._support_lines is not None:
            return [dict(item) for item in self._support_lines]
        request = alidns_models.DescribeSupportLinesRequest(domain_name=domain_name)
        try:
            response = self.client.describe_support_lines(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "获取解析线路列表")) from e
        data = response.body.to_map()
        lines = []
        for item in data.get("RecordLines", {}).get("RecordLine", []) or []:
            code = str(item.get("LineCode") or "").strip()
            if not code:
                continue
            name = str(item.get("LineName") or item.get("LineDisplayName") or code)
            display = str(item.get("LineDisplayName") or name)
            lines.append({"code": code, "name": name, "display_name": display})
        self._support_lines = lines
        return [dict(item) for item in lines]

    def get_custom_lines(self, domain_name: str) -> list[dict]:
        all_lines: list = []
        page_number = 1
        page_size = 100
        try:
            while True:
                request = alidns_models.DescribeCustomLinesRequest(
                    domain_name=domain_name,
                    page_number=page_number,
                    page_size=page_size,
                )
                response = self.client.describe_custom_lines(request)
                data = response.body.to_map()
                items = data.get("CustomLines", []) or []
                for item in items:
                    code = str(item.get("Code") or "").strip()
                    if code:
                        all_lines.append({"code": code, "name": str(item.get("Name") or code)})
                if not items or len(all_lines) >= int(data.get("TotalItems", 0) or 0):
                    break
                page_number += 1
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取域名 {domain_name} 的自定义线路")) from e
        return all_lines

    def set_record_remark(self, record_id: str, remark: str) -> None:
        """备注独立接口；remark 传空串 = 删除原备注。"""
        request = alidns_models.UpdateDomainRecordRemarkRequest(
            record_id=record_id, remark=remark if remark is not None else ""
        )
        try:
            self.client.update_domain_record_remark(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "设置解析记录备注")) from e

    def set_record_weight(self, record_id: str, weight: int) -> None:
        request = alidns_models.UpdateDNSSLBWeightRequest(record_id=record_id, weight=weight)
        try:
            self.client.update_dnsslb_weight(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "设置解析记录权重")) from e

    def enable_record_weight_group(
        self, domain_name: str, rr: str, record_type: str, line: str = "default", open_: bool = True
    ) -> int | None:
        """开启 / 关闭整组权重（SetDNSSLBStatus，幂等）。"""
        request = alidns_models.SetDNSSLBStatusRequest(
            domain_name=domain_name,
            sub_domain=f"{rr}.{domain_name}",
            open=open_,
            type=record_type,
            line=line,
        )
        try:
            response = self.client.set_dnsslb_status(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "设置权重开关")) from e
        return response.body.to_map().get("RecordCount")

    def get_slb_sub_domains(self, domain_name: str) -> list[dict]:
        """查询已配置权重的子域名（用于保存前判断是否需要开启整组权重）。"""
        all_items: list = []
        page_number = 1
        page_size = 100
        try:
            while True:
                request = alidns_models.DescribeDNSSLBSubDomainsRequest(
                    domain_name=domain_name,
                    page_number=page_number,
                    page_size=page_size,
                )
                response = self.client.describe_dnsslb_sub_domains(request)
                data = response.body.to_map()
                items = data.get("SlbSubDomains", {}).get("SlbSubDomain", []) or []
                for item in items:
                    lines = {}
                    for alg in item.get("LineAlgorithms", {}).get("LineAlgorithm", []) or []:
                        lines[str(alg.get("Line") or "default")] = bool(alg.get("Open"))
                    all_items.append({
                        "sub_domain": item.get("SubDomain"),
                        "type": item.get("Type"),
                        "record_count": item.get("RecordCount"),
                        "open": bool(item.get("Open")),
                        "lines": lines,
                    })
                if not items or len(all_items) >= int(data.get("TotalCount", 0) or 0):
                    break
                page_number += 1
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "获取权重配置")) from e
        return all_items


# ---------------------------------------------------------------- 记录比较 / 排序

def record_content_changed(
    record: dict,
    rr: str,
    record_type: str,
    value: str,
    ttl: int,
    *,
    priority: int | None = None,
    line: str | None = None,
) -> bool:
    """编辑后的内容是否与现有记录不同。

    阿里云 UpdateDomainRecord 在 RR/Type/Value 与原记录完全相同时会返回
    ``DomainRecordDuplicate``（解析记录已存在），因此调用方必须先做差异判断，
    内容未变化时跳过更新。priority / line 为 None 时表示调用方不比较该维度。
    """
    current_ttl = record.get("TTL")
    try:
        same_ttl = int(current_ttl) == int(ttl)
    except (TypeError, ValueError):
        same_ttl = str(current_ttl) == str(ttl)

    same_type = normalize_record_type(record.get("Type")) == normalize_record_type(record_type)
    same = (
        str(record.get("RR", "")).strip() == str(rr).strip()
        and same_type
        and str(record.get("Value", "")).strip() == str(value).strip()
        and same_ttl
    )
    if same and priority is not None:
        same = _to_int(record.get("Priority")) == _to_int(priority)
    if same and line is not None:
        same = _norm_line(record.get("Line")) == _norm_line(line)
    return not same


def sort_records(records: list, sort_type: int, sort_order: int) -> list:
    """返回排序后的新列表。sort_type: 0 创建时间 / 1 二级域名 / 2 首字母；sort_order: 0 逆序 / 1 正序。"""
    records = list(records)
    reverse = sort_order == 0
    if sort_type == 0:
        if reverse:
            records.reverse()
        return records
    if sort_type == 1:
        return sorted(records, key=lambda r: r.get("RR", "").rsplit(".", 1)[-1], reverse=reverse)
    if sort_type == 2:
        return sorted(records, key=lambda r: r.get("RR", ""), reverse=reverse)
    return records
