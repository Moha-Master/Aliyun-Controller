"""DNS 屏：域名列表 → 解析记录列表（增删改、启停、排序、筛选）。

版式：内容区 = 小容器（.panel：筛选 / 排序 / 主操作按钮）+ 大容器（.tbl 表格，1fr 单滚动条）。
"""
import asyncio

from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, DataTable, Input, Select, Static

from ..client import AliyunApiError
from ..dns import (
    PRIMARY_LINES,
    RECORD_TYPE_LABELS,
    RECORD_TYPE_MAP,
    SORT_ORDER_LABELS,
    SORT_TYPE_LABELS,
    TTL_CHOICES,
    compose_record_value,
    find_conflicting_records,
    get_record_type,
    normalize_record_type,
    parse_record_value,
    record_content_changed,
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
    PickItem,
    busy,
    filter_fuzzy,
    fit_table_columns,
    load_rows,
    make_table,
    rcell,
    shorten,
)

RR_MAX = 20   # 主机记录显示宽度上限
LINE_MAX = 14  # 解析线路显示宽度上限
VALUE_MAX = 40  # 记录值显示宽度上限


def _is_enabled(record: dict) -> bool:
    return (record.get("Status") or "").lower() == "enable"


def _line_key(line) -> str:
    return str(line or "default").strip().lower() or "default"


def _rr_key(rr) -> str:
    return str(rr or "").strip().lower()


def _supports_weight(record_type) -> bool:
    """该记录类型是否可参与加权轮询（阿里云仅 A / AAAA / CNAME / ALIAS）。"""
    spec = get_record_type(record_type)
    return bool(spec and spec.supports_weight)


