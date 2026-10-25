"""座舱告警音效（aircraft_gui）。

aircraft_gui 和乘员表不同：它是普通 FMOD 事件 bank，不是 AudioTable。
官方工程里每个 VWS 事件引用 `Assets/vws/<语音包>/<事件>.wav`，所以这里
按“事件素材路径”替换，而不是动态补 AudioTable。只重配 `vws_*` 人声；
雷达锁定、导弹发射、失速、RWR、界面提示等音效保持游戏原音。

官方工程的事件长度是固定的，直接换成长中文会被截断。构建前会按新音频
时长重写对应 SingleSound 的 length，并在每次构建前恢复官方事件 XML。
"""
from __future__ import annotations

import itertools
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app import localized, voice

BANK = "aircraft_gui"
VWS_ROOT = "vws"
LABEL = "座舱告警音效"
# 旧版官方工程缺少这条 2026-06 才加入的助攻 UI 提示音。
KILL_ASSIST_EVENT = "enemy_kill_assist"
KILL_ASSIST_STREAM = "ui_kill_assist"
KILL_ASSIST_ASSET = "gui/ui_kill_assist.wav"
KILL_ASSIST_AUDIO_GUID = "a85c6494-752a-4853-a498-eb87a29e4a16"

# 空战击杀提示音“叮”（用户已确认原音）：事件 enemy_destr_by_plane 与普通
# obj_complete 共用同一个 AudioFile (gui/obj_complete.wav)。旧事件 enemy_destroyed
# 的具体用途未经确认，不再假设它就是这声“叮”。构建时 event_override 会为目标事件
# 克隆一份独立素材，避免替换它时把 obj_complete 的原音一起改掉。
PLANE_KILL_EVENT = "enemy_destr_by_plane"
PLANE_KILL_STREAM = "obj_complete"
PLANE_KILL_ASSET = "gui/obj_complete.wav"
PLANE_KILL_ISOLATED_ASSET = "gui/wtvf_enemy_destr_by_plane.wav"
_DATA = Path(__file__).resolve().parent / "data" / "cockpit_vws_zh.json"

# 游戏里座舱人声按机种分三套：中系晓906、北约贝蒂、苏系丽塔。
# aircraft_gui 是全局 bank，安装后三套一起生效；试听和换声需要明确取哪一套。
VOICE_FAMILIES = (
    ("xiao906", "晓906（中）"),
    ("betty", "贝蒂（英）"),
    ("rita", "丽塔（俄）"),
)
VOICE_LABELS = dict(VOICE_FAMILIES)
_SOURCE_ORDER = {key: pos for pos, (key, _label) in enumerate(VOICE_FAMILIES)}

# 界面分组只影响勾选列表，不改变 FMOD 事件。
_GROUPS = (
    ("目标与方位", (
        "vws_ahead", "vws_behind", "vws_left", "vws_right",
        "vws_high", "vws_low",
    )),
    ("雷达与导弹", (
        "vws_attack_air", "vws_attack_gepard", "vws_attack_sam",
        "vws_attack_stinger", "vws_jammer", "vws_lock",
        "vws_lock_cockpit", "vws_missile", "vws_missile_launch",
    )),
    ("飞行与起降", (
        "vws_airbrake", "vws_altitude", "vws_critical_altitude",
        "vws_critical_angle", "vws_critical_speed", "vws_dangerous_close",
        "vws_dangerous_vibration", "vws_eject", "vws_gear_down",
        "vws_check_gear", "vws_over_g", "vws_pull_up",
    )),
    ("动力与系统", (
        "vws_caution", "vws_warning", "vws_critical_engine_vibration",
        "vws_critical_left_engine_vibration",
        "vws_critical_right_engine_vibration",
        "vws_decrease_engine_rpm", "vws_decrease_left_engine_rpm",
        "vws_decrease_right_engine_rpm", "vws_engine_failure",
        "vws_engine_fire", "vws_engine_fire_left", "vws_engine_fire_right",
        "vws_engine_overheat", "vws_fire", "vws_generator_failure",
        "vws_hydrosystem_failure", "vws_increase_engine_rpm",
        "vws_left_engine_failure", "vws_left_engine_overheat",
        "vws_left_generator_failure", "vws_main_gearbox_failure",
        "vws_right_engine_failure", "vws_right_engine_overheat",
        "vws_right_generator_failure", "vws_tailrotor_gearbox_failure",
        "vws_check_mech",
    )),
    ("燃油与对抗", (
        "vws_bingo", "vws_check_fuel", "vws_fuel_low",
        "vws_chaff_flare", "vws_counter", "vws_out",
    )),
)
_GROUP_BY_NAME = {
    name: group for group, names in _GROUPS for name in names
}
_GROUP_ORDER = [group for group, _names in _GROUPS] + ["其他告警"]

