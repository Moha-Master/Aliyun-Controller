import datetime
import re
import logging
import traceback
from InquirerPy import prompt
from InquirerPy.base.control import Choice
from modules.billing import get_outbound_traffic_module, summarize_billing_module
from modules.dns import dns_management_module

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

def _prompt_for_billing_cycle() -> str | None:
    """
    提示用户输入账单周期，并验证格式。
    支持 YYYY-MM 和 YYYY-M 格式。
    如果用户取消输入，则返回 None。
    """
    date_prompt = [
        {
            "type": "input",
            "message": "请输入要查询的月份 (格式 YYYY-MM 或 YYYY-M):",
            "name": "cycle",
            "validate": lambda val: re.match(r"^\d{4}-(0?[1-9]|1[0-2])$", val) is not None,
            "invalid_message": "格式错误，请输入 YYYY-MM 或 YYYY-M 格式的月份。"
        }
    ]
    try:
        answer = prompt(date_prompt)
        if not answer:  # 用户按 Ctrl+C
            return None
        
        cycle = answer.get("cycle")
        year, month = cycle.split('-')
        if len(month) == 1:
            month = '0' + month
        return f"{year}-{month}"
    except KeyboardInterrupt:
        return None
    except Exception as e:
        logging.error(f"输入月份时发生错误: {e}")
        logging.debug(traceback.format_exc())
        return None

def query_and_repeat(query_function):
    """
    包装查询函数：先查当月，然后提供子菜单让用户选择继续查询或返回。
    :param query_function: 接受 billing_cycle 参数的查询函数。
    """
    # 1. 默认查询当月
    current_cycle = datetime.datetime.now().strftime("%Y-%m")
    print(f"\n--- 正在查询默认月份 {current_cycle} 的账单 ---")
    try:
        query_function(current_cycle)
    except Exception as e:
        logging.error(f"查询账单时发生错误: {e}")
        logging.debug(traceback.format_exc())
        print(f"\n查询账单时发生错误，请查看日志了解详情。")

    # 2. 进入子菜单循环
    while True:
        sub_menu_prompt = [
            {
                "type": "list",
                "message": "请选择接下来的操作:",
                "choices": [
                    Choice("set_date", name="查询其他月份"),
                    Choice("return", name="返回主菜单")
                ],
                "name": "sub_action"
            }
        ]
        
        try:
            result = prompt(sub_menu_prompt)
            if not result: # 用户按 Ctrl+C
                print("\n已返回主菜单。")
                break

            action = result.get("sub_action")
            if action == "set_date":
                new_cycle = _prompt_for_billing_cycle()
                if new_cycle:
                    print(f"\n--- 正在查询 {new_cycle} 的账单 ---")
                    try:
                        query_function(new_cycle)
                    except Exception as e:
                        logging.error(f"查询账单时发生错误: {e}")
                        logging.debug(traceback.format_exc())
                        print(f"\n查询账单时发生错误，请查看日志了解详情。")
                else:
                    print("\n输入已取消。")
                    continue # 重新显示子菜单
            elif action == "return":
                print("\n已返回主菜单。")
                break
        except Exception as e:
            logging.error(f"执行操作时发生错误: {e}")
            logging.debug(traceback.format_exc())
            print(f"\n执行操作时发生错误，请查看日志了解详情。")
            continue

def main():
    """
    主函数，提供交互式菜单
    """
    print("阿里云控制台工具")
    print("=" * 30)
    
    while True:
        questions = [
            {
                "type": "list",
                "message": "请选择要执行的功能:",
                "choices": [
                    Choice("get_traffic", name="1. 查询总流出流量"),
                    Choice("summarize_bill", name="2. 归纳账单"),
                    Choice("manage_dns", name="3. DNS解析管理"),
                    Choice(value=None, name="[退出]")
                ],
                "name": "action",
            }
        ]

        try:
            result = prompt(questions)
            if not result:
                print("\n已退出。")
                break
                
            action = result.get("action")

            if action == "get_traffic":
                query_and_repeat(get_outbound_traffic_module)
            elif action == "summarize_bill":
                query_and_repeat(summarize_billing_module)
            elif action == "manage_dns":
                try:
                    dns_management_module()
                except Exception as e:
                    logging.error(f"DNS管理模块发生错误: {e}")
                    logging.debug(traceback.format_exc())
                    print(f"\nDNS管理模块发生错误，请查看日志了解详情。")
            elif action is None:
                print("已退出。")
                break
        except Exception as e:
            logging.error(f"主菜单执行时发生错误: {e}")
            logging.debug(traceback.format_exc())
            print(f"\n执行操作时发生错误，请查看日志了解详情。")
            continue

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n检测到中断，程序已退出。")
    except Exception as e:
        logging.error(f"程序运行时发生未处理的错误: {e}")
        logging.debug(traceback.format_exc())
        print(f"\n程序运行时发生未处理的错误，请查看日志了解详情。")