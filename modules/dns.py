import os
from dotenv import load_dotenv
from InquirerPy.resolver import prompt
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

    def delete_domain_record(self, record_id: str) -> bool:
        """
        删除解析记录
        """
        request = alidns_20150109_models.DeleteDomainRecordRequest(
            record_id=record_id
        )
        try:
            self.client.delete_domain_record(request)
            print(f"\n成功删除解析记录 (ID: {record_id})")
            return True
        except Exception as e:
            print(f"\n删除解析记录时出错: {e}")
            return False

def dns_management_module():
    """
    DNS解析管理模块
    """
    dns_querier = AliCloudDnsQuerier()

    while True: # 循环用于域名选择
        domains = dns_querier.get_domains()
        if not domains:
            print("未能获取到任何域名，请检查您的账户权限或配置。")
            return

        domain_choices = [
            Choice(value=domain['DomainName'], name=domain['DomainName']) for domain in domains
        ]
        domain_choices.append(Choice(value=None, name="[返回主菜单]"))

        questions = [
            {
                "type": "list",
                "message": "请选择要管理的域名:",
                "choices": domain_choices,
                "name": "domain_name",
            }
        ]

        result = prompt(questions)
        if not result: # 用户在域名选择时按 Ctrl+C
            print("\n操作已取消，返回主菜单。")
            return
        
        selected_domain = result.get("domain_name")
        if not selected_domain: # 用户选择 [返回主菜单]
            return

        while True: # 循环用于对选定域名进行操作
            records = dns_querier.get_domain_records(selected_domain)
            print("\n" + "="*80)
            print(f"域名 {selected_domain} 的解析记录".center(80))
            print("="*80)
            if not records:
                print("未找到任何解析记录。")
            else:
                print(f"{'主机记录(RR)':<20} {'类型':<10} {'记录值(Value)':<30} {'TTL'}")
                print("--------------------------------------------------------------------------------")
                for record in records:
                    print(f"{record.get('RR'):<20} {record.get('Type'):<10} {record.get('Value'):<30} {record.get('TTL')}")
            print("="*80)
            
            action_questions = [
                {
                    "type": "list",
                    "message": "请选择操作:",
                    "choices": [
                        Choice("add", "新增解析记录"),
                        Choice("update", "修改解析记录"),
                        Choice("delete", "删除解析记录"),
                        Choice(value=None, name="[返回域名选择]")
                    ],
                    "name": "dns_action",
                }
            ]
            action_result = prompt(action_questions)
            if not action_result: # 用户在操作选择时按 Ctrl+C
                print("\n操作已取消，返回域名选择。")
                break # 退出操作循环，返回域名选择

            dns_action = action_result.get("dns_action")
            if not dns_action: # 用户选择 [返回域名选择]
                break

            if dns_action == "add":
                add_questions = [
                    {"type": "input", "message": "主机记录 (例如 www):", "name": "rr"},
                    {"type": "input", "message": "记录类型 (例如 A, CNAME):", "name": "type"},
                    {"type": "input", "message": "记录值:", "name": "value"},
                    {"type": "input", "message": "TTL (默认 600):", "name": "ttl", "default": "600"},
                ]
                add_answers = prompt(add_questions)
                if not add_answers:
                    print("\n操作已取消，返回操作选择菜单。")
                    continue
                
                if not all(add_answers.get(k) for k in ['rr', 'type', 'value']):
                    print("\n缺少必要信息，操作取消。")
                    continue
                
                # TTL 是可选的，如果用户没输入，则使用默认值
                ttl_value = add_answers.get('ttl')
                if not ttl_value or not ttl_value.isdigit():
                    ttl_value = 600
                else:
                    ttl_value = int(ttl_value)

                dns_querier.add_domain_record(
                    domain_name=selected_domain,
                    rr=add_answers['rr'],
                    type=add_answers['type'].upper(),
                    value=add_answers['value'],
                    ttl=ttl_value
                )

            elif dns_action == "update":
                update_questions = [
                    {"type": "input", "message": "请输入要修改记录的主机记录 (RR):", "name": "rr_to_find"},
                ]
                update_answers = prompt(update_questions)
                if not update_answers:
                    print("\n操作已取消，返回操作选择菜单。")
                    continue

                rr_to_find = update_answers.get('rr_to_find')

                if not rr_to_find:
                    print("必须提供主机记录 (RR) 来定位记录。")
                    continue

                matching_records = [r for r in records if r.get('RR') == rr_to_find]

                if not matching_records:
                    print(f"未找到主机记录 {rr_to_find} 的记录。")
                    continue

                target_record = None
                if len(matching_records) > 1:
                    record_choices = []
                    for i, record in enumerate(matching_records):
                        record_choices.append(Choice(value=i, name=f"RR: {record.get('RR')}, Type: {record.get('Type')}, Value: {record.get('Value')}, TTL: {record.get('TTL')}"))

                    record_selection_question = [
                        {
                            "type": "list",
                            "message": "找到多条匹配记录，请选择要修改的记录:",
                            "choices": record_choices,
                            "name": "selected_record_index",
                        }
                    ]
                    selection_result = prompt(record_selection_question)
                    if not selection_result:
                        print("\n操作已取消，返回操作选择菜单。")
                        continue
                    selected_index = selection_result.get("selected_record_index")
                    target_record = matching_records[selected_index]
                else:
                    target_record = matching_records[0]
                
                if not target_record:
                    continue

                print(f"您选择了以下记录进行修改:")
                print(f"  主机记录 (RR): {target_record.get('RR')}")
                print(f"  记录类型 (Type): {target_record.get('Type')}")
                print(f"  记录值 (Value): {target_record.get('Value')}")
                print(f"  TTL: {target_record.get('TTL')}")

                update_fields_questions = [
                    {"type": "input", "message": f"新的主机记录 (当前: {target_record.get('RR')}, 留空则不修改):", "name": "rr", "default": target_record.get('RR')},
                    {"type": "input", "message": f"新的记录类型 (当前: {target_record.get('Type')}, 留空则不修改):", "name": "type", "default": target_record.get('Type')},
                    {"type": "input", "message": f"新的记录值 (当前: {target_record.get('Value')}, 留空则不修改):", "name": "value", "default": target_record.get('Value')},
                    {"type": "input", "message": f"新的TTL (当前: {target_record.get('TTL')}, 留空则不修改):", "name": "ttl", "default": str(target_record.get('TTL'))},
                ]
                update_answers = prompt(update_fields_questions)
                if not update_answers:
                    print("\n操作已取消，返回操作选择菜单。")
                    continue

                dns_querier.update_domain_record(
                    record_id=target_record.get('RecordId'),
                    rr=update_answers.get('rr') or target_record.get('RR'),
                    type=(update_answers.get('type') or target_record.get('Type')).upper(),
                    value=update_answers.get('value') or target_record.get('Value'),
                    ttl=int(update_answers.get('ttl') or target_record.get('TTL'))
                )

            elif dns_action == "delete":
                delete_questions = [
                    {"type": "input", "message": "请输入要删除记录的主机记录 (RR):", "name": "rr_to_delete"},
                ]
                delete_answers = prompt(delete_questions)
                if not delete_answers:
                    print("\n操作已取消，返回操作选择菜单。")
                    continue

                rr_to_delete = delete_answers.get('rr_to_delete')

                if not rr_to_delete:
                    print("必须提供主机记录 (RR) 来定位要删除的记录。")
                    continue

                matching_records = [r for r in records if r.get('RR') == rr_to_delete]

                if not matching_records:
                    print(f"未找到主机记录 {rr_to_delete} 的记录。")
                    continue
                
                target_record = None
                if len(matching_records) > 1:
                    record_choices = []
                    for i, record in enumerate(matching_records):
                        record_choices.append(Choice(value=i, name=f"RR: {record.get('RR')}, Type: {record.get('Type')}, Value: {record.get('Value')}, TTL: {record.get('TTL')}"))

                    record_selection_question = [
                        {
                            "type": "list",
                            "message": "找到多条匹配记录，请选择要删除的记录:",
                            "choices": record_choices,
                            "name": "selected_record_index",
                        }
                    ]
                    selection_result = prompt(record_selection_question)
                    if not selection_result:
                        print("\n操作已取消，返回操作选择菜单。")
                        continue
                    selected_index = selection_result.get("selected_record_index")
                    target_record = matching_records[selected_index]
                else:
                    target_record = matching_records[0]

                if not target_record:
                    continue

                full_record_name = f"{target_record.get('RR')}.{selected_domain}"
                confirmation_question = [
                    {
                        "type": "confirm",
                        "message": f"确定要删除解析记录 {full_record_name} (类型: {target_record.get('Type')}, 值: {target_record.get('Value')}) 吗?",
                        "default": False,
                        "name": "confirm_delete",
                    }
                ]
                confirmation_result = prompt(confirmation_question)
                if not confirmation_result:
                    print("\n操作已取消，返回操作选择菜单。")
                    continue
                
                if confirmation_result.get("confirm_delete"):
                    dns_querier.delete_domain_record(target_record.get('RecordId'))
                else:
                    print("删除操作已取消。")