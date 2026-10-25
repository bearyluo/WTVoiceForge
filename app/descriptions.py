"""把游戏里的流名翻译成普通人能看懂的用途说明。

流名本身是英文标识符，例如 `voice_message_chieftain_target_destroyed_v2`。
默认界面上只显示标识符，用户不知道它对应击杀、RWR 还是灭火提示。
这里用关键词规则生成“类别 + 短说明”，并保留原始流名供高级用户核对。
规则不追求覆盖所有长句，重点是让自定义文本时能一眼看出这一句是干什么的。
"""
from __future__ import annotations

import re

# 先匹配更具体的词，避免 destroyed 之类的大词把具体类别盖掉。
_RULES: tuple[tuple[str, str, str], ...] = (
    ("target_destroyed", "击毁目标", "击杀/战果"),
    ("enemy_destroyed", "击毁敌方", "击杀/战果"),
    ("vehicle_destroyed", "击毁载具", "击杀/战果"),
    ("destroyed", "击毁", "击杀/战果"),
    ("mission_complete", "任务完成", "任务/结算"),
    ("mission_start", "任务开始", "任务/结算"),
    ("victory", "胜利", "任务/结算"),
    ("defeat", "失败", "任务/结算"),
    ("rwr", "雷达告警", "告警/RWR"),
    ("radar_warning", "雷达告警", "告警/RWR"),
    ("missile", "导弹", "告警/导弹"),
    ("launch", "发射", "告警/导弹"),
    ("threat", "威胁", "告警/RWR"),
    ("incoming", "来袭", "告警/导弹"),
    ("critical", "重创", "命中/损伤"),
    ("ricochet", "跳弹", "命中/损伤"),
    ("penetration", "击穿", "命中/损伤"),
    ("hit", "命中", "命中/损伤"),
    ("fire", "起火", "损伤/灭火"),
    ("extinguish", "灭火", "损伤/灭火"),
    ("repair", "维修", "维修/损伤"),
    ("damage", "损伤", "维修/损伤"),
    ("ammo", "弹药", "装填/弹药"),
    ("reload", "装填", "装填/弹药"),
    ("artillery", "火炮支援", "火力支援"),
    ("bracketed", "夹叉射击", "火力支援"),
    ("acknowledged", "收到", "无线电"),
    ("affirmative", "确认", "无线电"),
    ("negative", "否定", "无线电"),
    ("commander", "车长", "乘员"),
    ("gunner", "炮手", "乘员"),
    ("driver", "驾驶员", "乘员"),
    ("loader", "装填手", "乘员"),
    ("crew", "乘员", "乘员"),
    ("engine", "发动机", "发动机/仪表"),
    ("oil", "油温", "发动机/仪表"),
    ("water", "水温", "发动机/仪表"),
    ("temperature", "温度", "发动机/仪表"),
    ("gear", "起落架", "起落架/襟翼"),
    ("flap", "襟翼", "起落架/襟翼"),
    ("landing", "降落", "起落架/襟翼"),
    ("takeoff", "起飞", "起落架/襟翼"),
    ("inzone", "进入战区", "任务/结算"),
    ("leave", "离开战区", "任务/结算"),
    ("base_", "基地/任务", "任务/结算"),
    ("target", "目标", "目标/指令"),
    ("move", "移动", "目标/指令"),
    ("attack", "攻击", "目标/指令"),
    ("defend", "防守", "目标/指令"),
)

# 先把常见组合写成完整用途，避免关键词拆成“雷达告警 · 导弹 · 发射”
# 这种看不出是击杀、RWR 还是导弹告警的碎片。
_PHRASES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("rwr", "missile"), "RWR 雷达告警（导弹发射）", "告警/RWR"),
    (("rwr",), "雷达告警（RWR）", "告警/RWR"),
    (("ucav_launch",), "无人机发射", "目标/指令"),
    (("load_guided_missile",), "装填制导导弹", "装填/弹药"),
    (("load_missile",), "装填导弹", "装填/弹药"),
    (("missile", "launch"), "导弹发射告警", "告警/导弹"),
    (("missile", "incoming"), "导弹来袭告警", "告警/导弹"),
    (("target_destroyed",), "击毁目标（击杀）", "击杀/战果"),
    (("enemy_destr_by_plane",), "空战击杀提示音（击落敌机）", "击杀/战果"),
    (("enemy_destroyed",), "击毁敌方（击杀）", "击杀/战果"),
    (("base_destroyed_ally",), "我方基地被摧毁", "任务/结算"),
    (("base_destroyed_enemy",), "敌方基地被摧毁", "击杀/战果"),
    (("base_underattack",), "基地遇袭", "任务/结算"),
)

