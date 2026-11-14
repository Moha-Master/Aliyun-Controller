"""ECS 屏：地域探测 → 实例列表 → 实例详情（基本信息 / 监控 / 网络与安全组 / 存储与备份）→ 安全组规则。

版式遵循 PageScreen：小容器（.panel：搜索 / 筛选 / 操作按钮）+ 大容器（.tbl 表格）。
"""
from __future__ import annotations

import asyncio
import datetime
import time
from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from ..client import AliyunApiError
from ..ecs import (
    RULE_PROTOCOLS,
    STATUS_STYLE,
    AutoRenewInfo,
    EipInfo,
    InstanceInfo,
    RegionInfo,
    RuleInfo,
    RuleInput,
    SecurityGroupInfo,
    can_reboot,
    can_start,
    can_stop,
    describe_direction_label,
    normalize_rule_type,
    parse_port_range,
    rule_auth_kind,
    rule_authorization_label,
    status_label,
    validate_rule,
)
from ..monitor import WINDOW_LABELS, MonitorSnapshot
from ..ui import ChartSeries, LineChart, PageScreen
from ..widgets import (
    STYLE_DIM,
    STYLE_ERR,
    STYLE_INFO,
    STYLE_OK,
    STYLE_WARN,
    ConfirmModal,
    FormField,
    FormModal,
    InputModal,
    OutputModal,
    PickItem,
    busy,
    filter_fuzzy,
    fit_table_columns,
    load_rows,
    make_table,
    rcell,
    shorten,
)

DEFAULT_PORTS = ("ICMP", "GRE", "ALL")

EXPIRE_WARN_DAYS = 15
"""到期提醒阈值（天）：剩余天数 ≤ 该值时高亮。"""

COMMAND_OUTPUT_LIMIT = 4000
"""OutputModal 最多展示的命令输出字符数。"""

STOP_CHARGING = "StopCharging"
KEEP_CHARGING = "KeepCharging"


@dataclass(frozen=True)
class StopOptions:
    force: bool = False
    stopped_mode: str = STOP_CHARGING


# ---------------------------------------------------------------- 小工具

