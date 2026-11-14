"""云监控服务层：ECS 自带监控 + 云监控（Cms）指标查询。

只读接口，零 Textual 依赖、不 print。权限 / 无数据类失败会转换成
``MetricSeries(available=False, reason=...)``，网络 / 未知异常统一抛
``AliyunApiError``。
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_openapi.client import Client as OpenApiClient
from alibabacloud_tea_util.models import RuntimeOptions

from .client import AliyunApiError, build_open_api_config, format_api_error
from .config import load_config

CMS_NAMESPACE = "acs_ecs_dashboard"
CMS_VERSION = "2019-01-01"
CMS_MAX_LENGTH = 1440
"""DescribeMetricList 单页上限。"""

PERMISSION_REASON = (
    "缺少云监控只读权限（需为 AK 授予 cms:QueryMetricList 或 AliyunCloudMonitorReadOnlyAccess）"
)
THROTTLE_REASON = "云监控查询过于频繁，请稍后重试"
EMPTY_REASON = "云监控无该指标数据（实例可能未安装云监控插件，或刚启动未满一个采集周期）"

# 时间窗 → (跨度, ECS period, CMS period)；点数 = 跨度 / period，均 ≤ 400
_WINDOWS: dict[str, tuple[timedelta, int, int]] = {
    "1h": (timedelta(hours=1), 60, 60),
    "6h": (timedelta(hours=6), 60, 60),
    "24h": (timedelta(hours=24), 600, 300),
    "7d": (timedelta(days=7), 3600, 3600),
}

WINDOW_LABELS: dict[str, str] = {
    "1h": "1 小时",
    "6h": "6 小时",
    "24h": "24 小时",
    "7d": "7 天",
}

_CACHE_TTL = 60.0
"""进程内缓存 60 秒：避免来回切页签重复消耗云监控配额。"""


# ---------------------------------------------------------------- 数据模型


@dataclass(frozen=True)
class MetricPoint:
    ts: datetime
    value: float


@dataclass(frozen=True)
class MetricSeries:
    key: str
    label: str
    unit: str = "%"
    points: list[MetricPoint] = field(default_factory=list)
    available: bool = True
    reason: str = ""
    source: str = ""
    detail: str = ""


@dataclass(frozen=True)
class MonitorSnapshot:
    instance_id: str
    window: str
    series: list[MetricSeries]
    fetched_at: datetime
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 纯函数


def window_to_periods(window: str) -> tuple[int, int]:
    """返回 (ECS period, CMS period)；未知窗口抛 ValueError。"""
    try:
        _, ecs_period, cms_period = _WINDOWS[window]
    except KeyError as e:
        raise ValueError(f"未知的时间窗: {window}") from e
    return ecs_period, cms_period


def window_delta(window: str) -> timedelta:
    try:
        return _WINDOWS[window][0]
    except KeyError as e:
        raise ValueError(f"未知的时间窗: {window}") from e


def drop_incomplete_tail(
    points: Iterable[MetricPoint], now: datetime | None = None
) -> list[MetricPoint]:
    """丢弃当前整分钟及之后的点（云监控 / ECS 当前分钟尚未聚合完）。"""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return [p for p in points if p.ts.astimezone(timezone.utc) < cutoff]


def aggregate_max_by_ts(groups: dict[str, list[MetricPoint]] | Iterable[list[MetricPoint]]) -> list[MetricPoint]:
    """多 device 序列按时间戳聚合，取同一时刻的最大值。"""
    values = groups.values() if isinstance(groups, dict) else groups
    best: dict[datetime, float] = {}
    for points in values:
        for point in points:
            ts = point.ts.astimezone(timezone.utc)
            if ts not in best or point.value > best[ts]:
                best[ts] = point.value
    return [MetricPoint(ts, value) for ts, value in sorted(best.items())]


def _error_parts(error) -> tuple[str, str]:
    """从异常或云监控响应 body 中提取 (code, message)。"""
    if isinstance(error, dict):
        code = str(error.get("Code") or error.get("code") or "")
        message = str(error.get("Message") or error.get("message") or "")
        return code, message
    code = str(getattr(error, "code", "") or "")
    message = str(getattr(error, "message", "") or str(error))
    data = getattr(error, "data", None) or {}
    if isinstance(data, dict):
        code = code or str(data.get("Code") or "")
        message = str(data.get("Message") or message)
    return code, message


def _is_permission_error(code: str, message: str) -> bool:
    text = f"{code} {message}".lower().replace("_", "")
    return any(
        key in text
        for key in (
            "403",
            "nopermission",
            "accessdenied",
            "accessforbidden",
            "invalidauthorization",
            "notauthorized",
            "forbidden",
        )
    )


def _is_throttle_error(code: str, message: str) -> bool:
    text = f"{code} {message}".lower()
    return "throttl" in text or code.lower().startswith("throttling")


def classify_cms_error(error) -> str:
    """把云监控失败映射成中文提示；权限 / 限流 / 无数据必须是不同句子。"""
    if error is None:
        return EMPTY_REASON
    code, message = _error_parts(error)
    if _is_permission_error(code, message):
        return PERMISSION_REASON
    if _is_throttle_error(code, message):
        return THROTTLE_REASON
    if not code and not message:
        return EMPTY_REASON
    return f"云监控查询失败：{code or message}"


# ---------------------------------------------------------------- 时间解析


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_iso(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _parse_millis(value) -> datetime | None:
    try:
        millis = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _datapoint_items(raw) -> list[dict]:
    """Datapoints 可能是 JSON 字符串 / list / 单个 dict / null。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _ecs_iso(value: datetime) -> str:
    """ECS DescribeInstanceMonitorData 的 StartTime/EndTime 必须 ISO8601 带秒。"""
    return _utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 查询客户端