_ATTENTION_RE = re.compile(r"voice_message_attention_to_point_(\d+)_(\d+)$", re.IGNORECASE)

# 实测：中文、法文、越南文的 common 表把这 12 条流做成了同一段 1.53 秒占位提示音；
# 其他语言（英/德/俄/日等）是 12 条不同的真人语音，不能一起标成非语音。
_ATTENTION_PLACEHOLDER_LANGS = frozenset({"zh", "fr", "vi"})

# VWS 事件名只用于目录/高级编辑展示；快速制作会用 cockpit_vws_zh.json 的
# 中文标签覆盖。这里不把雷达锁定、失速等音效误标成人声。
_VWS_HINTS: tuple[tuple[str, str], ...] = (
    ("lock_cockpit", "座舱锁定告警"),
    ("missile_launch", "导弹发射告警"),
    ("engine_fire", "发动机起火告警"),
    ("engine_failure", "发动机故障告警"),
    ("engine_overheat", "发动机过热告警"),
    ("critical_altitude", "高度告警"),
    ("critical_speed", "速度告警"),
    ("critical_angle", "迎角告警"),
    ("gear_down", "起落架放下"),
    ("check_gear", "检查起落架"),
    ("check_fuel", "检查燃油"),
    ("pull_up", "拉起告警"),
    ("over_g", "过载告警"),
    ("eject", "弹射告警"),
    ("fuel_low", "燃油不足"),
    ("bingo", "燃油返航提示"),
    ("missile", "导弹告警"),
    ("lock", "锁定告警"),
    ("attack_sam", "防空导弹威胁"),
    ("attack_air", "敌机告警"),
    ("attack_gepard", "自行高炮威胁"),
    ("attack_stinger", "单兵防空导弹威胁"),
    ("chaff_flare", "释放干扰弹"),
    ("ahead", "目标在正前方"),
    ("behind", "目标在正后方"),
    ("roll_left", "向左滚转"),
    ("roll_right", "向右滚转"),
    ("left", "目标在左"),
    ("right", "目标在右"),
    ("high", "目标在高位"),
    ("low", "目标在低位"),
    ("warning", "主警告"),
    ("caution", "注意"),
)


def _bank_language(bank: str) -> str:
    lowered = re.sub(r"[^a-z0-9_]+", "_", str(bank or "").lower())
    match = re.search(
        r"_crew_dialogs_(?:common|ground|naval)_(?:sm_)?([a-z]{2})(?:_[a-z]{2})?$",
        lowered,
    )
    if match:
        return match.group(1)
    parts = [part for part in lowered.split("_") if part]
    return parts[-1][:2] if parts else ""


def _raw_label(name: str) -> str:
    raw = str(name or "").strip()
    cleaned = re.sub(r"^voice_message_", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"^base_", "", cleaned, flags=re.IGNORECASE)
    return cleaned or raw or "未命名语音"


