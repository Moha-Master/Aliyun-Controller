import os
from dotenv import load_dotenv
from alibabacloud_bssopenapi20171214.client import Client as BssOpenApi20171214Client
from alibabacloud_bssopenapi20171214.models import DescribeInstanceBillRequest
from alibabacloud_tea_openapi import models as open_api_models

class AliCloudBssQuerier:
    def __init__(self):
        """
        初始化客户端
        """
        load_dotenv()
        self.client = BssOpenApi20171214Client(
            open_api_models.Config(
                access_key_id=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"),
                access_key_secret=os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"),
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

def get_outbound_traffic_module(billing_cycle: str):
    """
    流量查询模块
    """
    querier = AliCloudBssQuerier()
    total_usage_bytes = 0.0
    all_items = []

    TRAFFIC_ITEMS_CODES = [
        "ECS_Out_Bytes",
        "Eip_Out_Bytes",
        "Cdn_domestic_flow",
        "Cdn_overseas_flow",
        "OSS_Out_Traffic",
    ]

    print(f"\n正在查询账单周期 {billing_cycle} 的账单明细...")
    
    all_items.extend(querier.fetch_bill_details(billing_cycle, 'PayAsYouGo'))
    all_items.extend(querier.fetch_bill_details(billing_cycle, 'Subscription'))
    
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
                        usage_bytes = 0
                        if unit == 'GB':
                            usage_bytes = usage * 1024 * 1024 * 1024
                        elif unit == 'MB':
                            usage_bytes = usage * 1024 * 1024
                        elif unit == 'KB':
                            usage_bytes = usage * 1024
                        else:
                            usage_bytes = usage
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
    all_items = []
    summary = {}

    print(f"\n正在获取账单周期 {billing_cycle} 的所有账单明细...")
    all_items.extend(querier.fetch_bill_details(billing_cycle, 'PayAsYouGo'))
    all_items.extend(querier.fetch_bill_details(billing_cycle, 'Subscription'))

    if not all_items:
        print("未发现任何账单明细。")
        return

    for item in all_items:
        product_code = item.get('ProductCode', 'Unknown')
        amount = float(item.get('PretaxAmount', 0.0))
        if product_code not in summary:
            summary[product_code] = {'total_amount': 0.0, 'count': 0}
        summary[product_code]['total_amount'] += amount
        summary[product_code]['count'] += 1

    print("\n" + "="*50)
    print(f"账单周期 {billing_cycle} 消费归纳".center(50))
    print("="*50)
    print(f"{'产品代码':<20} {'总金额 (元)':<15} {'账单条数':<10}")
    print("-"*50)
    
    total_amount = 0.0
    for product, data in sorted(summary.items()):
        total_amount += data['total_amount']
        print(f"{product:<20} {data['total_amount']:<15.2f} {data['count']:<10}")

    print("-"*50)
    print(f"总计: {total_amount:.2f} 元".rjust(50))
    print("="*50)