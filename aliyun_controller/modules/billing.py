import os
import yaml
from pathlib import Path
from alibabacloud_bssopenapi20171214.client import Client as BssOpenApi20171214Client
from alibabacloud_bssopenapi20171214.models import DescribeInstanceBillRequest
from alibabacloud_tea_openapi import models as open_api_models

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

class AliCloudBssQuerier:
    def __init__(self):
        """
        初始化客户端
        """
        config = load_config()
        self.client = BssOpenApi20171214Client(
            open_api_models.Config(
                access_key_id=config['access_key_id'],
                access_key_secret=config['access_key_secret'],
                region_id="cn-hangzhou",
            )
        )

    def fetch_bill_details(self, billing_cycle: str, subscription_type: str) -> list:
        """
        根据指定的账单类型，分页获取所有账单明细。
        """
        all_items = []
        next_token = None
        try:
            while True:
                request = DescribeInstanceBillRequest(
                    billing_cycle=billing_cycle,
                    subscription_type=subscription_type,
                    is_billing_item=True,
                    max_results=300
                )
                if next_token:
                    request.next_token = next_token

                response = self.client.describe_instance_bill(request)
                
                response_dict = response.body.to_map()
                data = response_dict.get('Data', {})
                if not data:
                    break
                
                items_list = data.get('Items', [])
                all_items.extend(items_list)
                
                next_token = data.get('NextToken')
                if not next_token:
                    break
            
            return all_items

        except Exception as e:
            print(f"\n查询 [{subscription_type}] 类型账单时出错: {e}")
            return []

    def fetch_all_bill_details(self, billing_cycle: str) -> list:
        """
        获取所有类型的账单明细（PayAsYouGo + Subscription）
        """
        all_items = []
        all_items.extend(self.fetch_bill_details(billing_cycle, 'PayAsYouGo'))
        all_items.extend(self.fetch_bill_details(billing_cycle, 'Subscription'))
        return all_items

    def convert_usage_to_bytes(self, usage: float, unit: str) -> float:
        """
        将用量转换为字节
        """
        unit = unit.upper()
        if unit == 'GB':
            return usage * 1024 * 1024 * 1024
        elif unit == 'MB':
            return usage * 1024 * 1024
        elif unit == 'KB':
            return usage * 1024
        else:
            return usage

def get_outbound_traffic_module(billing_cycle: str):
    """
    流量查询模块
    """
    querier = AliCloudBssQuerier()
    total_usage_bytes = 0.0

    TRAFFIC_ITEMS_CODES = [
        "ECS_Out_Bytes",
        "Eip_Out_Bytes",
        "Cdn_domestic_flow",
        "Cdn_overseas_flow",
        "OSS_Out_Traffic",
    ]

    print(f"\n正在查询账单周期 {billing_cycle} 的账单明细...")
    
    all_items = querier.fetch_all_bill_details(billing_cycle)
    
    if not all_items:
        print("未发现任何账单明细。")
        return

    print("账单明细获取成功，开始计算总流量...")
    for item in all_items:
        if item.get('BillingItemCode') in TRAFFIC_ITEMS_CODES:
            usage_str = item.get('Usage')
            unit = (item.get('UsageUnit') or '').upper()
            if usage_str:
                try:
                    usage = float(usage_str)
                    if usage > 0:
                        usage_bytes = querier.convert_usage_to_bytes(usage, unit)
                        total_usage_bytes += usage_bytes
                except ValueError:
                    continue
    
    total_traffic_gb = total_usage_bytes / (1024 * 1024 * 1024)
    print("\n" + "="*45)
    print(f"账单周期 {billing_cycle} 的总公网流出流量: {total_traffic_gb:.4f} GB")
    print("="*45)

def summarize_billing_module(billing_cycle: str):
    """
    当月完整账单归纳模块
    """
    querier = AliCloudBssQuerier()
    summary = {}

    print(f"\n正在获取账单周期 {billing_cycle} 的所有账单明细...")
    all_items = querier.fetch_all_bill_details(billing_cycle)

    if not all_items:
        print("未发现任何账单明细。")
        return

    for item in all_items:
        product_code = item.get('ProductCode', 'Unknown')
        product_name = item.get('ProductName', 'Unknown')
        amount = float(item.get('PretaxAmount', 0.0))
        if product_code not in summary:
            summary[product_code] = {'product_name': product_name, 'total_amount': 0.0, 'count': 0}
        summary[product_code]['product_name'] = product_name  # 更新产品名称（同一产品代码可能有多个名称，取最后一个）
        summary[product_code]['total_amount'] += amount
        summary[product_code]['count'] += 1

    # 按金额从大到小排序
    sorted_summary = sorted(summary.items(), key=lambda x: x[1]['total_amount'], reverse=True)

    print("\n" + "="*70)
    print(f"账单周期 {billing_cycle} 消费归纳".center(70))
    print("="*70)
    print(f"{'产品名称':<25} {'产品代码':<15} {'账单条数':<10} {'总金额 (元)':<15}")
    print("-"*70)
    
    total_amount = 0.0
    for product_code, data in sorted_summary:
        total_amount += data['total_amount']
        product_name = data['product_name'][:24]  # 截断过长的产品名称
        print(f"{product_name:<25} {product_code:<15} {data['count']:<10} {data['total_amount']:<15.2f}")

    print("-"*70)
    print(f"总计: {total_amount:.2f} 元".rjust(70))
    print("="*70)