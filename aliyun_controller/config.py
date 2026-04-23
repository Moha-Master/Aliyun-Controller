import os
from pathlib import Path

import yaml
from InquirerPy.base.control import Choice
from InquirerPy.resolver import prompt


def _get_config_path() -> Path:
    config_dir = os.environ.get(
        "ALIYUN_CONTROLLER_CONFIG_DIR",
        os.path.expanduser("~/.config/aliyun-controller"),
    )
    return Path(config_dir) / "config.yaml"


def load_config() -> dict:
    config_path = _get_config_path()
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError("配置文件内容格式错误")

    access_key_id = config.get("access_key_id")
    access_key_secret = config.get("access_key_secret")
    if not isinstance(access_key_id, str) or not access_key_id.strip():
        raise ValueError("缺少有效的 access_key_id")
    if not isinstance(access_key_secret, str) or not access_key_secret.strip():
        raise ValueError("缺少有效的 access_key_secret")

    return {
        "access_key_id": access_key_id.strip(),
        "access_key_secret": access_key_secret.strip(),
    }


def _run_setup_flow() -> bool:
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        answers = prompt(
            [
                {
                    "type": "input",
                    "message": "请输入 access_key_id:",
                    "name": "access_key_id",
                    "validate": lambda val: isinstance(val, str) and len(val.strip()) > 0,
                    "invalid_message": "access_key_id 不能为空",
                },
                {
                    "type": "input",
                    "message": "请输入 access_key_secret:",
                    "name": "access_key_secret",
                    "validate": lambda val: isinstance(val, str) and len(val.strip()) > 0,
                    "invalid_message": "access_key_secret 不能为空",
                },
            ]
        )
    except KeyboardInterrupt:
        print("\n配置已取消。")
        return False

    if not answers:
        print("\n配置已取消。")
        return False

    config_data = {
        "access_key_id": str(answers.get("access_key_id", "")).strip(),
        "access_key_secret": str(answers.get("access_key_secret", "")).strip(),
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, allow_unicode=True, sort_keys=False)

    print(f"配置已保存到: {config_path}")
    return True


def ensure_config_ready() -> bool:
    config_path = _get_config_path()

    if not config_path.exists():
        print(f"未找到配置文件: {config_path}")
        print("现在进入交互式配置流程。")
        return _run_setup_flow()

    try:
        load_config()
        return True
    except Exception as e:
        print(f"配置文件损坏或不可读取: {e}")

    try:
        action = prompt(
            [
                {
                    "type": "list",
                    "message": "请选择操作:",
                    "choices": [
                        Choice("reconfigure", name="重新配置并覆盖"),
                        Choice("exit", name="退出程序"),
                    ],
                    "name": "action",
                }
            ]
        )
    except KeyboardInterrupt:
        print("\n操作已取消。")
        return False

    if not action:
        return False

    if action.get("action") == "reconfigure":
        return _run_setup_flow()
    return False
