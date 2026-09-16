"""主菜单屏：艺术字 logo（窄终端自动降级）+ 版本副标题 + 居中菜单 + 底部提示栏。"""
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import OptionList, Static

from .. import __version__
from ..ui import LOGO_WIDTH, logo_text

try:  # textual 各版本 Option 导出位置不同
    from textual.widgets.option_list import Option
except ImportError:  # pragma: no cover
    from textual.widgets._option_list import Option


class HomeScreen(Screen):
    """功能主菜单：流量 / 账单 / DNS / 设置 / 退出。"""

    BINDINGS = [
        Binding("q", "menu('quit')", "退出"),
        Binding("1", "menu('traffic')", "流量", show=False),
        Binding("2", "menu('billing')", "账单", show=False),
        Binding("3", "menu('dns')", "DNS", show=False),
        Binding("4", "menu('settings')", "设置", show=False),
        Binding("5", "menu('quit')", "退出", show=False),
    ]

    ITEMS = [
        ("traffic", "流量查询", "查询指定月份公网流出流量"),
        ("billing", "账单归纳", "按产品归纳指定月份消费"),
        ("dns", "DNS 解析管理", "域名解析记录增删改查"),
        ("settings", "设置", "查看 / 修改阿里云访问密钥"),
        ("quit", "退出程序", "保存配置并退出"),
    ]

    def compose(self):
        with Vertical(id="home-main"):
            with Vertical(id="home-col"):
                yield Static(logo_text(), id="home-banner")
                yield Static(Text("Aliyun Controller", style="bold"), id="home-plain")
                yield Static(f"v{__version__}", id="home-version")
                yield OptionList(id="home-list")
        yield Static("↑↓ 选择 · 回车 进入 · 1-5 直达 · Ctrl+Q 退出", classes="page-hint")

    def on_mount(self) -> None:
        ol = self.query_one("#home-list", OptionList)
        ol.add_options(self._options())
        ol.highlighted = 0
        ol.focus()
        self._apply_breakpoint()

    def on_resize(self, event) -> None:
        self._apply_breakpoint()

    def _apply_breakpoint(self) -> None:
        """宽度不足以容纳艺术字时降级为普通标题文本。"""
        self.set_class(self.size.width < LOGO_WIDTH + 6, "-sm")

    def _options(self):
        for i, (_, label, desc) in enumerate(self.ITEMS, start=1):
            t = Text()
            t.append(f" {i}  ", style="bold")
            t.append(f"{label:<14}", style="bold")
            t.append(desc, style="dim")
            yield Option(t)

    def _open(self, key: str) -> None:
        from .billing import SummaryScreen, TrafficScreen
        from .dns import DomainListScreen
        from .settings import SettingsScreen

        if key == "traffic":
            self.app.push_screen(TrafficScreen())
        elif key == "billing":
            self.app.push_screen(SummaryScreen())
        elif key == "dns":
            self.app.push_screen(DomainListScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())
        elif key == "quit":
            self.app.exit()

    @on(OptionList.OptionSelected, "#home-list")
    def _on_select(self, event: OptionList.OptionSelected) -> None:
        self._open(self.ITEMS[event.option_index][0])

    def action_menu(self, key: str) -> None:
        self._open(key)
