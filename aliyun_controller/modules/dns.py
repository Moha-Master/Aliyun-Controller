import os
import re
import yaml
from pathlib import Path
from InquirerPy.resolver import prompt
from InquirerPy.base.control import Choice
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_alidns20150109.client import Client as Alidns20150109Client
from alibabacloud_alidns20150109 import models as alidns_20150109_models

def load_config():
    """加载配置文件"""
    config_dir = os.environ.get('ALIYUN_CONTROLLER_CONFIG_DIR', 
                               os.path.expanduser('~/.config/aliyun-controller'))
    config_path = Path(config_dir) / 'config.yaml'
    example_path = Path(config_dir) / 'config.yaml.example'
    package_example_path = Path(__file__).parent.parent / 'config.yaml.example'
    
    # 检查配置目录是否存在，如果不存在则创建
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 如果示例文件不存在，则从包中复制
    if not example_path.exists() and package_example_path.exists():
        import shutil
        shutil.copy(package_example_path, example_path)
    
    # 如果配置文件不存在，则报错退出
    if not config_path.exists():
        print("配置文件不存在，请参考 config.yaml.example 创建 config.yaml 文件")
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

class AliCloudDnsQuerier:
    def __init__(self):
        """
        初始化DNS客户端
        """
        config = load_config()
        self.client = Alidns20150109Client(
            open_api_models.Config(
                access_key_id=config['access_key_id'],
                access_key_secret=config['access_key_secret'],
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
        # 验证输入参数
        if not self._validate_dns_record(rr, type, value, ttl):
            return False
            
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
        # 验证输入参数
        if not self._validate_dns_record(rr, type, value, ttl):
            return False
            
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

    def _validate_dns_record(self, rr: str, type: str, value: str, ttl: int) -> bool:
        """
        验证DNS记录参数的合法性
        """
        # 验证主机记录
        if not rr or len(rr) > 253:
            print("\n主机记录不能为空且长度不能超过253个字符")
            return False
            
        # 验证记录类型
        valid_types = ['A', 'CNAME', 'MX', 'TXT', 'SRV', 'AAAA', 'NS', 'ANAME']
        if type.upper() not in valid_types:
            print(f"\n不支持的记录类型: {type}")
            return False
            
        # 验证记录值
        if not value:
            print("\n记录值不能为空")
            return False
            
        # 验证TTL (60-86400秒)
        if not (60 <= ttl <= 86400):
            print("\nTTL值必须在60-86400之间")
            return False
            
        # 针对不同类型的记录进行额外验证
        if type.upper() == 'A':
            # IPv4地址验证
            if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', value):
                print("\nA记录的值必须是有效的IPv4地址")
                return False
            parts = value.split('.')
            if any(int(part) > 255 for part in parts):
                print("\nA记录的值必须是有效的IPv4地址")
                return False
        elif type.upper() == 'AAAA':
            # IPv6地址验证（简单验证）
            if not re.match(r'^[0-9a-fA-F:]+$', value):
                print("\nAAAA记录的值必须是有效的IPv6地址")
                return False
        elif type.upper() == 'CNAME':
            # CNAME验证（简单验证）
            if not re.match(r'^[a-zA-Z0-9.-]+$', value):
                print("\nCNAME记录的值格式不正确")
                return False
            if value.endswith('.'):
                print("\nCNAME记录的值不能以点结尾")
                return False
                
        return True

    def sort_records(self, records: list, sort_type: int, sort_order: int) -> list:
        """
        对DNS记录进行排序
        :param records: DNS记录列表
        :param sort_type: 排序类型 (0-创建时间, 1-二级域名, 2-首字母)
        :param sort_order: 排序顺序 (0-逆序, 1-正序)
        :return: 排序后的记录列表
        """
        if sort_type == 0:
            # 按创建时间排序 (默认就是按创建时间排序)
            if sort_order == 0:  # 逆序
                records.reverse()
        elif sort_type == 1:
            # 按二级域名字母排序
            records.sort(
                key=lambda r: r.get('RR', '').split('.')[-1] if '.' in r.get('RR', '') else r.get('RR', ''),
                reverse=(sort_order == 0)
            )
        elif sort_type == 2:
            # 按首字母排序
            records.sort(
                key=lambda r: r.get('RR', ''),
                reverse=(sort_order == 0)
            )
        return records

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

        # 排序设置：类型(0-创建时间, 1-二级域名, 2-首字母) 和 顺序(0-逆序, 1-正序)
        sort_type = 0  # 默认按创建时间排序
        sort_order = 0  # 默认逆序

        while True: # 循环用于对选定域名进行操作
            records = dns_querier.get_domain_records(selected_domain)
            # 根据排序设置对记录进行排序
            records = dns_querier.sort_records(records, sort_type, sort_order)
            
            # 构建记录选项列表
            record_choices = []
            if records:
                for i, record in enumerate(records):
                    record_name = f"{record.get('RR'):<20} {record.get('Type'):<10} {record.get('Value'):<30} {record.get('TTL')}"
                    record_choices.append(Choice(value=i, name=record_name))
                
                # 添加分隔线（不可选择）
                record_choices.append(Choice(value="separator", name="-" * 80, enabled=False))
            
            # 添加操作选项
            sort_type_text = ["创建时间", "二级域名", "首字母"][sort_type]
            sort_order_text = "逆序" if sort_order == 0 else "正序"
            record_choices.extend([
                Choice(value="add", name="新增解析记录"),
                Choice(value="sort", name=f"排序设置 [{sort_type_text}, {sort_order_text}]"),
                Choice(value="refresh", name="刷新记录列表"),
                Choice(value=None, name="[返回域名选择]")
            ])

            print("\n" + "="*80)
            print(f"域名 {selected_domain} 的解析记录".center(80))
            print("="*80)
            
            if not records:
                print("未找到任何解析记录。")
                print("="*80)
                # 如果没有记录，只显示添加和返回选项
                action_choices = [
                    Choice(value="add", name="新增解析记录"),
                    Choice(value=None, name="[返回域名选择]")]                
                action_questions = [
                    {
                        "type": "list",
                        "message": "请选择操作:",
                        "choices": action_choices,
                        "name": "dns_action",
                    }
                ]
            else:
                print(f"{'主机记录(RR)':<20} {'类型':<10} {'记录值(Value)':<30} {'TTL'}")
                print("--------------------------------------------------------------------------------")
                action_questions = [
                    {
                        "type": "list",
                        "message": "请选择要操作的记录或操作:",
                        "choices": record_choices,
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

            # 处理记录选择
            if isinstance(dns_action, int) and 0 <= dns_action < len(records):
                selected_record = records[dns_action]
                
                # 为选中的记录提供编辑/删除选项
                record_action_questions = [
                    {
                        "type": "list",
                        "message": f"对记录 {selected_record.get('RR')}.{selected_domain} ({selected_record.get('Type')}: {selected_record.get('Value')}) 执行操作:",
                        "choices": [
                            Choice("edit", "编辑记录"),
                            Choice("delete", "删除记录"),
                            Choice(value=None, name="[取消]")
                        ],
                        "name": "record_action",
                    }
                ]
                
                record_action_result = prompt(record_action_questions)
                if not record_action_result:
                    print("\n操作已取消，返回记录列表。")
                    continue
                    
                record_action = record_action_result.get("record_action")
                if not record_action:
                    print("\n操作已取消，返回记录列表。")
                    continue

                if record_action == "edit":
                    print(f"\n您正在编辑以下记录:")
                    print(f"  主机记录 (RR): {selected_record.get('RR')}")
                    print(f"  记录类型 (Type): {selected_record.get('Type')}")
                    print(f"  记录值 (Value): {selected_record.get('Value')}")
                    print(f"  TTL: {selected_record.get('TTL')}")

                    update_fields_questions = [
                        {"type": "input", "message": f"新的主机记录 (当前: {selected_record.get('RR')}, 留空则不修改):", "name": "rr", "default": selected_record.get('RR')},
                        {"type": "input", "message": f"新的记录类型 (当前: {selected_record.get('Type')}, 留空则不修改):", "name": "type", "default": selected_record.get('Type')},
                        {"type": "input", "message": f"新的记录值 (当前: {selected_record.get('Value')}, 留空则不修改):", "name": "value", "default": selected_record.get('Value')},
                        {"type": "input", "message": f"新的TTL (当前: {selected_record.get('TTL')}, 留空则不修改):", "name": "ttl", "default": str(selected_record.get('TTL'))},
                    ]
                    update_answers = prompt(update_fields_questions)
                    if not update_answers:
                        print("\n操作已取消，返回记录列表。")
                        continue

                    # 只有当用户输入了新值时才使用新值，否则保留原值
                    rr = update_answers.get('rr') or selected_record.get('RR')
                    type_val = (update_answers.get('type') or selected_record.get('Type')).upper()
                    value = update_answers.get('value') or selected_record.get('Value')
                    ttl = int(update_answers.get('ttl') or selected_record.get('TTL'))

                    dns_querier.update_domain_record(
                        record_id=selected_record.get('RecordId'),
                        rr=rr,
                        type=type_val,
                        value=value,
                        ttl=ttl
                    )

                elif record_action == "delete":
                    full_record_name = f"{selected_record.get('RR')}.{selected_domain}"
                    confirmation_question = [
                        {
                            "type": "confirm",
                            "message": f"确定要删除解析记录 {full_record_name} (类型: {selected_record.get('Type')}, 值: {selected_record.get('Value')}) 吗?",
                            "default": False,
                            "name": "confirm_delete",
                        }
                    ]
                    confirmation_result = prompt(confirmation_question)
                    if not confirmation_result:
                        print("\n操作已取消，返回记录列表。")
                        continue
                    
                    if confirmation_result.get("confirm_delete"):
                        dns_querier.delete_domain_record(selected_record.get('RecordId'))
                    else:
                        print("删除操作已取消。")
                
                # 继续显示记录列表
                continue

            # 处理其他操作
            if dns_action == "add":
                add_questions = [
                    {"type": "input", "message": "主机记录 (例如 www):", "name": "rr"},
                    {"type": "input", "message": "记录类型 (例如 A, CNAME):", "name": "type"},
                    {"type": "input", "message": "记录值:", "name": "value"},
                    {"type": "input", "message": "TTL (默认 600):", "name": "ttl", "default": "600"},
                ]
                add_answers = prompt(add_questions)
                if not add_answers:
                    print("\n操作已取消，返回记录列表。")
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

            elif dns_action == "sort":
                # 进入排序设置子页面
                # 保存当前设置以防用户取消操作
                prev_sort_type = sort_type
                prev_sort_order = sort_order
                
                # 创建排序设置界面
                sort_type_choices = [
                    Choice(0, "创建时间排序"),
                    Choice(1, "二级域名排序"),
                    Choice(2, "首字母排序")
                ]
                
                sort_order_choices = [
                    Choice(0, "逆序"),
                    Choice(1, "正序")
                ]
                
                # 显示当前选择
                print("\n" + "="*50)
                print("排序设置".center(50))
                print("="*50)
                print(f"当前设置: {['创建时间', '二级域名', '首字母'][sort_type]}, {'逆序' if sort_order == 0 else '正序'}")
                print("="*50)
                
                # 选择排序类型
                sort_type_question = [
                    {
                        "type": "list",
                        "message": "请选择排序类型:",
                        "choices": sort_type_choices,
                        "default": sort_type,
                        "name": "sort_type"
                    }
                ]
                
                sort_type_result = prompt(sort_type_question)
                if not sort_type_result or sort_type_result.get("sort_type") is None:
                    # 恢复原设置并返回
                    sort_type = prev_sort_type
                    sort_order = prev_sort_order
                    continue
                    
                new_sort_type = sort_type_result.get("sort_type")
                
                # 选择排序顺序
                sort_order_question = [
                    {
                        "type": "list",
                        "message": "请选择排序顺序:",
                        "choices": sort_order_choices,
                        "default": sort_order,
                        "name": "sort_order"
                    }
                ]
                
                sort_order_result = prompt(sort_order_question)
                if not sort_order_result or sort_order_result.get("sort_order") is None:
                    # 恢复原设置并返回
                    sort_type = prev_sort_type
                    sort_order = prev_sort_order
                    continue
                    
                new_sort_order = sort_order_result.get("sort_order")
                
                # 更新排序设置并直接返回主列表
                sort_type = new_sort_type
                sort_order = new_sort_order
                print(f"\n排序设置已更新为: {['创建时间', '二级域名', '首字母'][sort_type]}, {'逆序' if sort_order == 0 else '正序'}")
                print("正在返回DNS记录列表...")

            elif dns_action == "refresh":
                # 刷新操作，直接继续循环
                continue