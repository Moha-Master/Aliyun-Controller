"""界面骨架组件：PageScreen（顶栏 / 滚动内容区 / 底部提示栏 / 折叠菜单）、MonthPicker、QuotaBar、首页艺术字。

本模块是纯展示层组件，不做任何网络 / IO；与 widgets.py（模态与表格辅助）共同构成 UI 基础库。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, OptionList, Static

try:  # 不同 textual 版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option

from .widgets import STYLE_ERR

# ---------------------------------------------------------------- 首页 logo

LOGO_LINES = [
    " █████╗ ██╗     ██╗██╗   ██╗██╗   ██╗███╗   ██╗",
    "██╔══██╗██║     ██║╚██╗ ██╔╝██║   ██║████╗  ██║",
    "███████║██║     ██║ ╚████╔╝ ██║   ██║██╔██╗ ██║",
    "██╔══██║██║     ██║  ╚██╔╝  ██║   ██║██║╚██╗██║",
    "██║  ██║███████╗██║   ██║   ╚██████╔╝██║ ╚████║",
    "╚═╝  ╚═╝╚══════╝╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═══╝",
]

LOGO_WIDTH = max(len(line) for line in LOGO_LINES)


def logo_text() -> Text:
    return Text("\n".join(LOGO_LINES))


# ---------------------------------------------------------------- 月份工具

def shift_cycle(cycle: str, delta: int) -> str:
    """YYYY-MM 加减月份。"""
    year, month = (int(p) for p in cycle.split("-", 1))
    idx = year * 12 + (month - 1) + delta
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def current_cycle() -> str:
    return datetime.datetime.now().strftime("%Y-%m")


# ---------------------------------------------------------------- 月份选择器

class MonthPicker(Horizontal):
    """◀ YYYY-MM ▶ 组合月份选择器。

    - ◀/▶（或聚焦时 ←/→）逐月切换，范围 [min_cycle, max_cycle]；
    - 点击月份文本弹出页内浮层（年份切换 + 12 月列表，越界月份禁用）；
    - 值变化发 MonthPicker.Changed(value)。
    """

    class Changed(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

        @property
        def control(self) -> MonthPicker:
            """供 @on(..., "#id") 选择器匹配的控件。"""
            return self._sender

    value = reactive("")

    DEFAULT_CSS = """
    MonthPicker {
        layout: horizontal;
        width: auto;
        height: auto;
        margin-left: 2;
        layers: base overlay;
    }
    MonthPicker > Button {
        width: 3;
        min-width: 3;
        padding: 0;
        height: 1;
    }
    MonthPicker > #mp-cur {
        width: 12;
        min-width: 12;
        content-align: center middle;
        text-style: bold;
    }
    MonthPicker #mp-pop {
        display: none;
        position: absolute;
        overlay: screen;
        layer: overlay;
        width: 26;
        height: auto;
        border: round $primary;
        background: $surface;
    }
    MonthPicker #mp-years {
        height: auto;
        layout: horizontal;
    }
    MonthPicker #mp-years Button {
        width: 3;
        min-width: 3;
        padding: 0;
        height: 1;
    }
    MonthPicker #mp-years .mp-year {
        width: auto;
        padding: 0 1;
        content-align: center middle;
        text-style: bold;
    }
    MonthPicker #mp-grid {
        height: auto;
        padding: 0;
    }
    MonthPicker .mp-mrow {
        height: 1;
        layout: horizontal;
    }
    MonthPicker .mp-m {
        width: 4;
        min-width: 4;
        height: 1;
        padding: 0;
        margin: 0;
    }
    """

    POP_WIDTH = 26  # 与 DEFAULT_CSS #mp-pop width 保持一致

    BINDINGS = [
        Binding("left", "month_prev", show=False),
        Binding("right", "month_next", show=False),
    ]

    def __init__(
        self,
        *,
        value: str | None = None,
        min_cycle: str = "2020-01",
        max_cycle: str | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="month-picker")
        self.min_cycle = min_cycle
        self.max_cycle = max_cycle or current_cycle()
        self._min_year = int(min_cycle[:4])
        self._max_year = int(self.max_cycle[:4])
        start = value or current_cycle()
        start = max(min_cycle, min(start, self.max_cycle))
        self.value = start
        self._popup_year = int(start[:4])
        self._popup_open = False

    def compose(self):
        yield Button("◀", id="mp-prev", compact=True)
        yield Button(self.value, id="mp-cur", compact=True)
        yield Button("▶", id="mp-next", compact=True)
        with Vertical(id="mp-pop"):
            with Horizontal(id="mp-years"):
                yield Button("◀", id="mp-yprev", compact=True)
                yield Static(str(self._popup_year), classes="mp-year", id="mp-year-label")
                yield Button("▶", id="mp-ynext", compact=True)
            with Vertical(id="mp-grid"):
                for r in range(3):
                    with Horizontal(classes="mp-mrow"):
                        for m in range(r * 4 + 1, r * 4 + 5):
                            yield Button(f"{m:02d}", id=f"mp-m{m:02d}", classes="mp-m", compact=True)

    def watch_value(self, value: str) -> None:
        if self.is_mounted:
            self.query_one("#mp-cur", Button).label = value
            if self._popup_open:
                self._rebuild_popup()
            self.post_message(self.Changed(value))

    # ------------------------------------------------------------ 逐月切换

    def _step(self, delta: int) -> None:
        self.close_popup()
        nxt = shift_cycle(self.value, delta)
        if self.min_cycle <= nxt <= self.max_cycle:
            self.value = nxt

    def action_month_prev(self) -> None:
        self._step(-1)

    def action_month_next(self) -> None:
        self._step(1)

    @on(Button.Pressed, "#mp-prev")
    def _on_prev(self) -> None:
        self._step(-1)

    @on(Button.Pressed, "#mp-next")
    def _on_next(self) -> None:
        self._step(1)

    # ------------------------------------------------------------ 浮层选择

    @property
    def is_popup_open(self) -> bool:
        return self._popup_open

    @on(Button.Pressed, "#mp-cur")
    def _on_toggle(self) -> None:
        if self._popup_open:
            self.close_popup()
        else:
            self.open_popup()

    def open_popup(self) -> None:
        self._popup_open = True
        self._popup_year = int(self.value[:4])
        pop = self.query_one("#mp-pop")
        pop.display = True
        # 靠屏幕右缘的月份按钮：浮层右对齐（超出宽度向左展开）
        pop.styles.offset = (self.region.width - self.POP_WIDTH, 1)
        self._rebuild_popup()

    def close_popup(self) -> None:
        if not self._popup_open:
            return
        self._popup_open = False
        if self.is_mounted:
            self.query_one("#mp-pop").display = False

    def _rebuild_popup(self) -> None:
        year = self._popup_year
        self.query_one("#mp-year-label", Static).update(str(year))
        self.query_one("#mp-yprev", Button).disabled = year <= self._min_year
        self.query_one("#mp-ynext", Button).disabled = year >= self._max_year
        for m in range(1, 13):
            cycle = f"{year}-{m:02d}"
            button = self.query_one(f"#mp-m{m:02d}", Button)
            button.disabled = not (self.min_cycle <= cycle <= self.max_cycle)
            button.variant = "primary" if cycle == self.value else "default"

    def _shift_year(self, delta: int) -> None:
        year = self._popup_year + delta
        if self._min_year <= year <= self._max_year:
            self._popup_year = year
            self._rebuild_popup()

    @on(Button.Pressed, "#mp-yprev")
    def _on_yprev(self) -> None:
        self._shift_year(-1)

    @on(Button.Pressed, "#mp-ynext")
    def _on_ynext(self) -> None:
        self._shift_year(1)

    @on(Button.Pressed, ".mp-m")
    def _on_pick_month(self, event: Button.Pressed) -> None:
        assert event.button.id is not None
        month = int(event.button.id.removeprefix("mp-m"))
        cycle = f"{self._popup_year}-{month:02d}"
        self.close_popup()
        if self.min_cycle <= cycle <= self.max_cycle and cycle != self.value:
            self.value = cycle


# ---------------------------------------------------------------- 额度进度条

class QuotaBar(Horizontal):
    """双色额度条：额度内一段色，超出部分另一段色，剩余灰色。

    用量 <= 额度时量程为额度；用量 > 额度时量程为用量（即条满）。
    """

    def __init__(self, *, free: float = 20.0, id: str | None = None) -> None:
        super().__init__(id=id, classes="quota-bar")
        self.free = free
        self._safe = Static(classes="seg safe")
        self._over = Static(classes="seg over")
        self._rest = Static(classes="seg rest")

    def compose(self):
        yield self._safe
        yield self._over
        yield self._rest

    def set_usage(self, used: float) -> tuple[float, float, float]:
        """按已用量刷新三段宽度，返回 (安全段%, 超限段%, 计算用量程 GB)。"""
        used = max(0.0, float(used))
        scale = max(used, self.free, 1e-9)
        safe = min(used, self.free) / scale * 100.0
        over = max(0.0, used - self.free) / scale * 100.0
        rest = max(0.0, 100.0 - safe - over)
        self._safe.styles.width = f"{safe:.2f}%"
        self._over.styles.width = f"{over:.2f}%"
        self._rest.styles.width = f"{rest:.2f}%"
        return safe, over, scale


def quota_caption(used: float, free: float = 20.0) -> Text:
    """额度条下方说明文字。"""
    pct = used / free * 100.0 if free > 0 else 0.0
    t = Text(f"已用 {used:.2f} GB / 免费额度 {free:g} GB · {pct:.1f}%")
    if used > free:
        t.stylize(STYLE_ERR)
    return t


# ---------------------------------------------------------------- 页面骨架

MenuItem = tuple[str | Callable[[], str], Callable[[], None], bool]


class PageScreen(Screen):
    """功能页骨架：顶栏（返回 / 标题-副标题 / 右侧控件 / 折叠菜单）+ 内容区 + 底部提示栏。

    子类：设置 TITLE / HINT，重写 compose_page()（内容区）与可选 compose_toolbar()（顶栏右侧）。
    内容区版式：小容器（.panel：筛选 / 排序 / 主操作按钮）+ 大容器（表格 .tbl 1fr）。
    self.menu 仅登记次要 / 低频操作（Android ⋮ 思路），主操作应作为容器内按钮常驻；无为空则不显示菜单按钮。
    菜单项元素为 (标签或取标签函数, 回调, 是否危险)。
    """

    TITLE: ClassVar[str] = ""
    SUBTITLE: ClassVar[str] = ""
    HINT: ClassVar[str] = "↑↓ 移动 · 回车 执行 · Ctrl+R 刷新 · Esc 返回"

    BINDINGS = [
        Binding("escape", "page_back", "返回"),
        Binding("ctrl+r", "refresh_page", "刷新"),
        Binding("home", "page_top", "顶部", show=False),
        Binding("end", "page_bottom", "底部", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
    ]

    DEFAULT_CSS = """
    PageScreen {
        layers: base overlay;
        layout: vertical;
        overflow-y: hidden;
    }
    PageScreen #topbar {
        height: auto;
        padding: 0 1;
        background: $panel;
        layout: horizontal;
    }
    PageScreen #topbar Button {
        margin-right: 2;
    }
    PageScreen .tb-left {
        width: 1fr;
        height: auto;
        layout: horizontal;
    }
    PageScreen .tb-left Static {
        width: auto;
        content-align: left middle;
    }
    PageScreen .tb-title {
        text-style: bold;
    }
    PageScreen .tb-sub {
        margin-left: 2;
        color: $text-muted;
    }
    PageScreen .tb-right {
        width: auto;
        height: auto;
        layout: horizontal;
    }

    PageScreen .tb-right Select {
        margin-left: 2;
    }
    PageScreen #page {
        height: 1fr;
        padding: 0;
        layout: vertical;
        overflow-y: hidden;
    }
    PageScreen .page-hint {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }
    PageScreen #page-menu {
        display: none;
        position: absolute;
        overlay: screen;
        layer: overlay;
        width: 28;
        height: auto;
        border: round $primary;
        background: $surface;
    }
    PageScreen #menu-list {
        height: auto;
        border: none;
        background: transparent;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.menu: list[MenuItem] = []
        self._menu_open = False
        self._menu_prev_focus = None
        self.subtitle: str = self.SUBTITLE

    # ------------------------------------------------------------ 组合

    def compose(self):
        with Horizontal(id="topbar"):
            yield Button("◀ 返回", id="top-back", compact=True)
            with Horizontal(classes="tb-left"):
                yield Static(Text(self.TITLE, style="bold"), classes="tb-title")
                yield Static(self.subtitle, classes="tb-sub", id="tb-sub")
            with Horizontal(classes="tb-right"):
                yield from self.compose_toolbar()
                if self.menu:
                    yield Button("菜单 ▾", id="top-menu", compact=True)
        with Vertical(id="page"):
            yield from self.compose_page()
        yield Static(self.HINT, classes="page-hint")
        if self.menu:
            with Vertical(id="page-menu"):
                yield OptionList(id="menu-list")

    def compose_page(self):
        return iter(())

    def compose_toolbar(self):
        return iter(())

    # ------------------------------------------------------------ 标题 / 副标题

    def set_subtitle(self, text: str) -> None:
        self.subtitle = text
        if self.is_mounted:
            self.query_one("#tb-sub", Static).update(text)

    def set_title(self, text: str) -> None:
        self.TITLE = text
        if self.is_mounted:
            self.query_one(".tb-title", Static).update(Text(text, style="bold"))

    # ------------------------------------------------------------ 折叠菜单

    def _menu_options(self) -> list[Option]:
        opts = []
        for label, _, danger in self.menu:
            text = label() if callable(label) else label
            item = Text(text)
            if danger:
                item.stylize(STYLE_ERR)
            opts.append(Option(item, id=str(len(opts))))
        return opts

    def _open_menu(self) -> None:
        panel = self.query_one("#page-menu")
        if self._menu_open:
            self._close_menu()
            return
        self._menu_open = True
        self._menu_prev_focus = self.app.focused
        panel.display = True
        ol = self.query_one("#menu-list", OptionList)
        ol.clear_options()
        ol.add_options(self._menu_options())
        ol.highlighted = 0
        btn = self.query_one("#top-menu", Button)
        region = btn.region
        width = panel.region.width or 28
        panel.styles.offset = (max(0, self.size.width - width - 1), region.bottom)
        ol.focus()

    def _close_menu(self) -> None:
        if not self._menu_open:
            return
        self._menu_open = False
        self.query_one("#page-menu").display = False
        prev = self._menu_prev_focus
        if prev is not None and prev.can_focus and prev.is_mounted:
            prev.focus()
        self._menu_prev_focus = None

    @on(Button.Pressed, "#top-menu")
    def _on_menu_button(self) -> None:
        self._open_menu()

    @on(OptionList.OptionSelected, "#menu-list")
    def _on_menu_pick(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        self._close_menu()
        if 0 <= idx < len(self.menu):
            self.menu[idx][1]()

    # ------------------------------------------------------------ 动作

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "top-back":
            self.action_page_back()

    def action_page_back(self) -> None:
        if self._menu_open:
            self._close_menu()
            return
        for picker in self.query(MonthPicker):
            if picker.is_popup_open:
                picker.close_popup()
                return
        self.app.pop_screen()

    def on_click(self, event) -> None:
        """点击浮层外部时收起折叠菜单 / 月份选择浮层。"""
        if self._menu_open:
            panel = self.query_one("#page-menu")
            anchor = self.query_one("#top-menu")
            if not panel.region.contains(event.screen_x, event.screen_y) and not anchor.region.contains(event.screen_x, event.screen_y):
                self._close_menu()
        for picker in self.query(MonthPicker):
            if picker.is_popup_open:
                pop = picker.query_one("#mp-pop")
                if not pop.region.contains(event.screen_x, event.screen_y) and not picker.region.contains(event.screen_x, event.screen_y):
                    picker.close_popup()

    def action_refresh_page(self) -> None:
        self.reload_page()

    def reload_page(self) -> None:
        """子类覆写：重新取数。"""

    def _scroller(self):
        """Home/End/PgUp/PgDn 目标：优先内容区内的显式滚动容器（表格滚动走其自身键位）。"""
        page = self.query_one("#page", Vertical)
        inner = page.query(VerticalScroll)
        return inner.first() if inner else page

    def action_page_top(self) -> None:
        self._scroller().action_scroll_home()

    def action_page_bottom(self) -> None:
        self._scroller().action_scroll_end()

    def action_page_up(self) -> None:
        self._scroller().action_page_up()

    def action_page_down(self) -> None:
        self._scroller().action_page_down()
