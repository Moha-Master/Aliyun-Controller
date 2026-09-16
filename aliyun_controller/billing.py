"""账单服务层：阿里云 BSS OpenAPI 查询，封装流量统计与消费归纳。

本模块只负责取数与计算，不产生任何终端输出，也不依赖 TUI 库；
数据以结构化返回值交给界面层渲染。
"""
from alibabacloud_bssopenapi20171214.client import Client as BssOpenApi20171214Client
from alibabacloud_bssopenapi20171214.models import DescribeInstanceBillRequest

from .client import AliyunApiError, build_open_api_config, format_api_error
from .config import load_config

IPV4_TRAFFIC_CODES = [
    "ECS_Out_Bytes",
    "Eip_Out_Bytes",
    "Cdn_domestic_flow",
    "Cdn_overseas_flow",
    "OSS_Out_Traffic",
]
IPV6_TRAFFIC_CODES = ["IPv6_Out_Bytes"]


class AliCloudBssQuerier:
    """账单查询客户端。所有 API 失败统一抛出 AliyunApiError。"""

    def __init__(self) -> None:
        config = load_config()
        self.client = BssOpenApi20171214Client(
            build_open_api_config(
                config["access_key_id"],
                config["access_key_secret"],
                region_id="cn-hangzhou",
            )
        )

    def fetch_bill_details(self, billing_cycle: str, subscription_type: str) -> list:
        """分页获取指定账单类型（PayAsYouGo / Subscription）的全部明细。"""
        all_items: list = []
        next_token = None
        try:
            while True:
                request = DescribeInstanceBillRequest(
                    billing_cycle=billing_cycle,
                    subscription_type=subscription_type,
                    is_billing_item=True,
                    max_results=300,
                )
                if next_token:
                    request.next_token = next_token

                response = self.client.describe_instance_bill(request)
                data = response.body.to_map().get("Data", {})
                if not data:
                    break

                all_items.extend(data.get("Items", []))
                next_token = data.get("NextToken")
                if not next_token:
                    break
        except Exception as e:  # noqa: BLE001 — 统一转换为领域异常
            raise AliyunApiError(
                format_api_error(e, f"查询 [{subscription_type}] 类型账单")
            ) from e
        return all_items

    def fetch_all_bill_details(self, billing_cycle: str) -> list:
        """获取所有类型的账单明细（PayAsYouGo + Subscription）。"""
        all_items = self.fetch_bill_details(billing_cycle, "PayAsYouGo")
        all_items.extend(self.fetch_bill_details(billing_cycle, "Subscription"))
        return all_items

    @staticmethod
    def convert_usage_to_bytes(usage: float, unit: str) -> float:
        unit = (unit or "").upper()
        if unit == "GB":
            return usage * 1024 * 1024 * 1024
        if unit == "MB":
            return usage * 1024 * 1024
        if unit == "KB":
            return usage * 1024
        return usage


def _fetch_items(billing_cycle: str, querier: AliCloudBssQuerier | None) -> tuple[AliCloudBssQuerier, list]:
    querier = querier or AliCloudBssQuerier()
    return querier, querier.fetch_all_bill_details(billing_cycle)


def fetch_outbound_traffic(billing_cycle: str, querier: AliCloudBssQuerier | None = None) -> dict:
    """汇总公网流出流量（字节 → GB），并返回可展示的明细行。"""
    querier, all_items = _fetch_items(billing_cycle, querier)

    ipv4_bytes = 0.0
    ipv6_bytes = 0.0
    detail_rows: list[dict] = []
    for item in all_items:
        billing_code = item.get("BillingItemCode")
        if billing_code not in IPV4_TRAFFIC_CODES and billing_code not in IPV6_TRAFFIC_CODES:
            continue
        usage_str = item.get("Usage")
        if not usage_str:
            continue
        try:
            usage = float(usage_str)
        except (TypeError, ValueError):
            continue
        if usage <= 0:
            continue
        usage_bytes = querier.convert_usage_to_bytes(usage, item.get("UsageUnit") or "")
        is_v4 = billing_code in IPV4_TRAFFIC_CODES
        if is_v4:
            ipv4_bytes += usage_bytes
        else:
            ipv6_bytes += usage_bytes
        detail_rows.append({
            "product_name": item.get("ProductName") or item.get("ProductCode") or "Unknown",
            "billing_item": item.get("BillingItem") or billing_code,
            "usage": usage,
            "unit": item.get("UsageUnit") or "",
            "gb": usage_bytes / (1024 * 1024 * 1024),
            "family": "IPv4" if is_v4 else "IPv6",
        })

    detail_rows.sort(key=lambda r: r["gb"], reverse=True)
    gib = 1024 * 1024 * 1024
    return {
        "billing_cycle": billing_cycle,
        "ipv4_gb": ipv4_bytes / gib,
        "ipv6_gb": ipv6_bytes / gib,
        "total_gb": (ipv4_bytes + ipv6_bytes) / gib,
        "item_count": len(all_items),
        "detail_count": len(detail_rows),
        "items": detail_rows,
    }


def fetch_billing_summary(billing_cycle: str, querier: AliCloudBssQuerier | None = None) -> list[dict]:
    """按产品代码归纳账单消费，返回按金额降序排列的列表。"""
    _, all_items = _fetch_items(billing_cycle, querier)

    summary: dict[str, dict] = {}
    for item in all_items:
        product_code = item.get("ProductCode", "Unknown")
        try:
            amount = float(item.get("PretaxAmount", 0.0))
        except (TypeError, ValueError):
            amount = 0.0
        entry = summary.setdefault(
            product_code,
            {"product_code": product_code, "product_name": "Unknown", "total_amount": 0.0, "count": 0},
        )
        entry["product_name"] = item.get("ProductName", "Unknown")
        entry["total_amount"] += amount
        entry["count"] += 1

    return sorted(summary.values(), key=lambda x: x["total_amount"], reverse=True)
