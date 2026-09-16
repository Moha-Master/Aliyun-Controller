"""设置屏：查看 / 修改阿里云访问密钥。"""
from rich.text import Text
from textual import on
from textual.containers import Horizontal
from textual.widgets import Button, Static

from ..config import get_config_path, load_config, save_config
from ..ui import PageScreen
from ..widgets import STYLE_DIM, ConfirmModal, FormField, FormModal
from .setup import mask_key


class SettingsScreen(PageScreen):
    """阿里云访问密钥设置（配置文件损坏时也可在此重建）。"""

    TITLE = "设置"
    HINT = "回车 / 点击 编辑密钥 · 更改立即生效 · Esc 返回"

    def __init__(self) -> None:
        super().__init__()
        self._config: dict = {}

    def compose_page(self):
        yield Static("", id="st-cred", classes="panel")
        with Horizontal(classes="sub-toolbar"):
            yield Button("编辑访问密钥", id="edit", variant="primary")

    def on_mount(self) -> None:
        try:
            self._config = load_config()
        except Exception:  # noqa: BLE001 — 配置损坏时以 App 内存态兜底
            self._config = self.app.config or {}
        self._refresh()
        self.query_one("#edit", Button).focus()

    def _refresh(self) -> None:
        ak_id = self._config.get("access_key_id", "")
        ak_secret = self._config.get("access_key_secret", "")
        t = Text()
        t.append("配置文件     ", style="bold")
        t.append(f"{get_config_path()}\n", style=STYLE_DIM)
        t.append("AccessKey ID ", style="bold")
        t.append(f"{mask_key(ak_id)}\n")
        t.append("Secret       ", style="bold")
        t.append(mask_key(ak_secret))
        self.query_one("#st-cred", Static).update(t)

    @on(Button.Pressed, "#edit")
    def _edit(self) -> None:
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
        if data is None:
            return
        secret = data["access_key_secret"] or self._config.get("access_key_secret", "")
        new_config = {"access_key_id": data["access_key_id"].strip(), "access_key_secret": secret}

        def confirm(ok: bool) -> None:
            if not ok:
                return
            try:
                save_config(new_config)
            except Exception as e:  # noqa: BLE001
                self.app.notify_err(f"保存失败: {e}")
                return
            self._config = new_config
            self.app.reload_config()
            self._refresh()
            self.app.notify_ok("访问密钥已更新")

        self.app.push_screen(ConfirmModal("确认保存新的阿里云访问密钥？"), confirm)
