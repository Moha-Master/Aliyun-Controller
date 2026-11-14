"""界面骨架组件：PageScreen（顶栏 / 滚动内容区 / 底部提示栏 / 折叠菜单）、MonthPicker、QuotaBar、首页艺术字。

本模块是纯展示层组件，不做任何网络 / IO；与 widgets.py（模态与表格辅助）共同构成 UI 基础库。
"""
from __future__ import annotations

import contextlib
import datetime
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from rich.cells import cell_len
from rich.segment import Segment
from rich.text import Text
from textual import events, on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.strip import Strip, StripRenderable
from textual.widget import Widget
from textual.widgets import Button, Input, OptionList, Static, TextArea

try:  # 不同 textual 版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option

from .widgets import EDIT_HINT, STYLE_ERR, HintBar

# ---------------------------------------------------------------- 首页 logo

LOGO_LINES = [
    " █████╗ ██╗     ██╗██╗   ██╗██╗   ██╗███╗   ██╗ ██████╗████████╗██╗     ",
    "██╔══██╗██║     ██║╚██╗ ██╔╝██║   ██║████╗  ██║██╔════╝╚══██╔══╝██║     ",
    "███████║██║     ██║ ╚████╔╝ ██║   ██║██╔██╗ ██║██║        ██║   ██║     ",
    "██╔══██║██║     ██║  ╚██╔╝  ██║   ██║██║╚██╗██║██║        ██║   ██║     ",
    "██║  ██║███████╗██║   ██║   ╚██████╔╝██║ ╚████║╚██████╗   ██║   ███████╗",
    "╚═╝  ╚═╝╚══════╝╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝   ╚═╝   ╚══════╝",
]

LOGO_WIDTH = max(cell_len(line.rstrip()) for line in LOGO_LINES)  # 随 LOGO_LINES 自动计算，换 logo 无需改别处


def logo_text() -> Text:
    return Text("\n".join(line.rstrip() for line in LOGO_LINES))


def home_col_width() -> int:
    """首页栏列宽：跟随 logo，菜单文字更宽时以 40 列兜底。"""
    return max(LOGO_WIDTH, 40)


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
        width: 5;
        min-width: 5;
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
        width: 5;
        min-width: 5;
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
        width: 6;
        min-width: 6;
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
        yield Button("◀", id="mp-prev")
        yield Button(self.value, id="mp-cur")
        yield Button("▶", id="mp-next")
        with Vertical(id="mp-pop"):
            with Horizontal(id="mp-years"):
                yield Button("◀", id="mp-yprev")
                yield Static(str(self._popup_year), classes="mp-year", id="mp-year-label")
                yield Button("▶", id="mp-ynext")
            with Vertical(id="mp-grid"):
                for r in range(3):
                    with Horizontal(classes="mp-mrow"):
                        for m in range(r * 4 + 1, r * 4 + 5):
                            yield Button(f"{m:02d}", id=f"mp-m{m:02d}", classes="mp-m")

    def watch_value(self, value: str) -> None:
        # 父件 is_mounted 为 True 时子件可能已先行卸载（切屏 / 退出竞态），统一按查得到与否判定
        with contextlib.suppress(NoMatches):
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
        with contextlib.suppress(NoMatches):  # 同上：子件可能已先行卸载
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


# ---------------------------------------------------------------- 折线图

@dataclass
class ChartSeries:
    """一条曲线：points 可以是 float，也可以是带 ``ts`` / ``value`` 属性的 MetricPoint。"""

    label: str = ""
    points: list = field(default_factory=list)
    css_class: str = "chart--s0"


_BRAILLE_BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


def _point_value(point) -> float:
    if isinstance(point, (int, float)):
        return float(point)
    return float(point.value)


def _point_time(point):
    return getattr(point, "ts", None)


def _downsample(values: Sequence[float], target: int) -> list[float]:
    """均值降采样：数据点多于 dot 列数时按桶取均值。"""
    if target <= 0 or not values:
        return []
    if len(values) <= target:
        return [float(v) for v in values]
    out: list[float] = []
    total = len(values)
    for index in range(target):
        start = int(index * total / target)
        end = max(start + 1, int((index + 1) * total / target))
        chunk = values[start:end]
        out.append(sum(chunk) / len(chunk))
    return out


