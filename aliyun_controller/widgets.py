"""共享 UI 组件：确认、输入、多字段表单、输出查看、表格与模糊筛选辅助。

所有模态均为 ModalScreen 泛型返回值，配合 push_screen_wait / 回调消费；
数据展示统一使用 DataTable，不构造字符串伪表格。
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Select, Static, Switch

# ---------------------------------------------------------------- 文案与配色

STYLE_OK = "#4ec96a"
STYLE_ERR = "#e06c75"
STYLE_WARN = "#e5c07b"
STYLE_INFO = "#61afef"
STYLE_DIM = "dim"


def tint(value: Any, style: str) -> Text:
    t = Text(str(value))
    t.stylize(style)
    return t


def colored_text(value: str, *, err: bool = False, warn: bool = False, dim: bool = False) -> Text:
    t = Text(value)
    if err:
        t.stylize(STYLE_ERR)
    elif warn:
        t.stylize(STYLE_WARN)
    elif dim:
        t.stylize(STYLE_DIM)
    return t


HINT_PICK = "输入筛选 · ↑↓ 移动 · 回车 确认 · Esc 取消"
HINT_MENU = "↑↓ 移动 · 回车 确认 · Ctrl+Q 退出"


# ---------------------------------------------------------------- 模糊匹配

def fuzzy_score(needle: str, haystack: str):
    """返回可排序匹配分数（越小越优），不匹配返回 None。"""
    if not needle:
        return (0, 0, 0)
    n, h = needle.lower(), haystack.lower()
    pos = h.find(n)
    if pos >= 0:
        return (0, pos, 0)
    idx = -1
    first = -1
    for ch in n:
        idx = h.find(ch, idx + 1)
        if idx < 0:
            return None
        if first < 0:
            first = idx
    return (1, first, idx - first)


def filter_fuzzy(items: list, needle: str, key: Callable[[Any], str]) -> list:
    """按模糊分数排序过滤；needle 为空时原样返回。"""
    if not needle:
        return list(items)
    scored = []
    for i, it in enumerate(items):
        s = fuzzy_score(needle, key(it))
        if s is not None:
            scored.append((s, i, it))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [it for _, _, it in scored]


# ---------------------------------------------------------------- 确认弹窗

class ConfirmModal(ModalScreen[bool]):
    """通用确认框：三段式（标题栏 / 内容 / 按钮栏）。回车 = 确认，Esc / 取消 = 否。"""

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > .modal-box { width: 76; }
    ConfirmModal .cf-msg { height: auto; }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, message, title="请确认", *, yes="确认", no="取消", default_yes=True):
        super().__init__()
        self.message = message if isinstance(message, Text) else Text(str(message))
        self.title_text = title
        self.yes_label = yes
        self.no_label = no
        self.default_yes = default_yes

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with Vertical(classes="modal-main"):
                yield Static(self.message, classes="cf-msg")
            with Horizontal(classes="btn-row"):
                yield Button(self.no_label, id="no", variant="default")
                yield Button(self.yes_label, id="yes", variant="primary" if self.default_yes else "error")

    def on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------- 输入弹窗

class InputModal(ModalScreen[str | None]):
    """单行输入，三段式。validator 返回 False 时提示错误并停留。None=取消。"""

    DEFAULT_CSS = """
    InputModal { align: center middle; }
    InputModal > .modal-box { width: 80; }
    InputModal .im-error { color: $error; height: auto; }
    InputModal .im-hint { color: $text-muted; height: auto; }
    InputModal .im-msg { height: auto; margin-bottom: 1; }
    InputModal .im-input { margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(
        self,
        message,
        *,
        title="输入",
        value: str = "",
        validator: Callable[[str], bool] | None = None,
        error: str = "输入不合法",
        hint: str = "",
    ):
        super().__init__()
        self.message = message if isinstance(message, Text) else Text(str(message))
        self.title_text = title
        self.initial = value
        self.validator = validator
        self.error_text = error
        self.hint_text = hint

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with Vertical(classes="modal-main"):
                yield Static(self.message, classes="im-msg")
                yield Input(value=self.initial, id="im-input", classes="im-input")
                if self.hint_text:
                    yield Static(self.hint_text, classes="im-hint")
                yield Static("", classes="im-error", id="im-error")
            with Horizontal(classes="btn-row"):
                yield Button("取消", id="im-cancel", variant="default")
                yield Button("确定", id="im-ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#im-input", Input).focus()

    def _submit(self) -> None:
        val = self.query_one("#im-input", Input).value.strip()
        if self.validator and not self.validator(val):
            self.query_one("#im-error", Static).update(Text(self.error_text, style=STYLE_ERR))
            return
        self.dismiss(val)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "im-ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 表单弹窗

@dataclass
class FormField:
    name: str
    label: str
    kind: str = "text"  # text | password | choice | switch | note
    value: str = ""  # switch: "on" 表示开启
    options: list = field(default_factory=list)  # choice: list[(label, value)]
    validator: Callable[[str], bool] | None = None
    error: str = "输入不合法"
    placeholder: str = ""
    hint: str = ""


class FormModal(ModalScreen["dict | None"]):
    """多字段表单，三段式，返回 {name: value}；None=取消。password 留空保持原值。"""

    DEFAULT_CSS = """
    FormModal { align: center middle; }
    FormModal > .modal-box { width: 92; min-height: 24; }
    FormModal .frm-note { color: $text-muted; height: auto; }
    FormModal .frm-row { height: auto; margin-bottom: 0; }
    FormModal .frm-label { width: 16; height: 1; content-align: right middle; text-style: bold; margin-right: 1; }
    FormModal .frm-field { width: 1fr; height: auto; }
    FormModal .frm-field Input, FormModal .frm-field Select { width: 1fr; margin-top: 0; }
    FormModal .frm-field .frm-note { margin-top: 0; }
    FormModal .frm-err { color: $error; height: 1; }
    FormModal .frm-body { height: 1fr; padding: 1 2; }
    FormModal .frm-pad { height: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, fields: list, *, message: str = "", ok_label="保存", extra_buttons=None):
        super().__init__()
        self.title_text = title
        self.fields = list(fields)
        self.message = message
        self.ok_label = ok_label
        # 额外按钮：list[(id, label, variant)]，点击后以按钮 id 作为结果 dismiss
        self.extra_buttons = list(extra_buttons or [])

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with VerticalScroll(classes="frm-body"):
                if self.message:
                    yield Static(self.message, classes="frm-note")
                    yield Static("", classes="frm-pad")
                for f in self.fields:
                    if f.kind == "note":
                        yield Static(f.label, classes="frm-note")
                        continue
                    with Horizontal(classes="frm-row"):
                        yield Label(f.label, classes="frm-label")
                        with Vertical(classes="frm-field"):
                            if f.kind in ("text", "password"):
                                yield Input(
                                    value=f.value if f.kind == "text" else "",
                                    password=(f.kind == "password"),
                                    placeholder=(f.value if f.kind == "password" else f.placeholder),
                                    compact=True,
                                    id=f"in-{f.name}",
                                )
                            elif f.kind == "choice":
                                yield Select(
                                    [(lab, val) for lab, val in f.options],
                                    value=f.value if f.value else Select.NULL,
                                    allow_blank=not f.value,
                                    compact=True,
                                    id=f"se-{f.name}",
                                )
                            elif f.kind == "switch":
                                yield Switch(
                                    value=(str(f.value).lower() in ("on", "1", "true")),
                                    id=f"sw-{f.name}",
                                )
                            if f.hint:
                                yield Static(f.hint, classes="frm-note")
            yield Static("", classes="frm-err", id="frm-error")
            with Horizontal(classes="btn-row"):
                yield Button("取消", id="frm-cancel", variant="default")
                yield Button(self.ok_label, id="frm-ok", variant="primary")
                for bid, label, variant in self.extra_buttons:
                    yield Button(label, id=bid, variant=variant)

    def on_mount(self) -> None:
        first_input = self.query(Input)
        if first_input:
            first_input.first().focus()

    def _collect(self) -> "dict | None":
        """收集表单值；校验失败时写入错误信息并返回 None。可在子类扩展。"""
        err_box = self.query_one("#frm-error", Static)
        err_box.update(Text(""))
        out = {}
        for f in self.fields:
            if f.kind == "note":
                continue
            if f.kind == "choice":
                val = self.query_one(f"#se-{f.name}", Select).value
                val = "" if val is Select.NULL else str(val)
            elif f.kind == "switch":
                val = bool(self.query_one(f"#sw-{f.name}", Switch).value)
            else:
                val = self.query_one(f"#in-{f.name}", Input).value
                if f.kind == "password":
                    if not val and f.value:
                        val = f.value  # 密码留空保持原值
                else:
                    val = val.strip()
            if f.validator and not f.validator(val):
                err_box.update(Text(f"{f.label}: {f.error}", style=STYLE_ERR))
                return None
            out[f.name] = val
        return out

    def show_error(self, message: str) -> None:
        self.query_one("#frm-error", Static).update(Text(message, style=STYLE_ERR))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        data = self._collect()
        if data is not None:
            self.dismiss(data)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "frm-ok":
            data = self._collect()
            if data is not None:
                self.dismiss(data)
        elif bid == "frm-cancel":
            self.dismiss(None)
        else:
            # 额外按钮：以按钮 id 作为结果返回，由调用方解释
            self.dismiss(bid)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 输出查看弹窗

class OutputModal(ModalScreen[None]):
    """滚动文本查看弹窗（长文本/详情预览等），三段式，Esc / 回车 / 关闭按钮退出。"""

    DEFAULT_CSS = """
    OutputModal { align: center middle; }
    OutputModal > .modal-box { width: 100; height: 90%; }
    OutputModal .out-body { height: 1fr; border: none; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def __init__(self, title: str, content):
        super().__init__()
        self.title_text = title
        self.content = content if isinstance(content, Text) else Text(str(content))

    def compose(self):
        with Vertical(classes="modal-box"):
            yield Static(Text(self.title_text, style="bold"), classes="modal-title")
            with VerticalScroll(classes="out-body"):
                yield Static(self.content)
            with Horizontal(classes="btn-row"):
                yield Button("关闭", id="out-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- 表格辅助

def make_table(*headers, cursor: str = "row", zebra: bool = True, classes: str = "tbl") -> DataTable:
    dt = DataTable(cursor_type=cursor, zebra_stripes=zebra, classes=classes)
    dt.add_columns(*headers)
    return dt


def load_rows(table: DataTable, rows: list) -> DataTable:
    """全量重填表格。每行是可被 add_row 展开的单元格序列（str 或 Text）。"""
    table.clear()
    for row in rows:
        table.add_row(*row)
    return table


__all__ = [
    "ConfirmModal", "InputModal", "FormField", "FormModal", "OutputModal",
    "make_table", "load_rows", "filter_fuzzy", "fuzzy_score",
    "tint", "colored_text",
    "STYLE_OK", "STYLE_ERR", "STYLE_WARN", "STYLE_INFO", "STYLE_DIM",
    "HINT_PICK", "HINT_MENU",
]
