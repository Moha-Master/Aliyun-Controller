"""设置屏：单容器展示配置内容，容器右下角「编辑」按钮进入弹窗（litellm 同款）。"""
import asyncio

from rich.text import Text
from textual import on, work
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


class SettingsScreen(PageScreen):
    """阿里云访问密钥设置。"""

    TITLE = "设置"
    HINT = "「编辑访问密钥」修改 · Tab 轮切 · 回车 保存 · Esc/Ctrl+C 返回"

    def __init__(self) -> None:
        super().__init__()
        self._config: dict = {}

    def compose_page(self):
        with Vertical(classes="panel"):
            with Horizontal(classes="kv-row"):
                yield Static("配置文件", classes="kv-label")
                yield Static("", classes="kv-value", id="st-path")
            for key, label, _secret in FIELDS:
                with Horizontal(classes="kv-row gap-top"):
                    yield Static(label, classes="kv-label")
                    yield Static("", classes="kv-value", id=f"view-{key}")
            with Horizontal(classes="kv-row gap-top"):
                yield Static("更新方式", classes="kv-label")
                yield Static("更改后立即生效，无需重启", classes="kv-value", id="st-note")
            with Horizontal(classes="filter-row gap-top"):
                yield Static(classes="fill")
                yield Button("编辑访问密钥", id="edit", variant="primary")

    def on_mount(self) -> None:
        self.reload_page()
        self.query_one("#edit", Button).focus()

    def reload_page(self) -> None:
        self._load()

    @work(exclusive=True)
    async def _load(self) -> None:
        try:
            cfg = await asyncio.to_thread(load_config)
        except Exception:  # noqa: BLE001 — 配置损坏时以 App 内存态兜底
            cfg = self.app.config or {}
        self._config = cfg
        self._refresh()

    def _refresh(self) -> None:
        self.query_one("#st-path", Static).update(Text(str(get_config_path()), style=STYLE_DIM))
        for key, _, _ in FIELDS:
            self.query_one(f"#view-{key}", Static).update(mask_key(self._config.get(key, "")))

    # ------------------------------------------------------------ 编辑弹窗

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
