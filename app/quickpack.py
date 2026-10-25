"""快速制作：从训练好的音色直接准备多语言语音包台词。

这里只负责“准备一个包项目”：
- 复制训练项目的参考音频和权重，保持源项目干净；
- 把选中的国家/语言/场景对应的 AudioTable 全部加入新项目；
- 用内置的官方中文听写初稿 + 缺口首轮译文填好每句文本；
- 给每句加人类可读的用途说明，供用户只改自己关心的部分。

合成和构建仍走现有的 /api/voice/synthesize 和 /api/voice/build。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from app import cockpit, descriptions, dynamic_bank, jobs, localized, text_catalog, voice

SCOPES = (
    {"id": "common", "label": "通用/空战/其他（common 表）"},
    {"id": "ground", "label": "陆战"},
    {"id": "naval", "label": "海战"},
    {"id": "cockpit", "label": "座舱告警"},
)

# 通用表里其实混着三类台词：乘员无线电（通用）、空勤起降/航向/告警（空战）
# 和基地/任务/战报（其他）。它们可以分开勾选，但构建时仍然落在同一张
# common bank 里；座舱告警来自另一张 aircraft_gui 事件 bank。
LINE_SCOPES = (
    {"id": "common", "label": "通用（乘员无线电）"},
    {"id": "air", "label": "空战（起降、航向、空勤告警）"},
    {"id": "other", "label": "其他（基地、任务、战报）"},
    {"id": "ground", "label": "陆战"},
    {"id": "naval", "label": "海战（舰艇、潜艇）"},
    {"id": "cockpit", "label": "座舱告警音效"},
)

# 这些 voice_message_* 虽然是同一个 common bank，但只用于空勤/空战。
_AIR_VOICE_PREFIXES = (
    "voice_message_landing_",
    "voice_message_takeoff_",
    "voice_message_gear_",
    "voice_message_flap_",
    "voice_message_bearing_",
    "voice_message_degrees_",
    "voice_message_height_",
    "voice_message_altitude_",
    "voice_message_air_",
    "voice_message_return_to_base",
    "voice_message_cover_",
    "voice_message_check_your_six",
    "voice_message_attack_enemy_base",
    "voice_message_chief_air_danger",
)

MODES = (
    {
        "id": "zh",
        "label": "中文配音",
        "note": "翻译成中文，用训练音色朗读；适合做汉化包。",
    },
    {
        "id": "original",
        "label": "原语言换声",
        "note": "保留游戏原语言和语气，只换成训练音色；不需要翻译。",
    },
)

NATIONS = (
    {
        "id": "us",
        "label": "美国",
        "language": "en_us",
        "language_label": "美式英语",
        "banks": {
            "common": ["_crew_dialogs_common_en"],
            "ground": ["_crew_dialogs_ground_en_us", "_crew_dialogs_ground_sm_us"],
            "naval": ["_crew_dialogs_naval_en_us"],
        },
    },
    {
        "id": "de",
        "label": "德国",
        "language": "de",
        "language_label": "德语",
        "banks": {
            "common": ["_crew_dialogs_common_de"],
            "ground": ["_crew_dialogs_ground_de", "_crew_dialogs_ground_sm_de"],
            "naval": ["_crew_dialogs_naval_de"],
        },
    },
    {
        "id": "ru",
        "label": "苏联",
        "language": "ru",
        "language_label": "俄语",
        "banks": {
            "common": ["_crew_dialogs_common_ru"],
            "ground": ["_crew_dialogs_ground_ru", "_crew_dialogs_ground_sm_ru"],
            "naval": ["_crew_dialogs_naval_ru"],
        },
    },
    {
        "id": "gb",
        "label": "英国",
        "language": "en",
        "language_label": "英式英语",
        "banks": {
            "common": ["_crew_dialogs_common_en"],
            "ground": ["_crew_dialogs_ground_en", "_crew_dialogs_ground_sm_uk"],
            "naval": ["_crew_dialogs_naval_en"],
        },
    },
    {
        "id": "jp",
        "label": "日本",
        "language": "jp",
        "language_label": "日语",
        "banks": {
            "common": ["_crew_dialogs_common_jp"],
            "ground": ["_crew_dialogs_ground_jp", "_crew_dialogs_ground_sm_jp"],
            "naval": ["_crew_dialogs_naval_jp"],
        },
    },
    {
        "id": "cn",
        "label": "中国",
        "language": "zh",
        "language_label": "中文",
        "banks": {
            "common": ["_crew_dialogs_common_zh"],
            "ground": ["_crew_dialogs_ground_zh", "_crew_dialogs_ground_sm_zh"],
        },
        "notes": {"naval": "游戏没有官方中文海战语音表，中文模式暂不能配中国海战"},
    },
    {
        "id": "it",
        "label": "意大利",
        "language": "it",
        "language_label": "意大利语",
        "banks": {
            "common": ["_crew_dialogs_common_it"],
            "ground": ["_crew_dialogs_ground_it"],
            "naval": ["_crew_dialogs_naval_it"],
        },
    },
    {
        "id": "fr",
        "label": "法国",
        "language": "fr",
        "language_label": "法语",
        "banks": {
            "common": ["_crew_dialogs_common_fr"],
            "ground": ["_crew_dialogs_ground_fr"],
            "naval": ["_crew_dialogs_naval_fr"],
        },
    },
    {
        "id": "se",
        "label": "瑞典",
        "language": "sv",
        "language_label": "瑞典语",
        "banks": {
            "common": ["_crew_dialogs_common_en"],
            "ground": ["_crew_dialogs_ground_sv"],
        },
    },
    {
        "id": "il",
        "label": "以色列",
        "language": "he",
        "language_label": "希伯来语",
        "banks": {
            "common": ["_crew_dialogs_common_en"],
            "ground": ["_crew_dialogs_ground_he"],
        },
    },
)

_NATION_MAP = {item["id"]: item for item in NATIONS}
_MODE_MAP = {item["id"]: item for item in MODES}
_DATA_DIR = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def _gaps() -> dict:
    try:
        return json.loads((_DATA_DIR / "gap_translations.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _overrides() -> dict:
    try:
        return json.loads((_DATA_DIR / "quick_overrides.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _naval_texts() -> dict:
    try:
        data = json.loads(
            (_DATA_DIR / "naval_translations.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    banks = data.get("banks") if isinstance(data, dict) else None
    return banks if isinstance(banks, dict) else {}


def catalog_status(data_dir: str | Path) -> dict:
    catalog = text_catalog.status(data_dir)
    gaps = _gaps()
    naval = _naval_texts()
    return {
        **catalog,
        "gap_banks": len((gaps.get("banks") or {})),
        "naval_banks": len(naval),
        "naval_streams": sum(len(rows or {}) for rows in naval.values()),
    }


def _bank_label(bank: str, kit: str | Path | None) -> str:
    if cockpit.is_cockpit_bank(bank):
        return cockpit.LABEL
    try:
        if kit and bank in localized.tables(kit):
            return localized._label(bank)
    except OSError:
        pass
    return dynamic_bank.label(bank)


def _bank_entry(
    bank: str,
    scope: str,
    kit: str | Path | None,
    *,
    nation: dict | None = None,
    game_path: str | Path | None = None,
) -> dict:
    if cockpit.is_cockpit_bank(bank):
        info = cockpit.summary(kit, game_path)
        return {
            "bank": bank,
            "nation": "cockpit",
            "nation_label": "座舱音效",
            "language": "vws",
            "language_label": "VWS 人声",
            "scope": "cockpit",
            "scope_label": "座舱告警",
            "supplemental": False,
            "official": True,
            "available": bool(info["available"]),
            "in_game": bool(cockpit.game_bank(game_path)),
            "label": cockpit.LABEL,
            "voice_names": info["voice_names"],
            "voice_streams": info["voice_streams"],
            "note": info["note"] if not info["available"] else "",
        }
    parsed = dynamic_bank.parse_bank(bank)
    if not parsed:
        raise voice.VoiceError(f"无法识别语音表：{bank}")
    _, language, supplement = parsed
    scope_label = next((item["label"] for item in SCOPES if item["id"] == scope), scope)
    if supplement:
        scope_label = f"{scope_label}补充"
    try:
        official = bool(kit and bank in localized.tables(kit))
    except OSError:
        official = False
    in_game = bool(localized.game_bank_path(game_path, bank))
    sound_dir = Path(game_path).expanduser() / "sound" if game_path else None
    game_ready = bool(sound_dir and sound_dir.is_dir())
    # 没有可读的游戏 sound 目录时无法判定，先按可用处理；
    # 真正准备时会再检查，避免误挡测试目录或临时断开的游戏路径。
    available = bool(official or in_game or not game_ready)
    entry = {
        "bank": bank,
        "nation": nation["id"] if nation else "custom",
        "nation_label": nation["label"] if nation else "自定义语音表",
        "language": language,
        "language_label": dynamic_bank.language_label(language, supplement),
        "scope": scope,
        "scope_label": scope_label,
        "supplemental": supplement,
        "official": official,
        "available": available,
        "in_game": in_game,
        "label": _bank_label(bank, kit),
    }
    if not available:
        entry["note"] = "当前游戏版本里没有这张表"
    elif scope == "common" and nation and not official and language != nation["language"]:
        entry["note"] = f"该阵营空勤沿用{entry['language_label']}语音表"
    return entry


def game_banks(game_path: str | Path | None) -> list[dict]:
    """列出游戏里实际存在的空战/陆战乘员语音表。"""
    if not game_path:
        return []
    sound = Path(game_path).expanduser() / "sound"
    result: list[dict] = []
    try:
        files = sorted(sound.glob("_crew_dialogs_*.assets.bank"))
    except OSError:
        return []
    for item in files:
        bank = item.name[: -len(".assets.bank")]
        parsed = dynamic_bank.parse_bank(bank)
        if not parsed or parsed[0] not in {"common", "ground", "naval"}:
            continue
        result.append(
            {
                "bank": bank,
                "scope": parsed[0],
                "language": parsed[1],
                "supplemental": parsed[2],
                "language_label": dynamic_bank.language_label(parsed[1], parsed[2]),
                "label": dynamic_bank.label(bank),
            }
        )
    return result


def options(
    data_dir: str | Path,
    kit: str | Path | None = None,
    game_path: str | Path | None = None,
) -> dict:
    nations = []
    for item in NATIONS:
        banks = []
        for scope in SCOPES:
            for bank in item["banks"].get(scope["id"], []):
                banks.append(
                    _bank_entry(
                        bank, scope["id"], kit, nation=item, game_path=game_path
                    )
                )
        nation = {
            "id": item["id"],
            "label": item["label"],
            "language": item["language"],
            "language_label": item["language_label"],
            "banks": banks,
            "available": any(bank["available"] for bank in banks),
        }
        if item["id"] == "cn":
            nation["note"] = "中文表直接用中文文本，不需要翻译"
            if item.get("notes", {}).get("naval"):
                nation["note"] += "；" + item["notes"]["naval"]
        nations.append(nation)
    catalog = catalog_status(data_dir)
    cockpit_info = cockpit.summary(kit, game_path)
    if cockpit_info.get("available"):
        cockpit_info["items"] = cockpit.catalog(kit, game_path)
    return {
        "nations": nations,
        "languages": game_banks(game_path),
        "modes": [dict(item) for item in MODES],
        "scopes": [dict(item) for item in SCOPES],
        "line_scopes": [dict(item) for item in LINE_SCOPES],
        "cockpit": cockpit_info,
        "catalog": catalog,
        "game_ready": bool(game_path),
        "translation_note": (
            "外国语音表会先翻译成中文，再用训练音色朗读中文；"
            "中国表直接使用中文文本，不翻译；海战使用按流名整理的首轮译法，需要听原音校对。"
            "原语言换声模式不翻译，直接换音色。"
        ),
        "note": (
            "中文文本已在本机缓存：官方中文原音听写初稿 + 缺口译文；"
            "海战使用内置首轮译法；重点句可试听原音后修改。"
            if catalog["ready"]
            else (
                "中文配音首次准备会在本机听写官方中文原音（约 3-10 分钟），之后会缓存；"
                "座舱 VWS 使用内置中文告警词，海战使用首轮译法；原语言换声也不需要听写。"
            )
        ),
    }


def selected_banks(
    nation_ids: list[str],
    scopes: list[str],
    bank_ids: list[str] | None = None,
    kit: str | Path | None = None,
    game_path: str | Path | None = None,
) -> list[dict]:
    wanted_scopes = {
        str(item).strip()
        for item in scopes
        if str(item).strip() in {"common", "ground", "naval", "cockpit"}
    }
    if not wanted_scopes:
        raise voice.VoiceError("至少选择通用/空战/其他、陆战、海战或座舱告警")
    result: list[dict] = []
    seen: set[str] = set()

    wanted_nations = [item for item in (_NATION_MAP.get(str(n).strip()) for n in nation_ids) if item]
    for nation in wanted_nations:
        for scope in ("common", "ground", "naval"):
            if scope not in wanted_scopes:
                continue
            for bank in nation["banks"].get(scope, []):
                if bank in seen:
                    continue
                entry = _bank_entry(bank, scope, kit, nation=nation, game_path=game_path)
                parsed = dynamic_bank.parse_bank(bank)
                if parsed and parsed[2] and not entry["in_game"]:
                    # 补充表只存在于游戏本体；没有对应 bank 的版本直接跳过。
                    continue
                if not entry["available"]:
                    raise voice.VoiceError(
                        f"游戏里找不到 {entry['label']} 语音表，请重新选择战雷目录"
                    )
                seen.add(bank)
                result.append(entry)

    for raw in bank_ids or []:
        bank = str(raw or "").strip()
        parsed = dynamic_bank.parse_bank(bank)
        if not parsed:
            continue
        scope = parsed[0]
        if scope not in wanted_scopes or bank in seen:
            continue
        entry = _bank_entry(bank, scope, kit, game_path=game_path)
        if not entry["available"] or (parsed[2] and not entry["in_game"]):
            raise voice.VoiceError(
                f"游戏里找不到 {entry['label']} 语音表，请重新选择战雷目录"
            )
        seen.add(bank)
        result.append(entry)

    if "cockpit" in wanted_scopes and cockpit.BANK not in seen:
        entry = _bank_entry(cockpit.BANK, "cockpit", kit, game_path=game_path)
        if not entry["available"]:
            raise voice.VoiceError(
                "官方 FMOD 工程里没有座舱 VWS 素材，请重新选择工程目录"
            )
        seen.add(cockpit.BANK)
        result.append(entry)

    if not result:
        if wanted_scopes == {"naval"}:
            raise voice.VoiceError(
                "所选国家没有海战语音表；海战可选美、德、苏、英、日、意、法"
            )
        raise voice.VoiceError("至少选择一个国家，或至少勾选一张语音表")
    return result


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def clone_voice_project(
    data_dir: str | Path,
    source_project_id: str,
    name: str,
) -> dict:
    """复制训练项目的参考音频和模型到新的包项目，不复制台词。"""
    source = voice.load_project(data_dir, source_project_id)
    source_root = voice.project_dir(data_dir, source_project_id)
    ref = source.get("reference") or {}
    if not (ref.get("prompt_text") or "").strip():
        raise voice.VoiceError("训练项目还没有参考文字，请先填写并训练")
    if not ref.get("files"):
        raise voice.VoiceError("训练项目还没有参考音频")
    model = source.get("model") or {}
    if model.get("mode") == "fine_tuned":
        if model.get("state") != "ready":
            raise voice.VoiceError("训练项目还没有完成训练")
        for key in ("sovits_path", "gpt_path"):
            if not Path(str(model.get(key) or "")).is_file():
                raise voice.VoiceError("训练权重文件不存在，请重新训练")

    created = voice.create_project(data_dir, name)
    target_root = voice.project_dir(data_dir, created["id"])
    target_ref = target_root / "reference"
    target_ref.mkdir(parents=True, exist_ok=True)
    for filename in ref.get("files", []):
        name_ = Path(str(filename)).name
        source_file = source_root / "reference" / name_
        if source_file.is_file():
            shutil.copy2(source_file, target_ref / name_)
    created["reference"] = copy.deepcopy(ref)

    if model.get("mode") == "fine_tuned":
        target_model = target_root / "train" / "model"
        target_model.mkdir(parents=True, exist_ok=True)
        copied = copy.deepcopy(model)
        for key in ("sovits_path", "gpt_path"):
            source_file = Path(str(model.get(key) or ""))
            destination = target_model / source_file.name
            _link_or_copy(source_file, destination)
            copied[key] = str(destination)
        created["model"] = copied
    else:
        created["model"] = copy.deepcopy(model) if model else {
            "mode": "zero_shot",
            "state": "not_ready",
            "detail": "",
        }
    rvc = source.get("rvc") or {}
    if rvc.get("model") and Path(str(rvc["model"])).is_file():
        target_rvc = target_root / "train" / "rvc"
        target_rvc.mkdir(parents=True, exist_ok=True)
        copied = copy.deepcopy(rvc)
        model_file = Path(str(rvc["model"]))
        destination = target_rvc / model_file.name
        _link_or_copy(model_file, destination)
        copied["model"] = str(destination)
        index_file = Path(str(rvc.get("index") or ""))
        if index_file.is_file():
            index_destination = target_rvc / index_file.name
            _link_or_copy(index_file, index_destination)
            copied["index"] = str(index_destination)
        created["rvc"] = copied
    created["quick"] = {
        "source_project": source_project_id,
        "created_at": voice._now(),
    }
    voice.save_project(data_dir, created)
    return created


def _scope_of_bank(bank: str) -> str:
    if cockpit.is_cockpit_bank(bank):
        return "cockpit"
    scope, _language = localized._split_bank(bank)
    return scope


def bank_scopes_for_lines(line_scopes) -> list[str]:
    """把“通用/空战/其他/陆战/海战/座舱”的台词范围换算成 bank 范围。"""
    wanted = {str(item) for item in (line_scopes or []) if str(item)}
    result: list[str] = []
    if wanted & {"common", "air", "other"}:
        result.append("common")
    if "ground" in wanted:
        result.append("ground")
    if "naval" in wanted:
        result.append("naval")
    if "cockpit" in wanted:
        result.append("cockpit")
    return result


def _normalize_line_scopes(scopes, line_scopes) -> set[str]:
    """line_scopes 为空时按旧的 bank 范围兼容：common 表三类全部包含。"""
    valid = {"common", "air", "other", "ground", "naval", "cockpit"}
    if line_scopes is None:
        result: set[str] = set()
        if "common" in {str(item) for item in (scopes or [])}:
            result.update({"common", "air", "other"})
        if "ground" in {str(item) for item in (scopes or [])}:
            result.add("ground")
        if "naval" in {str(item) for item in (scopes or [])}:
            result.add("naval")
        if "cockpit" in {str(item) for item in (scopes or [])}:
            result.add("cockpit")
        return result
    return {str(item) for item in (line_scopes or []) if str(item) in valid}


def _line_name(line: dict) -> str:
    source = line.get("source") or {}
    name = str(source.get("event") or "").strip()
    if name:
        return name
    target = str(line.get("target_file") or "").strip()
    return Path(target).stem if target else ""


LINE_SCOPE_ORDER = ("common", "air", "other", "ground", "naval", "cockpit")


def infer_line_scopes(project: dict) -> list[str]:
    """按 bank 和流名推断旧项目实际包含的台词范围。"""
    found: set[str] = set()
    for line in project.get("lines", []):
        source = line.get("source") or {}
        bank = str(source.get("bank") or line.get("bank") or "")
        bank_scope = _scope_of_bank(bank)
        if not bank_scope:
            continue
        found.add(_line_scope(_line_name(line), bank_scope))
    return [scope for scope in LINE_SCOPE_ORDER if scope in found]


def _line_scope(name: str, bank_scope: str) -> str:
    """判断一条流属于通用、空战、其他、陆战、海战还是座舱告警。

    陆战 bank 全是陆战；通用 bank 里的 voice_message_* 默认是通用无线电，
    其中起降/航向/高度/返航等空勤专用句归空战；base/pilot/mission_complete
    是基地、任务和战报，归其他。base_player_inZone_ground 虽然是 base_
    前缀，但只用于陆战；base_player_inZone_leave 是通用无线电。
    """
    if bank_scope in ("ground", "naval", "cockpit"):
        return bank_scope
    lowered = re.sub(r"[^a-z0-9_]+", "_", str(name or "").lower())
    if lowered.startswith("voice_message_"):
        if any(lowered.startswith(prefix) for prefix in _AIR_VOICE_PREFIXES):
            return "air"
        if re.fullmatch(r"voice_message_\d+_(?:m|ft)", lowered):
            return "air"
        return "common"
    if lowered.startswith("base_player_inzone_ground"):
        return "ground"
    if lowered.startswith("base_player_inzone_leave"):
        return "common"
    return "other"


def _row_line_scope(row: dict, bank_scope: str) -> str:
    name = str(row.get("event") or "").strip()
    if not name:
        target = str(row.get("target_file") or "").strip()
        name = Path(target).stem if target else ""
    return _line_scope(name, bank_scope)


def _is_non_voice(
    name: str, prefixes: tuple[str, ...] = (), bank: str = ""
) -> bool:
    return descriptions.describe_non_voice(name, prefixes, bank) is not None


def _fill_texts(project: dict, data_dir: str | Path) -> dict:
    catalog = text_catalog.load(data_dir).get("scopes") or {}
    gaps = _gaps().get("banks") or {}
    overrides = _overrides()
    prefixes = tuple(
        str(item) for item in (overrides.get("non_voice_prefixes") or []) if str(item)
    )
    scope_translations = overrides.get("scope_translations") or {}
    counts = {
        "asr": 0,
        "gap_translation": 0,
        "naval_first_pass": 0,
        "placeholder_translation": 0,
        "cockpit_text": 0,
        "missing": 0,
        "non_voice": 0,
    }
    missing: list[dict] = []
    for line in project.get("lines", []):
        source = line.setdefault("source", {})
        name = _line_name(line)
        bank = str(source.get("bank") or line.get("bank") or "")
        scope = _scope_of_bank(bank)
        is_cockpit = cockpit.is_cockpit_bank(bank)
        if is_cockpit:
            meta = {
                "category": "座舱告警",
                "label": f"座舱告警 · {cockpit.label(name)}",
            }
            non_voice = None
        else:
            meta = descriptions.describe_stream(name)
            non_voice = descriptions.describe_non_voice(name, prefixes, bank)
        source["category"] = meta["category"]
        source["description"] = meta["label"]
        if non_voice:
            line["text"] = ""
            line["text_source"] = "non_voice"
            line["text_note"] = non_voice["note"]
            source["category"] = non_voice["category"]
            source["description"] = non_voice["label"]
            counts["non_voice"] += 1
            continue
        gap = (gaps.get(bank) or {}).get(name)
        text = ""
        text_source = ""
        note = ""
        if is_cockpit:
            candidate = cockpit.text(name)
            if candidate:
                text = candidate
                text_source = "cockpit_text"
                note = source.get("note") or "内置 VWS 中文告警词；可改成更短、更顺口的命令后重新生成"
                counts["cockpit_text"] += 1
        elif gap and str(gap.get("text") or "").strip():
            text = str(gap["text"]).strip()
            text_source = "gap_translation"
            note = str(gap.get("review_note") or "首轮译文，建议听原音核对")
            counts["gap_translation"] += 1
        else:
            naval = _naval_texts().get(bank, {}).get(name)
            if scope == "naval" and isinstance(naval, dict) and str(naval.get("text") or "").strip():
                text = str(naval["text"]).strip()
                text_source = "naval_first_pass"
                confidence = str(naval.get("confidence") or "medium")
                note = (
                    "海战首轮译法，流名语义不够明确，必须听原音重点校对"
                    if confidence == "low"
                    else "海战首轮译法，按流名整理；建议听原音核对语气和数字读法"
                )
                counts["naval_first_pass"] += 1
            else:
                candidate = (catalog.get(scope) or {}).get(name)
                if candidate:
                    text = str(candidate).strip()
                    text_source = "asr"
                    note = "官方中文原音自动听写，可能有错字"
                    counts["asr"] += 1
                else:
                    override = (scope_translations.get(scope) or {}).get(name)
                    if override and str(override.get("text") or "").strip():
                        text = str(override["text"]).strip()
                        text_source = "placeholder_translation"
                        note = str(
                            override.get("review_note")
                            or "官方中文表是占位音；按事件名翻译，建议试听核对"
                        )
                        counts["placeholder_translation"] += 1
        if not text:
            counts["missing"] += 1
        line["text"] = text
        line["text_source"] = text_source
        line["text_note"] = note
        if not text:
            missing.append(
                {
                    "line": line.get("id", ""),
                    "name": name,
                    "bank": bank,
                    "category": meta["category"],
                    "description": meta["label"],
                }
            )
    return {"counts": counts, "missing": missing}


def _describe_only(project: dict) -> dict:
    """原语言换声不需要台词文本，只补用途说明并标记音频来源。"""
    overrides = _overrides()
    prefixes = tuple(
        str(item) for item in (overrides.get("non_voice_prefixes") or []) if str(item)
    )
    counts = {"original": 0, "non_voice": 0}
    for line in project.get("lines", []):
        source = line.setdefault("source", {})
        name = _line_name(line)
        bank = str(source.get("bank") or line.get("bank") or "")
        if cockpit.is_cockpit_bank(bank):
            meta = {
                "category": "座舱告警",
                "label": f"座舱告警 · {cockpit.label(name)}",
            }
            non_voice = None
        else:
            meta = descriptions.describe_stream(name)
            non_voice = descriptions.describe_non_voice(name, prefixes, bank)
        source["category"] = meta["category"]
        source["description"] = meta["label"]
        if non_voice:
            line["text"] = ""
            line["text_source"] = "non_voice"
            line["text_note"] = non_voice["note"]
            source["category"] = non_voice["category"]
            source["description"] = non_voice["label"]
            counts["non_voice"] += 1
        else:
            line["text"] = ""
            line["text_source"] = "original"
            if cockpit.is_cockpit_bank(bank):
                kind = str(source.get("kind") or "")
                if kind == "effect":
                    line["text_note"] = (
                        "原音是音效，不是人声；勾选后会用换声模型替换整段，不朗读中文文本"
                    )
                elif kind == "voice":
                    line["text_note"] = (
                        "座舱炮手语音换声，替换整组语气变体；不朗读中文文本"
                    )
                else:
                    assets = source.get("assets") or []
                    voice = str((assets[0] or {}).get("voice_label") or "") if assets else ""
                    line["text_note"] = (
                        f"座舱 VWS 取{voice or '原声'}原音换声，替换中/英/俄三套；不朗读中文文本"
                    )
            else:
                line["text_note"] = "原语言换声，不朗读中文文本"
            counts["original"] += 1
    return {"counts": counts, "missing": []}


def prepare(
    job,
    data_dir: str | Path,
    kit: str | Path,
    game_path: str | Path | None,
    source_project_id: str,
    nation_ids: list[str],
    scopes: list[str],
    name: str = "",
    mode: str = "zh",
    bank_ids: list[str] | None = None,
    line_scopes: list[str] | None = None,
    cockpit_events: list[str] | None = None,
) -> dict:
    """在后台任务里准备包项目；返回项目、覆盖统计和缺文本清单。"""
    if mode not in _MODE_MAP:
        raise voice.VoiceError(f"未知的成品语言：{mode}")
    allowed = _normalize_line_scopes(scopes, line_scopes)
    if not allowed:
        raise voice.VoiceError("至少选择通用、空战、其他、陆战或座舱告警")
    chosen = selected_banks(
        nation_ids, bank_scopes_for_lines(allowed), bank_ids, kit, game_path
    )
    source = voice.load_project(data_dir, source_project_id)
    if mode == "original":
        rvc_model = (source.get("rvc") or {}).get("model")
        if not rvc_model or not Path(str(rvc_model)).is_file():
            raise voice.VoiceError("原语言换声需要先训练换声模型")
    suffix = "原语言包" if mode == "original" else "多语言包"
    pack_name = name.strip() or f"{source.get('name', '音色')} · {suffix}"
    jobs.check_cancel(job)
    crew_banks = [item for item in chosen if not cockpit.is_cockpit_bank(item["bank"])]
    catalog_banks = [
        item
        for item in crew_banks
        if _scope_of_bank(item["bank"]) in ("common", "ground")
    ]
    if mode == "zh" and catalog_banks:
        job.update(
            total=len(chosen) + 2,
            current=0,
            message="检查中文台词数据（首次会在本机听写官方中文原音）",
        )
        text_catalog.ensure(job, data_dir, game_path)
        jobs.check_cancel(job)
    elif mode == "zh" and crew_banks:
        job.update(
            total=len(chosen) + 2,
            current=0,
            message="准备海战首轮中文译法（合成前请听原音校对）",
        )
    elif mode == "zh":
        job.update(total=len(chosen) + 2, current=0, message="准备座舱 VWS 中文告警词")
    else:
        job.update(total=len(chosen) + 2, current=0, message="准备原语言换声台词")
    project = clone_voice_project(data_dir, source_project_id, pack_name)
    quick = {
        "mode": mode,
        "nations": list(dict.fromkeys(item["nation"] for item in chosen)),
        "scopes": list(dict.fromkeys(item["scope"] for item in chosen)),
        "line_scopes": [
            item for item in ("common", "air", "other", "ground", "naval", "cockpit")
            if item in allowed
        ],
        "banks": [item["bank"] for item in chosen],
    }
    if cockpit_events is not None:
        quick["cockpit_events"] = sorted({str(item) for item in cockpit_events if str(item)})
    project["quick"].update(quick)
    voice.save_project(data_dir, project)
    job.step()

    added_banks = []
    for item in chosen:
        jobs.check_cancel(job)
        job.update(message=f"读取语音表：{item['label']}")
        if cockpit.is_cockpit_bank(item["bank"]):
            rows = cockpit.rows(kit, game_path)
            if cockpit_events is None:
                # 兼容旧调用：默认只准备 VWS，炮手/击毁等可选播报不主动加入。
                rows = [row for row in rows if row.get("default_selected", True)]
            else:
                wanted = {str(value) for value in cockpit_events if str(value)}
                rows = [row for row in rows if row.get("event_id") in wanted]
        else:
            try:
                rows = localized.streams(kit, item["bank"], game_path)
            except localized.LocalizedError:
                rows = dynamic_bank.game_rows(item["bank"], game_path)
        rows = [
            row
            for row in rows
            if _row_line_scope(row, item["scope"]) in allowed
        ]
        if not rows:
            job.update(message=f"跳过 {item['label']}：勾选范围内没有台词")
            job.step()
            continue
        result = voice.add_localized_lines(
            data_dir, project["id"], item["bank"], rows
        )
        added_banks.append(
            {
                **item,
                "lines": result.get("added", 0),
                "streams": len(rows),
            }
        )
        job.step()

    jobs.check_cancel(job)
    if not added_banks:
        raise voice.VoiceError("勾选范围内没有可生成的台词")
    project = voice.load_project(data_dir, project["id"])
    if added_banks:
        project["quick"]["nations"] = list(
            dict.fromkeys(item["nation"] for item in added_banks)
        )
        project["quick"]["scopes"] = list(
            dict.fromkeys(item["scope"] for item in added_banks)
        )
        project["quick"]["banks"] = [item["bank"] for item in added_banks]
    if mode == "zh":
        job.update(message="整理中文文本与用途说明")
        filled = _fill_texts(project, data_dir)
    else:
        job.update(message="整理用途说明")
        filled = _describe_only(project)
    project["quick"]["text_counts"] = filled["counts"]
    project["quick"]["missing_count"] = len(filled["missing"])
    voice.save_project(data_dir, project)
    job.step()
    return {
        "project": project,
        "banks": added_banks,
        "text_counts": filled["counts"],
        "missing": filled["missing"][:500],
        "missing_total": len(filled["missing"]),
    }
