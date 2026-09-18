"""DNS 屏：域名列表 → 解析记录列表（增删改、启停、排序、筛选）。

版式：内容区 = 小容器（.panel：筛选 / 排序 / 主操作按钮）+ 大容器（.tbl 表格，1fr 单滚动条）。
"""
import asyncio

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Select, Static

from ..client import AliyunApiError
from ..dns import (
    DNS_RECORD_TYPES,
    SORT_ORDER_LABELS,
    SORT_TYPE_LABELS,
    sort_records,
    validate_dns_record,
)
from ..ui import PageScreen
from ..widgets import (
    STYLE_ERR,
    STYLE_OK,
    ConfirmModal,
    FormField,
    FormModal,
    filter_fuzzy,
    fit_table_columns,
    load_rows,
    make_table,
    rcell,
    shorten,
)

RR_MAX = 20   # 主机记录显示宽度上限
VALUE_MAX = 40  # 记录值显示宽度上限


def _is_enabled(record: dict) -> bool:
    return (record.get("Status") or "").lower() == "enable"


class DomainListScreen(PageScreen):
    """域名列表，回车进入该域名的解析记录。"""

    TITLE = "DNS 解析管理"
    HINT = "↑↓ 移动 · 回车 管理解析记录 · / 搜索 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [Binding("/", "focus_search", "搜索", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self._domains: list = []
        self._display: list = []
        self._filter = ""

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("搜索", classes="fl-label")
                yield Input(placeholder="按域名筛选（/ 聚焦）", compact=True, id="dm-search")
                yield Button("刷新", id="dm-refresh", compact=True)
        table = make_table("域名", "记录数")
        table.id = "dm-table"
        yield table

    def on_mount(self) -> None:
        self._load()
        self.query_one(DataTable).focus()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self) -> None:
        self.app.status("正在获取域名列表…")
        try:
            domains = await asyncio.to_thread(self.app.get_dns().get_domains)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._domains = []
            self._rebuild()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取域名列表失败: {type(e).__name__}: {e}")
            self._domains = []
            self._rebuild()
            return
        self._domains = domains
        self._rebuild()

    def _rebuild(self, *, after_layout: bool = False) -> None:
        domains = self._domains
        if self._filter:
            domains = filter_fuzzy(domains, self._filter, lambda d: d.get("DomainName", ""))
        self._display = domains
        table = self.query_one("#dm-table", DataTable)
        vis = fit_table_columns(table, [42, 8], rows=len(domains))
        cnt_key = list(table.columns.keys())[1]
        table.columns[cnt_key].label = rcell("记录数", vis[1] - 2)
        rows = [
            [Text(shorten(d.get("DomainName", "?"), vis[0] - 2)), rcell(d.get("RecordCount", "-"), vis[1] - 2)]
            for d in domains
        ]
        load_rows(table, rows)
        self.set_subtitle(f"{len(self._display)}/{len(self._domains)} 个域名")
        if not after_layout:
            # 布局稳定后（region/滚动条确定）再整体重建一次，保证列宽、表头、单元格三者一致
            self.call_after_refresh(lambda: self._rebuild(after_layout=True))
            return
        if not self._domains:
            self.app.status("未获取到任何域名，请检查账户权限", "warn")
        else:
            self.app.status(f"共 {len(self._domains)} 个域名")

    def on_resize(self, event) -> None:
        if self.is_mounted:
            self._rebuild()

    # ------------------------------------------------------------ 事件

    @on(Input.Changed, "#dm-search")
    def _on_search(self, event: Input.Changed) -> None:
        self._filter = event.value.strip()
        self._rebuild()

    @on(Button.Pressed, "#dm-refresh")
    def _on_refresh(self) -> None:
        self._load()

    @on(DataTable.RowSelected, "#dm-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._display):
            self.app.push_screen(RecordListScreen(self._display[idx].get("DomainName", "")))

    def action_focus_search(self) -> None:
        self.query_one("#dm-search", Input).focus()


class RecordFormModal(FormModal):
    """解析记录表单：TTL 解析 + 跨字段校验 + 启用状态开关。"""

    def __init__(self, record: dict | None = None) -> None:
        is_new = record is None
        record = record or {}
        cur_type = (record.get("Type") or "A").upper()
        types = list(DNS_RECORD_TYPES)
        if cur_type not in types:
            types.append(cur_type)
        fields = [
            FormField(
                "rr", "主机记录", value=record.get("RR", ""),
                validator=lambda v: bool(v and v.strip()), error="不能为空",
            ),
            FormField(
                "type", "记录类型", kind="choice", value=cur_type,
                options=[(t, t) for t in types],
            ),
            FormField(
                "value", "记录值", value=record.get("Value", ""),
                validator=lambda v: bool(v and v.strip()), error="不能为空",
            ),
            FormField("ttl", "TTL (秒)", value=str(record.get("TTL", 600))),
            FormField(
                "status", "启用状态", kind="switch",
                value="on" if (is_new or _is_enabled(record)) else "",
                hint="关闭后解析立即停止生效",
            ),
        ]
        extra = None
        if not is_new:
            extra = [("frm-delete", "删除", "error")]
        super().__init__("新增解析记录" if is_new else "编辑解析记录", fields, extra_buttons=extra)

    def _collect(self) -> "dict | None":
        data = super()._collect()
        if data is None:
            return None
        try:
            ttl = int(str(data.get("ttl", "")).strip())
        except (TypeError, ValueError):
            self.show_error("TTL 必须为 60-86400 之间的整数")
            return None
        data["ttl"] = ttl
        error = validate_dns_record(data["rr"], data["type"], data["value"], ttl)
        if error:
            self.show_error(error)
            return None
        return data


class RecordListScreen(PageScreen):
    """某域名的解析记录列表，支持筛选、排序、增删改与启停。"""

    TITLE = "解析记录"
    HINT = "单击/回车 编辑 · Ctrl+N 新增 · Ctrl+E 编辑 · Ctrl+D 删除 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [
        Binding("/", "focus_search", "搜索", show=False),
        Binding("ctrl+n", "new_record", "新增", show=False),
        Binding("ctrl+e", "edit_record", "编辑", show=False),
        Binding("ctrl+d", "delete_record", "删除", show=False),
    ]

    def __init__(self, domain_name: str) -> None:
        super().__init__()
        self.domain_name = domain_name
        self._records: list = []
        self._display: list = []
        self._filter = ""
        self._sort_type = 0
        self._sort_order = 0

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("搜索", classes="fl-label")
                yield Input(placeholder="按主机记录 / 记录值筛选（/ 聚焦）", compact=True, id="rc-search")
                yield Button("刷新", id="rc-refresh", compact=True)
            with Horizontal(classes="filter-row gap-top"):
                yield Static("排序", classes="fl-label")
                yield Select(
                    [(label, i) for i, label in enumerate(SORT_TYPE_LABELS)],
                    value=0, allow_blank=False, compact=True, id="sort-type",
                )
                yield Select(
                    [(label, i) for i, label in enumerate(SORT_ORDER_LABELS)],
                    value=0, allow_blank=False, compact=True, id="sort-order",
                )
                yield Static(classes="fill")
                yield Button("＋ 新增", id="rc-add", variant="primary", compact=True)
                yield Button("编辑", id="rc-edit", compact=True)
                yield Button("删除", id="rc-del", variant="error", compact=True)
        table = make_table("主机记录", "类型", "记录值", "TTL", "状态")
        table.id = "rc-table"
        yield table

    def on_mount(self) -> None:
        self.set_subtitle(self.domain_name)
        self._load()
        self.query_one(DataTable).focus()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self) -> None:
        self.app.status(f"正在获取 {self.domain_name} 的解析记录…")
        try:
            records = await asyncio.to_thread(self.app.get_dns().get_domain_records, self.domain_name)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._records = []
            self._rebuild()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"获取解析记录失败: {type(e).__name__}: {e}")
            self._records = []
            self._rebuild()
            return
        self._records = records
        self._rebuild()

    def _rebuild(self, *, after_layout: bool = False) -> None:
        records = sort_records(self._records, self._sort_type, self._sort_order)
        if self._filter:
            records = filter_fuzzy(
                records, self._filter, lambda r: f"{r.get('RR', '')} {r.get('Value', '')}"
            )
        self._display = records
        table = self.query_one("#rc-table", DataTable)
        # 权重：主机记录 / 类型 / 记录值 / TTL / 状态（含列内 padding）
        vis = fit_table_columns(table, [20, 6, 40, 6, 6], rows=len(records))
        keys = list(table.columns.keys())
        table.columns[keys[3]].label = rcell("TTL", vis[3] - 2)
        table.columns[keys[4]].label = rcell("状态", vis[4] - 2)
        rows = []
        for r in records:
            status = rcell("启用" if _is_enabled(r) else "禁用", vis[4] - 2)
            status.stylize(STYLE_OK if _is_enabled(r) else STYLE_ERR)
            rows.append([
                Text(shorten(r.get("RR", "?"), min(RR_MAX, vis[0] - 2))),
                Text(r.get("Type", "?")),
                Text(shorten(r.get("Value", ""), min(VALUE_MAX, vis[2] - 2))),
                rcell(r.get("TTL", ""), vis[3] - 2),
                status,
            ])
        load_rows(table, rows)
        sort_label = SORT_TYPE_LABELS[self._sort_type]
        order_label = "逆序" if self._sort_order == 0 else "正序"
        self.set_subtitle(
            f"{self.domain_name} · {len(self._display)}/{len(self._records)} · {sort_label} {order_label}"
        )
        self.app.status(f"共 {len(self._records)} 条解析记录")
        if not after_layout:
            # 布局稳定后整体重建一次：列宽、表头、右对齐单元格基于同一份宽度，避免错位
            self.call_after_refresh(lambda: self._rebuild(after_layout=True))

    def on_resize(self, event) -> None:
        if self.is_mounted:
            self._rebuild()

    # ------------------------------------------------------------ 记录操作

    def _table(self) -> DataTable:
        return self.query_one("#rc-table", DataTable)

    def _current(self) -> dict | None:
        idx = self._table().cursor_row
        if idx is None or not (0 <= idx < len(self._display)):
            return None
        return self._display[idx]

    def _action_edit(self) -> None:
        record = self._current()
        if record is None:
            self.app.notify_warn("未选中任何记录")
            return
        self._open_record(record)

    def _action_delete(self) -> None:
        record = self._current()
        if record is None:
            self.app.notify_warn("未选中任何记录")
            return
        self._delete_record(record)

    @on(DataTable.RowSelected, "#rc-table")
    def _on_row(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._display):
            self._open_record(self._display[idx])

    def _open_record(self, record: dict | None) -> None:
        is_new = record is None
        modal = RecordFormModal(record)
        self.app.push_screen(modal, lambda result: self._handle_result(record, is_new, result))

    def _handle_result(self, record: dict | None, is_new: bool, result) -> None:
        if result is None:
            return
        if result == "frm-delete":
            self._delete_record(record)
        elif isinstance(result, dict):
            self._save_record(record, is_new, result)

    def _save_record(self, record: dict | None, is_new: bool, data: dict) -> None:
        dns = self.app.get_dns()
        enabled = bool(data.get("status", True))
        try:
            if is_new:
                rid = dns.add_domain_record(
                    self.domain_name, data["rr"], data["type"], data["value"], data["ttl"]
                )
                if rid and not enabled:
                    dns.set_domain_record_status(rid, "Disable")
            else:
                dns.update_domain_record(
                    record.get("RecordId"), data["rr"], data["type"], data["value"], data["ttl"]
                )
                if enabled != _is_enabled(record):
                    dns.set_domain_record_status(
                        record.get("RecordId"), "Enable" if enabled else "Disable"
                    )
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        self.app.notify_ok("已新增解析记录" if is_new else "已更新解析记录")
        self._load()

    def _delete_record(self, record: dict) -> None:
        name = f"{record.get('RR', '')}.{self.domain_name}"

        def confirm(ok: bool) -> None:
            if not ok:
                return
            try:
                self.app.get_dns().delete_domain_record(record.get("RecordId"))
            except AliyunApiError as e:
                self.app.notify_err(str(e))
                return
            self.app.notify_ok(f"已删除解析记录 {name}")
            self._load()

        self.app.push_screen(
            ConfirmModal(
                f"确定要删除解析记录 {name}（类型: {record.get('Type')}, 值: {record.get('Value')}）吗？",
                title="删除确认",
                yes="删除",
                default_yes=False,
            ),
            confirm,
        )

    # ------------------------------------------------------------ 事件

    @on(Input.Changed, "#rc-search")
    def _on_search(self, event: Input.Changed) -> None:
        self._filter = event.value.strip()
        self._rebuild()

    @on(Select.Changed, "#sort-type")
    def _on_sort_type(self, event: Select.Changed) -> None:
        if event.value is not Select.NULL:
            self._sort_type = int(event.value)
            self._rebuild()

    @on(Select.Changed, "#sort-order")
    def _on_sort_order(self, event: Select.Changed) -> None:
        if event.value is not Select.NULL:
            self._sort_order = int(event.value)
            self._rebuild()

    @on(Button.Pressed, "#rc-add")
    def _on_add(self) -> None:
        self._open_record(None)

    @on(Button.Pressed, "#rc-edit")
    def _on_edit(self) -> None:
        self._action_edit()

    @on(Button.Pressed, "#rc-del")
    def _on_del(self) -> None:
        self._action_delete()

    @on(Button.Pressed, "#rc-refresh")
    def _on_refresh(self) -> None:
        self._load()

    def action_focus_search(self) -> None:
        self.query_one("#rc-search", Input).focus()

    def action_new_record(self) -> None:
        self._open_record(None)

    def action_edit_record(self) -> None:
        self._action_edit()

    def action_delete_record(self) -> None:
        self._action_delete()
