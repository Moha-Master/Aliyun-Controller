import os
from dotenv import load_dotenv
from InquirerPy import prompt
from InquirerPy.base.control import Choice
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_alidns20150109.client import Client as Alidns20150109Client
from alibabacloud_alidns20150109 import models as alidns_20150109_models

class AliCloudDnsQuerier:
    def __init__(self):
        """
        初始化DNS客户端
        """
        load_dotenv()
        self.client = Alidns20150109Client(
            open_api_models.Config(
                access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"),
                access_key_secret=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
                endpoint="dns.aliyuncs.com",
            )
        )

    def get_domains(self) -> list:
        """
        获取所有可管理的域名列表
        """
        request = alidns_20150109_models.DescribeDomainsRequest()
        try:
            response = self.client.describe_domains(request)
            return response.body.to_map().get('Domains', {}).get('Domain', [])
        except Exception as e:
            print(f"\n获取域名列表时出错: {e}")
            return []

    def get_domain_records(self, domain_name: str) -> list:
        """
        获取指定域名的所有解析记录
        """
        all_records = []
        page_number = 1
        page_size = 500
        try:
            while True:
                request = alidns_20150109_models.DescribeDomainRecordsRequest(
                    domain_name=domain_name,
                    page_number=page_number,
                    page_size=page_size
                )
                response = self.client.describe_domain_records(request)
                response_dict = response.body.to_map()
                records = response_dict.get('DomainRecords', {}).get('Record', [])
                if not records:
                    break
                all_records.extend(records)
                total_count = response_dict.get('TotalCount')
                if len(all_records) >= total_count:
                    break
                page_number += 1
            return all_records
        except Exception as e:
            print(f"\n获取域名 {domain_name} 的解析记录时出错: {e}")
            return []

    def add_domain_record(self, domain_name: str, rr: str, type: str, value: str, ttl: int = 600) -> bool:
        """
        添加新的解析记录
        """
        request = alidns_20150109_models.AddDomainRecordRequest(
            domain_name=domain_name,
            rr=rr,
            type=type,
            value=value,
            ttl=ttl
        )
        try:
            self.client.add_domain_record(request)
            print(f"\n成功添加解析记录: {rr}.{domain_name} -> {value}")
            return True
        except Exception as e:
            print(f"\n添加解析记录时出错: {e}")
            return False

    def update_domain_record(self, record_id: str, rr: str, type: str, value: str, ttl: int = 600) -> bool:
        """
        更新现有的解析记录
        """
        request = alidns_20150109_models.UpdateDomainRecordRequest(
            record_id=record_id,
            rr=rr,
            type=type,
            value=value,
            ttl=ttl
        )
        try:
            self.client.update_domain_record(request)
            print(f"\n成功更新解析记录 (ID: {record_id})")
            return True
        except Exception as e:
            print(f"\n更新解析记录时出错: {e}")
            return False

def dns_management_module():
    """
    DNS解析管理模块
    """
    dns_querier = AliCloudDnsQuerier()
    domains = dns_querier.get_domains()
    if not domains:
        print("未能获取到任何域名，请检查您的账户权限或配置。")
        return

    domain_choices = [
        Choice(value=domain['DomainName'], name=domain['DomainName']) for domain in domains
    ]
    domain_choices.append(Choice(value=None, name="[返回]"))

    questions = [
        {
            "type": "list",
            "message": "请选择要管理的域名:",
            "choices": domain_choices,
            "name": "domain_name",
        }
    ]
    result = prompt(questions)
    selected_domain = result.get("domain_name")

    if not selected_domain:
        return

    while True:
        records = dns_querier.get_domain_records(selected_domain)
        print("\n" + "="*80)
        print(f"域名 {selected_domain} 的解析记录".center(80))
        print("="*80)
        if not records:
            print("未找到任何解析记录。")
        else:
            print(f"{'ID':<15} {'主机记录(RR)':<20} {'类型':<10} {'记录值(Value)':<30} {'TTL'}")
            print("-"*80)
            for record in records:
                print(f"{record.get('RecordId'):<15} {record.get('RR'):<20} {record.get('Type'):<10} {record.get('Value'):<30} {record.get('TTL')}")
        print("="*80)
        
        action_questions = [
            {
                "type": "list",
                "message": "请选择操作:",
                "choices": [
                    Choice("add", "新增解析记录"),
                    Choice("update", "修改解析记录"),
                    Choice(value=None, name="[返回主菜单]")
                ],
                "name": "dns_action",
            }
        ]
        action_result = prompt(action_questions)
        dns_action = action_result.get("dns_action")

        if not dns_action:
            break

        if dns_action == "add":
            add_questions = [
                {"type": "input", "message": "主机记录 (例如 www):", "name": "rr"},
                {"type": "input", "message": "记录类型 (例如 A, CNAME):", "name": "type"},
                {"type": "input", "message": "记录值:", "name": "value"},
                {"type": "input", "message": "TTL (默认 600):", "name": "ttl", "default": "600"},
            ]
            add_answers = prompt(add_questions)
            dns_querier.add_domain_record(
                domain_name=selected_domain,
                rr=add_answers['rr'],
                type=add_answers['type'].upper(),
                value=add_answers['value'],
                ttl=int(add_answers['ttl'])
            )

        elif dns_action == "update":
            update_questions = [
                {"type": "input", "message": "请输入要修改记录的 Record ID:", "name": "record_id"},
                {"type": "input", "message": "新的主机记录 (留空则不修改):", "name": "rr"},
                {"type": "input", "message": "新的记录类型 (留空则不修改):", "name": "type"},
                {"type": "input", "message": "新的记录值 (留空则不修改):", "name": "value"},
                {"type": "input", "message": "新的TTL (留空则不修改):", "name": "ttl"},
            ]
            update_answers = prompt(update_questions)
            
            record_id = update_answers.get('record_id')
            if not record_id:
                print("必须提供 Record ID。")
                continue

            target_record = next((r for r in records if r.get('RecordId') == record_id), None)
            if not target_record:
                print(f"未找到 Record ID 为 {record_id} 的记录。")
                continue

            dns_querier.update_domain_record(
                record_id=record_id,
                rr=update_answers.get('rr') or target_record.get('RR'),
                type=(update_answers.get('type') or target_record.get('Type')).upper(),
                value=update_answers.get('value') or target_record.get('Value'),
                ttl=int(update_answers.get('ttl') or target_record.get('TTL'))
            )