def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class DomainListScreen(PageScreen):
    """域名列表，回车进入该域名的解析记录。"""

    TITLE = "DNS 解析管理"
    HINT = "↑↓ 移动 · 双击/回车 管理解析记录 · / 搜索 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

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
                yield Button("刷新", id="dm-refresh")
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
        table = self.query_one(DataTable)
        table.loading = True
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
        finally:
            table.loading = False
            table.focus()
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
    """解析记录表单：类型驱动子字段 + 线路 / 优先级 / 权重 / 备注 + 跨字段校验。"""

    def __init__(self, record: dict | None = None, domain_name: str = "") -> None:
        self.record = record or {}
        self.domain_name = domain_name
        self.is_new = record is None
        cur_type = normalize_record_type(self.record.get("Type") or "A") or "A"
        self._initial_type = cur_type
        self._initial_value = str(self.record.get("Value") or "")
        self._initial_line = str(self.record.get("Line") or "default")

        parsed = parse_record_value(cur_type, self._initial_value)
        self._parse_failed = parsed is None
        self._initial_parts = parsed if parsed is not None else self._fallback_parts(cur_type, self._initial_value)

        self._line_options = [(name, code) for code, name in PRIMARY_LINES]
        if self._initial_line not in [code for _, code in self._line_options]:
            self._line_options.append((self._initial_line, self._initial_line))
        self._all_line_options = list(self._line_options)

        fields = self._build_fields(self._initial_values())
        extra = None if self.is_new else [("frm-delete", "删除", "error")]
        super().__init__("新增解析记录" if self.is_new else "编辑解析记录", fields, extra_buttons=extra)

    # ------------------------------------------------------------ 初始值 / 字段

    def _fallback_parts(self, record_type: str, value: str) -> dict:
        spec = get_record_type(record_type)
        if spec is None:
            return {"value": value}
        for field in spec.fields:
            if field.kind == "text":
                return {field.name: value}
        return {spec.fields[0].name: value} if spec.fields else {"value": value}

    def _initial_values(self) -> dict:
        spec = get_record_type(self._initial_type)
        line = "default" if spec and spec.line_locked else self._initial_line
        priority = self.record.get("Priority")
        weight = self.record.get("Weight")
        values = {
            "type": self._initial_type,
            "rr": self.record.get("RR", ""),
            "line": line,
            "ttl": str(self.record.get("TTL", 600)),
            "priority": "" if priority is None else str(priority),
            "weight": "" if weight is None else str(weight),
            "remark": self.record.get("Remark") or "",
            "status": "on" if (self.is_new or _is_enabled(self.record)) else "",
        }
        values.update(self._initial_parts)
        return values

    def _ttl_options(self, raw) -> list:
        ttl = _to_int(raw)
        choices = list(TTL_CHOICES)
        if ttl is not None and ttl not in choices:
            choices.append(ttl)
            choices.sort()
        return [(f"{value} 秒", str(value)) for value in choices]

    def _part_field(self, spec_field, value: str) -> FormField:
        if spec_field.kind == "choice":
            options = list(spec_field.options)
            if value and value not in [val for _, val in options]:
                options.append((value, value))
            default = options[0][1] if options else ""
            return FormField(
                spec_field.name,
                spec_field.label,
                kind="choice",
                value=value or default,
                options=options,
                hint=spec_field.hint,
            )
        if not value and spec_field.name == "caa_flag":
            value = "0"  # CAA flag 常规填 0，避免新建时留空
        return FormField(
            spec_field.name,
            spec_field.label,
            value=value,
            placeholder=spec_field.placeholder,
            hint=spec_field.hint,
        )

    def _build_fields(self, values: dict) -> list:
        code = normalize_record_type(values.get("type") or self._initial_type) or "A"
        spec = get_record_type(code)
        type_options = list(RECORD_TYPE_LABELS)
        if code not in RECORD_TYPE_MAP:
            type_options.append((code, code))

        rr = str(values.get("rr", ""))
        line = str(values.get("line") or "default")
        if spec and spec.line_locked:
            line = "default"

        fields = [
            FormField("type", "记录类型", kind="choice", value=code, options=type_options, driver=True),
            FormField(
                "rr", "主机记录", value=rr, hint=(spec.rr_hint if spec else ""),
                validator=lambda v: bool(str(v).strip()), error="不能为空",
            ),
        ]

        if not (spec and spec.line_locked):
            options = list(self._line_options)
            if line and line not in [code_ for _, code_ in options]:
                options.append((line, line))
            fields.append(FormField(
                "line", "解析线路", kind="choice", value=line, options=options,
                pick_items=self._line_pick_items, pick_title="选择解析线路", browse_label="更多线路…",
            ))

        if spec:
            for spec_field in spec.fields:
                fields.append(self._part_field(spec_field, str(values.get(spec_field.name, ""))))
        else:
            fields.append(FormField("value", "记录值", value=str(values.get("value", ""))))

        if spec and spec.needs_priority:
            fields.append(FormField(
                "priority", "优先级", value=str(values.get("priority", "")), hint="1-50，越小越优先（MX 必填）"
            ))

        fields.append(FormField(
            "ttl", "TTL (秒)", kind="choice", value=str(_to_int(values.get("ttl")) or 600),
            options=self._ttl_options(values.get("ttl")), hint="免费版 / 个人版最小 600 秒",
        ))

        if spec and spec.supports_weight:
            fields.append(FormField(
                "weight", "权重", value=str(values.get("weight", "")),
                hint="0–100，留空=不修改；需同主机记录+同线路存在 2 条可加权记录；0=不参与返回，服务端可能拒绝",
            ))

        fields.append(FormField(
            "remark", "备注", value=str(values.get("remark", "")), hint="≤50 字符；留空=清空原备注"
        ))

        status_raw = values.get("status", "on" if self.is_new else "")
        enabled = status_raw is True or str(status_raw).lower() in ("on", "1", "true")
        fields.append(FormField(
            "status", "启用状态", kind="switch", value="on" if enabled else "", hint="关闭后解析立即停止生效"
        ))
        return fields

    def dynamic_fields(self, values: dict) -> list:
        return self._build_fields(values)

    # ------------------------------------------------------------ 线路数据（异步）

    def on_mount(self) -> None:
        super().on_mount()
        if self._parse_failed:
            self.app.notify_warn("原记录值格式无法解析，已原样填入首个字段，请核对后保存")
        self._load_lines()

    @work(exclusive=True)
    async def _load_lines(self) -> None:
        dns = self.app.get_dns()

        def _fetch():
            support = dns.get_support_lines(self.domain_name)
            try:
                custom = dns.get_custom_lines(self.domain_name)
            except Exception:  # noqa: BLE001
                custom = []
            return support, custom

        try:
            support, custom = await asyncio.to_thread(_fetch)
        except Exception as e:  # noqa: BLE001
            if self.is_mounted:
                detail = str(e) if isinstance(e, AliyunApiError) else f"{type(e).__name__}: {e}"
                self.app.notify_warn(f"解析线路列表获取失败，已使用内置线路：{detail}")
            return
        if not self.is_mounted or not support:
            return

        display_by_code = {
            str(item.get("code")): str(item.get("display_name") or item.get("name") or item.get("code"))
            for item in support
            if item.get("code")
        }
        primary = [(display_by_code[code], code) for code, _ in PRIMARY_LINES if code in display_by_code]
        if not primary:
            primary = [(name, code) for code, name in PRIMARY_LINES]
        current = self._current_line()
        if current and current not in [code for _, code in primary]:
            primary.append((display_by_code.get(current, current), current))
        self._line_options = primary

        self._all_line_options = [(name, code) for code, name in display_by_code.items()]
        known = {code for _, code in self._all_line_options}
        for item in custom:
            code = str(item.get("code") or "")
            if code and code not in known:
                self._all_line_options.append((str(item.get("name") or code), code))
                known.add(code)
        self._refresh_line_select()

    def _current_line(self) -> str:
        try:
            select = self.query_one("#se-line", Select)
            if select.value is not Select.NULL:
                return str(select.value)
        except NoMatches:
            pass
        return self._initial_line

    def _refresh_line_select(self) -> None:
        try:
            select = self.query_one("#se-line", Select)
        except NoMatches:
            return
        current = "" if select.value is Select.NULL else str(select.value)
        options = list(self._line_options)
        if current and current not in [code for _, code in options]:
            options.append((current, current))
        select.set_options([(label, code) for label, code in options])
        if current:
            try:
                select.value = current
            except Exception:  # noqa: BLE001
                pass

    def _line_pick_items(self) -> list:
        return [
            PickItem(value=code, label=label, hint=code)
            for label, code in self._all_line_options
        ]

    def _browse_picked(self, name: str, item: PickItem | None) -> None:
        super()._browse_picked(name, item)
        if item is not None and name == "line":
            code = str(item.value)
            if code not in [code_ for _, code_ in self._line_options]:
                self._line_options.append((item.label, code))

    # ------------------------------------------------------------ 收集 / 校验

    def _collect(self) -> "dict | None":
        data = super()._collect()
        if data is None:
            return None

        code = normalize_record_type(data.get("type") or self._initial_type)
        spec = get_record_type(code)
        data["type"] = code

        ttl = _to_int(data.get("ttl"))
        if ttl is None:
            self.show_error("TTL 必须为 1-86400 之间的整数")
            return None
        data["ttl"] = ttl

        priority = None
        if spec and spec.needs_priority:
            priority = _to_int(data.get("priority"))
            if priority is None:
                self.show_error("MX 记录必须填写优先级（1-50）")
                return None
        data["priority"] = priority

        weight_raw = str(data.get("weight", "") or "").strip()
        weight = None
        if weight_raw:
            weight = _to_int(weight_raw)
            if weight is None or not 0 <= weight <= 100:
                self.show_error("权重必须是 0-100 之间的整数（0=不参与返回）")
                return None
        data["weight"] = weight

        remark = str(data.get("remark", "") or "").strip()
        if len(remark) > 50:
            self.show_error("备注不能超过 50 个字符")
            return None
        data["remark"] = remark

        line = "default" if (spec and spec.line_locked) else (str(data.get("line") or "default") or "default")
        data["line"] = line

        parts = {}
        if spec:
            parts = {field.name: data.get(field.name, "") for field in spec.fields}
            value = compose_record_value(code, parts)
        else:
            value = str(data.get("value", ""))
        data["value"] = value

        error = validate_dns_record(
            data["rr"],
            code,
            value,
            ttl,
            priority=priority if spec and spec.needs_priority else None,
            line=line,
            parts=parts if spec else None,
        )
        if error:
            self.show_error(error)
            return None
        return data