class MonitorQuerier:
    """单实例监控查询。所有方法均为只读。"""

    def __init__(self) -> None:
        config = load_config()
        self._ak = config["access_key_id"]
        self._sk = config["access_key_secret"]
        self._clients: dict[str, EcsClient] = {}
        self._cms_clients: dict[str, OpenApiClient] = {}
        self._runtime = RuntimeOptions()
        self._cache: dict[tuple[str, str, str], tuple[float, MonitorSnapshot]] = {}

    # ------------------------------------------------------------ 客户端 / 缓存

    def _client(self, region: str) -> EcsClient:
        client = self._clients.get(region)
        if client is None:
            client = EcsClient(build_open_api_config(self._ak, self._sk, region_id=region))
            self._clients[region] = client
        return client

    def _cms_client(self, region: str) -> OpenApiClient:
        client = self._cms_clients.get(region)
        if client is None:
            client = OpenApiClient(
                build_open_api_config(
                    self._ak,
                    self._sk,
                    region_id=region,
                    endpoint=f"metrics.{region}.aliyuncs.com",
                )
            )
            self._cms_clients[region] = client
        return client

    def invalidate(self) -> None:
        self._cache.clear()

    def _cached(
        self, region: str, instance_id: str, window: str, *, max_age: float | None = None
    ) -> MonitorSnapshot | None:
        """取缓存；``max_age`` 限定可接受的最大存活秒数（``0`` 等于强制刷新）。"""
        item = self._cache.get((region, instance_id, window))
        if item is None:
            return None
        expires_at, snapshot = item
        remaining = expires_at - time.monotonic()
        if remaining <= 0:
            self._cache.pop((region, instance_id, window), None)
            return None
        if max_age is not None and _CACHE_TTL - remaining > max_age:
            return None
        return snapshot

    def _store(self, region: str, instance_id: str, window: str, snapshot: MonitorSnapshot) -> None:
        self._cache[(region, instance_id, window)] = (time.monotonic() + _CACHE_TTL, snapshot)

    # ------------------------------------------------------------ 云监控

    def _cms_call(self, region: str, action: str, query: dict) -> dict:
        params = open_api_models.Params(
            action=action,
            version=CMS_VERSION,
            protocol="HTTPS",
            pathname="/",
            method="POST",
            auth_type="AK",
            style="RPC",
            req_body_type="formData",
            body_type="json",
        )
        request = open_api_models.OpenApiRequest(query={k: str(v) for k, v in query.items()})
        response = self._cms_client(region).call_api(params, request, self._runtime)
        body = response.get("body", {}) if isinstance(response, dict) else getattr(response, "body", {})
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _body_failed(body: dict) -> bool:
        """DescribeMetricLast 的权限失败是 HTTP 200 + body Success=false。"""
        success = body.get("Success")
        if success is False or str(success).lower() == "false":
            return True
        code = str(body.get("Code") or "")
        return bool(code) and code not in ("200", "0")

    def _cms_metric(
        self,
        region: str,
        instance_id: str,
        metric: str,
        period: int,
        start: datetime,
        end: datetime,
        *,
        key: str,
        label: str,
        unit: str = "%",
    ) -> MetricSeries:
        query = {
            "Namespace": CMS_NAMESPACE,
            "MetricName": metric,
            "Dimensions": json.dumps([{"instanceId": instance_id}]),
            "Period": str(period),
            "StartTime": str(int(start.timestamp() * 1000)),
            "EndTime": str(int(end.timestamp() * 1000)),
            "Length": str(CMS_MAX_LENGTH),
        }
        try:
            body = self._cms_call(region, "DescribeMetricList", query)
        except Exception as e:  # noqa: BLE001 — 权限/限流转换成提示，其余上抛
            code, message = _error_parts(e)
            if _is_permission_error(code, message) or _is_throttle_error(code, message):
                return MetricSeries(key, label, unit, available=False, reason=classify_cms_error(e), source="cms")
            raise AliyunApiError(format_api_error(e, f"查询云监控指标 {metric}")) from e
        if self._body_failed(body):
            code, message = _error_parts(body)
            if _is_permission_error(code, message) or _is_throttle_error(code, message):
                return MetricSeries(
                    key, label, unit, available=False, reason=classify_cms_error(body), source="cms"
                )
            raise AliyunApiError(f"查询云监控指标 {metric} 失败：{code or message or '未知错误'}")
        raw = body.get("Datapoints")
        items = _datapoint_items(raw)
        points: list[MetricPoint] = []
        for item in items:
            ts = _parse_millis(item.get("timestamp"))
            value = _float(item.get("Average"))
            if value is None:
                value = _float(item.get("Value"))
            if ts is not None and value is not None:
                points.append(MetricPoint(ts, value))
        points.sort(key=lambda p: p.ts)
        points = drop_incomplete_tail(points, end)
        if not points:
            return MetricSeries(key, label, unit, available=False, reason=EMPTY_REASON, source="cms")
        return MetricSeries(key, label, unit, points=points, source="cms")

    def _cms_disk_metric(
        self, region: str, instance_id: str, period: int, start: datetime, end: datetime
    ) -> MetricSeries:
        """系统盘使用率：同一 timestamp 每个挂载点一条，取最大值并保留设备列表。"""
        query = {
            "Namespace": CMS_NAMESPACE,
            "MetricName": "diskusage_utilization",
            "Dimensions": json.dumps([{"instanceId": instance_id}]),
            "Period": str(period),
            "StartTime": str(int(start.timestamp() * 1000)),
            "EndTime": str(int(end.timestamp() * 1000)),
            "Length": str(CMS_MAX_LENGTH),
        }
        try:
            body = self._cms_call(region, "DescribeMetricList", query)
        except Exception as e:  # noqa: BLE001
            code, message = _error_parts(e)
            if _is_permission_error(code, message) or _is_throttle_error(code, message):
                return MetricSeries(
                    "disk", "系统盘使用率", "%", available=False, reason=classify_cms_error(e), source="cms"
                )
            raise AliyunApiError(format_api_error(e, "查询云监控指标 diskusage_utilization")) from e
        if self._body_failed(body):
            code, message = _error_parts(body)
            if _is_permission_error(code, message) or _is_throttle_error(code, message):
                return MetricSeries(
                    "disk",
                    "系统盘使用率",
                    "%",
                    available=False,
                    reason=classify_cms_error(body),
                    source="cms",
                )
            raise AliyunApiError(f"查询云监控指标 diskusage_utilization 失败：{code or message or '未知错误'}")
        raw = body.get("Datapoints")
        items = _datapoint_items(raw)
        groups: dict[str, list[MetricPoint]] = {}
        for item in items:
            ts = _parse_millis(item.get("timestamp"))
            value = _float(item.get("Average"))
            if value is None:
                value = _float(item.get("Value"))
            if ts is None or value is None:
                continue
            groups.setdefault(str(item.get("device") or ""), []).append(MetricPoint(ts, value))
        points = aggregate_max_by_ts(groups)
        points = drop_incomplete_tail(points, end)
        if not points:
            return MetricSeries("disk", "系统盘使用率", "%", available=False, reason=EMPTY_REASON, source="cms")
        devices = sorted(device for device in groups if device)
        detail = f"设备：{','.join(devices)}" if devices else ""
        return MetricSeries("disk", "系统盘使用率", "%", points=points, source="cms", detail=detail)

    def agent_status(self, region: str, instance_id: str) -> str | None:
        """云监控插件状态；无权限 / 未知时返回 None。"""
        try:
            body = self._cms_call(
                region, "DescribeMonitoringAgentStatuses", {"InstanceIds": instance_id}
            )
        except Exception:  # noqa: BLE001 — 仅用于补充提示
            return None
        if self._body_failed(body):
            return None
        node_list = body.get("NodeStatusList") or {}
        nodes = node_list.get("NodeStatus") if isinstance(node_list, dict) else node_list
        if isinstance(nodes, dict):
            nodes = [nodes]
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            if str(node.get("InstanceId") or "") not in ("", instance_id):
                continue
            return str(node.get("Status") or "") or None
        return None

    @staticmethod
    def _agent_reason(status: str | None) -> str:
        if status == "not_installed":
            return "实例未安装云监控插件，无法获取内存 / 磁盘使用率"
        if status == "stopped":
            return "云监控插件已停止运行，请在实例内启动插件后重试"
        if status == "installing":
            return "云监控插件正在安装，请稍后再试"
        if status == "install_faild":
            return "云监控插件安装失败，请在实例内重新安装"
        return EMPTY_REASON

    # ------------------------------------------------------------ ECS 监控

    def _ecs_points(
        self, region: str, instance_id: str, period: int, start: datetime, end: datetime
    ) -> dict[str, list[MetricPoint]]:
        """一次 ECS 监控请求，返回按字段名分组的点集（CPU / 内外网带宽）。"""
        request = ecs_models.DescribeInstanceMonitorDataRequest(
            instance_id=instance_id,
            start_time=_ecs_iso(start),
            end_time=_ecs_iso(end),
            period=period,
        )
        try:
            response = self._client(region).describe_instance_monitor_data_with_options(
                request, self._runtime
            )
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取实例 {instance_id} 监控数据")) from e
        body = response.body
        monitor = getattr(body, "monitor_data", None)
        items = getattr(monitor, "instance_monitor_data", None) or []
        fields: dict[str, list[MetricPoint]] = {}
        for item in items:
            ts = _parse_iso(getattr(item, "time_stamp", None))
            if ts is None:
                continue
            for name in ("cpu", "internet_bandwidth", "intranet_bandwidth"):
                value = _float(getattr(item, name, None))
                if value is not None:
                    fields.setdefault(name, []).append(MetricPoint(ts, value))
        for points in fields.values():
            points.sort(key=lambda p: p.ts)
            points[:] = drop_incomplete_tail(points, end)
        return fields

    # ------------------------------------------------------------ 对外接口

    def get_cpu(self, region: str, instance_id: str, window: str = "6h") -> MetricSeries:
        _, cms_period = window_to_periods(window)
        start, end = self._range(window)
        series = self._cms_metric(
            region,
            instance_id,
            "CPUUtilization",
            cms_period,
            start,
            end,
            key="cpu",
            label="CPU 使用率",
        )
        if series.points:
            return series
        reason = series.reason or EMPTY_REASON
        ecs_period, _ = window_to_periods(window)
        try:
            points = self._ecs_points(region, instance_id, ecs_period, start, end).get("cpu", [])
        except AliyunApiError as e:
            return MetricSeries(
                "cpu",
                "CPU 使用率",
                "%",
                available=False,
                reason=f"云监控不可用（{reason}）；ECS 监控查询失败：{e}",
                source="ecs",
            )
        if not points:
            return MetricSeries(
                "cpu",
                "CPU 使用率",
                "%",
                available=False,
                reason=f"云监控不可用（{reason}），ECS 监控也无 CPU 数据",
                source="ecs",
            )
        return MetricSeries("cpu", "CPU 使用率", "%", points=points, source="ecs")

    def get_memory(self, region: str, instance_id: str, window: str = "6h") -> MetricSeries:
        _, cms_period = window_to_periods(window)
        start, end = self._range(window)
        return self._cms_metric(
            region,
            instance_id,
            "memory_usedutilization",
            cms_period,
            start,
            end,
            key="memory",
            label="内存使用率",
        )

    def get_disk_usage(self, region: str, instance_id: str, window: str = "6h") -> MetricSeries:
        _, cms_period = window_to_periods(window)
        start, end = self._range(window)
        return self._cms_disk_metric(region, instance_id, cms_period, start, end)

    def get_network(self, region: str, instance_id: str, window: str = "6h") -> list[MetricSeries]:
        """ECS 自带的内 / 外网带宽（可选展示项，不参与页面默认三图）。"""
        ecs_period, _ = window_to_periods(window)
        start, end = self._range(window)
        try:
            fields = self._ecs_points(region, instance_id, ecs_period, start, end)
        except AliyunApiError as e:
            return [
                MetricSeries("net_internet", "公网带宽", "kbits/s", available=False, reason=str(e), source="ecs"),
                MetricSeries("net_intranet", "内网带宽", "kbits/s", available=False, reason=str(e), source="ecs"),
            ]
        result = []
        for key, label, field_name in (
            ("net_internet", "公网带宽", "internet_bandwidth"),
            ("net_intranet", "内网带宽", "intranet_bandwidth"),
        ):
            points = fields.get(field_name, [])
            result.append(
                MetricSeries(
                    key,
                    label,
                    "kbits/s",
                    points=points,
                    available=bool(points),
                    reason="" if points else "ECS 监控无该指标数据",
                    source="ecs",
                )
            )
        return result

    def get_series(
        self, region: str, instance_id: str, window: str = "6h", *, max_age: float | None = None
    ) -> MonitorSnapshot:
        """返回 CPU / 内存 / 系统盘三条序列。

        常规为 3 次 CMS 请求；CPU 无 CMS 数据时额外 1 次 ECS 降级；
        仅当内存 / 磁盘为空且非权限问题时，再查 1 次插件状态以给出准确原因。

        ``max_age`` 用于自动刷新：传 0 表示忽略缓存强制重查，默认走 60 秒进程内缓存。
        """
        cached = self._cached(region, instance_id, window, max_age=max_age)
        if cached is not None:
            return cached
        now = datetime.now(timezone.utc)

        cpu = self.get_cpu(region, instance_id, window)
        memory = self.get_memory(region, instance_id, window)
        disk = self.get_disk_usage(region, instance_id, window)

        # 内存 / 磁盘为空且不是权限问题时，查一次插件状态给出更准确的原因
        if any(item.reason == EMPTY_REASON for item in (memory, disk)):
            status = self.agent_status(region, instance_id)
            if status not in (None, "running"):
                reason = self._agent_reason(status)
                if memory.reason == EMPTY_REASON:
                    memory = MetricSeries(
                        memory.key, memory.label, memory.unit, available=False, reason=reason, source="cms"
                    )
                if disk.reason == EMPTY_REASON:
                    disk = MetricSeries(
                        disk.key,
                        disk.label,
                        disk.unit,
                        available=False,
                        reason=reason,
                        source="cms",
                        detail=disk.detail,
                    )

        series = [cpu, memory, disk]
        warnings: list[str] = []
        for item in series:
            if not item.available and item.reason and item.reason not in warnings:
                warnings.append(item.reason)
        snapshot = MonitorSnapshot(instance_id, window, series, now, warnings)
        self._store(region, instance_id, window, snapshot)
        return snapshot

    # ------------------------------------------------------------ 时间窗

    @staticmethod
    def _range(window: str) -> tuple[datetime, datetime]:
        end = datetime.now(timezone.utc)
        return end - window_delta(window), end
