"""DNS 服务层：阿里云解析（Alidns）域名与解析记录管理。

只负责 API 调用、参数校验与排序，不含任何终端输出或 TUI 依赖。
"""
import re

from alibabacloud_alidns20150109 import models as alidns_models
from alibabacloud_alidns20150109.client import Client as Alidns20150109Client

from .client import AliyunApiError, build_open_api_config, format_api_error
from .config import load_config

DNS_RECORD_TYPES = ["A", "CNAME", "MX", "TXT", "SRV", "AAAA", "NS", "ANAME"]

# 排序类型 / 顺序的展示文案（与服务端无关，仅用于界面）
SORT_TYPE_LABELS = ["创建时间", "二级域名", "首字母"]
SORT_ORDER_LABELS = ["逆序", "正序"]


class AliCloudDnsQuerier:
    """DNS 查询客户端。所有 API 失败统一抛出 AliyunApiError。"""

    def __init__(self) -> None:
        config = load_config()
        self.client = Alidns20150109Client(
            build_open_api_config(
                config["access_key_id"],
                config["access_key_secret"],
                endpoint="dns.aliyuncs.com",
            )
        )

    def get_domains(self) -> list:
        request = alidns_models.DescribeDomainsRequest()
        try:
            response = self.client.describe_domains(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "获取域名列表")) from e
        return response.body.to_map().get("Domains", {}).get("Domain", [])

    def get_domain_records(self, domain_name: str) -> list:
        all_records: list = []
        page_number = 1
        page_size = 500
        try:
            while True:
                request = alidns_models.DescribeDomainRecordsRequest(
                    domain_name=domain_name,
                    page_number=page_number,
                    page_size=page_size,
                )
                response = self.client.describe_domain_records(request)
                response_dict = response.body.to_map()
                records = response_dict.get("DomainRecords", {}).get("Record", [])
                if not records:
                    break
                all_records.extend(records)
                if len(all_records) >= response_dict.get("TotalCount", 0):
                    break
                page_number += 1
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, f"获取域名 {domain_name} 的解析记录")) from e
        return all_records

    def add_domain_record(self, domain_name: str, rr: str, type: str, value: str, ttl: int = 600) -> str | None:
        request = alidns_models.AddDomainRecordRequest(
            domain_name=domain_name, rr=rr, type=type, value=value, ttl=ttl
        )
        try:
            response = self.client.add_domain_record(request)
            return response.body.record_id
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "添加解析记录")) from e

    def update_domain_record(self, record_id: str, rr: str, type: str, value: str, ttl: int = 600) -> None:
        request = alidns_models.UpdateDomainRecordRequest(
            record_id=record_id, rr=rr, type=type, value=value, ttl=ttl
        )
        try:
            self.client.update_domain_record(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "更新解析记录")) from e

    def delete_domain_record(self, record_id: str) -> None:
        request = alidns_models.DeleteDomainRecordRequest(record_id=record_id)
        try:
            self.client.delete_domain_record(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "删除解析记录")) from e

    def set_domain_record_status(self, record_id: str, status: str) -> None:
        """status: "Enable" / "Disable"。"""
        request = alidns_models.SetDomainRecordStatusRequest(record_id=record_id, status=status)
        try:
            self.client.set_domain_record_status(request)
        except Exception as e:  # noqa: BLE001
            raise AliyunApiError(format_api_error(e, "设置解析记录状态")) from e


def validate_dns_record(rr: str, record_type: str, value: str, ttl: int) -> str | None:
    """校验 DNS 记录参数，合法返回 None，否则返回中文错误信息。"""
    if not rr or len(rr) > 253:
        return "主机记录不能为空且长度不能超过 253 个字符"

    if record_type.upper() not in DNS_RECORD_TYPES:
        return f"不支持的记录类型: {record_type}"

    if not value:
        return "记录值不能为空"

    if not (60 <= ttl <= 86400):
        return "TTL 值必须在 60-86400 之间"

    upper = record_type.upper()
    if upper == "A":
        if not re.match(r"^(\d{1,3}\.){3}\d{1,3}$", value):
            return "A 记录的值必须是有效的 IPv4 地址"
        if any(int(part) > 255 for part in value.split(".")):
            return "A 记录的值必须是有效的 IPv4 地址"
    elif upper == "AAAA":
        if not re.match(r"^[0-9a-fA-F:]+$", value):
            return "AAAA 记录的值必须是有效的 IPv6 地址"
    elif upper == "CNAME":
        if not re.match(r"^[a-zA-Z0-9.-]+$", value):
            return "CNAME 记录的值格式不正确"
        if value.endswith("."):
            return "CNAME 记录的值不能以点结尾"
    return None


def sort_records(records: list, sort_type: int, sort_order: int) -> list:
    """返回排序后的新列表。sort_type: 0 创建时间 / 1 二级域名 / 2 首字母；sort_order: 0 逆序 / 1 正序。"""
    records = list(records)
    reverse = sort_order == 0
    if sort_type == 0:
        if reverse:
            records.reverse()
        return records
    if sort_type == 1:
        return sorted(records, key=lambda r: r.get("RR", "").rsplit(".", 1)[-1], reverse=reverse)
    if sort_type == 2:
        return sorted(records, key=lambda r: r.get("RR", ""), reverse=reverse)
    return records
