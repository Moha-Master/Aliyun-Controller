"""ECS 服务层：地域探测、实例生命周期、安全组规则与磁盘查询。

只负责 API 调用、参数校验与归一化；零 Textual 依赖、不 print。
所有云 API 失败统一抛 ``AliyunApiError``（参数不合法抛 ``ValueError``）。
"""
from __future__ import annotations

import ipaddress
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from alibabacloud_ecs20140526 import models as ecs_models
from alibabacloud_ecs20140526.client import Client as EcsClient
from alibabacloud_tea_util.models import RuntimeOptions

from .client import DEFAULT_REGION, AliyunApiError, build_open_api_config, format_api_error
from .config import load_config

# ---------------------------------------------------------------- 状态与配色

INSTANCE_STATUS_LABELS: dict[str, str] = {
    "Pending": "创建中",
    "Starting": "启动中",
    "Running": "运行中",
    "Stopping": "停止中",
    "Stopped": "已停止",
}

# 与 widgets.STYLE_* 同值（服务层不依赖 Textual，故此处用字面量）
STATUS_STYLE: dict[str, str] = {
    "Running": "#4ec96a",   # STYLE_OK
    "Stopped": "#e06c75",   # STYLE_ERR
    "Pending": "#e5c07b",   # STYLE_WARN
    "Starting": "#e5c07b",
    "Stopping": "#e5c07b",
}

_STABLE_RUNNING = "Running"
_STABLE_STOPPED = "Stopped"

BATCH_LIMIT = 100
"""StartInstances / StopInstances 等批量接口单次最多 100 台。"""

_COMMAND_TERMINAL = {"Finished", "Success", "Failed", "Stopped", "Timeout"}


def _chunked(items: list[str], size: int = BATCH_LIMIT) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def status_label(status: str | None) -> str:
    """未知状态原样显示，避免新状态值被吞成空白。"""
    text = str(status or "").strip()
    return INSTANCE_STATUS_LABELS.get(text, text or "未知")


def can_start(status: str | None) -> bool:
    """只有已停止的实例可以开机；中间态一律 False。"""
    return str(status or "") == _STABLE_STOPPED


def can_stop(status: str | None) -> bool:
    return str(status or "") == _STABLE_RUNNING


def can_reboot(status: str | None) -> bool:
    return str(status or "") == _STABLE_RUNNING


# ---------------------------------------------------------------- 安全组规则常量

RULE_PROTOCOLS: tuple[str, ...] = ("TCP", "UDP", "ICMP", "GRE", "ALL")
_RULE_POLICIES = {"ACCEPT": "Accept", "DROP": "Drop"}
_DEFAULT_PORTS = {"ICMP", "GRE", "ALL"}


def describe_direction_label(direction: str | None) -> str:
    return {"ingress": "入方向", "egress": "出方向"}.get(str(direction or "").lower(), "全部")


def normalize_rule_type(value: str | None) -> str:
    """协议统一大写；空值按 TCP。非法值原样返回，交给 validate_rule 报错。"""
    return str(value or "TCP").strip().upper() or "TCP"


def parse_port_range(text: str) -> str:
    """把用户输入归一化为 ``起始/结束``。

    支持 ``22``、``22/22``、``8000/9000``、``-1/-1``；非法输入抛 ValueError。
    """
    raw = str(text or "").strip().replace(" ", "")
    if raw in ("-1/-1", "-1"):
        return "-1/-1"
    parts = raw.split("/")
    if len(parts) == 1:
        parts = [parts[0], parts[0]]
    if len(parts) != 2 or not all(parts):
        raise ValueError("端口格式应为 22 或 8000/9000")
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise ValueError("端口必须是整数（例：22 或 8000/9000）") from e
    if not (1 <= start <= 65535 and 1 <= end <= 65535):
        raise ValueError("端口必须在 1-65535 之间")
    if start > end:
        raise ValueError("端口起始值不能大于结束值")
    return f"{start}/{end}"


# ---------------------------------------------------------------- 数据模型

@dataclass(frozen=True)
class RegionInfo:
    region_id: str
    local_name: str = ""
    instance_count: int = 0
    status: str = "available"


@dataclass(frozen=True)
class InstanceInfo:
    instance_id: str = ""
    name: str = ""
    status: str = ""
    instance_type: str = ""
    cpu: int = 0
    memory: int = 0
    osname: str = ""
    image_id: str = ""
    private_ips: list[str] = field(default_factory=list)
    public_ips: list[str] = field(default_factory=list)
    eip: str = ""
    vpc_id: str = ""
    vswitch_id: str = ""
    zone_id: str = ""
    creation_time: str = ""
    expired_time: str = ""
    charge_type: str = ""
    internet_charge_type: str = ""
    bandwidth_out: int = 0
    security_group_ids: list[str] = field(default_factory=list)
    lock_reason: str = ""
    region_id: str = ""
    network_options: str = ""


@dataclass(frozen=True)
class SecurityGroupInfo:
    security_group_id: str = ""
    name: str = ""
    description: str = ""
    sg_type: str = ""
    vpc_id: str = ""
    rule_count: int = 0
    inner_access_policy: str = ""


@dataclass(frozen=True)
class RuleInfo:
    rule_id: str = ""
    direction: str = "ingress"
    ip_protocol: str = ""
    port_range: str = ""
    policy: str = ""
    priority: int = 100
    description: str = ""
    source_cidr_ip: str = ""
    source_group_id: str = ""
    source_group_name: str = ""
    ipv6_source_cidr_ip: str = ""
    source_prefix_list_id: str = ""
    dest_cidr_ip: str = ""
    dest_group_id: str = ""
    dest_prefix_list_id: str = ""
    dest_group_name: str = ""
    ipv6_dest_cidr_ip: str = ""
    nic_type: str = ""


@dataclass(frozen=True)
class DiskInfo:
    disk_id: str = ""
    name: str = ""
    category: str = ""
    size: int = 0
    status: str = ""
    disk_type: str = ""
    device: str = ""
    encrypted: bool = False
    charge_type: str = ""
    expired_time: str = ""


@dataclass(frozen=True)
class VpcInfo:
    vpc_id: str = ""
    name: str = ""
    cidr_block: str = ""
    is_default: bool = False
    status: str = ""


@dataclass(frozen=True)
class EipInfo:
    allocation_id: str = ""
    ip_address: str = ""
    bandwidth: int = 0
    internet_charge_type: str = ""
    status: str = ""
    charge_type: str = ""
    expired_time: str = ""


@dataclass(frozen=True)
class AutoRenewInfo:
    status: str = ""
    duration: int = 0
    period_unit: str = ""
    auto_renew_enabled: bool = False