# 社区 VWS 包主要重配人声，RWR/锁定/导弹提示音多数用自制音效而不是 TTS。
# 这里额外开放已经确认是真人语音的炮手台词，以及玩家常改的击毁音效。
_EXTRA_GROUPS = (
    {
        "group": "炮手语音",
        "kind": "voice",
        "default_selected": False,
        "items": (
            ("gunner_injured", ("gunner_injured",)),
            ("gunner_out_of_ammo", ("gunner_out_of_ammo",)),
            ("gunner_out_of_range", ("gunner_out_of_range",)),
            (
                "gunner_reload",
                (
                    "gunner_reload_1",
                    "gunner_reload_2",
                    "gunner_reload_3",
                    "gunner_reload_multiple",
                ),
            ),
        ),
    },
    {
        "group": "击杀/助攻提示音（原为音效）",
        "kind": "effect",
        "default_selected": False,
        "items": (
            ("enemy_destroyed", ("enemy_destroyed",)),
            (KILL_ASSIST_EVENT, (KILL_ASSIST_STREAM,)),
            (PLANE_KILL_EVENT, (PLANE_KILL_STREAM,)),
        ),
    },
)
_EXTRA_DEFAULT_TEXT = {
    "gunner_injured": "炮手受伤",
    "gunner_out_of_ammo": "炮手弹药耗尽",
    "gunner_out_of_range": "目标超出射程",
    "gunner_reload": "炮手装填中",
    KILL_ASSIST_EVENT: "助攻",
    "enemy_destroyed": "击毁敌机",
    PLANE_KILL_EVENT: "击落敌机",
}
_EXTRA_NOTE = {
    "gunner_reload": "炮手装填的几条语气变体会一起替换成这一句",
    "enemy_destroyed": (
        "旧版工程的击毁效果音，具体触发场景未经确认；"
        "勾选后会整段换成这句语音，默认保留原音"
    ),
    KILL_ASSIST_EVENT: (
        "原音是拿到击杀协助时的 2 秒 UI 提示音，不是击落敌机的“叮”声；"
        "勾选后整段换成这句语音，默认保留原音"
    ),
    PLANE_KILL_EVENT: (
        "原音就是空战击落敌机时那声清脆的“叮”，已确认与 obj_complete 共用原音；"
        "勾选后会整段换成这句语音，默认保留原音"
    ),
}


def group_for(name: str) -> str:
    return _GROUP_BY_NAME.get(str(name or ""), "其他告警")


def _base_event(name: str, known: set[str]) -> str:
    """把 critical_engine_vibration (2)、jammer_v2 并回主事件。"""
    value = str(name or "")
    stripped = re.sub(r"\s*\(\d+\)\s*$", "", value)
    if stripped != value and stripped in known:
        return stripped
    match = re.match(r"^(.+?)_v\d+$", value)
    if match and match.group(1) in known:
        return match.group(1)
    return value

_CATEGORY_ORDER = (
    ("vws", "VWS 人声告警（可重配）"),
    ("radar", "雷达/锁定/导弹提示音（保留原音）"),
    ("warning", "失速/迎角/过载/机炮警告音（保留原音）"),
    ("weapon", "武器/挂载提示音（保留原音）"),
    ("gunner", "炮手语音（可选重配）"),
    ("battle", "战果/任务/战场播报（击毁可选）"),
    ("ui", "界面/菜单音效（保留原音）"),
    ("other", "其他音效（保留原音）"),
)


def is_cockpit_bank(bank: str) -> bool:
    return str(bank or "").strip() == BANK


