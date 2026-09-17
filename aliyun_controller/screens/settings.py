"""设置屏：一个大容器直接呈现设置内容，点击「编辑」或密钥行进入与 DNS 同款的操作弹窗。"""
from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ..config import get_config_path, load_config, save_config
from ..ui import PageScreen
from ..widgets import STYLE_DIM, FormField, FormModal
from .setup import mask_key

FIELDS = [
    ("access_key_id", "AccessKey ID", False),
    ("access_key_secret", "AccessKey Secret", True),
]
_LABEL = {k: lab for k, lab, _ in FIELDS}


class SettingsScreen(PageScreen):
    """阿里云访问密钥设置。"""

    TITLE = "设置"
    HINT = "点击密钥条目或「编辑」按钮修改 · 回车 保存 · Esc 返回"

    def __init__(self) -> None:
        super().__init__()
        self._config: dict = {}

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="set-row"):
                yield Static("配置文件", classes="set-label")
                yield Static("", classes="set-value", id="st-path")
            for key, label, _secret in FIELDS:
                with Horizontal(classes="set-row", id=f"row-{key}"):
                    yield Static(label, classes="set-label")
                    yield Static("", classes="set-value", id=f"view-{key}")
                    yield Static("点击编辑 ▸", classes="set-edit-hint")
        with Horizontal(classes="sub-toolbar"):
            yield Button("编辑访问密钥", id="edit", variant="primary")
            yield Static("更改后立即生效，无需重启", classes="tb-note")

    def on_mount(self) -> None:
        try:
            self._config = load_config()
        except Exception:  # noqa: BLE001 — 配置损坏时以 App 内存态兜底
            self._config = self.app.config or {}
        self._refresh()
        self.query_one("#edit", Button).focus()

    def _refresh(self) -> None:
        self.query_one("#st-path", Static).update(Text(str(get_config_path()), style=STYLE_DIM))
        for key, _, _ in FIELDS:
            self.query_one(f"#view-{key}", Static).update(mask_key(self._config.get(key, "")))

    # ------------------------------------------------------------ 编辑弹窗（与 DNS 记录同款）

    def _open_editor(self) -> None:
        fields = [
            FormField(
                "access_key_id", "AccessKey ID",
                value=self._config.get("access_key_id", ""),
                validator=lambda v: bool(v and v.strip()),
                error="不能为空",
            ),
            FormField(
                "access_key_secret", "AccessKey Secret", kind="password",
                value=self._config.get("access_key_secret", ""),
                hint="留空保持原 Secret",
            ),
        ]
        self.app.push_screen(FormModal("编辑阿里云访问密钥", fields), self._on_edit_done)

    def _on_edit_done(self, data: dict | None) -> None:
        if not data:
            return
        secret = data["access_key_secret"] or self._config.get("access_key_secret", "")
        new_config = {"access_key_id": data["access_key_id"].strip(), "access_key_secret": secret}
        try:
            save_config(new_config)
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"保存失败: {e}")
            return
        self._config = new_config
        self.app.reload_config()
        self._refresh()
        self.app.notify_ok("访问密钥已更新")

    @on(Button.Pressed, "#edit")
    def _on_edit(self) -> None:
        self._open_editor()

    def on_click(self, event) -> None:
        """点击任一密钥条目行进入编辑弹窗（父类 PageScreen.on_click 仍会被派发）。"""
        for key, _, _ in FIELDS:
            row = self.query_one(f"#row-{key}")
            if row.region.contains(event.screen_x, event.screen_y):
                self._open_editor()
                return