@dataclass(frozen=True)
class CommandResult:
    """云助手命令执行结果；``finished`` 表示是否已到终态。"""

    invoke_id: str = ""
    status: str = ""
    exit_code: int | None = None
    output: str = ""
    error: str = ""
    finished: bool = False


@dataclass
class BatchResult:
    """批量启停结果：``success`` 为已下发接受的实例，``failed`` 为实例 → 错误文案。"""

    success: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass
class RuleInput:
    """新增 / 修改规则时的输入。existing 为原规则（修改时用于全量回填）。"""

    direction: str = "ingress"
    ip_protocol: str = "TCP"
    port_range: str = "22/22"
    policy: str = "Accept"
    priority: int = 100
    description: str = ""
    source_kind: str = "cidr"  # cidr | group
    source_value: str = ""
    rule_id: str | None = None
    existing: RuleInfo | None = None


# ---------------------------------------------------------------- 规则校验 / 展示

def rule_auth_kind(rule: RuleInfo) -> str:
    """规则授权对象类型：cidr / group / prefix。"""
    if rule.direction == "egress":
        if rule.dest_group_id:
            return "group"
        if rule.dest_prefix_list_id:
            return "prefix"
        return "cidr"
    if rule.source_group_id:
        return "group"
    if rule.source_prefix_list_id:
        return "prefix"
    return "cidr"


def rule_authorization_label(rule: RuleInfo) -> str:
    """列表展示用授权对象：CIDR、安全组名（ID）或前缀列表。"""
    if rule.direction == "egress":
        cidr = rule.dest_cidr_ip or rule.ipv6_dest_cidr_ip
        group_id, group_name = rule.dest_group_id, rule.dest_group_name
        prefix = rule.dest_prefix_list_id
    else:
        cidr = rule.source_cidr_ip or rule.ipv6_source_cidr_ip
        group_id, group_name = rule.source_group_id, rule.source_group_name
        prefix = rule.source_prefix_list_id
    if group_id:
        return f"{group_id}（{group_name}）" if group_name else group_id
    if prefix:
        return prefix
    return cidr or "-"


def _normalized_rule(rule: RuleInput) -> RuleInput:
    """复制并归一化输入，避免修改调用方对象。"""
    direction = str(rule.direction or "ingress").strip().lower()
    protocol = normalize_rule_type(rule.ip_protocol)
    port = str(rule.port_range or "").strip()
    if protocol in _DEFAULT_PORTS:
        port = "-1/-1"
    else:
        try:
            # 服务端要求 起始/结束；用户只填单端口时补成 22/22
            port = parse_port_range(port)
        except ValueError:
            pass  # 非法值留给 validate_rule 给出可读提示
    policy_raw = str(rule.policy or "").strip()
    policy = _RULE_POLICIES.get(policy_raw.upper(), policy_raw or "Accept")
    kind = str(rule.source_kind or "").strip().lower() or "cidr"
    return RuleInput(
        direction=direction,
        ip_protocol=protocol,
        port_range=port,
        policy=policy,
        priority=rule.priority,
        description=str(rule.description or "").strip(),
        source_kind=kind,
        source_value=str(rule.source_value or "").strip(),
        rule_id=rule.rule_id,
        existing=rule.existing,
    )


def validate_rule(rule: RuleInput) -> str | None:
    """校验规则，合法返回 None，否则返回中文错误文案。"""
    direction = str(rule.direction or "").strip().lower()
    if direction not in ("ingress", "egress"):
        return "方向只能是入方向或出方向"

    protocol = normalize_rule_type(rule.ip_protocol)
    if protocol not in RULE_PROTOCOLS:
        return f"协议只能是 {' / '.join(RULE_PROTOCOLS)}"

    try:
        port = parse_port_range(rule.port_range)
    except ValueError as e:
        return str(e)
    if protocol in _DEFAULT_PORTS and port != "-1/-1":
        return f"{protocol} 协议的端口范围必须为 -1/-1"
    if protocol not in _DEFAULT_PORTS and port == "-1/-1":
        return "只有 ICMP / GRE / ALL 协议可以使用 -1/-1"

    try:
        priority = int(rule.priority)
    except (TypeError, ValueError):
        return "优先级必须是 1-100 的整数"
    if not 1 <= priority <= 100:
        return "优先级必须是 1-100 的整数"

    if str(rule.policy or "").strip().upper() not in _RULE_POLICIES:
        return "策略只能是 Accept 或 Drop"

    kind = str(rule.source_kind or "").strip().lower()
    value = str(rule.source_value or "").strip()
    if kind not in ("cidr", "group"):
        return "授权对象类型只能是 CIDR 或安全组"
    if not value:
        return "授权对象不能为空"
    if kind == "cidr":
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            return "授权对象 CIDR 格式不正确（例：192.168.1.0/24 或 1.2.3.4）"
    elif not re.fullmatch(r"sg-[A-Za-z0-9]+", value):
        return "安全组 ID 格式不正确（应形如 sg-xxxxxxxx）"

    if len(str(rule.description or "")) > 512:
        return "描述不能超过 512 个字符"
    return None


# ---------------------------------------------------------------- SDK 归一化