@lru_cache(maxsize=1)
def _text_data() -> dict:
    try:
        data = json.loads(_DATA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


def text(name: str) -> str:
    item = _text_data().get(str(name or "").strip()) or {}
    return str(item.get("text") or "").strip()


def label(name: str) -> str:
    item = _text_data().get(str(name or "").strip()) or {}
    value = str(item.get("label") or "").strip()
    if value:
        return value
    cleaned = re.sub(r"^vws_", "", str(name or "").strip(), flags=re.IGNORECASE)
    return cleaned.replace("_", " ") or str(name or "VWS 告警")


def note(name: str) -> str:
    item = _text_data().get(str(name or "").strip()) or {}
    return str(item.get("note") or "").strip()


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _priority(rel: str) -> tuple:
    lowered = rel.lower()
    if "/betty/" in lowered:
        order = 0
    elif "/rita/" in lowered:
        order = 1
    elif "/xiao906/" in lowered:
        order = 2
    else:
        order = 3
    return (order, rel.lower())


def _family(rel: str) -> str:
    lowered = "/" + str(rel or "").lower().replace("\\", "/") + "/"
    for key, _label in VOICE_FAMILIES:
        if f"/{key}/" in lowered:
            return key
    return ""


def _source_order(rel: str) -> tuple:
    """试听/换声优先中文晓906，其次贝蒂英文，最后丽塔俄文。"""
    family = _family(rel)
    return (_SOURCE_ORDER.get(family, len(VOICE_FAMILIES)), rel.lower())


def _ogg_duration(path: Path) -> float:
    """读取 Ogg Vorbis 时长；官方工程的 .wav 实际是 Ogg。"""
    try:
        data = path.read_bytes()
    except OSError:
        return 0.0
    if not data.startswith(b"OggS"):
        return 0.0
    rate = 0
    marker = data.find(b"\x01vorbis")
    if marker >= 0 and marker + 16 <= len(data):
        rate = int.from_bytes(data[marker + 12 : marker + 16], "little")
    granule = 0
    start = 0
    while True:
        pos = data.find(b"OggS", start)
        if pos < 0:
            break
        if pos + 14 <= len(data):
            value = int.from_bytes(data[pos + 6 : pos + 14], "little")
            if value != 0xFFFFFFFFFFFFFFFF:
                granule = value
        start = pos + 4
    if not rate or not granule:
        return 0.0
    return granule / rate


def _duration(kit: str | Path, rel: str) -> float:
    path = Path(kit) / "Assets" / rel
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return 0.0
    if head == b"OggS":
        return round(_ogg_duration(path), 3)
    try:
        return round(voice.wav_duration(path), 3)
    except OSError:
        return 0.0


def _match_streams(
    targets: list[tuple[str, float]], streams: list[dict]
) -> dict[str, int]:
    """按素材时长给每套原声匹配游戏流序号。

    同一句可能同时存在中/英/俄三条流，游戏 bank 的排列顺序不固定，
    所以不能用“第一条”。这里按时长做一一匹配，序号才和语种对应。
    """
    entries = [(rel, float(seconds)) for rel, seconds in targets if seconds > 0]
    choices = [
        (int(item["index"]), round(float(item.get("duration_sec") or 0), 3))
        for item in streams
        if item.get("index") is not None
    ]
    if not entries or not choices:
        return {}
    if len(entries) <= len(choices) and len(choices) <= 6:
        best: tuple[float, tuple] | None = None
        for combo in itertools.permutations(choices, len(entries)):
            cost = sum(
                abs(seconds - stream_seconds)
                for (_rel, seconds), (_index, stream_seconds) in zip(entries, combo)
            )
            if best is None or cost < best[0]:
                best = (cost, combo)
        if best is not None:
            return {
                rel: index
                for (rel, _seconds), (index, _stream_seconds) in zip(entries, best[1])
            }
    used: set[int] = set()
    result: dict[str, int] = {}
    for rel, seconds in entries:
        candidates = [item for item in choices if item[0] not in used]
        if not candidates:
            break
        index, _seconds = min(candidates, key=lambda item: abs(item[1] - seconds))
        result[rel] = index
        used.add(index)
    return result


@lru_cache(maxsize=8)
def _asset_index(kit_str: str, signature: int) -> dict[str, tuple[str, ...]]:
    kit = Path(kit_str)
    root = kit / "Assets" / VWS_ROOT
    result: dict[str, list[str]] = {}
    if not root.is_dir():
        return {}
    for item in root.rglob("*.wav"):
        if not item.is_file():
            continue
        rel = item.relative_to(kit / "Assets").as_posix()
        stem = item.stem
        result.setdefault(stem, []).append(rel)
        normal = _norm(stem)
        if normal != stem:
            result.setdefault(normal, []).append(rel)
    return {
        key: tuple(sorted(set(paths), key=_priority))
        for key, paths in result.items()
    }


def asset_index(kit: str | Path) -> dict[str, tuple[str, ...]]:
    kit_path = Path(kit).expanduser().resolve()
    root = kit_path / "Assets" / VWS_ROOT
    try:
        signature = root.stat().st_mtime_ns
    except OSError:
        signature = 0
    return dict(_asset_index(str(kit_path), signature))


def _assets(name: str, index: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return index.get(str(name or "").strip()) or index.get(_norm(name)) or ()


@lru_cache(maxsize=8)
def _gui_index(kit_str: str, signature: int) -> dict[str, tuple[str, ...]]:
    """gui / world_war 下的非 VWS 音效，按文件名索引。"""
    kit = Path(kit_str)
    root = kit / "Assets" / "gui"
    result: dict[str, list[str]] = {}
    if not root.is_dir():
        return {}
    for item in root.rglob("*"):
        if not item.is_file() or item.suffix.lower() not in {".wav", ".ogg"}:
            continue
        rel = item.relative_to(kit / "Assets").as_posix()
        result.setdefault(item.stem, []).append(rel)
    return {
        key: tuple(sorted(set(paths), key=lambda rel: rel.lower()))
        for key, paths in result.items()
    }


def gui_index(kit: str | Path) -> dict[str, tuple[str, ...]]:
    kit_path = Path(kit).expanduser().resolve()
    root = kit_path / "Assets" / "gui"
    try:
        signature = root.stat().st_mtime_ns
    except OSError:
        signature = 0
    return dict(_gui_index(str(kit_path), signature))


def _effect_index(kit_str: str) -> frozenset[str]:
    """aircraft_gui 里非 VWS 音效的素材名，只用于审计工程/游戏差异。

    不缓存：官方工程补一个新增音效后，界面必须立刻从“缺失”名单里消失。
    """
    kit = Path(kit_str)
    result: set[str] = set()
    for folder in (VWS_ROOT, "gui", "fx"):
        root = kit / "Assets" / folder
        if not root.is_dir():
            continue
        for item in root.rglob("*"):
            if item.is_file() and item.suffix.lower() in {".wav", ".ogg"}:
                result.add(item.stem)
    return frozenset(result)


def game_bank(game_path: str | Path | None) -> Path | None:
    return localized.game_bank_path(game_path, BANK)


def game_names(game_path: str | Path | None) -> Counter:
    path = game_bank(game_path)
    if not path:
        return Counter()
    try:
        return Counter(item["name"] for item in localized.game_streams(path))
    except localized.LocalizedError:
        return Counter()


def primary_asset_index(assets: list[dict]) -> int:
    """没有指定语种时优先中文晓906，其次英文贝蒂、俄文丽塔。"""
    for family, _label in VOICE_FAMILIES:
        for index, asset in enumerate(assets):
            if _family(str((asset or {}).get("path") or "")) == family:
                return index
    return 0


def source_index(
    kit: str | Path,
    game_path: str | Path | None,
    name: str,
    rel: str,
) -> int | None:
    """给旧项目补算某套原声对应的游戏流序号。"""
    paths = sorted(_assets(name, asset_index(kit)), key=_source_order)
    if not paths:
        return None
    bank = game_bank(game_path)
    if not bank:
        return None
    try:
        streams = [
            item
            for item in localized.game_streams(bank)
            if str(item.get("name") or "") == str(name)
        ]
    except localized.LocalizedError:
        return None
    targets = [(path, _duration(kit, path)) for path in paths]
    return _match_streams(targets, streams).get(rel)


def source_audio(
    game_path: str | Path | None,
    name: str,
    index: int,
    data_dir: str | Path,
) -> Path | None:
    """按游戏流序号解出指定语种的原音，缓存供试听。"""
    path = game_bank(game_path)
    if not path:
        return None
    cache = (
        Path(data_dir)
        / "cache"
        / "cockpit"
        / f"{int(index):04d}_{name}.wav"
    )
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        localized.extract_stream(path, int(index), cache)
    return cache


def _kit_names(kit: str | Path) -> Counter:
    root = Path(kit) / "Assets" / VWS_ROOT
    if not root.is_dir():
        return Counter()
    return Counter(
        item.stem
        for item in root.rglob("*.wav")
        if item.is_file() and item.stem.startswith("vws_")
    )


def _category(name: str) -> str:
    lowered = str(name or "").strip().lower()
    if lowered.startswith("vws_"):
        return "vws"
    if lowered.startswith(("alarm_", "attack_angle_", "stall_warning")):
        return "warning"
    if lowered in {"critical_event_warning", "gun_overheat_low"}:
        return "warning"
    if lowered.startswith(
        (
            "radar_",
            "con_scan_",
            "spin_scan_",
            "heatseeker_",
            "contact_",
            "special_contact",
            "missile_launch",
        )
    ):
        return "radar"
    if lowered.startswith("gunner_"):
        return "gunner"
    if lowered.startswith(
        (
            "bomb_reloading",
            "out_of_bombs",
            "out_of_rockets",
        )
    ):
        return "weapon"
    if lowered.startswith(
        (
            "enemy_destroyed",
            "mission_failed",
            "misson_complete",
            "mission_complete",
            "obj_",
            "streak_",
            "wts_",
            "ww_",
            "debrf_",
        )
    ):
        return "battle"
    if lowered.startswith(
        (
            "click",
            "choose",
            "check",
            "uncheck",
            "focus",
            "menu_",
            "minimap_",
            "show_map",
            "slide_",
            "next_target",
            "pip_",
            "card_",
            "chat_",
            "roulette_",
            "chest_",
            "new_rang",
            "kick_",
            "level_load",
            "ui_",
        )
    ):
        return "ui"
    return "other"


def voice_names(kit: str | Path, game_path: str | Path | None) -> list[str]:
    index = asset_index(kit)
    names = game_names(game_path) or _kit_names(kit)
    return sorted(
        name
        for name in names
        if str(name).startswith("vws_") and _assets(name, index)
    )


def _language_label(assets: list[dict]) -> None:
    """同一行里重复出现同语种时标出变体，试听下拉不会只看到两个“丽塔”。"""
    seen: Counter = Counter()
    for asset in assets:
        family = str(asset.get("voice") or "")
        if not family:
            continue
        seen[family] += 1
        if seen[family] > 1:
            asset["voice_label"] = f"{asset['voice_label']}·变体{seen[family]}"


def rows(kit: str | Path, game_path: str | Path | None) -> list[dict]:
    """返回一种告警一行。

    - 同名的 2-3 个语气变体放在 assets 里，只生成一段并覆盖全部；
    - critical_engine_vibration (2) / jammer_v2 并回主事件；
    - 额外提供炮手语音、空战击杀提示音和助攻提示（可选，默认不勾选）。
    """
    index = asset_index(kit)
    counts = game_names(game_path)
    streams: dict[str, list[dict]] = {}
    durations: dict[str, float] = {}
    bank_path = game_bank(game_path)
    try:
        items = localized.game_streams(bank_path) if bank_path else []
    except localized.LocalizedError:
        items = []
    for item in items:
        name = str(item.get("name") or "")
        streams.setdefault(name, []).append(item)
        durations[name] = max(durations.get(name, 0.0), float(item.get("duration_sec") or 0))

    vws_names = voice_names(kit, game_path)
    known = set(vws_names)
    grouped: dict[str, list[str]] = {}
    for name in vws_names:
        grouped.setdefault(_base_event(name, known), []).append(name)

    result: list[dict] = []
    for base, names in sorted(grouped.items()):
        assets: list[dict] = []
        total_streams = 0
        max_duration = durations.get(base, 0.0)
        for name in names:
            paths = sorted(_assets(name, index), key=_source_order)
            matched = _match_streams(
                [(rel, _duration(kit, rel)) for rel in paths],
                streams.get(name, []),
            )
            for rel in paths:
                if any(item["path"] == rel for item in assets):
                    continue
                family = _family(rel)
                asset = {
                    "guid": "",
                    "path": rel,
                    "file": Path(rel).name,
                    "duration_sec": _duration(kit, rel),
                    "variant": len(assets),
                    "voice": family,
                    "voice_label": VOICE_LABELS.get(family, "原声"),
                    "event": name,
                }
                game_index = matched.get(rel)
                if game_index is not None:
                    # 原语言换声/试听从游戏 bank 解出对应语种的 PCM。
                    asset["game_index"] = game_index
                assets.append(asset)
            total_streams += int(counts.get(name) or len(paths))
            max_duration = max(max_duration, durations.get(name, 0.0))
        if not assets:
            continue
        assets.sort(key=lambda item: (_source_order(item["path"]), item["event"]))
        _language_label(assets)
        max_duration = max(
            max_duration,
            max(float(item["duration_sec"] or 0) for item in assets),
        )
        families = sorted(
            {str(item.get("voice") or "") for item in assets if item.get("voice")},
            key=lambda value: _SOURCE_ORDER.get(value, 99),
        )
        family_text = "、".join(VOICE_LABELS.get(value, value) for value in families)
        if len(families) >= 3:
            voice_note = "内置中文告警词，可改短；中/英/俄三套语音会一起替换"
        elif families:
            voice_note = (
                f"内置中文告警词，可改短；游戏里这句只有{family_text}原声，会一起替换"
            )
        else:
            voice_note = "内置中文告警词，可改短"
        result.append(
            {
                "event_id": f"fx:{BANK}:{base}",
                "event": base,
                "event_names": names,
                "bank": BANK,
                "label": f"座舱告警 · {label(base)}",
                "scope": "cockpit",
                "language": "vws",
                "localized": False,
                "game_only": False,
                "cockpit": True,
                "source_file": f"{base}.wav",
                "target_file": f"{base}.wav",
                "duration_sec": round(max_duration, 3),
                "variant_count": total_streams,
                "primary_voice": assets[0]["voice"],
                "primary_voice_label": assets[0]["voice_label"],
                "group": group_for(base),
                "kind": "vws",
                "default_selected": True,
                "text": text(base),
                "note": voice_note,
                "assets": assets,
            }
        )

    gui = gui_index(kit)
    for spec in _EXTRA_GROUPS:
        for base, names in spec["items"]:
            assets = []
            total_streams = 0
            max_duration = 0.0
            for name in names:
                if counts and name not in counts:
                    continue
                paths = gui.get(name) or ()
                first_index = int(streams[name][0]["index"]) if streams.get(name) else None
                if not paths and name == KILL_ASSIST_STREAM and name in streams:
                    # 旧版工程没有这个素材，但游戏 bank 有：先给一个虚拟条目，
                    # 构建时由 kit_compat 从游戏原音补回；试听也走 game_index。
                    assets.append(
                        {
                            "guid": KILL_ASSIST_AUDIO_GUID,
                            "path": KILL_ASSIST_ASSET,
                            "file": "ui_kill_assist.wav",
                            "duration_sec": durations.get(name, 2.0),
                            "variant": 0,
                            "voice": "",
                            "voice_label": "原声",
                            "event": name,
                            "game_index": first_index,
                            "virtual": True,
                        }
                    )
                for rel in paths:
                    if any(item["path"] == rel for item in assets):
                        continue
                    asset = {
                        "guid": "",
                        "path": rel,
                        "file": Path(rel).name,
                        "duration_sec": _duration(kit, rel),
                        "variant": len(assets),
                        "voice": "",
                        "voice_label": "原声",
                        "event": name,
                    }
                    if first_index is not None:
                        asset["game_index"] = first_index
                    assets.append(asset)
                total_streams += int(counts.get(name) or 1)
                max_duration = max(max_duration, durations.get(name, 0.0))
            if not assets:
                continue
            assets.sort(key=lambda item: item["path"].lower())
            max_duration = max(
                max_duration,
                max(float(item["duration_sec"] or 0) for item in assets),
            )
            result.append(
                {
                    "event_id": f"fx:{BANK}:{base}",
                    "event": base,
                    "event_names": list(names),
                    "bank": BANK,
                    "label": f"座舱告警 · {label(base)}",
                    "scope": "cockpit",
                    "language": "vws",
                    "localized": False,
                    "game_only": False,
                    "cockpit": True,
                    "source_file": (
                        Path(KILL_ASSIST_ASSET).name
                        if base == KILL_ASSIST_EVENT
                        else Path(PLANE_KILL_ASSET).name
                        if base == PLANE_KILL_EVENT
                        else f"{base}.wav"
                    ),
                    "target_file": (
                        Path(KILL_ASSIST_ASSET).name
                        if base == KILL_ASSIST_EVENT
                        else Path(PLANE_KILL_ISOLATED_ASSET).name
                        if base == PLANE_KILL_EVENT
                        else f"{base}.wav"
                    ),
                    "duration_sec": round(max_duration, 3),
                    "variant_count": total_streams,
                    "primary_voice": "",
                    "primary_voice_label": "原声",
                    "group": str(spec["group"]),
                    "kind": str(spec["kind"]),
                    "default_selected": bool(spec["default_selected"]),
                    "text": text(base) or _EXTRA_DEFAULT_TEXT.get(base, ""),
                    "note": note(base) or _EXTRA_NOTE.get(base, ""),
                    "assets": assets,
                }
            )
    return result


def catalog(kit: str | Path, game_path: str | Path | None) -> list[dict]:
    """给快速制作勾选列表的轻量元数据（不含素材路径）。"""
    items = []
    for row in rows(kit, game_path):
        voices = []
        seen = set()
        for asset in row.get("assets") or []:
            voice = str(asset.get("voice") or "")
            label_text = str(asset.get("voice_label") or "")
            if voice and label_text not in seen:
                seen.add(label_text)
                voices.append({"id": voice, "label": label_text})
        items.append(
            {
                "event_id": row["event_id"],
                "event": row["event"],
                "event_names": row.get("event_names") or [row["event"]],
                "label": row["label"],
                "group": row.get("group") or "其他告警",
                "kind": row.get("kind") or "vws",
                "default_selected": bool(row.get("default_selected", True)),
                "variant_count": int(row.get("variant_count") or 0),
                "text": row.get("text") or text(row["event"]),
                "note": row.get("note") or "",
                "voices": voices,
            }
        )
    return items


def summary(kit: str | Path | None, game_path: str | Path | None) -> dict:
    base = {
        "bank": BANK,
        "label": LABEL,
        "available": False,
        "total_streams": 0,
        "unique_streams": 0,
        "voice_streams": 0,
        "voice_names": 0,
        "extra_streams": 0,
        "extra_names": 0,
        "extra_groups": [],
        "preserved_streams": 0,
        "kit_missing": [],
        "kit_missing_effects": [],
        "text_missing": [],
        "categories": [],
        "families": [],
        "note": "",
    }
    if not kit:
        return base
    kit_path = Path(kit).expanduser().resolve()
    index = asset_index(kit_path)
    if not index:
        return base
    game_counts = game_names(game_path)
    counts = game_counts or _kit_names(kit_path)
    if not counts:
        return base
    unique = sorted(counts)
    bucket: dict[str, dict] = {
        key: {"id": key, "label": text_label, "names": 0, "streams": 0, "samples": []}
        for key, text_label in _CATEGORY_ORDER
    }
    voice_streams = 0
    voice_count = 0
    kit_missing: list[str] = []
    text_missing: list[str] = []
    for name in unique:
        key = _category(name)
        entry = bucket[key]
        streams = int(counts.get(name) or 1)
        entry["names"] += 1
        entry["streams"] += streams
        if len(entry["samples"]) < 8:
            entry["samples"].append(name)
        if key == "vws":
            voice_count += 1
            voice_streams += streams
            if not _assets(name, index):
                kit_missing.append(name)
            if not text(name):
                text_missing.append(name)
    total = sum(int(value) for value in counts.values())
    categories = [entry for entry in bucket.values() if entry["names"]]
    gui = gui_index(kit_path)
    extra_streams = 0
    extra_names = 0
    extra_groups = []
    for spec in _EXTRA_GROUPS:
        group_names = 0
        group_streams = 0
        for _base, names in spec["items"]:
            present = []
            for name in names:
                if game_counts and name not in game_counts:
                    continue
                # 旧版工程缺 ui_kill_assist 素材时也算可选：构建会从游戏原音补回。
                if gui.get(name) or (
                    name == KILL_ASSIST_STREAM and name in game_counts
                ):
                    present.append(name)
            if not present:
                continue
            group_names += 1
            group_streams += sum(int(counts.get(name) or 1) for name in present)
        if not group_names:
            continue
        extra_names += group_names
        extra_streams += group_streams
        extra_groups.append(
            {
                "group": str(spec["group"]),
                "kind": str(spec["kind"]),
                "default_selected": bool(spec["default_selected"]),
                "names": group_names,
                "streams": group_streams,
            }
        )
    vws_names = [name for name in unique if _category(name) == "vws"]
    families = []
    for family, family_label in VOICE_FAMILIES:
        paths = {
            rel
            for name in vws_names
            for rel in _assets(name, index)
            if _family(rel) == family
        }
        covered = sum(
            1
            for name in vws_names
            if any(_family(rel) == family for rel in _assets(name, index))
        )
        families.append(
            {
                "id": family,
                "label": family_label,
                "files": len(paths),
                "names": covered,
            }
        )
    known_effects = _effect_index(str(kit_path))
    missing_effects = sorted(set(unique) - set(known_effects))
    return {
        **base,
        "available": bool(categories),
        "total_streams": total,
        "unique_streams": len(unique),
        "voice_streams": voice_streams,
        "voice_names": voice_count,
        "extra_streams": extra_streams,
        "extra_names": extra_names,
        "extra_groups": extra_groups,
        "preserved_streams": max(0, total - voice_streams - extra_streams),
        "kit_missing": sorted(kit_missing),
        "kit_missing_effects": missing_effects,
        "text_missing": sorted(text_missing),
        "categories": categories,
        "families": families,
        "note": (
            "座舱告警分三套原声：晓906（中）、贝蒂（英）、丽塔（俄），"
            "按机种播放。aircraft_gui 是全局 bank，安装后三套会一起替换；"
            "中文模式会把三套都换成你的中文声线，没有生成或失败的行仍会保留原音。"
            "试听原音可切换三套。炮手语音、空战击杀提示音和助攻提示可选重配，默认不动；"
            "RWR、锁定、导弹提示音、失速/迎角、界面与任务音效不是人声，"
            "构建时保留原音；工程与游戏版本不一致时以官方工程为准。"
        ),
    }


def reset_event_metadata(base: str | Path, kit: str | Path, data: dict) -> int:
    """每次构建前用官方工程覆盖 aircraft_gui 事件 XML，清掉上次改的长度。"""
    kit_path = Path(kit).expanduser().resolve()
    target_root = Path(base) / "Metadata" / "Event"
    target_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for event in data.get("events", []):
        if event.get("bank") != BANK:
            continue
        name = str(event.get("source_file") or "").strip()
        if not name:
            continue
        source = kit_path / "Metadata" / "Event" / name
        if not source.is_file():
            continue
        shutil.copy2(source, target_root / name)
        copied += 1
    return copied


def _guid(value: str) -> str:
    return str(value or "").strip().strip("{}").lower()


def _new_duration(mapping: dict) -> float:
    values = []
    for item in mapping.values():
        audio = Path(str(item.get("audio") or ""))
        try:
            values.append(voice.wav_duration(audio))
        except OSError:
            values.append(0.0)
    return round(max(values or [0.0]), 6)


def adjust_event_lengths(base: str | Path, data: dict, mapping: dict) -> int:
    """把被替换素材对应的 SingleSound.length 改成新音频时长，避免被截断。"""
    assets = data.get("assets") or {}
    # VWS 和 gui/ 下的炮手/击杀“叮”/助攻提示都走同一套事件长度修正。
    replaced = {rel: item for rel, item in mapping.items() if rel}
    if not replaced:
        return 0
    target_root = Path(base) / "Metadata" / "Event"
    updated_events = 0
    for event in data.get("events", []):
        if event.get("bank") != BANK:
            continue
        guids = {
            _guid(guid)
            for guid in event.get("asset_guids", [])
            if str((assets.get(guid) or {}).get("path") or "") in replaced
        }
        if not guids:
            continue
        path = target_root / str(event.get("source_file") or "")
        if not path.is_file():
            continue
        paths = [
            str((assets.get(guid) or {}).get("path") or "")
            for guid in event.get("asset_guids", [])
            if _guid(guid) in guids
        ]
        duration = _new_duration({rel: replaced[rel] for rel in paths if rel in replaced})
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue
        root = tree.getroot()
        changed = 0
        for obj in root.iter("object"):
            if obj.get("class") != "SingleSound":
                continue
            relation = obj.find("relationship[@name='audioFile']")
            if relation is None:
                continue
            destination = (relation.findtext("destination") or "").strip()
            if _guid(destination) not in guids:
                continue
            value = obj.find("property[@name='length']/value")
            if value is None:
                continue
            value.text = f"{duration:.6f}"
            changed += 1
        if not changed:
            continue
        ET.indent(root, space="\t")
        path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        updated_events += 1
    return updated_events