class RecordListScreen(PageScreen):
    """某域名的解析记录列表，支持筛选、排序、增删改与启停。"""

    TITLE = "解析记录"
    HINT = "双击/回车 编辑 · Ctrl+N 新增 · Ctrl+E 编辑 · Ctrl+D 删除 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

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
        self._line_names: dict[str, str] = {}

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="filter-row"):
                yield Static("搜索", classes="fl-label")
                yield Input(placeholder="按主机记录 / 记录值筛选（/ 聚焦）", compact=True, id="rc-search")
                yield Button("刷新", id="rc-refresh")
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
                yield Button("＋ 新增", id="rc-add", variant="primary")
                yield Button("编辑", id="rc-edit")
                yield Button("删除", id="rc-del", variant="error")
        table = make_table("主机记录", "类型", "线路", "记录值", "TTL", "状态")
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
        table = self._table()
        table.loading = True
        dns = self.app.get_dns()

        def _fetch():
            records = dns.get_domain_records(self.domain_name)
            try:
                lines = dns.get_support_lines(self.domain_name)
            except Exception:  # noqa: BLE001
                lines = []
            return records, lines

        try:
            records, lines = await asyncio.to_thread(_fetch)
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
        finally:
            table.loading = False
            table.focus()
        self._records = records
        self._line_names = {
            str(item.get("code")): str(item.get("display_name") or item.get("name") or item.get("code"))
            for item in lines
            if item.get("code")
        }
        self._rebuild()

    def _line_label(self, line) -> str:
        code = str(line or "default")
        if code.lower() == "default":
            return "默认"
        return self._line_names.get(code, code)

    def _rebuild(self, *, after_layout: bool = False) -> None:
        records = sort_records(self._records, self._sort_type, self._sort_order)
        if self._filter:
            records = filter_fuzzy(
                records, self._filter, lambda r: f"{r.get('RR', '')} {r.get('Value', '')} {self._line_label(r.get('Line'))}"
            )
        self._display = records
        table = self.query_one("#rc-table", DataTable)
        # 权重：主机记录 / 类型 / 线路 / 记录值 / TTL / 状态（含列内 padding）
        vis = fit_table_columns(table, [20, 6, 14, 40, 6, 6], rows=len(records))
        keys = list(table.columns.keys())
        table.columns[keys[4]].label = rcell("TTL", vis[4] - 2)
        table.columns[keys[5]].label = rcell("状态", vis[5] - 2)
        rows = []
        for record in records:
            status = rcell("启用" if _is_enabled(record) else "禁用", vis[5] - 2)
            status.stylize(STYLE_OK if _is_enabled(record) else STYLE_ERR)
            rows.append([
                Text(shorten(record.get("RR", "?"), min(RR_MAX, vis[0] - 2))),
                Text(record.get("Type", "?")),
                Text(shorten(self._line_label(record.get("Line")), min(LINE_MAX, vis[2] - 2))),
                Text(shorten(record.get("Value", ""), min(VALUE_MAX, vis[3] - 2))),
                rcell(record.get("TTL", ""), vis[4] - 2),
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
        modal = RecordFormModal(record, self.domain_name)
        self.app.push_screen(modal, lambda result: self._handle_result(record, is_new, result))

    def _handle_result(self, record: dict | None, is_new: bool, result) -> None:
        if result is None:
            return
        if result == "frm-delete":
            self._delete_record(record)
        elif isinstance(result, dict):
            self._save_record(record, is_new, result)

    def _conflict_message(self, conflicts: list) -> str:
        first = conflicts[0]["record"]
        name = f"{first.get('RR', '')}.{self.domain_name}"
        detail = (
            f"与现有 {normalize_record_type(first.get('Type'))} 记录"
            f"（{name} → {shorten(str(first.get('Value', '')), 40)}）冲突"
        )
        if len(conflicts) > 1:
            detail += f" 等 {len(conflicts)} 条记录"
        reason = conflicts[0].get("reason")
        if reason and "完全相同" in reason:
            return f"{detail}，{reason}"
        return f"{detail}，请先暂停或删除该记录"

    def _ensure_weight_group(self, dns, rr: str, record_type: str, line: str) -> None:
        already_open = False
        getter = getattr(dns, "get_slb_sub_domains", None)
        if callable(getter):
            try:
                target = f"{rr}.{self.domain_name}".lower()
                for item in getter(self.domain_name):
                    if str(item.get("sub_domain", "")).lower() != target:
                        continue
                    item_type = str(item.get("type") or "").upper()
                    if item_type and item_type != record_type.upper():
                        continue
                    lines = item.get("lines") or {}
                    line_open = any(bool(open_) for key, open_ in lines.items() if _line_key(key) == _line_key(line))
                    if item.get("open") or line_open:
                        already_open = True
                        break
            except Exception:  # noqa: BLE001
                already_open = False
        if not already_open:
            # SetDNSSLBStatus 本身是幂等开关；查询失败也走这里
            dns.enable_record_weight_group(self.domain_name, rr, record_type, line, open_=True)

    @work(exclusive=True)
    async def _save_record(self, record: dict | None, is_new: bool, data: dict) -> None:
        dns = self.app.get_dns()
        record = record or {}
        rr = str(data.get("rr", "")).strip()
        record_type = normalize_record_type(data.get("type"))
        value = str(data.get("value", ""))
        ttl = _to_int(data.get("ttl"))
        if ttl is None:
            ttl = 600
        line = str(data.get("line") or "default") or "default"
        priority = _to_int(data.get("priority")) if record_type == "MX" else None
        weight = data.get("weight")
        remark = str(data.get("remark") or "").strip()
        enabled = bool(data.get("status", True))

        content_changed = True if is_new else record_content_changed(
            record, rr, record_type, value, ttl, priority=priority, line=line
        )
        status_changed = (not is_new) and enabled != _is_enabled(record)
        old_remark = str(record.get("Remark") or "").strip()
        remark_changed = bool(remark) if is_new else remark != old_remark
        old_weight = _to_int(record.get("Weight")) if not is_new else None
        weight_changed = weight is not None and (is_new or weight != old_weight)
        line_changed = (not is_new) and _line_key(record.get("Line")) != _line_key(line)

        if not is_new and not (content_changed or status_changed or remark_changed or weight_changed):
            self.app.notify_warn("未做任何修改")
            return

        if content_changed or is_new:
            others = [
                item for item in self._records
                if str(item.get("RecordId")) != str(record.get("RecordId"))
            ]
            conflicts = find_conflicting_records(others, rr, line, record_type, value=value)
            if conflicts:
                self.app.notify_err(self._conflict_message(conflicts))
                return

        if weight is not None:
            if not _supports_weight(record_type):
                self.app.notify_err(f"{record_type} 记录不支持权重设置")
                return
            same_group = [
                item for item in self._records
                if str(item.get("RecordId")) != str(record.get("RecordId"))
                and _rr_key(item.get("RR")) == _rr_key(rr)
                and _line_key(item.get("Line")) == _line_key(line)
                # 只有 A / AAAA / CNAME / ALIAS 能参与加权，TXT/MX 等同名记录不算数
                and _supports_weight(item.get("Type"))
            ]
            count = len(same_group) + 1
            if count < 2:
                self.app.notify_err(
                    f"同主机记录与线路下至少需要 2 条可加权记录（A/AAAA/CNAME/ALIAS，当前 {count} 条），请先添加其它记录"
                )
                return

        def _apply() -> str:
            head = ""
            extras: list[str] = []
            if is_new:
                head = "已新增解析记录"
                record_id = dns.add_domain_record(
                    self.domain_name, rr, record_type, value, ttl,
                    line=line, priority=priority if record_type == "MX" else None,
                )
                if record_id and remark:
                    dns.set_record_remark(record_id, remark)
                    extras.append("设置备注")
                if record_id and weight is not None:
                    self._ensure_weight_group(dns, rr, record_type, line)
                    dns.set_record_weight(record_id, weight)
                    extras.append("设置权重")
                if record_id and not enabled:
                    dns.set_domain_record_status(record_id, "Disable")
                    extras.append("禁用")
            else:
                record_id = record.get("RecordId")
                if content_changed:
                    dns.update_domain_record(
                        record_id, rr, record_type, value, ttl,
                        line=line, priority=priority if record_type == "MX" else None,
                    )
                    head = "已更新解析记录与线路" if line_changed else "已更新解析记录"
                if remark_changed:
                    dns.set_record_remark(record_id, remark)
                    if head:
                        extras.append("更新备注")
                    else:
                        head = "已更新备注"
                if weight_changed and record_id:
                    self._ensure_weight_group(dns, rr, record_type, line)
                    dns.set_record_weight(record_id, weight)
                    if head:
                        extras.append("设置权重")
                    else:
                        head = "已设置权重"
                if status_changed:
                    dns.set_domain_record_status(record_id, "Enable" if enabled else "Disable")
                    if head:
                        extras.append("调整启用状态")
                    else:
                        head = "已启用解析记录" if enabled else "已禁用解析记录"
            if not head:
                head = "已保存"
            if extras:
                return f"{head}并{'、'.join(extras)}"
            return head

        try:
            async with busy(self.app, "正在保存解析记录…", mode="bar"):
                message = await asyncio.to_thread(_apply)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            # 内容 / 备注 / 权重 / 状态是多次独立调用，失败时可能已部分生效；重载列表以反映实际状态
            self._load()
            return
        self.app.notify_ok(message)
        self._load()

    def _delete_record(self, record: dict) -> None:
        name = f"{record.get('RR', '')}.{self.domain_name}"

        def confirm(ok: bool) -> None:
            if not ok:
                return
            self._do_delete(record, name)

        self.app.push_screen(
            ConfirmModal(
                f"确定要删除解析记录 {name}（类型: {record.get('Type')}, 值: {record.get('Value')}）吗？",
                title="删除确认",
                yes="删除",
                default_yes=False,
            ),
            confirm,
        )

    @work(exclusive=True)
    async def _do_delete(self, record: dict, name: str) -> None:
        try:
            async with busy(self.app, f"正在删除 {name}…", mode="bar"):
                await asyncio.to_thread(self.app.get_dns().delete_domain_record, record.get("RecordId"))
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            return
        self.app.notify_ok(f"已删除解析记录 {name}")
        self._load()

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
