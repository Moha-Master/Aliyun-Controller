"""Textual 应用入口：全局配置、服务客户端、状态栏与首启配置向导。"""
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from .billing import AliCloudBssQuerier
from .config import config_exists, load_config
from .dns import AliCloudDnsQuerier


class StatusBar(Static):
    """底部状态栏：展示最近一次操作结果 / 进行中提示。"""


class AliyunControllerApp(App):
    """阿里云管理 TUI。"""

    CSS_PATH = Path(__file__).parent / "app.tcss"
    TITLE = "Aliyun Controller"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config: dict | None = None
        self._billing: AliCloudBssQuerier | None = None
        self._dns: AliCloudDnsQuerier | None = None

    # ------------------------------------------------------------ 全局资源

    def get_billing(self) -> AliCloudBssQuerier:
        if self._billing is None:
            self._billing = AliCloudBssQuerier()
        return self._billing

    def get_dns(self) -> AliCloudDnsQuerier:
        if self._dns is None:
            self._dns = AliCloudDnsQuerier()
        return self._dns

    def invalidate_clients(self) -> None:
        """连接参数变化后重建客户端。"""
        self._billing = None
        self._dns = None

    def reload_config(self) -> None:
        self.config = load_config()
        self.invalidate_clients()

    # ------------------------------------------------------------ 状态栏 / 提示

    def compose(self) -> ComposeResult:
        yield StatusBar("", id="status")

    def status(self, message: str, kind: str = "") -> None:
        try:
            bar = self.query_one("#status", StatusBar)
        except Exception:
            return
        bar.update_classes({"ok": kind == "ok", "warn": kind == "warn", "err": kind == "err"})
        bar.update(message)

    def notify_ok(self, message: str) -> None:
        self.status(message, "ok")
        self.notify(message, severity="success", timeout=4)

    def notify_err(self, message: str) -> None:
        self.status(message, "err")
        self.notify(message, severity="error", timeout=8)

    def notify_warn(self, message: str) -> None:
        self.status(message, "warn")
        self.notify(message, severity="warning", timeout=5)

    # ------------------------------------------------------------ 启动

    def on_mount(self) -> None:
        self._bootstrap()

    @work(exclusive=True)
    async def _bootstrap(self) -> None:
        from .screens.home import HomeScreen
        from .screens.setup import SetupWizardScreen

        err = None
        try:
            self.config = load_config()
        except Exception as e:  # noqa: BLE001
            err = str(e)

        if err is not None:
            self.status("配置文件缺失或损坏，正在打开配置向导…", "warn")
            saved = await self.push_screen_wait(
                SetupWizardScreen(reason=err, config_missing=not config_exists())
            )
            if not saved:
                self.exit()
                return
            try:
                self.reload_config()
            except Exception as e2:  # noqa: BLE001
                self.notify_err(f"配置仍无效: {e2}")
                self.exit()
                return
        self.push_screen(HomeScreen())
