"""账单屏：公网流出流量查询（额度条）与账单归纳（消费汇总 + 明细表格），共用月份选择器。"""
import asyncio

from rich.text import Text
from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from ..billing import fetch_billing_summary, fetch_outbound_traffic
from ..client import AliyunApiError
from ..ui import MonthPicker, PageScreen, QuotaBar, current_cycle, quota_caption
from ..widgets import STYLE_ERR, load_rows, make_table

# 免费公网流量额度（GB），额度内与安全段同色规则见 ui.QuotaBar
FREE_QUOTA_GB = 20.0


def _cycle_hint() -> str:
    return "◀▶ 切月 · Ctrl+R 刷新 · Esc/Ctrl+C 返回 · 滚轮浏览"


class _BillingScreen(PageScreen):
    """账单类页面公共逻辑：月份选择、重新查询。"""

    HINT = _cycle_hint()

    def __init__(self) -> None:
        super().__init__()
        self.billing_cycle = current_cycle()

    def compose_toolbar(self):
        yield MonthPicker(id="bl-month", value=self.billing_cycle)

    def on_mount(self) -> None:
        tables = self.query(DataTable)
        if tables:
            tables.first().focus()
        self.reload_page()

    # ------------------------------------------------------------ 数据加载

    def reload_page(self) -> None:
        self._reload()

    @work(exclusive=True)
    async def _reload(self) -> None:
        self.app.status(f"正在查询 {self.billing_cycle} 的账单明细…")
        querier = self.app.get_billing()
        try:
            payload = await asyncio.to_thread(self.fetch, self.billing_cycle, querier)
        except AliyunApiError as e:
            self.app.notify_err(str(e))
            self._show_error()
            return
        except Exception as e:  # noqa: BLE001
            self.app.notify_err(f"查询失败: {type(e).__name__}: {e}")
            self._show_error()
            return
        self.set_subtitle(self.billing_cycle)
        self._show_result(payload)

    def fetch(self, billing_cycle: str, querier):
        raise NotImplementedError

    def _show_result(self, payload) -> None:
        raise NotImplementedError

    def _show_error(self) -> None:
        pass

    # ------------------------------------------------------------ 事件

    @on(MonthPicker.Changed, "#bl-month")
    def _on_month(self, event: MonthPicker.Changed) -> None:
        if event.value != self.billing_cycle:
            self.billing_cycle = event.value
            self._reload()

    @on(Button.Pressed, "#bl-refresh")
    def _on_refresh(self) -> None:
        self._reload()


class TrafficScreen(_BillingScreen):
    """公网流出流量汇总：明细表格 + 20GB 额度进度条。"""

    TITLE = "公网流出流量"

    def compose_page(self):
        with Vertical(classes="panel"):
            yield Static("", id="tr-summary")
            yield QuotaBar(free=FREE_QUOTA_GB, id="tr-bar")
            with Horizontal(classes="cap-row"):
                yield Static("", classes="page-hint", id="tr-cap")
                yield Button("刷新", id="bl-refresh", variant="primary")
        table = make_table("产品名称", "计费项", "用量", "折算 GB", "类别")
        table.id = "tr-table"
        yield table

    def fetch(self, billing_cycle: str, querier):
        return fetch_outbound_traffic(billing_cycle, querier)

    def _show_result(self, payload: dict) -> None:
        t = Text()
        t.append(f"账单周期 {payload['billing_cycle']}\n", style="bold")
        t.append(f"IPv4 流出流量 : {payload['ipv4_gb']:.4f} GB\n")
        t.append(f"IPv6 流出流量 : {payload['ipv6_gb']:.4f} GB\n")
        t.append(f"总流出流量    : {payload['total_gb']:.4f} GB", style="bold")
        self.query_one("#tr-summary", Static).update(t)
        self.query_one("#tr-bar", QuotaBar).set_usage(payload["total_gb"])
        self.query_one("#tr-cap", Static).update(quota_caption(payload["total_gb"], FREE_QUOTA_GB))
        rows = []
        for r in payload["items"]:
            rows.append([
                Text(r["product_name"]),
                Text(r["billing_item"]),
                Text(f"{r['usage']:.4f} {r['unit']}".strip()),
                Text(f"{r['gb']:.4f}"),
                Text(r["family"], style="cyan" if r["family"] == "IPv4" else "magenta"),
            ])
        load_rows(self.query_one("#tr-table", DataTable), rows)
        if payload["detail_count"] == 0:
            self.app.status(f"{payload['billing_cycle']} 未发现公网流量计费项", "warn")
        else:
            self.app.status(f"{payload['billing_cycle']} 流量汇总完成 · {payload['detail_count']} 条计费项", "ok")

    def _show_error(self) -> None:
        self.query_one("#tr-summary", Static).update(Text("查询失败，请检查网络与权限", style=STYLE_ERR))


class SummaryScreen(_BillingScreen):
    """按产品归纳账单消费：消费汇总 + 明细表格。"""

    TITLE = "账单归纳"

    def compose_page(self):
        with Vertical(classes="panel"):
            yield Static("", id="sum-total")
            with Horizontal(classes="cap-row"):
                yield Button("刷新", id="bl-refresh", variant="primary")
        table = make_table("产品名称", "产品代码", "账单条数", "总金额 (元)")
        table.id = "sum-table"
        yield table

    def fetch(self, billing_cycle: str, querier):
        return fetch_billing_summary(billing_cycle, querier)

    def _show_result(self, rows: list[dict]) -> None:
        total = 0.0
        count = 0
        table_rows = []
        for r in rows:
            total += r["total_amount"]
            count += r["count"]
            table_rows.append([
                Text(r["product_name"]),
                Text(r["product_code"]),
                Text(str(r["count"])),
                Text(f"{r['total_amount']:.2f}"),
            ])
        load_rows(self.query_one("#sum-table", DataTable), table_rows)
        t = Text()
        t.append(f"消费汇总 · {self.billing_cycle}\n", style="bold")
        t.append(f"总消费 : {total:.2f} 元\n")
        t.append(f"产品数 : {len(rows)} · 账单条数 : {count}\n")
        if rows:
            top = rows[0]
            share = top["total_amount"] / total * 100.0 if total else 0.0
            t.append(f"占比最高 : {top['product_name']} {top['total_amount']:.2f} 元（{share:.1f}%）")
        self.query_one("#sum-total", Static).update(t)
        if not rows:
            self.app.status(f"{self.billing_cycle} 未发现任何账单明细", "warn")
        else:
            self.app.status(f"共 {len(rows)} 个产品，总计 {total:.2f} 元", "ok")

    def _show_error(self) -> None:
        load_rows(self.query_one("#sum-table", DataTable), [])
