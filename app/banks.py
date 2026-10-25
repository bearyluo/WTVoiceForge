"""bank 扫描与分类。

只读操作：扫描战雷 sound 目录下的 *.assets.bank，配合同名 *.bank。
分类用于 UI 展示和后续目录映射，不代表替换粒度——替换始终以整个 bank 为单位。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

CREW_RE = re.compile(r"^_crew_dialogs_(common|ground)_([a-z0-9_]+)\.assets\.bank$", re.I)


@dataclass(frozen=True)
class GlobalBankMeta:
    label: str
    scope: str
    note: str = ""


GLOBAL_BANKS: dict[str, GlobalBankMeta] = {
    "aircraft_gui": GlobalBankMeta("座舱 VWS / 告警 / 击杀提示", "air", "VWS、RWR、锁定、导弹告警、击杀“叮”/助攻提示"),
    "aircraft_guns": GlobalBankMeta("空战武器 / 导弹", "air", "机炮、导弹发射/飞行、音爆"),
    "aircraft_effects": GlobalBankMeta("空战效果", "air"),
    "aircraft_common": GlobalBankMeta("空战通用", "air"),
    "aircraft_ambient": GlobalBankMeta("空战环境", "air"),
    "aircraft_engines": GlobalBankMeta("空战引擎", "air"),
    "tanks_effects": GlobalBankMeta("陆战效果 / 击杀镜头", "ground", "击杀镜头、命中、弹道"),
    "tanks_weapons": GlobalBankMeta("陆战武器", "ground"),
    "tanks_engines": GlobalBankMeta("陆战引擎", "ground"),
    "tanks_explosions": GlobalBankMeta("陆战爆炸", "ground"),
    "tanks_ambient": GlobalBankMeta("陆战环境", "ground"),
    "tanks_effects_radio": GlobalBankMeta("陆战电台/效果", "ground"),
    "tanks_object_crash": GlobalBankMeta("陆战碰撞", "ground"),
    "ships_weapon": GlobalBankMeta("海战武器", "naval"),
    "ships_effects": GlobalBankMeta("海战效果", "naval"),
    "ships_engines": GlobalBankMeta("海战引擎", "naval"),
    "ships_explosions": GlobalBankMeta("海战爆炸", "naval"),
    "ships_ambient": GlobalBankMeta("海战环境", "naval"),
    "infantry_weapon": GlobalBankMeta("步兵武器", "infantry"),
    "infantry_effects": GlobalBankMeta("步兵效果", "infantry"),
    "infantry_ambient": GlobalBankMeta("步兵环境", "infantry"),
    "infantry_music": GlobalBankMeta("步兵音乐", "infantry"),
    "event": GlobalBankMeta("通用事件", "common"),
    "event_tgw": GlobalBankMeta("通用事件（TGW）", "common"),
    "masterbank": GlobalBankMeta("Master Bank", "common"),
    "dialogs": GlobalBankMeta("对话", "common"),
    "football": GlobalBankMeta("足球活动", "other"),
}

LANG_LABELS: dict[str, str] = {
    "en": "英语",
    "en_us": "英语（美）",
    "en_au": "英语（澳）",
    "en_za": "英语（南非）",
    "zh": "中文",
    "de": "德语",
    "ru": "俄语",
    "fr": "法语",
    "it": "意大利语",
    "jp": "日语",
    "ko": "韩语",
    "pl": "波兰语",
    "pt": "葡萄牙语",
    "sp": "西班牙语",
    "sr": "塞尔维亚语",
    "tr": "土耳其语",
    "vi": "越南语",
    "cz": "捷克语",
    "hu": "匈牙利语",
    "th": "泰语",
    "ar": "阿拉伯语",
    "he": "希伯来语",
    "fi": "芬兰语",
    "lt": "立陶宛语",
    "nl": "荷兰语",
    "nw": "挪威语",
    "sv": "瑞典语",
    "el": "希腊语",
    "id": "印尼语",
    "gl": "加利西亚语",
    "hi": "印地语",
}

SCOPE_ORDER = {"ground": 0, "air": 1, "naval": 2, "infantry": 3, "common": 4, "other": 5}


@dataclass
class BankInfo:
    name: str
    assets_path: str
    bank_path: str
    category: str
    category_label: str
    scope: str
    language: str | None
    language_label: str | None
    size_bytes: int
    modified: str
    has_meta: bool
    label: str

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_stat(path: Path) -> tuple[int, str]:
    try:
        stat = path.stat()
        return stat.st_size, datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    except OSError:
        return 0, ""


def classify_bank(name: str) -> dict:
    match = CREW_RE.match(name + ".assets.bank")
    if match:
        kind = match.group(1).lower()
        language = match.group(2).lower()
        if language.startswith("sm_"):
            base_language = language[3:]
            lang_label = LANG_LABELS.get(base_language, base_language.upper())
            return {
                "category": "crew_variant",
                "category_label": "陆战乘员 · 备用录音",
                "scope": "ground",
                "language": language,
                "language_label": f"备用 · {lang_label}",
                "label": f"陆战乘员备用录音 · {lang_label}",
            }
        lang_label = LANG_LABELS.get(language, language.upper())
        if kind == "common":
            return {
                "category": "crew_common",
                "category_label": "陆战乘员 · 通用",
                "scope": "ground",
                "language": language,
                "language_label": lang_label,
                "label": f"陆战乘员通用 · {lang_label}",
            }
        return {
            "category": "crew_ground",
            "category_label": "陆战乘员 · 地面",
            "scope": "ground",
            "language": language,
            "language_label": lang_label,
            "label": f"陆战乘员 · {lang_label}",
        }

    meta = GLOBAL_BANKS.get(name)
    if meta:
        return {
            "category": name,
            "category_label": meta.label,
            "scope": meta.scope,
            "language": None,
            "language_label": None,
            "label": meta.label,
        }

    if name.startswith("dialogs_chat_"):
        language = name[len("dialogs_chat_") :]
        lang_label = LANG_LABELS.get(language, language.upper())
        return {
            "category": "dialogs_chat",
            "category_label": "聊天对话",
            "scope": "common",
            "language": language,
            "language_label": lang_label,
            "label": f"聊天对话 · {lang_label}",
        }

    if name.startswith("_dialogs_"):
        language = name[len("_dialogs_") :]
        lang_label = LANG_LABELS.get(language, language.upper())
        return {
            "category": "dialogs",
            "category_label": "对话",
            "scope": "common",
            "language": language,
            "language_label": lang_label,
            "label": f"对话 · {lang_label}",
        }

    return {
        "category": "other",
        "category_label": "其他",
        "scope": "other",
        "language": None,
        "language_label": None,
        "label": name,
    }


def scan_banks(sound_dir: str | Path) -> list[BankInfo]:
    root = Path(sound_dir)
    if not root.is_dir():
        return []

    result: list[BankInfo] = []
    try:
        files = sorted(root.glob("*.assets.bank"))
    except OSError:
        return []

    for path in files:
        name = path.name[: -len(".assets.bank")]
        meta = classify_bank(name)
        size, modified = _safe_stat(path)
        bank_path = path.with_name(name + ".bank")
        result.append(
            BankInfo(
                name=name,
                assets_path=str(path),
                bank_path=str(bank_path),
                category=meta["category"],
                category_label=meta["category_label"],
                scope=meta["scope"],
                language=meta["language"],
                language_label=meta["language_label"],
                size_bytes=size,
                modified=modified,
                has_meta=bank_path.is_file(),
                label=meta["label"],
            )
        )

    result.sort(key=lambda item: (SCOPE_ORDER.get(item.scope, 99), item.name.lower()))
    return result


def summarize_banks(items: Iterable[BankInfo]) -> dict:
    items = list(items)
    crew: dict[str, dict] = {}
    crew_variants: list[dict] = []
    category_counts: dict[str, int] = {}
    total_size = 0

    for item in items:
        total_size += item.size_bytes
        category_counts[item.category_label] = category_counts.get(item.category_label, 0) + 1
        if item.category in ("crew_common", "crew_ground") and item.language:
            entry = crew.setdefault(
                item.language,
                {"code": item.language, "label": item.language_label, "common": False, "ground": False},
            )
            if item.category == "crew_common":
                entry["common"] = True
            else:
                entry["ground"] = True
        elif item.category == "crew_variant":
            crew_variants.append(
                {"code": item.language, "label": item.language_label, "bank": item.name}
            )

    key_names = ["aircraft_gui", "aircraft_guns", "tanks_effects", "event"]
    by_name = {item.name: item for item in items}
    key_banks = []
    for name in key_names:
        item = by_name.get(name)
        key_banks.append(
            {
                "name": name,
                "present": item is not None,
                "label": item.label if item else GLOBAL_BANKS.get(name, GlobalBankMeta(name, "other")).label,
                "size_mb": round(item.size_bytes / 1024 / 1024, 1) if item else 0,
                "event_scope": GLOBAL_BANKS.get(name).note if name in GLOBAL_BANKS else "",
            }
        )

    return {
        "total": len(items),
        "total_size_mb": round(total_size / 1024 / 1024, 1),
        "category_counts": dict(sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "crew_languages": sorted(crew.values(), key=lambda entry: entry["label"]),
        "crew_variants": crew_variants,
        "key_banks": key_banks,
        "other_count": sum(1 for item in items if item.category == "other"),
    }


def find_bank(items: Iterable[BankInfo], name: str) -> BankInfo | None:
    for item in items:
        if item.name == name:
            return item
    return None