def _obj_attr(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


def _ip_list(wrapper) -> list[str]:
    return [str(ip) for ip in (_obj_attr(wrapper, "ip_address", None) or []) if ip]


def _lock_reason(item) -> str:
    locks = _obj_attr(item, "operation_locks", None)
    reasons = _obj_attr(locks, "lock_reason", None) or []
    out = []
    for lock in reasons:
        reason = str(_obj_attr(lock, "lock_reason", "") or "")
        if reason:
            out.append(reason)
    return "、".join(out)


def _network_options(body) -> str:
    opts = _obj_attr(body, "network_options", None)
    if opts is None:
        return ""
    parts = [
        f"巨型帧{'开' if _obj_attr(opts, 'enable_jumbo_frame', False) else '关'}",
        f"网络加密{'开' if _obj_attr(opts, 'enable_network_encryption', False) else '关'}",
    ]
    weighting = str(_obj_attr(opts, "bandwidth_weighting", "") or "")
    if weighting:
        parts.append(f"带宽权重 {weighting}")
    return " · ".join(parts)


def _instance_from_sdk(item, region: str) -> InstanceInfo:
    vpc = _obj_attr(item, "vpc_attributes", None)
    private_ips = _ip_list(_obj_attr(vpc, "private_ip_address", None))
    if not private_ips:
        private_ips = _ip_list(_obj_attr(item, "inner_ip_address", None))
    return InstanceInfo(
        instance_id=str(_obj_attr(item, "instance_id", "") or ""),
        name=str(_obj_attr(item, "instance_name", "") or ""),
        status=str(_obj_attr(item, "status", "") or ""),
        instance_type=str(_obj_attr(item, "instance_type", "") or ""),
        cpu=int(_obj_attr(item, "cpu", 0) or 0),
        memory=int(_obj_attr(item, "memory", 0) or 0),
        osname=str(_obj_attr(item, "osname", "") or ""),
        image_id=str(_obj_attr(item, "image_id", "") or ""),
        private_ips=private_ips,
        public_ips=_ip_list(_obj_attr(item, "public_ip_address", None)),
        eip=str(_obj_attr(_obj_attr(item, "eip_address", None), "ip_address", "") or ""),
        vpc_id=str(_obj_attr(vpc, "vpc_id", "") or ""),
        vswitch_id=str(_obj_attr(vpc, "v_switch_id", "") or ""),
        zone_id=str(_obj_attr(item, "zone_id", "") or ""),
        creation_time=str(_obj_attr(item, "creation_time", "") or ""),
        expired_time=str(_obj_attr(item, "expired_time", "") or ""),
        charge_type=str(_obj_attr(item, "instance_charge_type", "") or ""),
        internet_charge_type=str(_obj_attr(item, "internet_charge_type", "") or ""),
        bandwidth_out=int(_obj_attr(item, "internet_max_bandwidth_out", 0) or 0),
        security_group_ids=[
            str(sg) for sg in (_obj_attr(_obj_attr(item, "security_group_ids", None), "security_group_id", None) or [])
        ],
        lock_reason=_lock_reason(item),
        region_id=str(_obj_attr(item, "region_id", "") or region),
    )


def _instance_from_attribute(body, region: str, base: InstanceInfo | None = None) -> InstanceInfo:
    vpc = _obj_attr(body, "vpc_attributes", None)
    private_ips = _ip_list(_obj_attr(vpc, "private_ip_address", None))
    if not private_ips:
        private_ips = _ip_list(_obj_attr(body, "inner_ip_address", None))
    return InstanceInfo(
        instance_id=str(_obj_attr(body, "instance_id", "") or (base.instance_id if base else "")),
        name=str(_obj_attr(body, "instance_name", "") or (base.name if base else "")),
        status=str(_obj_attr(body, "status", "") or (base.status if base else "")),
        instance_type=str(_obj_attr(body, "instance_type", "") or (base.instance_type if base else "")),
        cpu=int(_obj_attr(body, "cpu", 0) or (base.cpu if base else 0)),
        memory=int(_obj_attr(body, "memory", 0) or (base.memory if base else 0)),
        osname=base.osname if base else "",
        image_id=str(_obj_attr(body, "image_id", "") or (base.image_id if base else "")),
        private_ips=private_ips or (base.private_ips if base else []),
        public_ips=_ip_list(_obj_attr(body, "public_ip_address", None))
        or (base.public_ips if base else []),
        eip=str(_obj_attr(_obj_attr(body, "eip_address", None), "ip_address", "") or (base.eip if base else "")),
        vpc_id=str(_obj_attr(vpc, "vpc_id", "") or (base.vpc_id if base else "")),
        vswitch_id=str(_obj_attr(vpc, "v_switch_id", "") or (base.vswitch_id if base else "")),
        zone_id=str(_obj_attr(body, "zone_id", "") or (base.zone_id if base else "")),
        creation_time=str(_obj_attr(body, "creation_time", "") or (base.creation_time if base else "")),
        expired_time=str(_obj_attr(body, "expired_time", "") or (base.expired_time if base else "")),
        charge_type=str(_obj_attr(body, "instance_charge_type", "") or (base.charge_type if base else "")),
        internet_charge_type=str(
            _obj_attr(body, "internet_charge_type", "") or (base.internet_charge_type if base else "")
        ),
        bandwidth_out=int(
            _obj_attr(body, "internet_max_bandwidth_out", 0) or (base.bandwidth_out if base else 0)
        ),
        security_group_ids=[
            str(sg)
            for sg in (_obj_attr(_obj_attr(body, "security_group_ids", None), "security_group_id", None) or [])
        ] or (base.security_group_ids if base else []),
        lock_reason=_lock_reason(body) or (base.lock_reason if base else ""),
        region_id=str(_obj_attr(body, "region_id", "") or (base.region_id if base else region)),
        network_options=_network_options(body),
    )


def _security_group_from_sdk(item) -> SecurityGroupInfo:
    return SecurityGroupInfo(
        security_group_id=str(_obj_attr(item, "security_group_id", "") or ""),
        name=str(_obj_attr(item, "security_group_name", "") or ""),
        description=str(_obj_attr(item, "description", "") or ""),
        sg_type=str(_obj_attr(item, "security_group_type", "") or ""),
        vpc_id=str(_obj_attr(item, "vpc_id", "") or ""),
        rule_count=int(_obj_attr(item, "rule_count", 0) or 0),
    )


def _rule_from_sdk(item, direction: str) -> RuleInfo:
    try:
        priority = int(str(_obj_attr(item, "priority", 100) or 100))
    except (TypeError, ValueError):
        priority = 100
    protocol = str(_obj_attr(item, "ip_protocol", "") or "")
    return RuleInfo(
        rule_id=str(_obj_attr(item, "security_group_rule_id", "") or ""),
        direction=str(_obj_attr(item, "direction", "") or direction or "ingress"),
        ip_protocol=normalize_rule_type(protocol) if protocol else "",
        port_range=str(_obj_attr(item, "port_range", "") or ""),
        policy=str(_obj_attr(item, "policy", "") or ""),
        priority=priority,
        description=str(_obj_attr(item, "description", "") or ""),
        source_cidr_ip=str(_obj_attr(item, "source_cidr_ip", "") or ""),
        source_group_id=str(_obj_attr(item, "source_group_id", "") or ""),
        source_group_name=str(_obj_attr(item, "source_group_name", "") or ""),
        ipv6_source_cidr_ip=str(_obj_attr(item, "ipv_6source_cidr_ip", "") or ""),
        source_prefix_list_id=str(_obj_attr(item, "source_prefix_list_id", "") or ""),
        dest_cidr_ip=str(_obj_attr(item, "dest_cidr_ip", "") or ""),
        dest_group_id=str(_obj_attr(item, "dest_group_id", "") or ""),
        dest_prefix_list_id=str(_obj_attr(item, "dest_prefix_list_id", "") or ""),
        dest_group_name=str(_obj_attr(item, "dest_group_name", "") or ""),
        ipv6_dest_cidr_ip=str(_obj_attr(item, "ipv_6dest_cidr_ip", "") or ""),
        nic_type=str(_obj_attr(item, "nic_type", "") or ""),
    )


def _disk_from_sdk(item) -> DiskInfo:
    device = ""
    attachments = _obj_attr(_obj_attr(item, "attachments", None), "attachment", None) or []
    if attachments:
        device = str(_obj_attr(attachments[0], "device", "") or "")
    if not device:
        device = str(_obj_attr(item, "device", "") or "")
    return DiskInfo(
        disk_id=str(_obj_attr(item, "disk_id", "") or ""),
        name=str(_obj_attr(item, "disk_name", "") or ""),
        category=str(_obj_attr(item, "category", "") or ""),
        size=int(_obj_attr(item, "size", 0) or 0),
        status=str(_obj_attr(item, "status", "") or ""),
        disk_type=str(_obj_attr(item, "type", "") or ""),
        device=device,
        encrypted=bool(_obj_attr(item, "encrypted", False)),
        charge_type=str(_obj_attr(item, "disk_charge_type", "") or ""),
        expired_time=str(_obj_attr(item, "expired_time", "") or ""),
    )


def _auth_fields(direction: str, kind: str, value: str) -> dict:
    """按方向 / 类型生成授权对象字段；IPv6 CIDR 走独立字段。"""
    is_v6 = ":" in value
    if direction == "egress":
        if kind == "group":
            return {"dest_group_id": value, "nic_type": "intranet"}
        return {"ipv_6dest_cidr_ip" if is_v6 else "dest_cidr_ip": value}
    if kind == "group":
        return {"source_group_id": value, "nic_type": "intranet"}
    return {"ipv_6source_cidr_ip" if is_v6 else "source_cidr_ip": value}


# ---------------------------------------------------------------- 服务客户端

class EcsQuerier:
    """ECS 查询与操作客户端。所有 API 失败统一抛出 AliyunApiError。"""

    def __init__(self) -> None:
        config = load_config()
        self._ak = config["access_key_id"]
        self._sk = config["access_key_secret"]
        self._clients: dict[str, EcsClient] = {}
        self._discovery: list[RegionInfo] | None = None
        self._runtime = RuntimeOptions()

    # ------------------------------------------------------------ 客户端

    def _client(self, region: str) -> EcsClient:
        client = self._clients.get(region)
        if client is None:
            client = EcsClient(build_open_api_config(self._ak, self._sk, region_id=region))
            self._clients[region] = client
        return client

    # ------------------------------------------------------------ 地域

    def list_regions(self) -> list[RegionInfo]:
        request = ecs_models.DescribeRegionsRequest(accept_language="zh-CN")
        try:
            response = self._client(DEFAULT_REGION).describe_regions_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "获取地域列表")) from e
        body = response.body
        items = (body.regions.region if body.regions else None) or []
        regions = [
            RegionInfo(
                region_id=str(item.region_id or ""),
                local_name=str(item.local_name or item.region_id or ""),
                status=str(item.status or "available").lower(),
            )
            for item in items
            if item.region_id
        ]
        regions.sort(key=lambda r: (0 if r.status == "available" else 1, r.region_id))
        return regions

    def _count_instances(self, region: str) -> int:
        request = ecs_models.DescribeInstancesRequest(region_id=region, max_results=10)
        try:
            response = self._client(region).describe_instances_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"探测 {region} 实例数量")) from e
        body = response.body
        if body.total_count is not None:
            return int(body.total_count)
        return len((body.instances.instance if body.instances else None) or [])

    def discover_regions(self) -> list[RegionInfo]:
        """并发探测各地域实例数；单地域失败按 0 处理，全部失败才报错。"""
        if self._discovery is not None:
            return list(self._discovery)

        regions = self.list_regions()
        if not regions:
            self._discovery = []
            return []

        # 先串行建好客户端，避免多线程同时写缓存
        for region in regions:
            self._client(region.region_id)

        counts: dict[str, int] = {}
        errors: list[str] = []
        workers = min(8, len(regions))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._count_instances, region.region_id): region.region_id
                for region in regions
            }
            for future in as_completed(futures):
                region_id = futures[future]
                try:
                    counts[region_id] = future.result()
                except Exception as e:  # noqa: BLE001
                    counts[region_id] = 0
                    errors.append(str(e))

        if len(errors) == len(regions):
            raise AliyunApiError(f"探测各地域实例数量全部失败：{errors[0]}")

        result = [
            RegionInfo(
                region_id=region.region_id,
                local_name=region.local_name or region.region_id,
                instance_count=counts.get(region.region_id, 0),
                status=region.status,
            )
            for region in regions
        ]
        result.sort(key=self._region_sort_key)
        self._discovery = result
        return list(result)

    @staticmethod
    def _region_sort_key(region: RegionInfo):
        if region.instance_count > 0:
            return (0, -region.instance_count, region.region_id)
        return (1, 0 if region.status == "available" else 1, region.region_id)

    def invalidate_discovery(self) -> None:
        """清空本次进程的地域探测缓存（不落盘）。"""
        self._discovery = None

    # ------------------------------------------------------------ 实例

    def list_instances(self, region: str) -> list[InstanceInfo]:
        items: list = []
        next_token: str | None = None
        try:
            while True:
                request = ecs_models.DescribeInstancesRequest(
                    region_id=region,
                    max_results=100,
                    next_token=next_token,
                )
                response = self._client(region).describe_instances_with_options(request, self._runtime)
                body = response.body
                page = (body.instances.instance if body.instances else None) or []
                items.extend(page)
                token = str(body.next_token or "")
                if not token or token == next_token or not page:
                    break
                next_token = token
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取 {region} 实例列表")) from e
        return [_instance_from_sdk(item, region) for item in items]

    def _describe_instance_by_id(self, region: str, instance_id: str):
        request = ecs_models.DescribeInstancesRequest(
            region_id=region, instance_ids=instance_id, max_results=10
        )
        response = self._client(region).describe_instances_with_options(request, self._runtime)
        body = response.body
        items = (body.instances.instance if body.instances else None) or []
        return items[0] if items else None

    def get_instance(self, region: str, instance_id: str) -> InstanceInfo:
        """列表数据 + DescribeInstanceAttribute 补齐；单次失败时尽量降级。"""
        base: InstanceInfo | None = None
        list_error: AliyunApiError | None = None
        try:
            item = self._describe_instance_by_id(region, instance_id)
            if item is not None:
                base = _instance_from_sdk(item, region)
        except AliyunApiError as e:
            list_error = e
        except Exception as e:  # noqa: BLE001
            list_error = AliyunApiError(format_api_error(e, f"获取实例 {instance_id}"))

        request = ecs_models.DescribeInstanceAttributeRequest(instance_id=instance_id)
        try:
            response = self._client(region).describe_instance_attribute_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            if base is not None:
                return base
            if list_error is not None:
                raise list_error from e
            raise AliyunApiError(format_api_error(e, f"获取实例 {instance_id} 详情")) from e
        return _instance_from_attribute(response.body, region, base)

    def start_instance(self, region: str, instance_id: str, *, init_local_disk: bool = False) -> None:
        request = ecs_models.StartInstanceRequest(instance_id=instance_id, init_local_disk=init_local_disk)
        try:
            self._client(region).start_instance_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"启动实例 {instance_id}")) from e

    def stop_instance(
        self,
        region: str,
        instance_id: str,
        *,
        force: bool = False,
        stopped_mode: str | None = None,
    ) -> None:
        request = ecs_models.StopInstanceRequest(
            instance_id=instance_id,
            force_stop=force,
            stopped_mode=stopped_mode,
        )
        try:
            self._client(region).stop_instance_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"停止实例 {instance_id}")) from e

    def reboot_instance(self, region: str, instance_id: str, *, force: bool = False) -> None:
        request = ecs_models.RebootInstanceRequest(instance_id=instance_id, force_stop=force)
        try:
            self._client(region).reboot_instance_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"重启实例 {instance_id}")) from e

    # ------------------------------------------------------------ 批量生命周期

    def _batch_power(
        self,
        region: str,
        instance_ids: list[str],
        *,
        action: str,
        force: bool = False,
        stopped_mode: str | None = None,
    ) -> BatchResult:
        """按 100 台分批下发启停指令。

        约定：单批失败只把该批实例记入 ``failed``（附格式化错误），不影响其它批；
        若所有批次都失败则抛 ``AliyunApiError``（带首批错误文案）。部分成功时由调用方
        根据 ``success`` / ``failed`` 自行汇总与提示，不谎报全部成功。
        """
        ids = list(dict.fromkeys(iid for iid in instance_ids if iid))
        result = BatchResult()
        if not ids:
            return result
        for chunk in _chunked(ids):
            try:
                if action == "start":
                    request = ecs_models.StartInstancesRequest(region_id=region, instance_id=chunk)
                    self._client(region).start_instances_with_options(request, self._runtime)
                elif action == "stop":
                    request = ecs_models.StopInstancesRequest(
                        region_id=region,
                        instance_id=chunk,
                        force_stop=force,
                        stopped_mode=stopped_mode,
                    )
                    self._client(region).stop_instances_with_options(request, self._runtime)
                else:
                    request = ecs_models.RebootInstancesRequest(
                        region_id=region,
                        instance_id=chunk,
                        force_reboot=force,
                    )
                    self._client(region).reboot_instances_with_options(request, self._runtime)
            except Exception as e:  # noqa: BLE001
                verb = {"start": "批量启动", "stop": "批量停止", "reboot": "批量重启"}[action]
                message = format_api_error(e, f"{verb}实例")
                for instance_id in chunk:
                    result.failed[instance_id] = message
            else:
                result.success.extend(chunk)
        if not result.success:
            raise AliyunApiError(next(iter(result.failed.values())))
        return result

    def start_instances(self, region: str, instance_ids: list[str]) -> BatchResult:
        """批量开机；返回已下发与失败明细（全部失败抛 AliyunApiError）。"""
        return self._batch_power(region, instance_ids, action="start")

    def stop_instances(
        self,
        region: str,
        instance_ids: list[str],
        *,
        force: bool = False,
        stopped_mode: str | None = None,
    ) -> BatchResult:
        """批量关机；``stopped_mode`` 按阿里云语义传 StopCharging / KeepCharging。"""
        return self._batch_power(
            region, instance_ids, action="stop", force=force, stopped_mode=stopped_mode
        )

    def reboot_instances(
        self, region: str, instance_ids: list[str], *, force: bool = False
    ) -> BatchResult:
        """批量重启；注意批量接口的强制参数名是 ``force_reboot``（不是 force_stop）。"""
        return self._batch_power(region, instance_ids, action="reboot", force=force)

    def wait_instance_status(
        self,
        region: str,
        instance_id: str,
        targets: set[str],
        *,
        timeout: float = 120,
        interval: float = 3,
    ) -> str:
        """轮询实例状态；超时返回最后状态（不谎报成功）。"""
        statuses = self.wait_instances_status(
            region, [instance_id], targets, timeout=timeout, interval=interval
        )
        return statuses.get(instance_id, "")

    def wait_instances_status(
        self,
        region: str,
        instance_ids: list[str],
        targets: set[str],
        *,
        timeout: float = 120,
        interval: float = 3,
    ) -> dict[str, str]:
        """批量轮询状态：每次 DescribeInstanceStatus 查 ≤100 台，超时返回最后状态。"""
        ids = list(dict.fromkeys(iid for iid in instance_ids if iid))
        last: dict[str, str] = dict.fromkeys(ids, "")
        if not ids:
            return last
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            pending = [iid for iid in ids if last.get(iid) not in targets]
            if not pending:
                return last
            for chunk in _chunked(pending):
                request = ecs_models.DescribeInstanceStatusRequest(
                    region_id=region, instance_id=chunk
                )
                try:
                    response = self._client(region).describe_instance_status_with_options(
                        request, self._runtime
                    )
                except Exception as e:  # noqa: BLE001
                    raise AliyunApiError(format_api_error(e, "批量查询实例状态")) from e
                body = response.body
                items = (body.instance_statuses.instance_status if body.instance_statuses else None) or []
                for item in items:
                    iid = str(item.instance_id or "")
                    if iid in last:
                        last[iid] = str(item.status or "")
            if all(last.get(iid) in targets for iid in ids):
                return last
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return last
            time.sleep(min(interval, remaining))

    # ------------------------------------------------------------ 安全组

    def list_vpcs(self, region: str) -> list[VpcInfo]:
        """列出地域内 VPC；DescribeVpcs 只支持 page_number / page_size 翻页。"""
        items: list = []
        page = 1
        try:
            while True:
                request = ecs_models.DescribeVpcsRequest(
                    region_id=region, page_number=page, page_size=50
                )
                response = self._client(region).describe_vpcs_with_options(request, self._runtime)
                body = response.body
                batch = (body.vpcs.vpc if body.vpcs else None) or []
                items.extend(batch)
                total = int(body.total_count or 0)
                if not batch or len(items) >= total:
                    break
                page += 1
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取 {region} VPC 列表")) from e
        return [
            VpcInfo(
                vpc_id=str(_obj_attr(item, "vpc_id", "") or ""),
                name=str(_obj_attr(item, "vpc_name", "") or ""),
                cidr_block=str(_obj_attr(item, "cidr_block", "") or ""),
                is_default=bool(_obj_attr(item, "is_default", False)),
                status=str(_obj_attr(item, "status", "") or ""),
            )
            for item in items
            if _obj_attr(item, "vpc_id", None)
        ]

    def create_security_group(
        self,
        region: str,
        *,
        name: str,
        description: str = "",
        vpc_id: str = "",
    ) -> str:
        """创建普通安全组并返回 ID。``security_group_type`` 不传即 normal。"""
        clean = str(name or "").strip()
        if not 2 <= len(clean) <= 128:
            raise ValueError("安全组名称长度需为 2-128 个字符")
        request = ecs_models.CreateSecurityGroupRequest(
            region_id=region,
            security_group_name=clean,
            description=str(description or "").strip() or None,
            vpc_id=str(vpc_id or "").strip() or None,
        )
        try:
            response = self._client(region).create_security_group_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "创建安全组")) from e
        group_id = str(_obj_attr(response.body, "security_group_id", "") or "")
        if not group_id:
            raise AliyunApiError("创建安全组失败：服务端未返回安全组 ID")
        return group_id

    def delete_security_group(self, region: str, sg_id: str) -> None:
        """删除安全组；仍被引用时转换为中文可读提示。"""
        request = ecs_models.DeleteSecurityGroupRequest(region_id=region, security_group_id=sg_id)
        try:
            self._client(region).delete_security_group_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            data = getattr(e, "data", None) or {}
            code = str(data.get("Code") or getattr(e, "code", "") or "")
            message = str(data.get("Message") or str(e))
            if "DependencyViolation" in code or "DependencyViolation" in message:
                raise AliyunApiError("安全组仍被实例或其他资源使用，请先解绑") from e
            raise AliyunApiError(format_api_error(e, f"删除安全组 {sg_id}")) from e

    def _inner_access_policy(self, region: str, sg_id: str) -> str:
        request = ecs_models.DescribeSecurityGroupAttributeRequest(
            region_id=region,
            security_group_id=sg_id,
            direction="all",
            max_results=10,
        )
        try:
            response = self._client(region).describe_security_group_attribute_with_options(
                request, self._runtime
            )
        except Exception:  # noqa: BLE001 — 组内连通只是展示项，取不到不阻塞列表
            return ""
        return str(response.body.inner_access_policy or "")

    def _page_security_groups(self, region: str, security_group_id: str | None = None) -> list:
        """按 next_token 翻页取安全组原始数据（可限定单个安全组）。

        注意：DescribeSecurityGroups 不接受 SecurityGroupIds（复数、逗号分隔）过滤，
        传了会被服务端判为 400 InvalidParameter「The specified parameters are not valid.」，
        只支持单个 SecurityGroupId，因此多 ID 过滤由调用方逐个查询。
        """
        items: list = []
        next_token: str | None = None
        while True:
            request = ecs_models.DescribeSecurityGroupsRequest(
                region_id=region,
                max_results=100,
                next_token=next_token,
                security_group_id=security_group_id,
            )
            response = self._client(region).describe_security_groups_with_options(request, self._runtime)
            body = response.body
            page = (body.security_groups.security_group if body.security_groups else None) or []
            items.extend(page)
            token = str(body.next_token or "")
            if not token or token == next_token or not page:
                break
            next_token = token
        return items

    def list_security_groups(
        self,
        region: str,
        *,
        security_group_ids: list[str] | None = None,
        with_policy: bool = True,
    ) -> list[SecurityGroupInfo]:
        try:
            if security_group_ids:
                ids = list(dict.fromkeys(sid for sid in security_group_ids if sid))
                items = [item for sid in ids for item in self._page_security_groups(region, sid)]
            else:
                items = self._page_security_groups(region)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取 {region} 安全组列表")) from e

        groups = [_security_group_from_sdk(item) for item in items]
        if with_policy and groups:
            # 组内连通策略只在 attribute 接口返回，列表页并发补齐（数量通常很小）
            with ThreadPoolExecutor(max_workers=min(8, len(groups))) as executor:
                policies = list(
                    executor.map(lambda g: self._inner_access_policy(region, g.security_group_id), groups)
                )
            groups = [
                SecurityGroupInfo(
                    security_group_id=g.security_group_id,
                    name=g.name,
                    description=g.description,
                    sg_type=g.sg_type,
                    vpc_id=g.vpc_id,
                    rule_count=g.rule_count,
                    inner_access_policy=policy,
                )
                for g, policy in zip(groups, policies, strict=True)
            ]
        return groups

    def get_security_group(self, region: str, sg_id: str) -> SecurityGroupInfo:
        groups = self.list_security_groups(
            region, security_group_ids=[sg_id], with_policy=False
        )
        base = groups[0] if groups else SecurityGroupInfo(security_group_id=sg_id)
        return SecurityGroupInfo(
            security_group_id=base.security_group_id or sg_id,
            name=base.name,
            description=base.description,
            sg_type=base.sg_type,
            vpc_id=base.vpc_id,
            rule_count=base.rule_count,
            inner_access_policy=self._inner_access_policy(region, sg_id),
        )

    def list_rules(self, region: str, sg_id: str, direction: str = "all") -> list[RuleInfo]:
        """规则明细必须翻 next_token；默认 500 条/页会静默丢规则，故显式 1000。"""
        rules: list[RuleInfo] = []
        next_token: str | None = None
        try:
            while True:
                request = ecs_models.DescribeSecurityGroupAttributeRequest(
                    region_id=region,
                    security_group_id=sg_id,
                    direction=direction,
                    max_results=1000,
                    next_token=next_token,
                )
                response = self._client(region).describe_security_group_attribute_with_options(
                    request, self._runtime
                )
                body = response.body
                page = (body.permissions.permission if body.permissions else None) or []
                rules.extend(_rule_from_sdk(item, direction) for item in page)
                token = str(body.next_token or "")
                if not token or token == next_token or not page:
                    break
                next_token = token
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取安全组 {sg_id} 规则")) from e
        return rules

    def _permission(self, rule: RuleInput):
        port = "-1/-1" if rule.ip_protocol in _DEFAULT_PORTS else parse_port_range(rule.port_range)
        common = {
            "ip_protocol": rule.ip_protocol,
            "port_range": port,
            "policy": rule.policy,
            "priority": str(rule.priority),
            "description": rule.description or None,
            "nic_type": "intranet" if rule.source_kind == "group" else None,
        }
        common.update(_auth_fields(rule.direction, rule.source_kind, rule.source_value))
        if rule.direction == "egress":
            return ecs_models.AuthorizeSecurityGroupEgressRequestPermissions(**common)
        return ecs_models.AuthorizeSecurityGroupRequestPermissions(**common)

    def authorize_rule(self, region: str, sg_id: str, rule: RuleInput) -> None:
        normalized = _normalized_rule(rule)
        error = validate_rule(normalized)
        if error:
            raise ValueError(error)
        permission = self._permission(normalized)
        if normalized.direction == "egress":
            request = ecs_models.AuthorizeSecurityGroupEgressRequest(
                region_id=region, security_group_id=sg_id, permissions=[permission]
            )
            action = "新增出方向规则"
            call = self._client(region).authorize_security_group_egress_with_options
        else:
            request = ecs_models.AuthorizeSecurityGroupRequest(
                region_id=region, security_group_id=sg_id, permissions=[permission]
            )
            action = "新增入方向规则"
            call = self._client(region).authorize_security_group_with_options
        try:
            call(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"安全组 {sg_id} {action}")) from e

    def _existing_rule(self, region: str, sg_id: str, rule: RuleInput) -> RuleInfo:
        rule_id = rule.rule_id or (rule.existing.rule_id if rule.existing else "")
        if rule_id:
            for item in self.list_rules(region, sg_id, rule.direction):
                if item.rule_id == rule_id:
                    return item
        if rule.existing is not None:
            return rule.existing
        raise AliyunApiError("修改安全组规则失败：找不到原规则，请刷新后重试")

    def modify_rule(self, region: str, sg_id: str, rule: RuleInput) -> None:
        normalized = _normalized_rule(rule)
        error = validate_rule(normalized)
        if error:
            raise ValueError(error)
        existing = self._existing_rule(region, sg_id, normalized)
        if rule_auth_kind(existing) == "prefix":
            raise ValueError("前缀列表规则不支持在终端修改，请到阿里云控制台操作")
        if normalized.source_kind != rule_auth_kind(existing):
            raise ValueError("不能修改授权对象类型（CIDR 与安全组不可互换）")
        if normalized.source_kind == "cidr":
            existing_v6 = bool(existing.ipv6_source_cidr_ip or existing.ipv6_dest_cidr_ip)
            if existing_v6 != (":" in normalized.source_value):
                # IPv4 / IPv6 走不同的授权对象字段，服务端不允许就地互换
                raise ValueError("不能把授权对象的 IP 版本在 IPv4 / IPv6 间互换，请新增规则后删除旧规则")

        # 读-改-写：未改动字段必须回填原值，否则 priority 可能被重置为 1
        auth = _auth_fields(normalized.direction, rule_auth_kind(existing), normalized.source_value)
        # 服务端不支持「非空 → 空」；用户清空描述时保留原值，避免整条修改被拒
        description = normalized.description or existing.description
        common = {
            "region_id": region,
            "security_group_id": sg_id,
            "security_group_rule_id": existing.rule_id,
            "ip_protocol": normalized.ip_protocol,
            "port_range": normalized.port_range,
            "policy": normalized.policy,
            "priority": str(normalized.priority),
            "description": description or None,
            "nic_type": existing.nic_type or None,
        }
        common.update(auth)
        if normalized.direction == "egress":
            request = ecs_models.ModifySecurityGroupEgressRuleRequest(**common)
        else:
            request = ecs_models.ModifySecurityGroupRuleRequest(**common)
        try:
            if normalized.direction == "egress":
                self._client(region).modify_security_group_egress_rule_with_options(
                    request, self._runtime
                )
            else:
                self._client(region).modify_security_group_rule_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"修改安全组 {sg_id} 规则")) from e

    def revoke_rule(self, region: str, sg_id: str, rule: RuleInfo) -> None:
        if not rule.rule_id:
            raise ValueError("删除规则需要规则 ID，请刷新后重试")
        direction = str(rule.direction or "ingress").lower()
        if direction == "egress":
            request = ecs_models.RevokeSecurityGroupEgressRequest(
                region_id=region, security_group_id=sg_id, security_group_rule_id=[rule.rule_id]
            )
            call = self._client(region).revoke_security_group_egress_with_options
        else:
            request = ecs_models.RevokeSecurityGroupRequest(
                region_id=region, security_group_id=sg_id, security_group_rule_id=[rule.rule_id]
            )
            call = self._client(region).revoke_security_group_with_options
        try:
            call(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"删除安全组 {sg_id} 规则")) from e

    # ------------------------------------------------------------ 磁盘

    def list_disks(self, region: str, instance_id: str) -> list[DiskInfo]:
        items: list = []
        next_token: str | None = None
        try:
            while True:
                request = ecs_models.DescribeDisksRequest(
                    region_id=region,
                    instance_id=instance_id,
                    max_results=100,
                    next_token=next_token,
                )
                response = self._client(region).describe_disks_with_options(request, self._runtime)
                body = response.body
                page = (body.disks.disk if body.disks else None) or []
                items.extend(page)
                token = str(body.next_token or "")
                if not token or token == next_token or not page:
                    break
                next_token = token
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取实例 {instance_id} 磁盘")) from e
        return [_disk_from_sdk(item) for item in items]

    # ------------------------------------------------------------ 云助手

    def run_command(
        self,
        region: str,
        instance_id: str,
        *,
        content: str,
        timeout: int = 60,
    ) -> str:
        """下发 Shell 命令（立即返回 invoke_id；不会等待执行完成）。"""
        command = str(content or "")
        if not command.strip():
            raise ValueError("命令内容不能为空")
        if not 1 <= int(timeout) <= 86400:
            raise ValueError("超时必须是 1-86400 秒")
        request = ecs_models.RunCommandRequest(
            region_id=region,
            instance_id=[instance_id],
            type="Shell",
            command_content=command,
            content_encoding="PlainText",
            timeout=int(timeout),
            name="aliyunctl",
        )
        try:
            response = self._client(region).run_command_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "下发云助手命令")) from e
        invoke_id = str(_obj_attr(response.body, "invoke_id", "") or "")
        if not invoke_id:
            raise AliyunApiError("下发云助手命令失败：服务端未返回 InvokeId")
        return invoke_id

    def get_command_result(self, region: str, instance_id: str, invoke_id: str) -> CommandResult:
        """查询一次命令执行结果；未完成时 status 为 Running / Pending 等。"""
        request = ecs_models.DescribeInvocationResultsRequest(
            region_id=region,
            instance_id=instance_id,
            invoke_id=invoke_id,
        )
        try:
            response = self._client(region).describe_invocation_results_with_options(
                request, self._runtime
            )
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "查询云助手命令结果")) from e
        body = response.body
        invocation = _obj_attr(body, "invocation", None)
        results = _obj_attr(_obj_attr(invocation, "invocation_results", None), "invocation_result", None) or []
        if not results:
            return CommandResult(invoke_id=invoke_id, status="Pending")
        item = results[0]
        status = str(_obj_attr(item, "invoke_record_status", "") or "")
        exit_raw = _obj_attr(item, "exit_code", None)
        try:
            exit_code = int(exit_raw) if exit_raw is not None else None
        except (TypeError, ValueError):
            exit_code = None
        error_code = str(_obj_attr(item, "error_code", "") or "")
        error_info = str(_obj_attr(item, "error_info", "") or "")
        error = " ".join(part for part in (error_code, error_info) if part)
        return CommandResult(
            invoke_id=invoke_id,
            status=status,
            exit_code=exit_code,
            output=str(_obj_attr(item, "output", "") or ""),
            error=error,
            finished=status in _COMMAND_TERMINAL,
        )

    def wait_command_result(
        self,
        region: str,
        instance_id: str,
        invoke_id: str,
        *,
        timeout: float = 120,
        interval: float = 2,
    ) -> CommandResult:
        """轮询到终态；超时返回最后一次结果（finished=False，不谎报成功）。"""
        deadline = time.monotonic() + max(0.0, timeout)
        result = CommandResult(invoke_id=invoke_id)
        while True:
            result = self.get_command_result(region, instance_id, invoke_id)
            if result.finished:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            time.sleep(min(interval, remaining))

    # ------------------------------------------------------------ 续费 / 网络

    def describe_auto_renew(self, region: str, instance_ids: list[str]) -> dict[str, AutoRenewInfo]:
        """查询自动续费属性；失败返回空 dict（不阻塞实例列表 / 详情）。"""
        ids = [iid for iid in instance_ids if iid]
        if not ids:
            return {}
        out: dict[str, AutoRenewInfo] = {}
        page = 1
        try:
            while True:
                request = ecs_models.DescribeInstanceAutoRenewAttributeRequest(
                    region_id=region,
                    instance_id=",".join(ids),
                    page_number=str(page),
                    page_size="100",
                )
                response = self._client(region).describe_instance_auto_renew_attribute_with_options(
                    request, self._runtime
                )
                body = response.body
                attrs = _obj_attr(body, "instance_renew_attributes", None)
                items = _obj_attr(attrs, "instance_renew_attribute", None) or []
                for item in items:
                    iid = str(_obj_attr(item, "instance_id", "") or "")
                    if not iid:
                        continue
                    out[iid] = AutoRenewInfo(
                        status=str(_obj_attr(item, "renewal_status", "") or ""),
                        duration=int(_obj_attr(item, "duration", 0) or 0),
                        period_unit=str(_obj_attr(item, "period_unit", "") or ""),
                        auto_renew_enabled=bool(_obj_attr(item, "auto_renew_enabled", False)),
                    )
                total = int(_obj_attr(body, "total_count", 0) or 0)
                if not items or len(out) >= total:
                    break
                page += 1
        except Exception:  # noqa: BLE001 — 续费信息只是展示项
            return {}
        return out

    def get_eip(self, region: str, instance_id: str) -> EipInfo | None:
        """查询实例绑定的 EIP；未绑定返回 None。"""
        page = 1
        try:
            while True:
                request = ecs_models.DescribeEipAddressesRequest(
                    region_id=region,
                    associated_instance_id=instance_id,
                    page_number=page,
                    page_size=50,
                )
                response = self._client(region).describe_eip_addresses_with_options(
                    request, self._runtime
                )
                body = response.body
                items = (body.eip_addresses.eip_address if body.eip_addresses else None) or []
                if items:
                    return _eip_from_sdk(items[0])
                total = int(body.total_count or 0)
                if not items or page * 50 >= total:
                    return None
                page += 1
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取实例 {instance_id} EIP")) from e

    def get_bandwidth_limit(
        self, region: str, instance_type: str, internet_charge_type: str
    ) -> tuple[int, int] | None:
        """查询实例规格的公网带宽上下限；查询失败返回 None（调用方回落到常量）。"""
        if not instance_type:
            return None
        request = ecs_models.DescribeBandwidthLimitationRequest(
            region_id=region, instance_type=instance_type
        )
        try:
            response = self._client(region).describe_bandwidth_limitation_with_options(
                request, self._runtime
            )
        except Exception:  # noqa: BLE001 — 上限只是提示，失败不阻塞带宽调整
            return None
        bandwidths = _obj_attr(_obj_attr(response.body, "bandwidths", None), "bandwidth", None) or []
        for item in bandwidths:
            charge = str(_obj_attr(item, "internet_charge_type", "") or "")
            if charge and charge != internet_charge_type:
                continue
            try:
                low = int(_obj_attr(item, "min", 0) or 0)
                high = int(_obj_attr(item, "max", 0) or 0)
            except (TypeError, ValueError):
                continue
            if high > 0:
                return low, high
        return None

    # 服务端对各规格上限不同，由 DescribeBandwidthLimitation 决定；这里只做离谱值兜底
    BANDWIDTH_HARD_MAX = 1000

    def modify_bandwidth(self, region: str, instance_id: str, *, bandwidth_out: int) -> None:
        """调整公网出带宽（会产生费用变更，调用方必须二次确认）。

        合法区间由调用方用 ``get_bandwidth_limit()`` 按规格实时确定，本方法只拦离谱值。
        """
        value = int(bandwidth_out)
        if not 1 <= value <= self.BANDWIDTH_HARD_MAX:
            raise ValueError(f"出网带宽必须是 1-{self.BANDWIDTH_HARD_MAX} 的整数（Mbps）")
        request = ecs_models.ModifyInstanceNetworkSpecRequest(
            instance_id=instance_id, internet_max_bandwidth_out=value
        )
        try:
            self._client(region).modify_instance_network_spec_with_options(request, self._runtime)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"调整实例 {instance_id} 带宽")) from e


def _eip_from_sdk(item) -> EipInfo:
    try:
        bandwidth = int(str(_obj_attr(item, "bandwidth", 0) or 0))
    except (TypeError, ValueError):
        bandwidth = 0
    return EipInfo(
        allocation_id=str(_obj_attr(item, "allocation_id", "") or ""),
        ip_address=str(_obj_attr(item, "ip_address", "") or ""),
        bandwidth=bandwidth,
        internet_charge_type=str(_obj_attr(item, "internet_charge_type", "") or ""),
        status=str(_obj_attr(item, "status", "") or ""),
        charge_type=str(_obj_attr(item, "charge_type", "") or ""),
        expired_time=str(_obj_attr(item, "expired_time", "") or ""),
    )
