"""首启 / 重置配置向导：采集阿里云 RAM 访问密钥并写盘。"""
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..config import save_config
from ..widgets import FormField, FormModal


def mask_key(key: str) -> str:
    if not key:
        return "(空)"
    if len(key) <= 10:
        return "****"
    return f"{key[:6]}…{key[-4:]}"


class SetupWizardScreen(ModalScreen[bool]):
    """首启 / 重置配置向导。True=已保存，False=取消（App 应退出）。"""

    DEFAULT_CSS = """
    SetupWizardScreen { align: center middle; }
    SetupWizardScreen > .sw-intro { width: 90; height: auto; }
    """

    BINDINGS = [Binding("escape,ctrl+c", "cancel", show=False)]

    def __init__(self, reason: str = "", config_missing: bool = True) -> None:
        super().__init__()
        self.reason = reason
        self.config_missing = config_missing

    def compose(self):
        title = "👋 首次运行：配置阿里云访问密钥" if self.config_missing else "⚠ 配置不可用：重新配置"
        body = self.reason or "尚未创建配置文件，请设置 RAM 用户的 AccessKey ID 与 AccessKey Secret。"
        with Vertical(classes="modal-box sw-intro"):
            yield Static(Text(title, style="bold"), classes="modal-title")
            with Vertical(classes="modal-main"):
                yield Static(body, id="sw-msg")
                yield Static("Esc/Ctrl+C 取消向导并退出程序", classes="page-hint")

    def on_mount(self) -> None:
        self._flow()

    def action_cancel(self) -> None:
        self.dismiss(False)

    @work(exclusive=True)
    async def _flow(self) -> None:
        fields = [
            FormField(
                "access_key_id", "AccessKey ID",
                validator=lambda v: bool(v and v.strip()),
                error="不能为空",
                hint="RAM 用户的 AccessKey ID",
            ),
            FormField(
                "access_key_secret", "AccessKey Secret", kind="password",
                validator=lambda v: bool(v and v.strip()),
                error="不能为空",
                hint="仅保存到本地配置文件，不会上传",
            ),
        ]
        data = await self.app.push_screen_wait(
            FormModal("配置阿里云访问密钥", fields, message="请输入 RAM 用户的访问密钥")
        )
        if data is None:
            self.dismiss(False)
            return
        save_config(data)
        self.app.notify_ok("配置已保存")
        self.dismiss(True)
