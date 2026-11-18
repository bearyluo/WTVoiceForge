"""应用配置与路径。

战雷目录、官方 FMOD 工程目录、GPT-SoVITS 运行时、数据目录。
配置保存在项目下的 data/config.json（已在 .gitignore 中排除）。
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_NAME = "WT Voice Forge"
APP_VERSION = "0.9.21"
MILESTONE = "M7.20"

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # PyInstaller 单文件模式：资源在临时解包目录，配置/数据放在 exe 旁边。
    APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    BASE_DIR = Path(sys.executable).resolve().parent
    DEFAULT_DATA_DIR = BASE_DIR / "data"
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    BASE_DIR = APP_ROOT
    DEFAULT_DATA_DIR = APP_ROOT / "data"

WEB_DIR = APP_ROOT / "web"
CONFIG_PATH = DEFAULT_DATA_DIR / "config.json"


@dataclass
class AppConfig:
    game_path: str = ""
    kit_path: str = ""
    gpt_sovits_path: str = ""
    rvc_path: str = ""
    components_dir: str = ""
    fmod_studio_path: str = ""
    data_dir: str = str(DEFAULT_DATA_DIR)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config() -> AppConfig:
    cfg = AppConfig()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return cfg

    if isinstance(raw, dict):
        for field in fields(AppConfig):
            value = raw.get(field.name)
            if isinstance(value, str):
                setattr(cfg, field.name, value.strip())
    return cfg


def save_config(cfg: AppConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n"
    CONFIG_PATH.write_text(payload, encoding="utf-8")


def update_config(**changes: Any) -> AppConfig:
    cfg = load_config()
    allowed = {field.name for field in fields(AppConfig)}
    for key, value in changes.items():
        if key in allowed and isinstance(value, str):
            setattr(cfg, key, value.strip())
    save_config(cfg)
    return cfg


def ensure_data_dir(cfg: AppConfig | None = None) -> Path:
    cfg = cfg or load_config()
    path = Path(cfg.data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path
