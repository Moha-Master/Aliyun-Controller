"""配置层：配置文件路径解析、加载/保存与纯数据校验。

交互式向导已迁移至 screens/setup.py，本模块不再依赖任何 TUI 库。
"""
import os
from pathlib import Path

import yaml

CONFIG_DIR_ENV = "ALIYUN_CONTROLLER_CONFIG_DIR"
DEFAULT_CONFIG_DIR = "~/.config/aliyun-controller"


def get_config_dir() -> Path:
    config_dir = os.environ.get(CONFIG_DIR_ENV, DEFAULT_CONFIG_DIR)
    return Path(config_dir).expanduser()


def get_config_path() -> Path:
    return get_config_dir() / "config.yaml"


def config_exists() -> bool:
    return get_config_path().exists()


def load_config() -> dict:
    """读取并校验配置；不合法时抛出 ValueError。"""
    config_path = get_config_path()
    if not config_path.exists():
        raise ValueError(f"配置文件不存在: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError("配置文件内容格式错误：顶层必须是映射")

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


def save_config(data: dict) -> Path:
    """写入配置（保持 access_key_id / access_key_secret 的固有顺序）。"""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_key_id": str(data.get("access_key_id", "")).strip(),
        "access_key_secret": str(data.get("access_key_secret", "")).strip(),
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return config_path