def _nice_ceiling(value: float) -> float:
    """非百分比指标的取整上界（5/10/20/25/50/100…）。"""
    if value <= 0:
        return 1.0
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
        if value <= step:
            return float(step)
    return float(math.ceil(value / 1000.0) * 1000)


class LineChart(Widget):
    """自绘 braille 折线图：固定 Y 轴量程、时间序列、光标取值。

    - 颜色只走 component class（``chart--s0/1/2``），色值在 app.tcss 定义；
    - 空序列画坐标框 + 「无数据」，单点画一个点，均不崩；
    - 数据点多于 dot 列数时按均值降采样；
    - 按 (宽, 高, 数据版本, 光标) 缓存 StripRenderable。
    """

    COMPONENT_CLASSES = {
        "chart--axis",
        "chart--grid",
        "chart--cursor",
        "chart--s0",
        "chart--s1",
        "chart--s2",
    }

    DEFAULT_CSS = """
    LineChart {
        width: 1fr;
        height: 10;
    }
    """

    BINDINGS = [
        Binding("left", "cursor_left", show=False),
        Binding("right", "cursor_right", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
    ]

    series = reactive([], init=False)
    cursor = reactive(None)

    class CursorMoved(Message):
        """光标移动：index 为 None 表示已清除光标。"""

        def __init__(self, chart: LineChart, index: int | None, point) -> None:
            super().__init__()
            self.chart = chart
            self.index = index
            self.point = point

        @property
        def control(self) -> LineChart:
            """供 @on(..., "#id") 选择器匹配；同时用于区分是哪张图发出的消息。"""
            return self.chart

    def __init__(
        self,
        *,
        id: str | None = None,
        ymin: float = 0.0,
        ymax: float = 100.0,
        chart_height: int = 6,
        x_labels: tuple[str, str, str] = ("", "", ""),
        auto_scale: bool = False,
        y_suffix: str = "%",
        **kwargs,
    ) -> None:
        super().__init__(id=id, **kwargs)
        self.can_focus = True
        self.ymin = float(ymin)
        self.ymax = float(ymax)
        self.chart_height = max(1, int(chart_height))
        self.auto_scale = auto_scale
        self.y_suffix = y_suffix
        self._x_labels = tuple(x_labels)
        self._version = 0
        self._cache_key = None
        self._cache: StripRenderable | None = None
        self._height_set = self.chart_height + 1
        self.styles.height = self._height_set

    # ------------------------------------------------------------ 数据

    def set_series(self, series: Sequence[ChartSeries], *, x_labels=None) -> None:
        self.series = list(series)
        if x_labels is not None:
            self._x_labels = tuple(x_labels)
        self._version += 1
        self._cache_key = None
        length = self._length()
        if self.cursor is not None and (length == 0 or self.cursor >= length):
            self.cursor = None
        self.refresh()

    def watch_series(self, value) -> None:
        self._version += 1
        self._cache_key = None
        self.refresh(layout=True)

    def watch_cursor(self, value) -> None:
        self._cache_key = None
        self.refresh()

    def _first_points(self) -> list:
        for item in self.series:
            if item.points:
                return list(item.points)
        return []

    def _length(self) -> int:
        return max((len(item.points) for item in self.series), default=0)

    # ------------------------------------------------------------ 光标

    def action_cursor_left(self) -> None:
        length = self._length()
        if length == 0:
            return
        self.cursor = length - 1 if self.cursor is None else max(0, self.cursor - 1)
        self._emit_cursor()

    def action_cursor_right(self) -> None:
        length = self._length()
        if length == 0:
            return
        self.cursor = 0 if self.cursor is None else min(length - 1, self.cursor + 1)
        self._emit_cursor()

    def action_cursor_home(self) -> None:
        if self._length():
            self.cursor = 0
            self._emit_cursor()

    def action_cursor_end(self) -> None:
        length = self._length()
        if length:
            self.cursor = length - 1
            self._emit_cursor()

    def key_escape(self, event: events.Key) -> None:
        """有光标时清除并吃掉 Esc；无光标时冒泡给 PageScreen 返回。"""
        if self.cursor is None:
            return
        self.cursor = None
        self._emit_cursor()
        event.stop()

    def _emit_cursor(self) -> None:
        point = None
        points = self._first_points()
        if self.cursor is not None and 0 <= self.cursor < len(points):
            point = points[self.cursor]
        self.post_message(self.CursorMoved(self, self.cursor, point))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        length = self._length()
        if length == 0:
            return
        axis_width = self._axis_width()
        plot_width = max(1, self.size.width - axis_width)
        x = max(0, min(event.x - axis_width, plot_width - 1))
        self.cursor = 0 if length == 1 else round(x * (length - 1) / max(1, plot_width - 1))
        self._emit_cursor()

    # ------------------------------------------------------------ 尺寸 / 渲染

    def _axis_width(self) -> int:
        return 0 if self.size.width < 46 else 6

    def _plot_height(self) -> int:
        return max(1, min(self.chart_height, self.size.height - 1))

    def on_resize(self, event: events.Resize) -> None:
        desired = (4 if self.size.width < 46 else self.chart_height) + 1
        if desired != self._height_set:
            self._height_set = desired
            self.styles.height = desired
        self._cache_key = None
        self.refresh()

    def _scale(self) -> tuple[float, float]:
        ymin, ymax = self.ymin, self.ymax
        if self.auto_scale:
            values = [_point_value(p) for item in self.series for p in item.points]
            ymin = 0.0
            ymax = _nice_ceiling(max(values) if values else 0.0)
        if ymax <= ymin:
            ymax = ymin + 1.0
        return ymin, ymax

    def _format_tick(self, value: float) -> str:
        if self.y_suffix == "%":
            return f"{value:.0f}%"
        return f"{value:g}{self.y_suffix}"

    def _default_x_labels(self) -> tuple[str, str, str]:
        if any(self._x_labels):
            return self._x_labels
        points = self._first_points()
        if len(points) < 2:
            return ("", "", "")
        times = [_point_time(p) for p in points]
        times = [t for t in times if isinstance(t, datetime.datetime)]
        if len(times) < 2:
            return ("", "", "")
        span = times[-1] - times[0]
        fmt = "%m-%d %H:%M" if span > datetime.timedelta(days=2) else "%H:%M"
        local = [t.astimezone() for t in (times[0], times[len(times) // 2], times[-1])]
        return tuple(t.strftime(fmt) for t in local)  # type: ignore[return-value]

    @staticmethod
    def _put_text(row: list, start: int, text: str, style) -> None:
        """按显示宽度把文本写入单元格行（宽字符占两格）。"""
        col = start
        for char in text:
            width = cell_len(char)
            if col + width > len(row):
                break
            row[col] = (char, style)
            for offset in range(1, width):
                row[col + offset] = ("", style)
            col += width

    @staticmethod
    def _row_strip(cells: list[tuple[str, object]]) -> Strip:
        segments: list[Segment] = []
        run: list[str] = []
        run_style = None
        for char, style in cells:
            if not char:
                continue
            if run and style != run_style:
                segments.append(Segment("".join(run), run_style))
                run = []
            run_style = style
            run.append(char)
        if run:
            segments.append(Segment("".join(run), run_style))
        return Strip(segments, sum(cell_len(char) for char, _ in cells))

    def _build(self, width: int, height: int) -> StripRenderable:
        if width <= 0 or height <= 0:
            return StripRenderable([], max(0, width))
        axis_style = self.get_component_rich_style("chart--axis")
        grid_style = self.get_component_rich_style("chart--grid")
        cursor_style = self.get_component_rich_style("chart--cursor")
        plot_height = max(1, min(self.chart_height, height - 1))
        axis_width = 0 if width < 46 else 6
        plot_width = max(1, width - axis_width)
        ymin, ymax = self._scale()

        rows: list[list[tuple[str, object]]] = []
        label_rows = {0, plot_height // 2, plot_height - 1}

        # 数据 → braille 点阵
        bits = [[0] * plot_width for _ in range(plot_height)]
        colors: list[list[object]] = [[None] * plot_width for _ in range(plot_height)]
        has_data = any(item.points for item in self.series)
        if has_data:
            dot_width = plot_width * 2
            dot_height = plot_height * 4
            for item in self.series[:3]:
                values = [_point_value(p) for p in item.points]
                if not values:
                    continue
                values = _downsample(values, dot_width)
                count = len(values)
                style_name = item.css_class if item.css_class in self.COMPONENT_CLASSES else "chart--s0"
                style = self.get_component_rich_style(style_name)
                dots = []
                for i, value in enumerate(values):
                    x = (dot_width - 1) // 2 if count == 1 else round(i * (dot_width - 1) / (count - 1))
                    ratio = (value - ymin) / (ymax - ymin)
                    y = dot_height - 1 - round(max(0.0, min(1.0, ratio)) * (dot_height - 1))
                    dots.append((x, y))
                if count == 1:
                    segments = [(dots[0], dots[0])]
                else:
                    segments = list(zip(dots, dots[1:], strict=False))
                for (x0, y0), (x1, y1) in segments:
                    dx, dy = abs(x1 - x0), -abs(y1 - y0)
                    sx = 1 if x0 < x1 else -1
                    sy = 1 if y0 < y1 else -1
                    err = dx + dy
                    while True:
                        cell_x, cell_y = x0 >> 1, y0 >> 2
                        if 0 <= cell_x < plot_width and 0 <= cell_y < plot_height:
                            bits[cell_y][cell_x] |= _BRAILLE_BITS[x0 & 1][y0 & 3]
                            colors[cell_y][cell_x] = style
                        if x0 == x1 and y0 == y1:
                            break
                        e2 = 2 * err
                        if e2 >= dy:
                            err += dy
                            x0 += sx
                        if e2 <= dx:
                            err += dx
                            y0 += sy

        for row_index in range(plot_height):
            row: list[tuple[str, object]] = [(" ", None)] * plot_width
            # 网格点线（只画刻度行）
            if row_index in label_rows and plot_height > 1:
                for col in range(plot_width):
                    row[col] = ("·", grid_style)
            if has_data:
                for col in range(plot_width):
                    if bits[row_index][col]:
                        row[col] = (chr(0x2800 + bits[row_index][col]), colors[row_index][col])
            # 底部横轴
            if row_index == plot_height - 1 and not has_data:
                for col in range(plot_width):
                    row[col] = ("─", axis_style)
            if axis_width:
                if row_index in label_rows and plot_height > 1:
                    value = ymax - (ymax - ymin) * row_index / (plot_height - 1)
                    label = self._format_tick(value)[: axis_width - 1].rjust(axis_width - 1)
                else:
                    label = " " * (axis_width - 1)
                prefix = [(char, axis_style) for char in label] + [("│", axis_style)]
                row = prefix + row
            rows.append(row)

        if not has_data:
            # 中间一行灰字提示（任何情况下都不画 0%）
            middle = rows[plot_height // 2]
            text = "无数据"
            text_width = cell_len(text)
            start = axis_width + max(0, (plot_width - text_width) // 2)
            self._put_text(middle, start, text, axis_style)

        # 光标列
        if self.cursor is not None and has_data:
            points = self._first_points()
            count = len(points)
            if count:
                dot_width = plot_width * 2
                x = (dot_width - 1) // 2 if count == 1 else round(self.cursor * (dot_width - 1) / (count - 1))
                col = min(plot_width - 1, max(0, x >> 1))
                for row_index in range(plot_height):
                    rows[row_index][axis_width + col] = ("│", cursor_style)

        strips = [self._row_strip(row) for row in rows]

        # 底部 X 轴时间标签
        if height > plot_height:
            label_row: list[tuple[str, object]] = [(" ", None)] * width
            first, middle, last = self._default_x_labels()
            first = first[: max(0, plot_width)] if first else ""
            last = last[: max(0, plot_width)] if last else ""
            if first and last and cell_len(first) + cell_len(last) + 2 <= plot_width:
                self._put_text(label_row, axis_width, first, axis_style)
                self._put_text(label_row, width - cell_len(last), last, axis_style)
                if middle and cell_len(middle) + 4 <= plot_width:
                    middle_start = axis_width + (plot_width - cell_len(middle)) // 2
                    if middle_start > axis_width + cell_len(first) + 1 and (
                        middle_start + cell_len(middle) < width - cell_len(last) - 1
                    ):
                        self._put_text(label_row, middle_start, middle, axis_style)
            elif last:
                self._put_text(label_row, width - cell_len(last), last, axis_style)
            strips.append(self._row_strip(label_row))

        return StripRenderable(strips, width)

    def render(self) -> StripRenderable:
        width, height = self.size.width, self.size.height
        _, ymax = self._scale()
        key = (width, height, self._version, self.cursor, round(ymax, 6), self.auto_scale)
        if self._cache_key == key and self._cache is not None:
            return self._cache
        result = self._build(width, height)
        self._cache_key = key
        self._cache = result
        return result


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
    HINT: ClassVar[str] = "↑↓ 移动 · 回车 执行 · Ctrl+R 刷新 · Esc/Ctrl+C 返回"

    BINDINGS = [
        Binding("escape,ctrl+c", "page_back", "返回"),
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
    PageScreen #topbar #top-back,
    PageScreen #topbar #top-menu {
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
        height: 1;
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
            yield Button("◀ 返回", id="top-back")
            with Horizontal(classes="tb-left"):
                yield Static(Text(self.TITLE, style="bold"), classes="tb-title")
                yield Static(self.subtitle, classes="tb-sub", id="tb-sub")
            with Horizontal(classes="tb-right"):
                yield from self.compose_toolbar()
                if self.menu:
                    yield Button("菜单 ▾", id="top-menu")
        with Vertical(id="page"):
            yield from self.compose_page()
        yield HintBar(self.HINT, classes="page-hint", id="page-hint")
        if self.menu:
            with Vertical(id="page-menu"):
                yield OptionList(id="menu-list")

    def compose_page(self):
        return iter(())

    def compose_toolbar(self):
        return iter(())

    # ------------------------------------------------------------ 标题 / 副标题

    def set_subtitle(self, text: str) -> None:
        """更新顶栏副标题。

        判定不能用 ``is_mounted``：`on_mount` 里它仍是 `False`（Textual 派发完
        Mount 事件之后才置位），首屏副标题会永远停在空串。改为按顶栏控件是否
        已组合判断：未组合时仅保存文本，交由 compose 使用。
        """
        self.subtitle = text
        try:
            self.query_one("#tb-sub", Static).update(text)
        except NoMatches:
            pass

    def set_title(self, text: str) -> None:
        """更新顶栏标题；判定口径同 set_subtitle。"""
        self.TITLE = text
        try:
            self.query_one(".tb-title", Static).update(Text(text, style="bold"))
        except NoMatches:
            pass

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

    # ------------------------------------------------------------ 文本编辑模式

    def on_descendant_focus(self, event) -> None:
        self._refresh_hint()

    def on_descendant_blur(self, event) -> None:
        self._refresh_hint()

    def _editing(self) -> bool:
        return isinstance(self.app.focused, (Input, TextArea))

    def _refresh_hint(self) -> None:
        """焦点进出输入框时，在页面 HINT 与编辑键位 HINT 间切换。"""
        if not self.is_mounted:
            return
        try:
            hint = self.query_one("#page-hint", HintBar)
        except Exception:  # noqa: BLE001 — 组合早期尚未挂载
            return
        hint.update(EDIT_HINT if self._editing() else self.HINT)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """文本编辑模式下禁用页面刷新（Ctrl+R），其余交给控件原生键位。"""
        if action == "refresh_page" and self._editing():
            return False
        return True

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