def _to_int(value, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _join(values: list[str] | None) -> str:
    return "、".join(values) if values else "-"


def _set_loading(widget, loading: bool) -> None:
    """卸载过程中设置 loading 会因 NoScreen 抛错，清理路径需静默忽略。"""
    try:
        widget.loading = loading
    except Exception:  # noqa: BLE001
        pass


def _restore_cursor(table: DataTable, items: list, key, keep: str) -> None:
    """重载后按 id 把光标放回原条目，避免整表刷新后跳回第一行。"""
    if not keep:
        return
    for idx, item in enumerate(items):
        if key(item) == keep:
            table.move_cursor(row=idx)
            return


def _date(value: str | None) -> str:
    text = str(value or "").strip()
    return text[:10] if text else "-"


def _parse_time(value: str | None) -> datetime.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.datetime.strptime(text[:10], "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            return None


def _days_left(value: str | None) -> int | None:
    """剩余天数（已过期为负数）；无法解析返回 None。"""
    dt = _parse_time(value)
    if dt is None:
        return None
    return (dt.date() - datetime.date.today()).days


def _expire_cell(inst: InstanceInfo, width: int) -> Text:
    """实例列表「到期」单元格：YYYY-MM-DD 剩N天，临期黄色、过期红色。"""
    if inst.charge_type == "PostPaid":
        return rcell("-", width)
    days = _days_left(inst.expired_time)
    if days is None:
        return rcell(shorten(_date(inst.expired_time), width), width)
    if days < 0:
        text = rcell(shorten("已过期", width), width)
        text.stylize(STYLE_ERR)
        return text
    full = f"{_date(inst.expired_time)} 剩{days}天"
    label = full if cell_len(full) <= width else f"剩{days}天"
    text = rcell(shorten(label, width), width)
    if days <= EXPIRE_WARN_DAYS:
        text.stylize(STYLE_WARN)
    return text


def _is_expiring(inst: InstanceInfo) -> bool:
    """是否属于「即将到期」（还在有效期内且 ≤ 阈值）。"""
    if inst.charge_type == "PostPaid":
        return False
    days = _days_left(inst.expired_time)
    return days is not None and 0 <= days <= EXPIRE_WARN_DAYS


def _renew_label(info: AutoRenewInfo | None, *, postpaid: bool = False) -> str:
    """自动续费状态；``info is None`` 表示查不到（可能是权限或接口失败），不臆测成手动续费。"""
    if postpaid:
        return "-"
    if info is None:
        return "未知"
    if info.status == "AutoRenewal" or info.auto_renew_enabled:
        return "自动续费"
    if info.status == "NotRenewal":
        return "不续费"
    return "手动续费"


def _remaining_label(inst: InstanceInfo) -> str:
    if inst.charge_type == "PostPaid":
        return "-"
    days = _days_left(inst.expired_time)
    if days is None:
        return "-"
    if days < 0:
        return "已过期"
    return f"{days} 天"


def _eip_charge_label(value: str | None) -> str:
    return {
        "PayByBandwidth": "按固定带宽",
        "PayByTraffic": "按使用流量",
    }.get(str(value or ""), str(value or "") or "-")


def _bandwidth_allowed(inst: InstanceInfo | None, eip: EipInfo | None) -> bool:
    """只有按固定带宽计费、且实例处于稳定态时才允许就地调整带宽。"""
    if inst is None or inst.status not in ("Running", "Stopped"):
        return False
    charge = ""
    if eip is not None:
        charge = eip.internet_charge_type
    if not charge:
        charge = inst.internet_charge_type
    return str(charge or "") == "PayByBandwidth"


def _charge_label(value: str | None) -> str:
    return {"PrePaid": "包年包月", "PostPaid": "按量付费"}.get(str(value or ""), str(value or "") or "-")


def _sg_type_label(value: str | None) -> str:
    return {"normal": "普通", "enterprise": "企业级"}.get(str(value or ""), str(value or "") or "-")


def _inner_policy_label(value: str | None) -> str:
    return {"accept": "允许", "drop": "拒绝"}.get(str(value or "").lower(), "-")


def _policy_label(value: str | None) -> str:
    return {"accept": "Accept", "drop": "Drop"}.get(str(value or "").lower(), str(value or "") or "-")


def _status_text(status: str, width: int) -> Text:
    text = rcell(status_label(status), width)
    style = STATUS_STYLE.get(str(status or ""))
    if style:
        text.stylize(style)
    return text


def _policy_text(policy: str, width: int) -> Text:
    text = rcell(_policy_label(policy), width)
    lowered = str(policy or "").lower()
    if lowered == "accept":
        text.stylize(STYLE_OK)
    elif lowered == "drop":
        text.stylize(STYLE_ERR)
    return text


def _rule_auth_value(rule: RuleInfo) -> str:
    if rule.direction == "egress":
        return (
            rule.dest_cidr_ip
            or rule.ipv6_dest_cidr_ip
            or rule.dest_group_id
            or rule.dest_prefix_list_id
        )
    return (
        rule.source_cidr_ip
        or rule.ipv6_source_cidr_ip
        or rule.source_group_id
        or rule.source_prefix_list_id
    )


def _rule_values(rule: RuleInfo | None) -> dict:
    if rule is None:
        return {
            "direction": "ingress",
            "protocol": "TCP",
            "port": "22/22",
            "auth_kind": "cidr",
            "auth_value": "",
            "policy": "Accept",
            "priority": "100",
            "description": "",
        }
    return {
        "direction": rule.direction or "ingress",
        "protocol": rule.ip_protocol or "TCP",
        "port": rule.port_range or "22/22",
        "auth_kind": rule_auth_kind(rule),
        "auth_value": _rule_auth_value(rule),
        "policy": _policy_label(rule.policy),
        "priority": str(rule.priority),
        "description": rule.description,
    }


def _group_options(groups: list[SecurityGroupInfo], current: str = "") -> list[tuple[str, str]]:
    options = [
        (f"{g.name or g.security_group_id}（{g.security_group_id}）", g.security_group_id)
        for g in groups
        if g.security_group_id
    ]
    if current and current not in [value for _, value in options]:
        options.append((current, current))
    return options


def _group_pick_items(groups: list[SecurityGroupInfo]) -> list[PickItem]:
    return [
        PickItem(value=g.security_group_id, label=g.name or g.security_group_id, hint=g.security_group_id)
        for g in groups
        if g.security_group_id
    ]


# ---------------------------------------------------------------- 规则表单

def build_rule_fields(
    rule: RuleInfo | None,
    groups: list[SecurityGroupInfo],
    allow_group: bool,
    values: dict | None = None,
) -> list[FormField]:
    """按当前原始值构造字段；driver 字段变化时由 RuleFormModal 真实重建。"""
    current = dict(_rule_values(rule))
    current.update(values or {})
    is_new = rule is None

    direction = str(current.get("direction") or "ingress")
    protocol = normalize_rule_type(current.get("protocol"))
    port = str(current.get("port") or "")
    auth_kind = str(current.get("auth_kind") or ("group" if allow_group else "cidr"))
    if auth_kind == "prefix":
        auth_kind = "cidr"
    if not allow_group:
        auth_kind = "cidr"
    auth_value = str(current.get("auth_value") or "")
    policy = _policy_label(current.get("policy"))
    priority = str(current.get("priority") or "100")
    description = str(current.get("description") or "")

    fields: list[FormField] = []
    if is_new:
        fields.append(FormField(
            "direction", "方向", kind="choice", value=direction,
            options=[("入方向", "ingress"), ("出方向", "egress")], driver=True,
        ))
    else:
        fields.append(FormField(
            "direction_note", f"方向：{describe_direction_label(direction)}（不可修改）", kind="note"
        ))

    protocol_options = [(name, name) for name in RULE_PROTOCOLS]
    if protocol not in RULE_PROTOCOLS:
        protocol_options.append((protocol, protocol))
    fields.append(FormField(
        "protocol", "协议", kind="choice", value=protocol,
        options=protocol_options, driver=True,
        hint="ICMP / GRE / ALL 的端口自动为 -1/-1",
    ))
    fields.append(FormField(
        "port", "端口范围", value=port, placeholder="22 或 8000/9000",
        hint="单端口写 22 或 22/22；范围写 8000/9000",
    ))

    if is_new:
        kind_options = [("CIDR", "cidr")]
        if allow_group:
            kind_options.append(("安全组", "group"))
        fields.append(FormField(
            "auth_kind", "授权对象类型", kind="choice", value=auth_kind,
            options=kind_options, driver=True,
            hint="" if allow_group else "企业级安全组不支持授权对象为安全组",
        ))
    else:
        label = "安全组" if auth_kind == "group" else "CIDR"
        fields.append(FormField("auth_kind_note", f"授权对象类型：{label}（不可修改）", kind="note"))

    if auth_kind == "group":
        fields.append(FormField(
            "auth_value", "授权对象", kind="choice", value=auth_value,
            options=_group_options(groups, auth_value),
            pick_items=lambda: _group_pick_items(groups),
            pick_title="选择安全组", browse_label="更多…",
            hint="同一 VPC 内的安全组 ID",
        ))
    else:
        fields.append(FormField(
            "auth_value", "授权对象", value=auth_value,
            placeholder="0.0.0.0/0 或 1.2.3.4",
            hint="CIDR 或裸 IP；IPv6 可直接填写",
        ))

    policy_options = [("Accept（允许）", "Accept"), ("Drop（拒绝）", "Drop")]
    if policy not in ("Accept", "Drop"):
        policy_options.append((policy, policy))
    fields.append(FormField(
        "policy", "策略", kind="choice", value=policy,
        options=policy_options,
    ))
    fields.append(FormField("priority", "优先级", value=priority, hint="1-100，越小越优先"))
    fields.append(FormField(
        "description",
        "描述",
        value=description,
        hint="≤512 字符" if is_new else "≤512 字符；服务端不支持非空改空，清空会保留原描述",
    ))
    return fields


class RuleFormModal(FormModal):
    """安全组规则表单：方向 / 授权对象类型切换时真实增删行。"""

    def __init__(
        self,
        rule: RuleInfo | None,
        groups: list[SecurityGroupInfo],
        allow_group: bool,
    ) -> None:
        self.rule = rule
        self.groups = list(groups)
        self.allow_group = allow_group
        self.is_new = rule is None
        initial = _rule_values(rule)
        if not allow_group:
            initial["auth_kind"] = "cidr"
        self._last_auth_kind = str(initial["auth_kind"])
        self._direction = str(initial["direction"])
        title = "新增安全组规则" if self.is_new else "编辑安全组规则"
        super().__init__(
            title,
            build_rule_fields(rule, self.groups, allow_group, initial),
            message="保存后规则立即生效，请确认端口与授权对象。",
        )

    def dynamic_fields(self, values: dict) -> list[FormField]:
        protocol = normalize_rule_type(values.get("protocol"))
        port = str(values.get("port") or "").strip()
        if protocol in DEFAULT_PORTS:
            values["port"] = "-1/-1"
        elif port in ("-1/-1", "-1"):
            values["port"] = "22/22"
        kind = str(values.get("auth_kind") or self._last_auth_kind)
        if kind != self._last_auth_kind:
            # 切换 CIDR / 安全组时旧值必然无效，清空避免把 IP 当安全组 ID
            values["auth_value"] = ""
            self._last_auth_kind = kind
        values["direction"] = str(values.get("direction") or self._direction)
        return build_rule_fields(self.rule, self.groups, self.allow_group, values)

    def _collect(self) -> dict | None:
        data = super()._collect()
        if data is None:
            return None

        direction = self._direction if not self.is_new else str(data.get("direction") or "ingress")
        auth_kind = str(data.get("auth_kind") or self._last_auth_kind)
        if not self.allow_group:
            auth_kind = "cidr"
        protocol = normalize_rule_type(data.get("protocol"))
        port = "-1/-1" if protocol in DEFAULT_PORTS else str(data.get("port") or "")
        try:
            port = parse_port_range(port)
        except ValueError as e:
            self.show_error(str(e))
            return None

        priority = _to_int(data.get("priority"))
        if priority is None:
            self.show_error("优先级必须是 1-100 的整数")
            return None

        policy = _policy_label(data.get("policy"))
        description = str(data.get("description") or "").strip()
        rule_input = RuleInput(
            direction=direction,
            ip_protocol=protocol,
            port_range=port,
            policy=policy,
            priority=priority,
            description=description,
            source_kind=auth_kind,
            source_value=str(data.get("auth_value") or "").strip(),
            rule_id=self.rule.rule_id if self.rule else None,
            existing=self.rule,
        )
        error = validate_rule(rule_input)
        if error:
            self.show_error(error)
            return None

        data.update({
            "direction": direction,
            "auth_kind": auth_kind,
            "protocol": protocol,
            "port": port,
            "priority": priority,
            "policy": policy,
            "description": description,
            "_rule": rule_input,
        })
        return data


# ---------------------------------------------------------------- 关机选项 / 命令

class StopOptionsModal(ModalScreen["StopOptions | None"]):
    """关机选项：正常 / 强制 + 节省停机 / 保留实例。仅由按钮提交，Enter 不误触发。"""

    DEFAULT_CSS = """
    StopOptionsModal { align: center middle; }
    StopOptionsModal > .modal-box { width: 64; max-width: 95%; }
    StopOptionsModal .so-main { height: auto; padding: 1 2; }
    StopOptionsModal .so-note { color: $text-muted; height: auto; margin-bottom: 1; }
    StopOptionsModal .so-label { height: 1; color: $text-muted; margin-top: 1; }
    StopOptionsModal Select { width: 1fr; margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape,ctrl+c", "cancel", show=False)]

    def __init__(self, *, force: bool = False) -> None:
        super().__init__()
        self.force = force

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text("关机选项", style="bold"), classes="modal-title")
            with Vertical(classes="so-main"):
                yield Static("强制关机等同断电，可能丢失未落盘的数据；节省停机可能释放公网 IP。", classes="so-note")
                yield Static("关机方式", classes="so-label")
                yield Select(
                    [("正常关机", "normal"), ("强制关机（等同断电）", "force")],
                    value="force" if self.force else "normal",
                    allow_blank=False,
                    compact=True,
                    id="so-force",
                )
                yield Static("停止模式", classes="so-label")
                yield Select(
                    [
                        ("节省停机（StopCharging，不计实例费用）", STOP_CHARGING),
                        ("保留实例（KeepCharging，继续计费）", KEEP_CHARGING),
                    ],
                    value=STOP_CHARGING,
                    allow_blank=False,
                    compact=True,
                    id="so-mode",
                )
            with Horizontal(classes="btn-row"):
                yield Button("取消", id="so-cancel", variant="default")
                yield Button("关机", id="so-ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#so-force", Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "so-ok":
            force = str(self.query_one("#so-force", Select).value) == "force"
            mode = str(self.query_one("#so-mode", Select).value)
            self.dismiss(StopOptions(force=force, stopped_mode=mode))
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommandModal(ModalScreen["str | None"]):
    """云助手命令输入（sheet 式）：多行命令 + 超时，按钮提交；返回命令内容或 None。

    超时值在提交成功后通过 ``timeout_value`` 暴露给调用方（结果类型保持 str）。
    """

    DEFAULT_CSS = """
    CommandModal { align: center middle; }
    CommandModal > .modal-box { width: 92; max-width: 95%; height: 80%; }
    CommandModal .cmd-main { height: 1fr; padding: 1 2; }
    CommandModal .cmd-note { height: auto; color: $warning; margin-bottom: 1; }
    CommandModal .cmd-label { height: 1; color: $text-muted; margin-top: 1; }
    CommandModal .cmd-timeout { width: 16; margin-bottom: 1; }
    CommandModal TextArea { height: 1fr; border: round $secondary; }
    CommandModal .cmd-err { height: 1; color: $error; }
    """

    BINDINGS = [Binding("escape,ctrl+c", "cancel", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self.timeout_value = 60

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text("执行命令", style="bold"), classes="modal-title")
            with Vertical(classes="cmd-main"):
                yield Static("命令将在实例内真实执行，请谨慎操作。", classes="cmd-note")
                yield Static("超时（秒，1-86400）", classes="cmd-label")
                yield Input(value="60", id="cmd-timeout", classes="cmd-timeout", compact=True)
                yield Static("命令内容（Shell）", classes="cmd-label")
                yield TextArea(id="cmd-content", tab_behavior="focus")
                yield Static("", id="cmd-err", classes="cmd-err")
            with Horizontal(classes="btn-row"):
                yield Button("取消", id="cmd-cancel", variant="default")
                yield Button("执行", id="cmd-ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#cmd-content", TextArea).focus()

    def _submit(self) -> None:
        error = self.query_one("#cmd-err", Static)
        error.update(Text(""))
        raw = self.query_one("#cmd-timeout", Input).value.strip()
        try:
            timeout = int(raw)
        except ValueError:
            error.update(Text("超时必须是 1-86400 的整数", style=STYLE_ERR))
            return
        if not 1 <= timeout <= 86400:
            error.update(Text("超时必须是 1-86400 的整数", style=STYLE_ERR))
            return
        content = self.query_one("#cmd-content", TextArea).text
        if not content.strip():
            error.update(Text("命令内容不能为空", style=STYLE_ERR))
            return
        self.timeout_value = timeout
        self.dismiss(content)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cmd-ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 地域列表

class RegionListScreen(PageScreen):
    """并发探测有实例的地域；仅 1 个地域有实例时自动进入实例列表。"""

    TITLE = "云服务器"
    HINT = "↑↓ 移动 · 回车 进入该地域 · Ctrl+R 重新探测 · Esc/Ctrl+C 返回"

    def __init__(self, *, auto_enter: bool = True, on_pick=None) -> None:
        super().__init__()
        self._regions: list[RegionInfo] = []
        self._display: list[RegionInfo] = []
        self._auto_entered = False
        self.auto_enter = auto_enter
        # 切换地域模式下由调用方接管：选中后关掉本页再回调，避免返回栈里留下中间页
        self.on_pick = on_pick

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("", classes="fl-label", id="rg-note")
                yield Static(classes="fill")
                yield Button("重新探测", id="rg-refresh")
        table = make_table("地域", "地域 ID", "实例数")
        table.id = "rg-table"
        yield table

    def on_mount(self) -> None:
        self._load()
        self.query_one(DataTable).focus()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        self.app.get_ecs().invalidate_discovery()
        self._auto_entered = False
        self._load()

    @work(exclusive=True)
    async def _load(self) -> None:
        self.app.status("正在探测各地域实例…")
        # 加载中保持空白（与其它页面一致），避免把「还没结果」误报成权限问题
        self._set_note("")
        table = self.query_one("#rg-table", DataTable)
        table.loading = True
        try:
            regions = await asyncio.to_thread(self.app.get_ecs().discover_regions)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._regions = []
            self._set_note(str(e).splitlines()[0])
            table.loading = False
            self._rebuild()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"探测地域失败: {type(e).__name__}: {e}")
            self._regions = []
            self._set_note(f"探测地域失败：{type(e).__name__}")
            table.loading = False
            self._rebuild()
            return
        table.loading = False
        if not self.is_mounted:
            return

        self._regions = regions
        positive = [r for r in regions if r.instance_count > 0]
        if len(positive) == 1 and self.auto_enter and not self._auto_entered:
            self._auto_entered = True
            # 替换当前地域页，避免 Esc 时回到空壳页面
            self.app.switch_screen(InstanceListScreen(positive[0].region_id))
            return
        self._rebuild()
        self._update_summary(len(positive))
        table.focus()

    def _set_note(self, text: str) -> None:
        try:
            self.query_one("#rg-note", Static).update(text)
        except NoMatches:
            pass

    def _update_summary(self, positive: int) -> None:
        """探测完成后才给提示：加载中一律空白，失败时展示真实错误。"""
        if not self._regions:
            note = "未获取到任何地域"
        elif positive == 0:
            note = "未发现实例；可进入地域查看安全组"
        else:
            note = f"共 {len(self._regions)} 个地域，{positive} 个有实例"
        self._set_note(note)
        self.app.status(f"{positive} 个地域有实例" if positive else note, "warn" if positive == 0 else "")

    def _rebuild(self, *, after_layout: bool = False) -> None:
        self._display = list(self._regions)
        table = self.query_one("#rg-table", DataTable)
        vis = fit_table_columns(table, [18, 18, 10], rows=len(self._display))
        keys = list(table.columns.keys())
        table.columns[keys[2]].label = rcell("实例数", vis[2] - 2)
        rows = [
            [
                Text(shorten(region.local_name or region.region_id, vis[0] - 2)),
                Text(shorten(region.region_id, vis[1] - 2)),
                rcell(region.instance_count, vis[2] - 2),
            ]
            for region in self._display
        ]
        load_rows(table, rows)

        available = sum(1 for r in self._regions if r.status == "available")
        positive = sum(1 for r in self._regions if r.instance_count > 0)
        self.set_subtitle(f"共 {positive} 个地域有实例，{available} 个可用地域")
        if not after_layout:
            self.call_after_refresh(lambda: self._rebuild(after_layout=True))

    def on_resize(self, event) -> None:
        if self.is_mounted:
            self._rebuild()

    # ------------------------------------------------------------ 事件

    @on(DataTable.RowSelected, "#rg-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if not (0 <= idx < len(self._display)):
            return
        region = self._display[idx].region_id
        if self.on_pick is not None:
            self.app.pop_screen()
            self.on_pick(region)
        else:
            self.app.push_screen(InstanceListScreen(region))

    @on(Button.Pressed, "#rg-refresh")
    def _on_refresh(self) -> None:
        self.reload_page()


# ---------------------------------------------------------------- 实例列表

class InstanceListScreen(PageScreen):
    """某地域的 ECS 实例列表：搜索、标记批量启停、详情与安全组入口。"""

    TITLE = "云服务器"
    HINT = "空格 标记 · 双击/回车 详情 · / 搜索 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [
        Binding("/", "focus_search", "搜索", show=False),
        Binding("space", "toggle_mark", "标记", show=False),
    ]

    def __init__(self, region: str) -> None:
        super().__init__()
        self.region_id = region
        self._instances: list[InstanceInfo] = []
        self._display: list[InstanceInfo] = []
        self._marked: set[str] = set()
        self._filter = ""
        self._keep_id = ""
        self._dirty = False
        self._notice: tuple[str, str] | None = None
        self.menu = [
            ("刷新", self._load, False),
            ("切换地域", self._switch_region, False),
            ("安全组管理", self._open_security_groups, False),
            ("强制关机", lambda: self._confirm_stop(force=True), True),
            ("强制重启", lambda: self._confirm_reboot(force=True), True),
        ]

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("搜索", classes="fl-label")
                yield Input(placeholder="按名称 / ID / IP 筛选（/ 聚焦）", compact=True, id="il-search")
                yield Button("刷新", id="il-refresh")
            with Horizontal(classes="filter-row gap-top"):
                yield Static(classes="fill")
                yield Button("详情", id="il-detail")
                yield Button("标记", id="il-mark")
                yield Button("全选", id="il-all")
                yield Button("清空", id="il-clear")
            with Horizontal(classes="filter-row gap-top"):
                yield Static(classes="fill")
                yield Button("开机", id="il-start")
                yield Button("关机", id="il-stop")
                yield Button("重启", id="il-reboot")
            with Horizontal(classes="filter-row gap-top"):
                yield Static(classes="fill")
                yield Button("批量开机", id="il-batch-start")
                yield Button("批量关机", id="il-batch-stop")
                yield Button("批量重启", id="il-batch-reboot")
        table = make_table("选", "名称", "状态", "规格", "私网 IP", "公网 IP", "到期")
        table.id = "il-table"
        yield table

    def on_mount(self) -> None:
        self.set_subtitle(self.region_id)
        self._load()
        self.query_one(DataTable).focus()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self, notice: tuple[str, str] | None = None) -> None:
        self._notice = notice
        current = self._current()
        self._keep_id = current.instance_id if current is not None else ""
        self.app.status(f"正在获取 {self.region_id} 实例列表…")
        table = self.query_one("#il-table", DataTable)
        table.loading = True
        try:
            instances = await asyncio.to_thread(self.app.get_ecs().list_instances, self.region_id)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._instances = []
            table.loading = False
            self._rebuild()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取实例列表失败: {type(e).__name__}: {e}")
            self._instances = []
            table.loading = False
            self._rebuild()
            return
        table.loading = False
        if not self.is_mounted:
            return
        self._instances = sorted(instances, key=lambda i: (i.name or i.instance_id))
        # 已不存在的实例从标记里剔除，避免把失效 ID 带进批量请求
        self._marked &= {inst.instance_id for inst in self._instances}
        self._rebuild()
        table.focus()

    def _rebuild(self, *, after_layout: bool = False) -> None:
        instances = self._instances
        if self._filter:
            instances = filter_fuzzy(
                instances,
                self._filter,
                lambda i: (
                    f"{i.name} {i.instance_id} {' '.join(i.private_ips)} "
                    f"{' '.join(i.public_ips)} {i.eip}"
                ),
            )
        self._display = instances
        table = self.query_one("#il-table", DataTable)
        vis = fit_table_columns(table, [3, 24, 9, 16, 15, 15, 18], rows=len(instances))
        keys = list(table.columns.keys())
        table.columns[keys[0]].label = Text("选")
        table.columns[keys[2]].label = rcell("状态", vis[2] - 2)
        table.columns[keys[6]].label = rcell("到期", vis[6] - 2)
        rows = []
        for inst in instances:
            marked = Text("✓" if inst.instance_id in self._marked else "")
            if inst.instance_id in self._marked:
                marked.stylize(STYLE_OK)
            rows.append([
                marked,
                Text(shorten(inst.name or inst.instance_id, vis[1] - 2)),
                _status_text(inst.status, vis[2] - 2),
                Text(shorten(inst.instance_type or "-", vis[3] - 2)),
                Text(shorten(_join(inst.private_ips), vis[4] - 2)),
                Text(shorten(_join(inst.public_ips) if inst.public_ips else (inst.eip or "-"), vis[5] - 2)),
                _expire_cell(inst, vis[6] - 2),
            ])
        load_rows(table, rows)
        keep, self._keep_id = self._keep_id, ""
        _restore_cursor(table, instances, lambda inst: inst.instance_id, keep)
        expiring = sum(1 for inst in self._instances if _is_expiring(inst))
        marked = f" · 已标记 {len(self._marked)} 台" if self._marked else ""
        subtitle = f"{self.region_id} · {len(self._display)}/{len(self._instances)} 台实例{marked}"
        if expiring:
            subtitle += f" · {expiring} 台即将到期"
        self.set_subtitle(subtitle)
        if self._notice is not None:
            message, kind = self._notice
            self.app.status(message, kind)
        elif not self._instances:
            self.app.status(f"{self.region_id} 下未找到实例", "warn")
        elif expiring:
            self.app.status(f"共 {len(self._instances)} 台实例，{expiring} 台即将到期", "warn")
        else:
            self.app.status(f"共 {len(self._instances)} 台实例")
        self._sync_actions()
        if not after_layout:
            self.call_after_refresh(lambda: self._rebuild(after_layout=True))

    def on_resize(self, event) -> None:
        if self.is_mounted:
            self._rebuild()

    # ------------------------------------------------------------ 选中 / 门控

    def _current(self) -> InstanceInfo | None:
        table = self.query_one("#il-table", DataTable)
        idx = table.cursor_row
        if idx is None or not (0 <= idx < len(self._display)):
            return None
        return self._display[idx]

    def _sync_actions(self) -> None:
        inst = self._current()
        has = inst is not None
        buttons = {
            "detail": self.query_one("#il-detail", Button),
            "mark": self.query_one("#il-mark", Button),
            "all": self.query_one("#il-all", Button),
            "clear": self.query_one("#il-clear", Button),
            "batch_start": self.query_one("#il-batch-start", Button),
            "batch_stop": self.query_one("#il-batch-stop", Button),
            "batch_reboot": self.query_one("#il-batch-reboot", Button),
            "start": self.query_one("#il-start", Button),
            "stop": self.query_one("#il-stop", Button),
            "reboot": self.query_one("#il-reboot", Button),
        }
        buttons["detail"].disabled = not has
        buttons["mark"].disabled = not has
        buttons["all"].disabled = not self._display
        buttons["clear"].disabled = not self._marked
        for key in ("batch_start", "batch_stop", "batch_reboot"):
            buttons[key].disabled = not self._marked
            buttons[key].tooltip = "先按空格标记实例" if not self._marked else ""
        buttons["start"].disabled = not (has and can_start(inst.status))
        buttons["stop"].disabled = not (has and can_stop(inst.status))
        buttons["reboot"].disabled = not (has and can_reboot(inst.status))

        if not has:
            reason = "未选中实例"
            for key in ("start", "stop", "reboot"):
                buttons[key].tooltip = reason
            return
        middle = status_label(inst.status)
        buttons["start"].tooltip = "" if can_start(inst.status) else f"实例{middle}，暂不可开机"
        buttons["stop"].tooltip = "" if can_stop(inst.status) else f"实例{middle}，暂不可关机"
        buttons["reboot"].tooltip = "" if can_reboot(inst.status) else f"实例{middle}，暂不可重启"

    @on(DataTable.RowHighlighted, "#il-table")
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        self._sync_actions()

    @on(DataTable.RowSelected, "#il-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        inst = self._current()
        if inst is not None:
            self.app.push_screen(
                InstanceDetailScreen(
                    self.region_id, inst.instance_id, inst, on_changed=self._mark_dirty
                )
            )

    # ------------------------------------------------------------ 生命周期

    def _confirm_start(self) -> None:
        inst = self._current()
        if inst is None:
            self.app.notify_warn("未选中任何实例")
            return
        if not can_start(inst.status):
            self.app.notify_warn(f"实例{status_label(inst.status)}，暂不可开机")
            return

        def done(ok: bool) -> None:
            if ok:
                self._run_power("start", inst, force=False)

        self.app.push_screen(
            ConfirmModal(
                f"确定要启动实例 {inst.name or inst.instance_id}（{inst.instance_id}）吗？",
                title="开机确认",
                yes="开机",
                default_yes=True,
            ),
            done,
        )

    def _confirm_stop(self, *, force: bool = False) -> None:
        inst = self._current()
        if inst is None:
            self.app.notify_warn("未选中任何实例")
            return
        if not can_stop(inst.status):
            self.app.notify_warn(f"实例{status_label(inst.status)}，暂不可关机")
            return

        def options(opts: StopOptions | None) -> None:
            if opts is None:
                return
            action = "强制关机" if opts.force else "关机"
            extra = "强制关机等同于断电，未落盘数据可能丢失。" if opts.force else "请确认业务已做好停机准备。"

            def done(ok: bool) -> None:
                if ok:
                    self._run_power(
                        "stop", inst, force=opts.force, stopped_mode=opts.stopped_mode
                    )

            self.app.push_screen(
                ConfirmModal(
                    f"确定要{action}实例 {inst.name or inst.instance_id}（{inst.instance_id}）吗？{extra}",
                    title=f"{action}确认",
                    yes=action,
                    default_yes=not opts.force,
                ),
                done,
            )

        self.app.push_screen(StopOptionsModal(force=force), options)

    def _confirm_reboot(self, *, force: bool = False) -> None:
        inst = self._current()
        if inst is None:
            self.app.notify_warn("未选中任何实例")
            return
        if not can_reboot(inst.status):
            self.app.notify_warn(f"实例{status_label(inst.status)}，暂不可重启")
            return
        action = "强制重启" if force else "重启"
        extra = "强制重启会立即中断实例，未落盘数据可能丢失。" if force else "重启期间业务会短暂中断。"

        def done(ok: bool) -> None:
            if ok:
                self._run_power("reboot", inst, force=force)

        self.app.push_screen(
            ConfirmModal(
                f"确定要{action}实例 {inst.name or inst.instance_id}（{inst.instance_id}）吗？{extra}",
                title=f"{action}确认",
                yes=action,
                default_yes=not force,
            ),
            done,
        )

    @work(exclusive=True)
    async def _run_power(
        self,
        action: str,
        inst: InstanceInfo,
        *,
        force: bool,
        stopped_mode: str | None = None,
    ) -> None:
        ecs = self.app.get_ecs()
        verbs = {"start": ("启动", "开机"), "stop": ("停止", "关机"), "reboot": ("重启", "重启")}
        verb, done_word = verbs[action]
        targets = {"Running"} if action in ("start", "reboot") else {"Stopped"}
        try:
            async with busy(self.app, f"正在{verb}实例…", mode="dots") as overlay:
                if action == "start":
                    await asyncio.to_thread(ecs.start_instance, self.region_id, inst.instance_id)
                elif action == "stop":
                    await asyncio.to_thread(
                        ecs.stop_instance,
                        self.region_id,
                        inst.instance_id,
                        force=force,
                        stopped_mode=stopped_mode,
                    )
                else:
                    await asyncio.to_thread(ecs.reboot_instance, self.region_id, inst.instance_id, force=force)
                overlay.set_message(f"正在等待实例{verb}完成…")
                status = await asyncio.to_thread(
                    ecs.wait_instance_status, self.region_id, inst.instance_id, targets
                )
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._load()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"{verb}实例失败: {type(e).__name__}: {e}")
            self._load()
            return

        if status in targets:
            self.app.notify_ok(f"实例已{done_word}（当前状态 {status_label(status)}）")
        else:
            self.app.notify_warn(
                f"已下发{done_word}请求，当前状态 {status_label(status) or '未知'}，请稍后刷新确认"
            )
        self._load()

    # ------------------------------------------------------------ 标记 / 批量

    def action_toggle_mark(self) -> None:
        inst = self._current()
        if inst is None:
            self.app.notify_warn("未选中任何实例")
            return
        if inst.instance_id in self._marked:
            self._marked.discard(inst.instance_id)
        else:
            self._marked.add(inst.instance_id)
        self._keep_id = inst.instance_id
        self._rebuild()

    def _select_all(self) -> None:
        self._marked.update(inst.instance_id for inst in self._display)
        self._rebuild()

    def _clear_marks(self) -> None:
        self._marked.clear()
        self._rebuild()

    def _marked_instances(self) -> list[InstanceInfo]:
        return [inst for inst in self._instances if inst.instance_id in self._marked]

    def _confirm_batch(self, action: str) -> None:
        chosen = self._marked_instances()
        if not chosen:
            self.app.notify_warn("请先按空格标记实例")
            return
        gate = {"start": can_start, "stop": can_stop, "reboot": can_reboot}[action]
        eligible = [inst for inst in chosen if gate(inst.status)]
        if not eligible:
            word = {"start": "开机", "stop": "关机", "reboot": "重启"}[action]
            self.app.notify_warn(f"已标记的实例当前状态均不可{word}")
            return
        if action == "stop":
            self._batch_stop_options(eligible)
        else:
            self._batch_confirm_then_run(action, eligible)

    def _batch_stop_options(self, eligible: list[InstanceInfo]) -> None:
        def options(opts: StopOptions | None) -> None:
            if opts is not None:
                self._batch_confirm_then_run(
                    "stop", eligible, force=opts.force, stopped_mode=opts.stopped_mode
                )

        self.app.push_screen(StopOptionsModal(force=False), options)

    def _batch_confirm_then_run(
        self,
        action: str,
        eligible: list[InstanceInfo],
        *,
        force: bool = False,
        stopped_mode: str | None = None,
    ) -> None:
        word = {"start": "开机", "stop": "关机", "reboot": "重启"}[action]
        names = "、".join(inst.name or inst.instance_id for inst in eligible[:3])
        if len(eligible) > 3:
            names += "…"
        skipped = len(self._marked_instances()) - len(eligible)
        message = f"确定要批量{word}共 {len(eligible)} 台实例吗？\n{names}"
        if skipped > 0:
            message += f"\n另有 {skipped} 台因状态不允许将被跳过。"
        if force:
            message += "\n强制操作等同断电，未落盘数据可能丢失。"

        def done(ok: bool) -> None:
            if ok:
                self._run_batch(
                    action,
                    [inst.instance_id for inst in eligible],
                    force=force,
                    stopped_mode=stopped_mode,
                )

        self.app.push_screen(
            ConfirmModal(
                message,
                title=f"批量{word}确认",
                yes=f"批量{word}",
                default_yes=False,
            ),
            done,
        )

    @work(exclusive=True)
    async def _run_batch(
        self,
        action: str,
        ids: list[str],
        *,
        force: bool = False,
        stopped_mode: str | None = None,
    ) -> None:
        ecs = self.app.get_ecs()
        verbs = {"start": ("启动", "开机"), "stop": ("停止", "关机"), "reboot": ("重启", "重启")}
        verb, done_word = verbs[action]
        targets = {"Running"} if action in ("start", "reboot") else {"Stopped"}
        try:
            async with busy(self.app, f"正在批量{verb}实例…", mode="dots") as overlay:
                if action == "start":
                    result = await asyncio.to_thread(ecs.start_instances, self.region_id, ids)
                elif action == "stop":
                    result = await asyncio.to_thread(
                        ecs.stop_instances,
                        self.region_id,
                        ids,
                        force=force,
                        stopped_mode=stopped_mode,
                    )
                else:
                    result = await asyncio.to_thread(
                        ecs.reboot_instances, self.region_id, ids, force=force
                    )
                statuses: dict[str, str] = {}
                if result.success:
                    overlay.set_message(f"正在等待实例{verb}完成…")
                    statuses = await asyncio.to_thread(
                        ecs.wait_instances_status, self.region_id, result.success, targets
                    )
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._load()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"批量{verb}失败: {type(e).__name__}: {e}")
            self._load()
            return

        self._summarize_batch(action, ids, done_word, result, statuses, targets)
        self._marked.clear()
        notice = self._notice
        self._load(notice)

    def _summarize_batch(
        self,
        action: str,
        ids: list[str],
        done_word: str,
        result,
        statuses: dict[str, str],
        targets: set[str],
    ) -> None:
        names = {inst.instance_id: (inst.name or inst.instance_id) for inst in self._instances}
        arrived = [iid for iid in result.success if statuses.get(iid) in targets]
        pending = [iid for iid in result.success if statuses.get(iid) not in targets]
        failed = result.failed
        if not failed and not pending:
            message = f"{len(arrived)} 台已{done_word}"
            self.app.notify_ok(message)
            self._notice = (message, "ok")
            return
        message = (
            f"批量{done_word}：{len(arrived)} 台已到位，{len(pending)} 台未到位，{len(failed)} 台下发失败"
        )
        self.app.notify_warn(message)
        self._notice = (message, "warn")
        lines = [f"共 {len(ids)} 台，已到位 {len(arrived)} 台。"]
        if arrived:
            lines.append("")
            lines.append(f"已到位（{len(arrived)} 台）：")
            for iid in arrived:
                lines.append(f"  {names.get(iid, iid)}（{iid}） {status_label(statuses.get(iid))}")
        if pending:
            lines.append("")
            lines.append(f"未到位（{len(pending)} 台）：")
            for iid in pending:
                state = status_label(statuses.get(iid)) or "未知"
                lines.append(f"  {names.get(iid, iid)}（{iid}） 最后状态：{state}")
        if failed:
            lines.append("")
            lines.append(f"下发失败（{len(failed)} 台）：")
            for iid, message in failed.items():
                first = message.splitlines()[0] if message else "未知错误"
                lines.append(f"  {names.get(iid, iid)}（{iid}） {first}")
        self.app.push_screen(OutputModal(f"批量{done_word}结果", Text("\n".join(lines))))

    # ------------------------------------------------------------ 菜单 / 事件

    def _switch_region(self) -> None:
        # 选中后先关掉地域页，再由本页切换 region，返回栈里不会多出一层
        self.app.push_screen(
            RegionListScreen(auto_enter=False, on_pick=self._change_region)
        )

    def _change_region(self, region: str) -> None:
        if region == self.region_id:
            return
        self.region_id = region
        self._filter = ""
        self._marked.clear()
        try:
            self.query_one("#il-search", Input).value = ""
        except NoMatches:
            pass
        self._keep_id = ""
        self.set_subtitle(region)
        self._load()

    def _mark_dirty(self) -> None:
        self._dirty = True

    def on_screen_resume(self) -> None:
        """详情页里做过启停后返回本页时补一次刷新，避免状态陈旧。"""
        if self._dirty:
            self._dirty = False
            self._load()

    def _open_security_groups(self) -> None:
        self.app.push_screen(SecurityGroupListScreen(self.region_id))

    def _open_detail(self) -> None:
        inst = self._current()
        if inst is None:
            self.app.notify_warn("未选中任何实例")
            return
        self.app.push_screen(
            InstanceDetailScreen(
                self.region_id, inst.instance_id, inst, on_changed=self._mark_dirty
            )
        )

    @on(Input.Changed, "#il-search")
    def _on_search(self, event: Input.Changed) -> None:
        self._filter = event.value.strip()
        self._rebuild()

    @on(Button.Pressed, "#il-refresh")
    def _on_refresh(self) -> None:
        self._load()

    @on(Button.Pressed, "#il-detail")
    def _on_detail(self) -> None:
        self._open_detail()

    @on(Button.Pressed, "#il-start")
    def _on_start(self) -> None:
        self._confirm_start()

    @on(Button.Pressed, "#il-stop")
    def _on_stop(self) -> None:
        self._confirm_stop()

    @on(Button.Pressed, "#il-reboot")
    def _on_reboot(self) -> None:
        self._confirm_reboot()

    @on(Button.Pressed, "#il-mark")
    def _on_mark(self) -> None:
        self.action_toggle_mark()

    @on(Button.Pressed, "#il-all")
    def _on_select_all(self) -> None:
        self._select_all()

    @on(Button.Pressed, "#il-clear")
    def _on_clear_marks(self) -> None:
        self._clear_marks()

    @on(Button.Pressed, "#il-batch-start")
    def _on_batch_start(self) -> None:
        self._confirm_batch("start")

    @on(Button.Pressed, "#il-batch-stop")
    def _on_batch_stop(self) -> None:
        self._confirm_batch("stop")

    @on(Button.Pressed, "#il-batch-reboot")
    def _on_batch_reboot(self) -> None:
        self._confirm_batch("reboot")

    def action_focus_search(self) -> None:
        self.query_one("#il-search", Input).focus()


# ---------------------------------------------------------------- 实例详情

class InstanceDetailScreen(PageScreen):
    """实例详情：基本信息 / 监控 / 网络与安全组 / 存储与备份 四页签。"""

    AUTO_OVERVIEW_SECONDS = 60
    """基本信息页「监控」读数卡的自动刷新间隔。"""
    AUTO_MONITOR_SECONDS = 15
    """监控页三条折线的自动刷新间隔。"""
    OVERVIEW_WINDOW = "1h"
    """基本信息页读数卡用的时间窗（只取最新点，1 小时足够且点数最少）。"""

    TITLE = "实例详情"
    HINT = (
        "←→ 切换页签 · Tab 聚焦控件/图表 ←→ 取值 · 安全组页 双击/回车 管理规则 · "
        "监控 15 秒自动刷新 · Ctrl+R 刷新 · Esc 返回"
    )

    DEFAULT_CSS = """
    InstanceDetailScreen TabbedContent { height: 1fr; }
    InstanceDetailScreen TabPane { height: 1fr; }
    InstanceDetailScreen #ov-scroll { height: 1fr; padding: 0 1; }
    InstanceDetailScreen #mon-scroll { height: 1fr; padding: 0 1; }
    InstanceDetailScreen .ov-sec {
        height: 1;
        margin: 2 0 0 0;
        color: $text-muted;
        text-style: bold;
    }
    InstanceDetailScreen .ov-sec-first {
        height: 1;
        color: $text-muted;
        text-style: bold;
    }
    """

    def __init__(self, region: str, instance_id: str, instance: InstanceInfo | None = None, on_changed=None) -> None:
        super().__init__()
        self.region_id = region
        self.instance_id = instance_id
        self._instance = instance
        self._groups: list[SecurityGroupInfo] = []
        self._disks: list = []
        self._eip: EipInfo | None = None
        self._renew: dict[str, AutoRenewInfo] = {}
        self._mon_loaded = False
        self._mon_loading = False
        self._mon_window = "6h"
        self._mon_snapshot: MonitorSnapshot | None = None
        self._ov_snapshot: MonitorSnapshot | None = None
        self._ov_failed = False
        self._ov_loading = False
        # 做过启停后通知列表页刷新（列表页只置脏标记，返回时才重新取数）
        self.on_changed = on_changed

    def compose_toolbar(self):
        """刷新挂在顶栏：基本信息页按钮行在 60 列下放不下第 5 个按钮。"""
        yield Button("刷新", id="dt-refresh")

    def compose_page(self):
        with TabbedContent(id="dt-tabs"):
            with TabPane("基本信息", id="dt-tab-ov"):
                with VerticalScroll(id="ov-scroll"):
                    with Vertical(classes="panel"):
                        yield Static("基本信息", classes="ov-sec-first")
                        yield from self._kv("名称", "ov-name")
                        yield from self._kv("实例 ID", "ov-id")
                        yield from self._kv("状态", "ov-status")
                        yield from self._kv("规格", "ov-type")
                        yield from self._kv("CPU / 内存", "ov-spec")
                        yield from self._kv("操作系统", "ov-os")
                        yield from self._kv("镜像 ID", "ov-image")
                        yield from self._kv("可用区", "ov-zone")
                        yield from self._kv("锁定原因", "ov-lock")
                        with Horizontal(classes="filter-row gap-top"):
                            yield Static(classes="fill")
                            yield Button("开机", id="dt-start")
                            yield Button("关机", id="dt-stop")
                            yield Button("重启", id="dt-reboot")
                            yield Button("执行命令", id="dt-exec")
                    with Vertical(classes="panel"):
                        yield Static("监控", classes="ov-sec-first")
                        yield Static(
                            "最新 —  CPU — · 内存 — · 磁盘 —",
                            id="ov-mon-readout",
                            classes="mon-readout",
                        )
                        yield Static("", id="ov-mon-note", classes="mon-empty")
                    with Vertical(classes="panel"):
                        yield Static("网络", classes="ov-sec-first")
                        yield from self._kv("私网 IP", "ov-private")
                        yield from self._kv("公网 IP", "ov-public")
                        yield from self._kv("EIP", "ov-eip")
                        yield from self._kv("EIP 带宽", "ov-eip-bw")
                        yield from self._kv("VPC", "ov-vpc")
                        yield from self._kv("交换机", "ov-vswitch")
                        yield from self._kv("网络选项", "ov-netopts")
                        yield from self._kv("关联安全组", "ov-sgs")
                        with Horizontal(classes="filter-row gap-top"):
                            yield Static(classes="fill")
                            yield Button("调整带宽", id="dt-bandwidth")
                    with Vertical(classes="panel"):
                        yield Static("计费与时间", classes="ov-sec-first")
                        yield from self._kv("付费类型", "ov-charge")
                        yield from self._kv("网络计费", "ov-inet")
                        yield from self._kv("公网带宽", "ov-bandwidth")
                        yield from self._kv("自动续费", "ov-renew")
                        yield from self._kv("剩余天数", "ov-remain")
                        yield from self._kv("创建时间", "ov-created")
                        yield from self._kv("到期时间", "ov-expired")
            with TabPane("监控", id="dt-tab-mon"):
                with VerticalScroll(id="mon-scroll"):
                    with Vertical(classes="panel"):
                        with Horizontal(classes="filter-row"):
                            yield Static("时间窗", classes="fl-label")
                            for key in ("1h", "6h", "24h", "7d"):
                                yield Button(
                                    WINDOW_LABELS[key],
                                    id=f"mon-w{key}",
                                    classes="mon-win",
                                    variant="primary" if key == "6h" else "default",
                                )
                    with Vertical(classes="panel"):
                        yield Static(
                            "←→ 移动光标取值（未定位时显示最新数值）",
                            id="mon-readout",
                            classes="mon-readout",
                        )
                    for key, label in (
                        ("cpu", "CPU 使用率"),
                        ("mem", "内存使用率"),
                        ("disk", "系统盘使用率"),
                    ):
                        with Vertical(classes="panel"):
                            yield Static(label, id=f"mon-{key}-head", classes="mon-head")
                            yield Static("", id=f"mon-{key}-bar", classes="mon-bar")
                            yield LineChart(id=f"mon-{key}", chart_height=6)
                            yield Static("", id=f"mon-{key}-empty", classes="mon-empty")
            with TabPane("网络与安全组", id="dt-tab-sg"):
                table = make_table("名称", "ID", "类型", "规则数")
                table.id = "dt-sg-table"
                yield table
            with TabPane("存储与备份", id="dt-tab-disk"):
                table = make_table("磁盘 ID", "名称", "类型", "容量", "状态", "挂载点", "加密")
                table.id = "dt-disk-table"
                yield table

    def _kv(self, label: str, value_id: str):
        with Horizontal(classes="kv-row"):
            yield Static(label, classes="kv-label")
            yield Static("-", classes="kv-value", id=value_id)

    def on_mount(self) -> None:
        # 焦点放在页签栏：既保留 ←→ 切页签，又不会像聚焦页内按钮那样把内容滚到底部。
        # TabbedContent 的子件在个别时序下（如 App 正在退出）尚未 compose，取不到就跳过聚焦。
        try:
            self.query_one("#dt-tabs Tabs").focus()
        except NoMatches:
            pass
        instance = self._instance
        if instance is not None:
            # 页签内容在 on_mount 时不一定已挂载，延后一帧再渲染
            self.call_after_refresh(lambda: self._render_overview(instance))
        else:
            self.set_subtitle(self.instance_id)
        self._load()
        # 指标自动刷新：基本信息页 60 秒、监控页 15 秒（仅对应页签可见时触发）
        self.set_interval(self.AUTO_OVERVIEW_SECONDS, self._tick_overview)
        self.set_interval(self.AUTO_MONITOR_SECONDS, self._tick_monitor)
        self._request_readout()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        try:
            active = self.query_one("#dt-tabs", TabbedContent).active
        except NoMatches:
            active = ""
        if active == "dt-tab-mon":
            self.app.get_monitor().invalidate()
            self._load_monitor()
        elif active == "dt-tab-ov":
            self.app.get_monitor().invalidate()
            self._load()
            self._request_readout(force=True)
        else:
            self._load()

    # ------------------------------------------------------------ 自动刷新

    def _active_pane(self) -> str:
        try:
            return self.query_one("#dt-tabs", TabbedContent).active or ""
        except NoMatches:
            return ""

    def _can_auto_refresh(self, pane_id: str) -> bool:
        """仅当本屏在前台、且停留在目标页签时自动刷新；弹窗或其它屏在上层时让路。"""
        if not self.is_mounted or self.app.screen is not self:
            return False
        return self._active_pane() == pane_id

    def _tick_overview(self) -> None:
        if self._can_auto_refresh("dt-tab-ov"):
            self._request_readout(force=True)

    def _tick_monitor(self) -> None:
        if self._mon_loading or not self._can_auto_refresh("dt-tab-mon"):
            return
        self._load_monitor(auto=True)

    def _request_readout(self, force: bool = False) -> None:
        """发起一次读数卡取数；已在取数中则跳过，避免挂载/切页签重复打接口。"""
        if self._ov_loading:
            return
        self._ov_loading = True
        self._run_readout(force)

    @work(exclusive=True, group="readout")
    async def _run_readout(self, force: bool = False) -> None:
        """取基本信息页「监控」卡的最新值。静默进行，不盖遮罩、不打断当前操作。"""
        try:
            try:
                monitor = self.app.get_monitor()
            except Exception as e:  # noqa: BLE001 — 配置缺失等
                if not self._ov_failed:
                    self._ov_failed = True
                    self.app.notify_err(f"初始化云监控客户端失败: {e}")
                return
            failure = ""
            try:
                snapshot = await asyncio.to_thread(
                    monitor.get_series,
                    self.region_id,
                    self.instance_id,
                    self.OVERVIEW_WINDOW,
                    max_age=0 if force else None,
                )
            except AliyunApiError as e:
                failure = str(e)
                snapshot = None
            except Exception as e:  # noqa: BLE001
                failure = f"{type(e).__name__}: {e}"
                snapshot = None
            if snapshot is None:
                # 自动刷新失败只在首次提示，避免每分钟弹一次同样的告警
                if failure and not self._ov_failed:
                    self._ov_failed = True
                    self.app.notify_warn(f"获取最新监控失败: {failure}")
                return
            self._ov_snapshot = snapshot
            self._ov_failed = False
            if not self.is_mounted or self._active_pane() not in ("", "dt-tab-ov"):
                # 已离开基本信息页：数据仍记下，切回时会重新渲染
                return
            self._render_ov_readout()
        finally:
            self._ov_loading = False

    # ------------------------------------------------------------ 监控

    @work(exclusive=True, group="monitor")
    async def _load_monitor(self, auto: bool = False) -> None:
        window = self._mon_window
        try:
            monitor = self.app.get_monitor()
        except Exception as e:  # noqa: BLE001 — 配置缺失等
            self.app.notify_err(f"初始化云监控客户端失败: {e}")
            return
        try:
            scroll = self.query_one("#mon-scroll", VerticalScroll)
        except NoMatches:
            return
        self._mon_loading = True
        previous = self.app.focused
        if not auto:
            self.app.status(f"正在获取 {WINDOW_LABELS[window]} 监控数据…")
            scroll.loading = True
        try:
            snapshot = await asyncio.to_thread(
                monitor.get_series,
                self.region_id,
                self.instance_id,
                window,
                max_age=0 if auto else None,
            )
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取监控数据失败: {type(e).__name__}: {e}")
            return
        finally:
            self._mon_loading = False
            if not auto and scroll.is_mounted:
                _set_loading(scroll, False)
        if not self.is_mounted or window != self._mon_window:
            return
        self._mon_snapshot = snapshot
        self._mon_loaded = True
        self._render_monitor(snapshot, quiet=auto)
        # 还原焦点要谨慎：TabbedContent 收到「页签内控件获得焦点」会把页签切回该页，
        # 若期间用户已切走页签或把焦点移到别处，强行 focus 会把页签拽回监控页。
        if (
            not auto
            and previous is not None
            and previous.is_mounted
            and previous.can_focus
            and self.app.focused in (None, previous)
            and self._active_pane() == "dt-tab-mon"
        ):
            previous.focus()

    @staticmethod
    def _fmt_metric(value: float, unit: str) -> str:
        rounded = round(value)
        text = f"{rounded:.0f}" if abs(value - rounded) < 0.05 else f"{value:.1f}"
        return f"{text}{unit}"

    @staticmethod
    def _metric_bar(percent: float) -> Text:
        width = 30
        ratio = max(0.0, min(1.0, percent / 100.0))
        filled = round(ratio * width)
        style = STYLE_OK if percent < 60 else STYLE_WARN if percent < 80 else STYLE_ERR
        bar = Text()
        bar.append("█" * filled, style=style)
        bar.append("░" * (width - filled), style=STYLE_DIM)
        return bar

    def _sync_window_buttons(self) -> None:
        for key in WINDOW_LABELS:
            try:
                button = self.query_one(f"#mon-w{key}", Button)
            except NoMatches:
                continue
            button.variant = "primary" if key == self._mon_window else "default"

    def _render_monitor(self, snapshot: MonitorSnapshot, *, quiet: bool = False) -> None:
        series_by_key = {item.key: item for item in snapshot.series}
        for chart_key, series_key, style_name in (
            ("cpu", "cpu", "chart--s0"),
            ("mem", "memory", "chart--s1"),
            ("disk", "disk", "chart--s2"),
        ):
            try:
                head = self.query_one(f"#mon-{chart_key}-head", Static)
                bar = self.query_one(f"#mon-{chart_key}-bar", Static)
                chart = self.query_one(f"#mon-{chart_key}", LineChart)
                empty = self.query_one(f"#mon-{chart_key}-empty", Static)
            except NoMatches:
                continue
            item = series_by_key.get(series_key)
            values = [p.value for p in item.points] if item is not None else []
            if item is None or not values:
                reason = (item.reason if item is not None else "") or "无数据"
                head.update(Text(f"{item.label if item is not None else series_key}   当前 —"))
                bar.update("")
                bar.display = False
                chart.display = False
                empty.update(Text(reason))
                empty.display = True
                continue
            current = values[-1]
            head_text = Text()
            head_text.append(item.label, style="bold")
            head_text.append(
                "   当前 "
                f"{self._fmt_metric(current, item.unit)}   峰值 {self._fmt_metric(max(values), item.unit)}"
                f"   均值 {self._fmt_metric(sum(values) / len(values), item.unit)}"
            )
            if item.detail:
                head_text.append(f"\n{item.detail}", style=STYLE_DIM)
            if item.source == "ecs":
                head_text.append("   （数据源：ECS）", style=STYLE_DIM)
            head.update(head_text)
            bar.update(self._metric_bar(current))
            bar.display = True
            chart.set_series([ChartSeries(item.label, item.points, style_name)])
            chart.display = True
            empty.display = False
        self._sync_window_buttons()
        self._update_readout()
        if quiet:
            return  # 自动刷新不动状态栏，避免每 15 秒覆盖其它提示
        if snapshot.warnings:
            self.app.status("部分指标不可用", "warn")
        else:
            self.app.status(f"监控数据已更新 · {WINDOW_LABELS.get(snapshot.window, snapshot.window)}")

    def _series_value(self, key: str, ts, snapshot: MonitorSnapshot | None = None) -> str:
        snapshot = snapshot if snapshot is not None else self._mon_snapshot
        if snapshot is None:
            return "—"
        item = next((s for s in snapshot.series if s.key == key), None)
        if item is None or not item.points:
            return "—"
        point = item.points[-1]
        if ts is not None:
            point = min(item.points, key=lambda p: abs((p.ts - ts).total_seconds()))
        return self._fmt_metric(point.value, item.unit)

    def _readout_text(self, snapshot: MonitorSnapshot, ts=None) -> Text:
        """一行读数：未指定 ts 时取最新点，否则取光标所在时刻。"""
        has_cursor = ts is not None
        if not has_cursor:
            latest = [s.points[-1].ts for s in snapshot.series if s.points]
            ts = max(latest) if latest else None
        when = ts.astimezone().strftime("%H:%M") if ts is not None else "—"
        text = Text()
        text.append(f"{'光标' if has_cursor else '最新'} {when}   ")
        text.append(f"CPU {self._series_value('cpu', ts, snapshot)}", style=STYLE_OK)
        text.append(" · ")
        text.append(f"内存 {self._series_value('memory', ts, snapshot)}", style=STYLE_INFO)
        text.append(" · ")
        text.append(f"磁盘 {self._series_value('disk', ts, snapshot)}", style=STYLE_WARN)
        return text

    def _update_readout(self, ts=None) -> None:
        """监控页读数行：定位光标时显示该时刻数值，未定位时提示操作方式。"""
        snapshot = self._mon_snapshot
        if snapshot is None:
            return
        text = (
            Text("←→ 移动光标取值（未定位时显示最新数值）", style=STYLE_DIM)
            if ts is None
            else self._readout_text(snapshot, ts)
        )
        try:
            self.query_one("#mon-readout", Static).update(text)
        except NoMatches:
            pass

    def _render_ov_readout(self) -> None:
        """基本信息页「监控」卡：最新一次采集的 CPU / 内存 / 磁盘 + 不可用原因。"""
        snapshot = self._ov_snapshot
        try:
            readout = self.query_one("#ov-mon-readout", Static)
            note = self.query_one("#ov-mon-note", Static)
        except NoMatches:
            return
        if snapshot is None:
            return
        readout.update(self._readout_text(snapshot))
        missing = [item for item in snapshot.series if not item.available]
        if not missing:
            note.update("")
            note.display = False
            return
        names = "、".join(item.label.replace("使用率", "") for item in missing)
        note.update(Text(f"{names}：{missing[0].reason or '无数据'}", style=STYLE_DIM))
        note.display = True

    @on(LineChart.CursorMoved)
    def _on_chart_cursor(self, event: LineChart.CursorMoved) -> None:
        for chart in self.query(LineChart):
            if chart is not event.chart and event.index is not None and chart.cursor is not None:
                chart.cursor = None
        point = event.point
        self._update_readout(getattr(point, "ts", None) if point is not None else None)

    def _find_table(self, selector: str) -> DataTable | None:
        try:
            return self.query_one(selector, DataTable)
        except NoMatches:
            return None

    @work(exclusive=True)
    async def _load(self) -> None:
        self.app.status(f"正在获取实例 {self.instance_id} 详情…")
        # 页签内容是分阶段挂载的：刚进入 / 快速切页签 / 正在退出时可能查不到表格，
        # 取不到就跳过遮罩（数据仍会存进 self._groups/_disks，切到该页签时再渲染）。
        sg_table = self._find_table("#dt-sg-table")
        disk_table = self._find_table("#dt-disk-table")
        _set_loading(sg_table, True)
        _set_loading(disk_table, True)
        ecs = self.app.get_ecs()

        def _fetch():
            instance: InstanceInfo | None = None
            groups: list[SecurityGroupInfo] = []
            disks: list = []
            eip: EipInfo | None = None
            renew: dict[str, AutoRenewInfo] = {}
            errors: list[str] = []
            try:
                instance = ecs.get_instance(self.region_id, self.instance_id)
            except AliyunApiError as e:
                errors.append(str(e))
            base = instance or self._instance
            if base is not None and base.security_group_ids:
                try:
                    groups = ecs.list_security_groups(
                        self.region_id,
                        security_group_ids=base.security_group_ids,
                        with_policy=False,
                    )
                except AliyunApiError as e:
                    errors.append(str(e))
            if base is not None:
                try:
                    disks = ecs.list_disks(self.region_id, self.instance_id)
                except AliyunApiError as e:
                    errors.append(str(e))
                # 新接口：Fake / 旧客户端可能没有，取不到不影响详情
                eip_fn = getattr(ecs, "get_eip", None)
                if callable(eip_fn):
                    try:
                        eip = eip_fn(self.region_id, self.instance_id)
                    except Exception:  # noqa: BLE001
                        eip = None
                renew_fn = getattr(ecs, "describe_auto_renew", None)
                if callable(renew_fn):
                    try:
                        renew = renew_fn(self.region_id, [self.instance_id])
                    except Exception:  # noqa: BLE001
                        renew = {}
            return instance, groups, disks, eip, renew, errors

        try:
            instance, groups, disks, eip, renew, errors = await asyncio.to_thread(_fetch)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取实例详情失败: {type(e).__name__}: {e}")
            return
        finally:
            _set_loading(sg_table, False)
            _set_loading(disk_table, False)

        if not self.is_mounted:
            return
        if instance is None:
            instance = self._instance
        if instance is None:
            self.app.notify_err(errors[0] if errors else "获取实例详情失败")
            return
        if errors:
            self.app.notify_warn(f"部分详情获取失败：{errors[0]}")
        self._instance = instance
        self._groups = groups
        self._disks = disks
        self._eip = eip
        self._renew = renew
        self._render_overview(instance)
        self._rebuild_groups()
        self._rebuild_disks()
        self.app.status(f"实例 {instance.name or instance.instance_id} 详情已更新")

    # ------------------------------------------------------------ 基本信息

    def _set_value(self, value_id: str, value) -> None:
        try:
            self.query_one(f"#{value_id}", Static).update(value)
        except NoMatches:  # 页签内容尚未挂载时跳过，等下一次渲染
            pass

    def _render_overview(self, inst: InstanceInfo) -> None:
        self._set_value("ov-name", inst.name or "-")
        self._set_value("ov-id", inst.instance_id)
        status = Text(status_label(inst.status))
        if STATUS_STYLE.get(inst.status):
            status.stylize(STATUS_STYLE[inst.status])
        self._set_value("ov-status", status)
        self._set_value("ov-type", inst.instance_type or "-")
        spec = f"{inst.cpu} 核 / {inst.memory} MB" if inst.cpu or inst.memory else "-"
        self._set_value("ov-spec", spec)
        self._set_value("ov-os", inst.osname or "-")
        self._set_value("ov-image", inst.image_id or "-")
        self._set_value("ov-zone", inst.zone_id or "-")
        self._set_value("ov-private", _join(inst.private_ips))
        self._set_value("ov-public", _join(inst.public_ips))
        eip = self._eip
        if eip is not None:
            eip_text = f"{eip.ip_address}（{eip.allocation_id}）" if eip.allocation_id else eip.ip_address
            self._set_value("ov-eip", eip_text or "-")
            self._set_value(
                "ov-eip-bw", f"{eip.bandwidth} Mbps · {_eip_charge_label(eip.internet_charge_type)}"
            )
        else:
            self._set_value("ov-eip", inst.eip or "-")
            self._set_value("ov-eip-bw", "-")
        self._set_value("ov-vpc", inst.vpc_id or "-")
        self._set_value("ov-vswitch", inst.vswitch_id or "-")
        self._set_value("ov-netopts", inst.network_options or "-")
        self._set_value("ov-charge", _charge_label(inst.charge_type))
        self._set_value("ov-inet", inst.internet_charge_type or "-")
        self._set_value("ov-bandwidth", f"{inst.bandwidth_out} Mbps" if inst.bandwidth_out else "-")
        self._set_value(
            "ov-renew",
            _renew_label(
                self._renew.get(inst.instance_id), postpaid=inst.charge_type == "PostPaid"
            ),
        )
        remain = _remaining_label(inst)
        remaining_days = _days_left(inst.expired_time)
        remain_text = Text(remain)
        if inst.charge_type != "PostPaid":
            if remain == "已过期":
                remain_text.stylize(STYLE_ERR)
            elif remaining_days is not None and remaining_days <= EXPIRE_WARN_DAYS:
                remain_text.stylize(STYLE_WARN)
        self._set_value("ov-remain", remain_text)
        self._set_value("ov-created", inst.creation_time or "-")
        self._set_value("ov-expired", _date(inst.expired_time))
        self._set_value("ov-lock", inst.lock_reason or "无")
        self._set_value("ov-sgs", str(len(inst.security_group_ids)) if inst.security_group_ids else "无")
        self.set_subtitle(f"{inst.name or inst.instance_id} · {inst.instance_id}")
        self._sync_actions()
        self._render_ov_readout()

    def _sync_actions(self) -> None:
        inst = self._instance
        has = inst is not None
        try:
            buttons = {
                "start": self.query_one("#dt-start", Button),
                "stop": self.query_one("#dt-stop", Button),
                "reboot": self.query_one("#dt-reboot", Button),
                "exec": self.query_one("#dt-exec", Button),
                "bandwidth": self.query_one("#dt-bandwidth", Button),
            }
        except NoMatches:  # 基本信息页签内容尚未挂载（进入即切页签 / 正在退出），下次渲染再同步
            return
        buttons["start"].disabled = not (has and can_start(inst.status))
        buttons["stop"].disabled = not (has and can_stop(inst.status))
        buttons["reboot"].disabled = not (has and can_reboot(inst.status))
        buttons["exec"].disabled = not (has and inst.status == "Running")
        buttons["bandwidth"].disabled = not _bandwidth_allowed(inst, self._eip)
        if not has:
            for button in buttons.values():
                button.tooltip = "实例信息未加载"
            return
        middle = status_label(inst.status)
        buttons["start"].tooltip = "" if can_start(inst.status) else f"实例{middle}，暂不可开机"
        buttons["stop"].tooltip = "" if can_stop(inst.status) else f"实例{middle}，暂不可关机"
        buttons["reboot"].tooltip = "" if can_reboot(inst.status) else f"实例{middle}，暂不可重启"
        buttons["exec"].tooltip = (
            "" if inst.status == "Running" else f"实例{middle}，云助手命令需要运行中的实例"
        )
        if self._eip is None and not inst.bandwidth_out:
            buttons["bandwidth"].tooltip = "实例未开通公网带宽，无法调整"
        elif str(self._eip.internet_charge_type if self._eip is not None else "") == "PayByTraffic" or (
            self._eip is None and str(inst.internet_charge_type or "") == "PayByTraffic"
        ):
            buttons["bandwidth"].tooltip = "按流量计费无需设置带宽"
        elif not _bandwidth_allowed(inst, self._eip):
            buttons["bandwidth"].tooltip = f"实例{middle}，暂不可调整带宽"
        else:
            buttons["bandwidth"].tooltip = ""

    # ------------------------------------------------------------ 安全组 / 磁盘

    def _rebuild_groups(self, *, after_layout: bool = False) -> None:
        try:
            table = self.query_one("#dt-sg-table", DataTable)
        except NoMatches:  # 页签内容尚未挂载，切到该页签时 _on_tab 会再渲染
            return
        groups = self._groups
        vis = fit_table_columns(table, [24, 22, 10, 8], rows=len(groups))
        keys = list(table.columns.keys())
        table.columns[keys[3]].label = rcell("规则数", vis[3] - 2)
        rows = [
            [
                Text(shorten(g.name or "-", vis[0] - 2)),
                Text(shorten(g.security_group_id, vis[1] - 2)),
                Text(_sg_type_label(g.sg_type)),
                rcell(g.rule_count, vis[3] - 2),
            ]
            for g in groups
        ]
        load_rows(table, rows)
        if not after_layout:
            self.call_after_refresh(lambda: self._rebuild_groups(after_layout=True))

    def _rebuild_disks(self, *, after_layout: bool = False) -> None:
        try:
            table = self.query_one("#dt-disk-table", DataTable)
        except NoMatches:  # 页签内容尚未挂载，切到该页签时 _on_tab 会再渲染
            return
        disks = self._disks
        vis = fit_table_columns(table, [22, 18, 14, 8, 10, 12, 6], rows=len(disks))
        keys = list(table.columns.keys())
        table.columns[keys[3]].label = rcell("容量", vis[3] - 2)
        table.columns[keys[6]].label = rcell("加密", vis[6] - 2)
        rows = [
            [
                Text(shorten(d.disk_id, vis[0] - 2)),
                Text(shorten(d.name or "-", vis[1] - 2)),
                Text(shorten(d.category or "-", vis[2] - 2)),
                rcell(f"{d.size} GiB", vis[3] - 2),
                Text(shorten(d.status or "-", vis[4] - 2)),
                Text(shorten(d.device or "-", vis[5] - 2)),
                rcell("是" if d.encrypted else "否", vis[6] - 2),
            ]
            for d in disks
        ]
        load_rows(table, rows)
        if not after_layout:
            self.call_after_refresh(lambda: self._rebuild_disks(after_layout=True))

    def on_resize(self, event) -> None:
        if not self.is_mounted:
            return
        if self._groups:
            self._rebuild_groups()
        if self._disks:
            self._rebuild_disks()

    def _scroller(self):
        """Home/End/PgUp/PgDn 只作用于当前页签内的滚动容器（基本信息 / 监控）。"""
        try:
            pane = self.query_one("#dt-tabs", TabbedContent).active_pane
        except NoMatches:
            pane = None
        if pane is not None:
            inner = pane.query(VerticalScroll)
            if inner:
                return inner.first()
        return super()._scroller()

    @on(TabbedContent.TabActivated, "#dt-tabs")
    def _on_tab(self, event: TabbedContent.TabActivated) -> None:
        pane_id = event.pane.id or ""
        if pane_id == "dt-tab-ov":
            if self._ov_snapshot is None:
                self._request_readout()
            else:
                self._render_ov_readout()
        elif pane_id == "dt-tab-sg":
            self._rebuild_groups()
        elif pane_id == "dt-tab-disk":
            self._rebuild_disks()
        elif pane_id == "dt-tab-mon":
            self._sync_window_buttons()
            if not self._mon_loaded:
                self._load_monitor()

    @on(Button.Pressed, ".mon-win")
    def _on_mon_window(self, event: Button.Pressed) -> None:
        key = (event.button.id or "").removeprefix("mon-w")
        if key not in WINDOW_LABELS or key == self._mon_window:
            return
        self._mon_window = key
        self._sync_window_buttons()
        self._load_monitor()

    @on(DataTable.RowSelected, "#dt-sg-table")
    def _on_group_row(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._groups):
            group = self._groups[idx]
            self.app.push_screen(
                SecurityGroupRuleScreen(self.region_id, group.security_group_id, group.name)
            )

    # ------------------------------------------------------------ 生命周期

    def _confirm(self, action: str, *, force: bool = False) -> None:
        inst = self._instance
        if inst is None:
            self.app.notify_warn("实例信息未加载")
            return
        allowed = {"start": can_start, "stop": can_stop, "reboot": can_reboot}[action](inst.status)
        if not allowed:
            word = {"start": "开机", "stop": "关机", "reboot": "重启"}[action]
            self.app.notify_warn(f"实例{status_label(inst.status)}，暂不可{word}")
            return
        if action == "stop":
            self._confirm_stop_options(inst, force=force)
            return
        action_word = {"start": "开机", "stop": "关机", "reboot": "强制重启" if force else "重启"}[action]
        extra = "强制重启会立即中断实例，未落盘数据可能丢失。" if action == "reboot" and force else ""

        def done(ok: bool) -> None:
            if ok:
                self._run_power(action, force=force)

        self.app.push_screen(
            ConfirmModal(
                f"确定要{action_word}实例 {inst.name or inst.instance_id}"
                f"（{inst.instance_id}）吗？{extra}",
                title=f"{action_word}确认",
                yes=action_word,
                default_yes=not force,
            ),
            done,
        )

    def _confirm_stop_options(self, inst: InstanceInfo, *, force: bool = False) -> None:
        def options(opts: StopOptions | None) -> None:
            if opts is None:
                return
            action = "强制关机" if opts.force else "关机"
            extra = "强制关机等同于断电，未落盘数据可能丢失。" if opts.force else ""

            def done(ok: bool) -> None:
                if ok:
                    self._run_power(
                        "stop", force=opts.force, stopped_mode=opts.stopped_mode
                    )

            self.app.push_screen(
                ConfirmModal(
                    f"确定要{action}实例 {inst.name or inst.instance_id}"
                    f"（{inst.instance_id}）吗？{extra}",
                    title=f"{action}确认",
                    yes=action,
                    default_yes=not opts.force,
                ),
                done,
            )

        self.app.push_screen(StopOptionsModal(force=force), options)

    @work(exclusive=True)
    async def _run_power(
        self, action: str, *, force: bool, stopped_mode: str | None = None
    ) -> None:
        inst = self._instance
        if inst is None:
            return
        ecs = self.app.get_ecs()
        verb = {"start": "启动", "stop": "停止", "reboot": "重启"}[action]
        targets = {"Running"} if action in ("start", "reboot") else {"Stopped"}
        try:
            async with busy(self.app, f"正在{verb}实例…", mode="dots") as overlay:
                if action == "start":
                    await asyncio.to_thread(ecs.start_instance, self.region_id, inst.instance_id)
                elif action == "stop":
                    await asyncio.to_thread(
                        ecs.stop_instance,
                        self.region_id,
                        inst.instance_id,
                        force=force,
                        stopped_mode=stopped_mode,
                    )
                else:
                    await asyncio.to_thread(ecs.reboot_instance, self.region_id, inst.instance_id, force=force)
                overlay.set_message(f"正在等待实例{verb}完成…")
                status = await asyncio.to_thread(
                    ecs.wait_instance_status, self.region_id, inst.instance_id, targets
                )
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._load()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"{verb}实例失败: {type(e).__name__}: {e}")
            self._load()
            return
        if status in targets:
            self.app.notify_ok(f"实例状态已变为 {status_label(status)}")
        else:
            self.app.notify_warn(
                f"已下发{verb}请求，当前状态 {status_label(status) or '未知'}，请稍后刷新确认"
            )
        if self.on_changed is not None:
            self.on_changed()
        self._load()

    # ------------------------------------------------------------ 云助手 / 带宽

    def _open_command(self) -> None:
        inst = self._instance
        if inst is None:
            self.app.notify_warn("实例信息未加载")
            return
        if inst.status != "Running":
            self.app.notify_warn(f"实例{status_label(inst.status)}，云助手命令需要运行中的实例")
            return
        modal = CommandModal()

        def done(content: str | None) -> None:
            if content:
                self._run_command(content, modal.timeout_value)

        self.app.push_screen(modal, done)

    @work(exclusive=True)
    async def _run_command(self, content: str, timeout: int) -> None:
        inst = self._instance
        if inst is None:
            return
        ecs = self.app.get_ecs()
        started = time.monotonic()
        try:
            async with busy(self.app, "正在下发命令…", mode="bar") as overlay:
                invoke_id = await asyncio.to_thread(
                    ecs.run_command,
                    self.region_id,
                    inst.instance_id,
                    content=content,
                    timeout=timeout,
                )
                overlay.set_message("正在等待命令执行…")
                result = await asyncio.to_thread(
                    ecs.wait_command_result,
                    self.region_id,
                    inst.instance_id,
                    invoke_id,
                    timeout=max(120, timeout + 30),
                )
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        except ValueError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"执行命令失败: {type(e).__name__}: {e}")
            return
        elapsed = time.monotonic() - started
        self._show_command_result(result, elapsed)

    def _show_command_result(self, result, elapsed: float) -> None:
        status = result.status or "未知"
        exit_code = "-" if result.exit_code is None else str(result.exit_code)
        header = f"状态：{status}   退出码：{exit_code}   耗时：{elapsed:.1f}s"
        if not result.finished:
            header += "（超时未完成）"
        output = result.output or "（无输出）"
        truncated = False
        if len(output) > COMMAND_OUTPUT_LIMIT:
            output = output[:COMMAND_OUTPUT_LIMIT]
            truncated = True
        lines = [header, "---- 输出 ----", output]
        if truncated:
            lines.append(f"…（输出已截断，仅展示前 {COMMAND_OUTPUT_LIMIT} 字符）")
        if result.error:
            lines.append("")
            lines.append(f"错误：{result.error}")
        if result.exit_code not in (None, 0):
            self.app.notify_err(f"命令执行结束，退出码 {result.exit_code}")
        else:
            self.app.notify_ok("命令执行完成")
        self.app.push_screen(OutputModal("命令执行结果", Text("\n".join(lines))))

    def _adjust_bandwidth(self) -> None:
        inst = self._instance
        eip = self._eip
        if inst is None:
            self.app.notify_warn("实例信息未加载")
            return
        charge = str((eip.internet_charge_type if eip is not None else "") or inst.internet_charge_type or "")
        if charge == "PayByTraffic":
            self.app.notify_warn("按流量计费无需设置带宽")
            return
        if not _bandwidth_allowed(inst, eip):
            self.app.notify_warn("当前实例状态或计费方式不支持调整带宽")
            return
        self._query_bandwidth_limit(inst, eip, charge)

    @work(exclusive=True, group="bandwidth")
    async def _query_bandwidth_limit(self, inst: InstanceInfo, eip: EipInfo | None, charge: str) -> None:
        """先查规格带宽上限；查询失败回落到本地常量，绝不因此禁用入口。"""
        # 本地兜底：包年包月 200 Mbps，按量付费 100 Mbps
        fallback = 200 if str(inst.charge_type or "") == "PrePaid" else 100
        ecs = self.app.get_ecs()
        self.app.spinner(True, "正在查询带宽上限…")
        try:
            limit = await asyncio.to_thread(
                ecs.get_bandwidth_limit, self.region_id, inst.instance_type, charge
            )
        except Exception:  # noqa: BLE001 — 上限只是提示
            limit = None
        finally:
            self.app.spinner(False)
        if not self.is_mounted:
            return
        low, high = limit if limit else (1, fallback)
        low = max(1, int(low))
        high = max(low, int(high))
        current = (eip.bandwidth if eip is not None and eip.bandwidth else inst.bandwidth_out) or ""

        def validator(value: str) -> bool:
            return value.isdigit() and low <= int(value) <= high

        def done(value: str | None) -> None:
            if value is None:
                return
            bandwidth = int(value)

            def confirmed(ok: bool) -> None:
                if ok:
                    self._run_bandwidth(bandwidth)

            self.app.push_screen(
                ConfirmModal(
                    f"确定将实例 {inst.name or inst.instance_id} 的公网出带宽调整为 {bandwidth} Mbps 吗？\n"
                    "带宽变更会立即影响计费。",
                    title="调整带宽确认",
                    yes="调整",
                    default_yes=False,
                ),
                confirmed,
            )

        self.app.push_screen(
            InputModal(
                f"出网带宽（Mbps，{low}-{high}）",
                title="调整带宽",
                value=str(current),
                validator=validator,
                error=f"带宽必须是 {low}-{high} 的整数",
                hint="仅按固定带宽计费的实例可调整",
            ),
            done,
        )

    @work(exclusive=True)
    async def _run_bandwidth(self, bandwidth: int) -> None:
        inst = self._instance
        if inst is None:
            return
        ecs = self.app.get_ecs()
        try:
            async with busy(self.app, "正在调整带宽…", mode="dots"):
                await asyncio.to_thread(
                    ecs.modify_bandwidth, self.region_id, inst.instance_id, bandwidth_out=bandwidth
                )
        except ValueError as e:
            self.app.notify_err(str(e))
            return
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"调整带宽失败: {type(e).__name__}: {e}")
            return
        self.app.notify_ok(f"公网出带宽已调整为 {bandwidth} Mbps")
        if self.on_changed is not None:
            self.on_changed()
        self._load()

    # ------------------------------------------------------------ 事件

    @on(Button.Pressed, "#dt-start")
    def _on_start(self) -> None:
        self._confirm("start")

    @on(Button.Pressed, "#dt-stop")
    def _on_stop(self) -> None:
        self._confirm("stop")

    @on(Button.Pressed, "#dt-reboot")
    def _on_reboot(self) -> None:
        self._confirm("reboot")

    @on(Button.Pressed, "#dt-exec")
    def _on_exec(self) -> None:
        self._open_command()

    @on(Button.Pressed, "#dt-bandwidth")
    def _on_bandwidth(self) -> None:
        self._adjust_bandwidth()

    @on(Button.Pressed, "#dt-refresh")
    def _on_refresh(self) -> None:
        self.reload_page()


# ---------------------------------------------------------------- 安全组列表

class SecurityGroupListScreen(PageScreen):
    """地域内安全组列表；双击进入规则页。"""

    TITLE = "安全组"
    HINT = "双击/回车 管理规则 · / 搜索 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [Binding("/", "focus_search", "搜索", show=False)]

    def __init__(self, region: str, group_ids: list[str] | None = None) -> None:
        super().__init__()
        self.region_id = region
        self.group_ids = list(group_ids) if group_ids else None
        self._groups: list[SecurityGroupInfo] = []
        self._display: list[SecurityGroupInfo] = []
        self._filter = ""
        self._notice: tuple[str, str] | None = None

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("搜索", classes="fl-label")
                yield Input(placeholder="按名称 / ID / 描述筛选（/ 聚焦）", compact=True, id="sg-search")
                yield Button("刷新", id="sg-refresh")
            with Horizontal(classes="filter-row gap-top"):
                yield Static(classes="fill")
                yield Button("新建", id="sg-create", variant="primary")
                yield Button("删除", id="sg-delete", variant="error")
        table = make_table("名称", "ID", "类型", "所属 VPC", "规则数", "组内连通")
        table.id = "sg-table"
        yield table

    def on_mount(self) -> None:
        self._load()
        self.query_one(DataTable).focus()

    def reload_page(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self, notice: tuple[str, str] | None = None) -> None:
        self._notice = notice
        self.app.status(f"正在获取 {self.region_id} 安全组…")
        table = self.query_one("#sg-table", DataTable)
        table.loading = True
        ecs = self.app.get_ecs()
        try:
            if self.group_ids:
                groups = await asyncio.to_thread(
                    ecs.list_security_groups, self.region_id, security_group_ids=self.group_ids
                )
            else:
                groups = await asyncio.to_thread(ecs.list_security_groups, self.region_id)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._groups = []
            table.loading = False
            self._rebuild()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取安全组失败: {type(e).__name__}: {e}")
            self._groups = []
            table.loading = False
            self._rebuild()
            return
        table.loading = False
        if not self.is_mounted:
            return
        self._groups = sorted(groups, key=lambda g: (g.name or g.security_group_id))
        self._rebuild()
        table.focus()

    def _rebuild(self, *, after_layout: bool = False) -> None:
        groups = self._groups
        if self._filter:
            groups = filter_fuzzy(
                groups, self._filter, lambda g: f"{g.name} {g.security_group_id} {g.description}"
            )
        self._display = groups
        table = self.query_one("#sg-table", DataTable)
        vis = fit_table_columns(table, [22, 20, 10, 20, 8, 10], rows=len(groups))
        keys = list(table.columns.keys())
        table.columns[keys[4]].label = rcell("规则数", vis[4] - 2)
        table.columns[keys[5]].label = rcell("组内连通", vis[5] - 2)
        rows = [
            [
                Text(shorten(g.name or "-", vis[0] - 2)),
                Text(shorten(g.security_group_id, vis[1] - 2)),
                Text(_sg_type_label(g.sg_type)),
                Text(shorten(g.vpc_id or "-", vis[3] - 2)),
                rcell(g.rule_count, vis[4] - 2),
                rcell(_inner_policy_label(g.inner_access_policy), vis[5] - 2),
            ]
            for g in groups
        ]
        load_rows(table, rows)
        self.set_subtitle(f"{self.region_id} · {len(self._display)}/{len(self._groups)} 个安全组")
        if self._notice is not None:
            message, kind = self._notice
            self.app.status(message, kind)
        elif not self._groups:
            self.app.status(f"{self.region_id} 下未找到安全组", "warn")
        else:
            self.app.status(f"共 {len(self._groups)} 个安全组")
        self._sync_actions()
        if not after_layout:
            self.call_after_refresh(lambda: self._rebuild(after_layout=True))

    def on_resize(self, event) -> None:
        if self.is_mounted:
            self._rebuild()

    # ------------------------------------------------------------ 选中 / 增删

    def _current(self) -> SecurityGroupInfo | None:
        table = self.query_one("#sg-table", DataTable)
        idx = table.cursor_row
        if idx is None or not (0 <= idx < len(self._display)):
            return None
        return self._display[idx]

    def _sync_actions(self) -> None:
        current = self._current()
        try:
            button = self.query_one("#sg-delete", Button)
        except NoMatches:
            return
        button.disabled = current is None
        button.tooltip = "未选中安全组" if current is None else ""

    @on(DataTable.RowHighlighted, "#sg-table")
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        self._sync_actions()

    @work(exclusive=True)
    async def _create(self) -> None:
        ecs = self.app.get_ecs()
        self.app.status(f"正在获取 {self.region_id} VPC…")
        try:
            vpcs = await asyncio.to_thread(ecs.list_vpcs, self.region_id)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取 VPC 列表失败: {type(e).__name__}: {e}")
            return
        if not self.is_mounted:
            return
        fields = [
            FormField(
                "name",
                "名称",
                placeholder="2-128 个字符",
                validator=lambda value: 2 <= len(value.strip()) <= 128,
                error="名称长度需为 2-128 个字符",
            ),
            FormField("description", "描述", placeholder="可选"),
        ]
        if vpcs:
            options = [
                (f"{v.name}（{v.vpc_id}）" if v.name else v.vpc_id, v.vpc_id) for v in vpcs
            ]
            # 默认选中第一个 VPC：不指定 VPC 会创建经典网络安全组，容易直接失败
            value = vpcs[0].vpc_id
            fields.append(
                FormField(
                    "vpc_id",
                    "专有网络 VPC",
                    kind="choice",
                    value=value,
                    options=options,
                    hint="已默认选中第一个 VPC",
                )
            )
        else:
            fields.append(FormField("vpc_note", "VPC：未找到 VPC，将不指定 VPC", kind="note"))

        def done(data: dict | None) -> None:
            if not data:
                return
            self._do_create(
                str(data.get("name") or ""),
                str(data.get("description") or ""),
                str(data.get("vpc_id") or ""),
            )

        self.app.push_screen(
            FormModal("新建安全组", fields, message="安全组创建后默认为普通安全组。", ok_label="创建"),
            done,
        )

    @work(exclusive=True)
    async def _do_create(self, name: str, description: str, vpc_id: str) -> None:
        ecs = self.app.get_ecs()
        try:
            async with busy(self.app, "正在创建安全组…", mode="dots"):
                group_id = await asyncio.to_thread(
                    ecs.create_security_group,
                    self.region_id,
                    name=name,
                    description=description,
                    vpc_id=vpc_id,
                )
        except ValueError as e:
            self.app.notify_err(str(e))
            return
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"创建安全组失败: {type(e).__name__}: {e}")
            return
        self.app.notify_ok(f"已创建安全组 {group_id}")
        self._load((f"已创建安全组 {group_id}", "ok"))

    def _delete(self) -> None:
        group = self._current()
        if group is None:
            self.app.notify_warn("未选中任何安全组")
            return
        label = f"{group.name or group.security_group_id}（{group.security_group_id}）"

        def done(ok: bool) -> None:
            if ok:
                self._do_delete(group)

        self.app.push_screen(
            ConfirmModal(
                f"确定要删除安全组 {label} 吗？\n将删除该安全组及其全部规则，不可恢复。",
                title="删除安全组确认",
                yes="删除",
                default_yes=False,
            ),
            done,
        )

    @work(exclusive=True)
    async def _do_delete(self, group: SecurityGroupInfo) -> None:
        ecs = self.app.get_ecs()
        try:
            async with busy(self.app, "正在删除安全组…", mode="dots"):
                await asyncio.to_thread(
                    ecs.delete_security_group, self.region_id, group.security_group_id
                )
        except AliyunApiError as e:
            # 失败时列表未变化，保持原始错误提示可见（避免被刷新状态覆盖）
            self.app.notify_err(str(e))
            return
        except ValueError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"删除安全组失败: {type(e).__name__}: {e}")
            self._load()
            return
        self.app.notify_ok("已删除安全组")
        if self.group_ids is not None:
            # 实例详情的过滤视图：删除后返回上一页，避免停留在空列表
            self.app.pop_screen()
        else:
            self._load(("已删除安全组", "ok"))

    @on(Input.Changed, "#sg-search")
    def _on_search(self, event: Input.Changed) -> None:
        self._filter = event.value.strip()
        self._rebuild()

    @on(Button.Pressed, "#sg-refresh")
    def _on_refresh(self) -> None:
        self._load()

    @on(Button.Pressed, "#sg-create")
    def _on_create(self) -> None:
        self._create()

    @on(Button.Pressed, "#sg-delete")
    def _on_delete(self) -> None:
        self._delete()

    @on(DataTable.RowSelected, "#sg-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._display):
            group = self._display[idx]
            self.app.push_screen(
                SecurityGroupRuleScreen(self.region_id, group.security_group_id, group.name)
            )

    def action_focus_search(self) -> None:
        self.query_one("#sg-search", Input).focus()


# ---------------------------------------------------------------- 安全组规则

class SecurityGroupRuleScreen(PageScreen):
    """安全组规则列表：方向筛选、搜索、新增 / 编辑 / 删除。"""

    TITLE = "安全组规则"
    HINT = "双击/回车 编辑 · Ctrl+N 新增 · / 搜索 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [
        Binding("/", "focus_search", "搜索", show=False),
        Binding("ctrl+n", "new_rule", "新增", show=False),
    ]

    def __init__(self, region: str, sg_id: str, sg_name: str = "") -> None:
        super().__init__()
        self.region_id = region
        self.sg_id = sg_id
        self.sg_name = sg_name
        self._rules: list[RuleInfo] = []
        self._display: list[RuleInfo] = []
        self._groups: list[SecurityGroupInfo] = []
        self._sg_info: SecurityGroupInfo | None = None
        self._filter = ""
        self._direction = "all"
        self._keep_id = ""

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("搜索", classes="fl-label")
                yield Input(placeholder="按协议 / 端口 / 授权对象 / 描述筛选（/ 聚焦）", compact=True, id="rl-search")
                yield Select(
                    [("全部方向", "all"), ("入方向", "ingress"), ("出方向", "egress")],
                    value="all", allow_blank=False, compact=True, id="rl-direction",
                )
            with Horizontal(classes="filter-row gap-top"):
                yield Static(classes="fill")
                yield Button("＋ 新增", id="rl-add", variant="primary")
                yield Button("编辑", id="rl-edit")
                yield Button("删除", id="rl-del", variant="error")
        table = make_table("方向", "优先级", "策略", "协议", "端口", "授权对象", "描述")
        table.id = "rl-table"
        yield table

    def on_mount(self) -> None:
        self.set_subtitle(f"{self.sg_name or self.sg_id} · {self.sg_id}")
        self._load()
        self.query_one(DataTable).focus()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self) -> None:
        current = self._current()
        self._keep_id = current.rule_id if current is not None else ""
        self.app.status(f"正在获取安全组 {self.sg_id} 规则…")
        table = self.query_one("#rl-table", DataTable)
        table.loading = True
        ecs = self.app.get_ecs()

        def _fetch():
            rules = ecs.list_rules(self.region_id, self.sg_id, "all")
            try:
                groups = ecs.list_security_groups(self.region_id, with_policy=False)
            except Exception:  # noqa: BLE001 — 仅用于表单候选 / 企业级判断
                groups = []
            return rules, groups

        try:
            rules, groups = await asyncio.to_thread(_fetch)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._rules = []
            table.loading = False
            self._rebuild()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取规则失败: {type(e).__name__}: {e}")
            self._rules = []
            table.loading = False
            self._rebuild()
            return
        table.loading = False
        if not self.is_mounted:
            return
        self._rules = rules
        self._groups = groups
        self._sg_info = next((g for g in groups if g.security_group_id == self.sg_id), None)
        if self._sg_info is None:
            try:
                self._sg_info = await asyncio.to_thread(ecs.get_security_group, self.region_id, self.sg_id)
            except Exception:  # noqa: BLE001
                self._sg_info = None
        if self._sg_info and self._sg_info.name and not self.sg_name:
            self.sg_name = self._sg_info.name
        self._rebuild()
        table.focus()

    def _sorted(self, rules: list[RuleInfo]) -> list[RuleInfo]:
        return sorted(
            rules,
            key=lambda r: (r.priority, 0 if str(r.policy).lower() == "drop" else 1, r.rule_id),
        )

    def _rebuild(self, *, after_layout: bool = False) -> None:
        rules = self._rules
        if self._direction != "all":
            rules = [r for r in rules if str(r.direction).lower() == self._direction]
        if self._filter:
            rules = filter_fuzzy(
                rules,
                self._filter,
                lambda r: (
                    f"{describe_direction_label(r.direction)} {r.ip_protocol} {r.port_range} "
                    f"{rule_authorization_label(r)} {r.description} {_policy_label(r.policy)}"
                ),
            )
        self._display = self._sorted(rules)

        table = self.query_one("#rl-table", DataTable)
        vis = fit_table_columns(table, [8, 8, 8, 8, 12, 26, 22], rows=len(self._display))
        keys = list(table.columns.keys())
        for idx, label in ((0, "方向"), (1, "优先级"), (2, "策略")):
            table.columns[keys[idx]].label = rcell(label, vis[idx] - 2)
        rows = []
        for rule in self._display:
            rows.append([
                rcell(describe_direction_label(rule.direction), vis[0] - 2),
                rcell(rule.priority, vis[1] - 2),
                _policy_text(rule.policy, vis[2] - 2),
                Text(shorten(rule.ip_protocol or "-", vis[3] - 2)),
                Text(shorten(rule.port_range or "-", vis[4] - 2)),
                Text(shorten(rule_authorization_label(rule), vis[5] - 2)),
                Text(shorten(rule.description or "-", vis[6] - 2)),
            ])
        load_rows(table, rows)
        keep, self._keep_id = self._keep_id, ""
        _restore_cursor(table, self._display, lambda rule: rule.rule_id, keep)

        ingress = sum(1 for r in self._rules if str(r.direction).lower() == "ingress")
        egress = sum(1 for r in self._rules if str(r.direction).lower() == "egress")
        self.set_subtitle(
            f"{self.sg_name or self.sg_id} · {self.sg_id} · 入 {ingress} / 出 {egress}"
        )
        if not self._rules:
            self.app.status("该安全组暂无规则", "warn")
        else:
            self.app.status(f"共 {len(self._rules)} 条规则")
        if not after_layout:
            self.call_after_refresh(lambda: self._rebuild(after_layout=True))

    def on_resize(self, event) -> None:
        if self.is_mounted:
            self._rebuild()

    # ------------------------------------------------------------ 选中 / 操作

    def _current(self) -> RuleInfo | None:
        table = self.query_one("#rl-table", DataTable)
        idx = table.cursor_row
        if idx is None or not (0 <= idx < len(self._display)):
            return None
        return self._display[idx]

    def _open_form(self, rule: RuleInfo | None) -> None:
        if rule is not None and rule_auth_kind(rule) == "prefix":
            self.app.notify_warn("前缀列表规则不支持在终端修改，请到阿里云控制台操作")
            return
        allow_group = not (self._sg_info and self._sg_info.sg_type == "enterprise")
        groups = self._groups
        if allow_group and self._sg_info is not None and self._sg_info.vpc_id:
            # 安全组互相授权要求同 VPC，跨 VPC 的组不放进候选
            groups = [g for g in groups if g.vpc_id == self._sg_info.vpc_id]
        modal = RuleFormModal(rule, groups, allow_group)
        self.app.push_screen(modal, lambda data: self._on_form(rule, data))

    def _on_form(self, rule: RuleInfo | None, data: dict | None) -> None:
        if not data:
            return
        rule_input = data.get("_rule")
        if not isinstance(rule_input, RuleInput):
            return
        self._save(rule, rule_input)

    @work(exclusive=True)
    async def _save(self, rule: RuleInfo | None, rule_input: RuleInput) -> None:
        ecs = self.app.get_ecs()
        before = len(self._rules)
        try:
            async with busy(self.app, "正在保存安全组规则…", mode="dots"):
                if rule is None:
                    await asyncio.to_thread(ecs.authorize_rule, self.region_id, self.sg_id, rule_input)
                else:
                    await asyncio.to_thread(ecs.modify_rule, self.region_id, self.sg_id, rule_input)
                rules = await asyncio.to_thread(ecs.list_rules, self.region_id, self.sg_id, "all")
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._load()
            return
        except ValueError as e:
            self.app.notify_err(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"保存规则失败: {type(e).__name__}: {e}")
            self._load()
            return

        self._rules = rules
        self._keep_id = rule_input.rule_id or ""
        self._rebuild()
        if rule is None:
            if len(rules) > before:
                self.app.notify_ok("已新增安全组规则")
            else:
                # 重复规则会返回成功但不新增，不能谎报
                self.app.notify_warn("规则未新增（可能已存在完全相同的规则），列表已刷新")
        else:
            self.app.notify_ok("已更新安全组规则")

    def _delete(self) -> None:
        rule = self._current()
        if rule is None:
            self.app.notify_warn("未选中任何规则")
            return
        direction = describe_direction_label(rule.direction)
        auth = rule_authorization_label(rule)

        def done(ok: bool) -> None:
            if ok:
                self._do_delete(rule)

        self.app.push_screen(
            ConfirmModal(
                f"确定要删除{direction}规则（{rule.ip_protocol} {rule.port_range} → {auth}）吗？\n"
                "规则立即生效，可能导致业务断连。",
                title="删除规则确认",
                yes="删除",
                default_yes=False,
            ),
            done,
        )

    @work(exclusive=True)
    async def _do_delete(self, rule: RuleInfo) -> None:
        ecs = self.app.get_ecs()
        try:
            async with busy(self.app, "正在删除安全组规则…", mode="dots"):
                await asyncio.to_thread(ecs.revoke_rule, self.region_id, self.sg_id, rule)
        except AliyunApiError as e:
            message = str(e)
            if "SecurityGroupRuleId" in message:
                self.app.notify_warn("规则可能已被删除，正在刷新列表")
            else:
                self.app.notify_err(message)
            self._load()
            return
        except ValueError as e:
            self.app.notify_err(str(e))
            self._load()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"删除规则失败: {type(e).__name__}: {e}")
            self._load()
            return
        self.app.notify_ok("已删除安全组规则")
        self._load()

    # ------------------------------------------------------------ 事件

    @on(Input.Changed, "#rl-search")
    def _on_search(self, event: Input.Changed) -> None:
        self._filter = event.value.strip()
        self._rebuild()

    @on(Select.Changed, "#rl-direction")
    def _on_direction(self, event: Select.Changed) -> None:
        if event.value is not Select.NULL:
            self._direction = str(event.value)
            self._rebuild()

    @on(DataTable.RowSelected, "#rl-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        rule = self._current()
        if rule is not None:
            self._open_form(rule)

    @on(Button.Pressed, "#rl-add")
    def _on_add(self) -> None:
        self._open_form(None)

    @on(Button.Pressed, "#rl-edit")
    def _on_edit(self) -> None:
        rule = self._current()
        if rule is None:
            self.app.notify_warn("未选中任何规则")
            return
        self._open_form(rule)

    @on(Button.Pressed, "#rl-del")
    def _on_del(self) -> None:
        self._delete()

    def action_focus_search(self) -> None:
        self.query_one("#rl-search", Input).focus()

    def action_new_rule(self) -> None:
        self._open_form(None)