def describe_non_voice(
    name: str, prefixes: tuple[str, ...] = (), bank: str = ""
) -> dict | None:
    """识别已知非人声音效，返回类别、用途和保留原音说明。

    只对能确认的流名给出具体名称；确认不了的不硬猜成击杀/RWR/导弹。
    同名流在不同语言里可能是语音，必须结合 bank 判断。
    """
    raw = str(name or "").strip()
    if not raw:
        return None
    lowered = re.sub(r"[^a-z0-9_]+", "_", raw.lower())
    if str(bank or "").strip() == "aircraft_gui" and not lowered.startswith("vws_"):
        meta = describe_stream(raw)
        return {
            "category": "音效/座舱",
            "label": f"座舱音效（{meta['label']}）· 保留原音",
            "note": "雷达、锁定、失速、界面或战报音效，不是 VWS 人声；构建时保留游戏原音。",
        }
    if lowered == "mission_complete_fx" or (
        "mission_complete" in lowered and lowered.endswith("_fx")
    ):
        return {
            "category": "音效/任务完成",
            "label": "任务完成音效（结算提示）· 保留原音",
            "note": "非人声的任务完成音效，不是击杀语音；构建时保留游戏原音。",
        }
    match = _ATTENTION_RE.fullmatch(lowered)
    if match:
        if _bank_language(bank) not in _ATTENTION_PLACEHOLDER_LANGS:
            return None
        point = int(match.group(1))
        variant = int(match.group(2))
        return {
            "category": "音效/目标点提示",
            "label": f"目标点提示音（{point} 号点 · 变体 {variant}）· 保留原音",
            "note": "中文/法文/越南文表里的占位提示音，不是人声；构建时保留游戏原音。",
        }
    if any(lowered.startswith(str(prefix).lower()) for prefix in prefixes if str(prefix)):
        return {
            "category": "音效/非语音",
            "label": f"非语音提示音（{raw}）· 保留原音",
            "note": "已标记为非语音，构建时保留游戏原音。",
        }
    if lowered.endswith("_fx") or "_fx_" in lowered:
        base = re.sub(r"_fx(?:_|$)", "", lowered).strip("_")
        meta = describe_stream(base) if base else {"label": "", "category": "其他"}
        label = meta.get("label") or raw
        return {
            "category": "音效/非语音",
            "label": f"{label}音效 · 保留原音",
            "note": "非人声音效，构建时保留游戏原音。",
        }
    if "music" in lowered:
        return {
            "category": "音效/音乐",
            "label": "音乐/配乐 · 保留原音",
            "note": "非人声音乐，构建时保留游戏原音。",
        }
    if "ambient" in lowered:
        return {
            "category": "音效/环境音",
            "label": "环境音 · 保留原音",
            "note": "非人声环境音，构建时保留游戏原音。",
        }
    return None


def _variant(text: str) -> str:
    match = re.search(r"_v(\d+)$", text, re.IGNORECASE)
    if match:
        return f"版本 {match.group(1)}"
    match = re.search(r"_(\d+)_(\d+)$", text)
    if match and any(
        token in text
        for token in ("reloading", "loading", "repair", "extinguish", "attention")
    ):
        # 游戏里同一句会录 2-3 个语气变体，例如
        # voice_message_reloading_0_2 / _0_3；文本相同但流名不同。
        return f"录音变体 {match.group(1)}-{match.group(2)}"
    return ""


def _vws_label(raw: str) -> str:
    lowered = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").lower())
    for token, label in _VWS_HINTS:
        if token in lowered:
            return label
    return _raw_label(raw)


def describe_stream(name: str) -> dict:
    """返回 {'category': ..., 'label': ...}，未知流名保留原文。"""
    raw = str(name or "").strip()
    lowered = re.sub(r"[^a-z0-9_]+", "_", raw.lower())
    variant = _variant(lowered)
    if lowered.startswith("vws_"):
        labels = [_vws_label(raw)]
        if "critical_engine_vibration" in lowered:
            labels = ["发动机异常振动"]
        if variant:
            labels.append(variant)
        return {"category": "座舱告警", "label": " · ".join(labels)[:120]}
    attention = _ATTENTION_RE.fullmatch(lowered)
    if attention:
        label = (
            f"目标点提示语音（{int(attention.group(1))} 号点 · "
            f"变体 {int(attention.group(2))}）"
        )
        if variant:
            label += f" · {variant}"
        return {"category": "目标/指令", "label": label[:120]}
    for tokens, label, group in _PHRASES:
        if all(token in lowered for token in tokens):
            labels = [label]
            if variant:
                labels.append(variant)
            return {"category": group, "label": " · ".join(labels)[:120]}
    labels: list[str] = []
    category = "其他"
    for token, label, group in _RULES:
        if token not in lowered:
            continue
        if category == "其他":
            category = group
        if label not in labels:
            labels.append(label)
    if variant:
        labels.append(variant)
    if not labels:
        # 没有命中规则时，去掉常见前缀，至少让用户看到原始标识。
        labels = [_raw_label(raw)]
    return {"category": category, "label": " · ".join(labels)[:120]}
