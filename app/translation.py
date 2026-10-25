"""把乘员语音表整理成可翻译、可导入的任务。

海战没有官方中文表，只能逐句翻译后合成。语音流名里带大量数字档位
（角度/方位/深度/距离）和同一句的多个变体（v1/v2…），直接把 700 多条
流名丢给译者会重复劳动。这里做两件事：

* 数字档位抽成模板（例如“深度 {n} 米”），填一次模板覆盖几十条流；
* 同一句的 `v1/v2/...` 合并成一个翻译任务，译文共用，但每条流仍单独合成。

只根据流名生成“建议草稿”，不把草稿当成真实台词；最终译文必须听原音校对。
"""
from __future__ import annotations

import csv
import io
import re

ROLE_LABELS = {
    "commander": "指挥官",
    "officer": "军官",
    "sailor": "水兵",
    "chief": "车长",
    "driver": "驾驶员",
    "loader": "装填手",
    "gunner": "炮手",
    "radio": "无线电员",
    "other": "其他",
}
ROLE_ORDER = {
    "commander": 0,
    "officer": 1,
    "chief": 2,
    "gunner": 3,
    "driver": 4,
    "loader": 5,
    "sailor": 6,
    "radio": 7,
    "other": 9,
}

CATEGORY_ORDER = [
    "角度",
    "方位",
    "深度",
    "距离",
    "机动",
    "武器",
    "航空",
    "目标",
    "损管",
    "潜艇",
    "战况",
    "舰种",
    "其他",
]

# 流名 → 中文类别。顺序有意义：先判航空，避免 torpedo_bomber 被当成武器。
CATEGORY_KEYWORDS = [
    ("航空", ("aircraft", "bomber", "fighter", "supportplane", "attack_aircraft", "air_target")),
    ("损管", (
        "fire", "flood", "damage", "breach", "compart", "flotation", "surviv",
        "drying", "collision", "stranded", "roll_to",
    )),
    ("武器", (
        "torpedo", "salvo", "bomb", "mine", "rbu", "zrk", "missile", "load_",
        "smoke", "gun", "misfire", "ammo",
    )),
    ("目标", ("target", "contact", "sonar", "radar", "acoustic", "approaching", "on_target", "shot_")),
    ("潜艇", ("submarine", "cellar", "periscope", "ascent", "diving", "depth")),
    ("机动", (
        "move", "rudder", "straight", "left", "right", "back", "forward", "stop",
        "speed", "trim", "silence", "bearing",
    )),
    ("战况", ("battle", "we_won", "we_lost", "abandon", "alarm")),
    ("舰种", (
        "battleship", "battlecruiser", "cruiser", "destroyer", "frigate",
        "carrier", "dreadnought", "guard_ship", "boat", "ship",
    )),
]

NAME_RE = re.compile(r"^voice_message_([a-z]+)_(.+)$")

# (kind, pattern, 模板, 类别, 单位)
NUMERIC_RULES = [
    ("angle", re.compile(r"^angle_(\d+)(?:_alt|_\d+)?$"), "角度 {n}", "角度", "度"),
    ("bearing", re.compile(r"^bearing_(\d+)(?:_alt)?$"), "方位 {n:03d}", "方位", "度"),
    ("depth_m", re.compile(r"^depth_(\d+)m(?:_\d+)?$"), "深度 {n} 米", "深度", "米"),
    ("depth_ft", re.compile(r"^depth_(\d+)ft(?:_\d+)?$"), "深度 {n} 英尺", "深度", "英尺"),
    ("distance_m", re.compile(r"^(\d+)_m(?:_\d+)?$"), "距离 {n} 米", "距离", "米"),
    ("distance_yd", re.compile(r"^(\d+)_yd(?:_\d+)?$"), "距离 {n} 码", "距离", "码"),
]

# 已有官方中文原音的流名（来自陆战中文表）。音色可能和海战不一致，仅供选用。
OFFICIAL_CN_NAMES = {
    "voice_message_commander_move_backward_v1",
    "voice_message_commander_move_backward_v2",
    "voice_message_commander_move_forward_v1",
    "voice_message_commander_move_forward_v2",
    "voice_message_commander_move_stop_v1",
    "voice_message_commander_move_stop_v2",
    "voice_message_commander_target",
}

GLOSSARY = [
    ("commander", "指挥官", "voice_message_commander_*", "舰长/编队指挥语音"),
    ("officer", "军官", "voice_message_officer_*", "枪炮、声呐、损管等专业报告"),
    ("sailor", "水兵", "voice_message_sailor_*", "执行确认、复诵与损管报告"),
    ("angle", "角度", "commander_angle_10", "目标航向角，数字档位用模板"),
    ("bearing", "方位", "officer_bearing_080", "目标方位，按三位数读"),
    ("depth", "深度", "depth_100m / depth_325ft", "潜艇深度，米/英尺分开"),
    ("distance", "距离", "1000_m / 1000_yd", "距离档位，米/码分开"),
    ("abandon_ship", "弃舰", "commander_abandon_ship_v1", "全舰弃舰"),
    ("battle_alarm", "战斗警报", "commander_battle_alarm", "进入战斗"),
    ("battle_start_ship", "水面舰艇战斗开始", "commander_battle_start_ship_v1", "开战语音"),
    ("battle_start_submarine", "潜艇战斗开始", "commander_battle_start_submarine_v1", "开战语音"),
    ("torpedo", "鱼雷", "sailor_ship_torpedo_away", "发射/准备鱼雷"),
    ("salvo", "齐射", "commander_salvo_v1", "主炮齐射"),
    ("rbu", "火箭深弹", "commander_rbu_fire_v1", "反潜火箭"),
    ("zrk", "防空导弹", "commander_zrk_ready", "舰空导弹"),
    ("missile_launch_detected", "发现导弹发射", "officer_missile_launch_detected_v1", "导弹来袭告警"),
    ("rudder", "舵", "commander_rudder_left", "左/右舵、回正"),
    ("full", "满舵", "commander_rudder_left_full_v1", "满左/满右舵"),
    ("straight", "回正", "commander_rudder_straight", "舵回正"),
    ("move_forward", "前进", "commander_move_forward_v1", "车钟前进"),
    ("move_backward", "后退", "commander_move_backward_v1", "车钟后退"),
    ("move_stop", "停船", "commander_move_stop_v1", "停车/停船"),
    ("move_full_speed", "全速", "commander_move_full_speed", "全速前进"),
    ("move_slow", "慢速", "commander_move_slow", "低速"),
    ("diving", "下潜", "commander_diving_v1", "潜艇下潜"),
    ("ascent", "上浮", "commander_ascent_v1", "潜艇上浮"),
    ("periscope", "潜望镜", "commander_periscope_up", "升/降潜望镜"),
    ("silence", "静默", "commander_silence", "无线电/静音航行"),
    ("fire", "起火", "commander_fire_v1", "舰上火灾"),
    ("flood", "进水", "officer_compartment_flooded", "舱室进水"),
    ("flotation", "浮力", "officer_low_flotation", "浮力不足"),
    ("roll_to_port", "左倾", "officer_roll_to_port", "舰体横倾"),
    ("roll_to_starboard", "右倾", "officer_roll_to_starboard", "舰体横倾"),
    ("got_damage", "受损", "officer_got_damage", "舰体受损报告"),
    ("got_critical_damage", "严重受损", "officer_got_critical_damage", "关键损伤"),
    ("engine_damage", "发动机受损", "officer_got_critical_engine_damage", "动力受损"),
    ("rudder_damage", "舵机受损", "officer_got_rudder_damage", "操舵受损"),
    ("ship_under_attack", "本舰遭攻击", "officer_ship_under_attack_v1", "被命中/被攻击"),
    ("we_are_target", "我们被瞄准", "officer_we_are_target_v1", "被敌方锁定"),
    ("contact_report", "发现接触", "sailor_contact_report_v1", "声呐/雷达接触"),
    ("sonar_report", "声呐报告", "officer_sonar_report", "水下接触"),
    ("radar_report", "雷达报告", "officer_radar_report", "水面/空中接触"),
    ("acoustic_report", "水声报告", "officer_acoustic_report", "听音器报告"),
    ("shot_on_target", "命中", "officer_shot_on_target_v1", "校射：命中"),
    ("shot_over", "过远", "officer_shot_over_v1", "校射：远弹"),
    ("shot_short", "过近", "officer_shot_short_v1", "校射：近弹"),
    ("target_off", "目标丢失", "officer_target_off_v1", "失去目标"),
    ("on_target", "对准目标", "officer_on_target_v1", "瞄准完成"),
    ("air_target_destroyed", "空中目标击落", "officer_air_target_destroyed_v1", "击落飞机"),
    ("mines_ahead", "前方有水雷", "sailor_mines_ahead", "水雷告警"),
    ("torpedo_noise", "鱼雷噪声", "officer_torpedo_noise", "听到鱼雷航行声"),
    ("ship_stranded", "搁浅", "officer_ship_stranded", "舰船搁浅"),
    ("battleship", "战列舰", "commander_battleship", "舰种识别"),
    ("battlecruiser", "战列巡洋舰", "commander_battlecruiser", "舰种识别"),
    ("heavy_cruiser", "重巡洋舰", "commander_heavy_cruiser", "舰种识别"),
    ("light_cruiser", "轻巡洋舰", "commander_light_cruiser", "舰种识别"),
    ("destroyer", "驱逐舰", "commander_destroyer", "舰种识别"),
    ("frigate", "护卫舰", "commander_frigate", "舰种识别"),
    ("carrier", "航空母舰", "commander_carrier", "舰种识别"),
    ("dreadnought", "无畏舰", "commander_dreadnought", "舰种识别"),
    ("guard_ship", "警戒舰", "commander_guard_ship", "舰种识别"),
    ("boat", "艇", "commander_boat", "鱼雷艇/巡逻艇"),
    ("submarine", "潜艇", "commander_submarine", "水下目标"),
    ("aircraft", "飞机", "commander_aircraft", "空中目标"),
    ("bomber", "轰炸机", "commander_bomber", "空中目标"),
    ("fighter", "战斗机", "commander_fighter", "空中目标"),
    ("torpedo_bomber", "鱼雷机", "commander_torpedo_bomber", "空中目标"),
    ("supportPlane", "支援飞机", "commander_supportPlane_launch_v1", "水上飞机"),
    ("cellar", "弹药库", "officer_cellar_hit_v1", "弹药库命中/起火"),
    ("compartment", "舱室", "officer_compartment_flooded", "隔舱"),
    ("waterline_breach", "水线破口", "sailor_waterline_breach", "舰体破口"),
]


def _category(body: str) -> str:
    lowered = body.lower()
    for label, keywords in CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in lowered:
                return label
    return "其他"


def _role_and_body(name: str) -> tuple[str, str]:
    match = NAME_RE.match(name)
    if not match:
        return "other", name
    return match.group(1), match.group(2)


def parse_name(name: str) -> dict:
    """拆出角色、类别、数字模板和分组键。"""
    role, body = _role_and_body(name)
    for kind, pattern, template, category, unit in NUMERIC_RULES:
        match = pattern.match(body)
        if match:
            return {
                "role": role,
                "kind": kind,
                "category": category,
                "group_key": f"{role}:{kind}",
                "template": template,
                "unit": unit,
                "number": int(match.group(1)),
                "draft": template.format(n=int(match.group(1))),
            }
    zone = re.match(r"^pilot_tickets_lead_zone_(ally|enemy)_(\d+)(?:_v\d+)?$", body)
    if zone:
        team, number = zone.group(1), zone.group(2)
        return {
            "role": role,
            "kind": "pilot_zone",
            "category": "战况",
            "group_key": f"{role}:pilot_zone_{team}_{number}",
            "template": "",
            "unit": "",
            "number": None,
            "draft": "",
        }
    # `..._v1_1` / `..._v6_001` 仍是同一句的补录变体，先收回主名。
    base = re.sub(r"^(.*?_v\d+)_\d{1,3}$", r"\1", body)
    base = re.sub(r"_v\d+$", "", base)
    base = re.sub(r"v\d+$", "", base)
    # 末尾 `_1/_2` 多为重复录制；acoustic_report_000..004 是不同报告，保留。
    if (
        base != "acoustic_report"
        and not re.match(r"^(depth|distance)_\d+$", base)
        and not re.search(r"_(depth|distance)_\d+$", base)
    ):
        base = re.sub(r"_\d{1,3}$", "", base)
    if base in ("angle", "bearing"):
        base += "_report"
    return {
        "role": role,
        "kind": "phrase",
        "category": _category(body),
        "group_key": f"{role}:{base.lower()}",
        "template": "",
        "unit": "",
        "number": None,
        "draft": "",
    }


def _stream_row(name: str, meta: dict, bank: str, language: str) -> dict:
    parsed = parse_name(name)
    note = ""
    if parsed["number"] is not None:
        note = f"数字档位模板：{parsed['template']}"
    elif name in OFFICIAL_CN_NAMES:
        note = "可选用陆战中文原音（音色可能不同）"
    return {
        "task_id": "",
        "event_id": f"at:{bank}:{name}",
        "event": name,
        "bank": bank,
        "language": language,
        "source_file": f"{name}.wav",
        "source_path": "",
        "stream_index": int(meta.get("index") or 0),
        "duration_sec": round(float(meta.get("duration_sec") or 0.0), 3),
        "role": parsed["role"],
        "role_label": ROLE_LABELS.get(parsed["role"], parsed["role"]),
        "category": parsed["category"],
        "group_key": parsed["group_key"],
        "template": parsed["template"],
        "draft": parsed["draft"],
        "text": "",
        "status": "todo",
        "note": note,
    }


def build(
    sources: dict[str, dict],
    *,
    prefix: str = "T",
    scope_label: str = "",
) -> dict:
    """sources: {language: {"bank": str, "streams": [game_stream dict, ...]}}"""
    rows: list[dict] = []
    by_group: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    language_names: dict[str, set[str]] = {}
    durations: dict[str, float] = {}

    for language, source in sources.items():
        bank = str(source.get("bank") or "")
        names: set[str] = set()
        total = 0.0
        for meta in source.get("streams") or []:
            name = str(meta.get("name") or "").strip()
            # 游戏 bank 里同名流可能有多个音质/变体条目；翻译任务按流名去重。
            if not name or name in names:
                continue
            names.add(name)
            total += float(meta.get("duration_sec") or 0.0)
            row = _stream_row(name, meta, bank, language)
            rows.append(row)
            bucket = by_group.setdefault(
                row["group_key"],
                {"first": row, "names": set(), "languages": set(), "rows": []},
            )
            bucket["names"].add(name)
            bucket["languages"].add(language)
            bucket["rows"].append(row)
            entry = by_name.setdefault(
                name,
                {
                    "event": name,
                    "role": row["role"],
                    "role_label": row["role_label"],
                    "category": row["category"],
                    "template": row["template"],
                    "draft": row["draft"],
                    "languages": [],
                    "by_language": {},
                },
            )
            entry["languages"].append(language)
            entry["by_language"][language] = {
                "bank": bank,
                "index": row["stream_index"],
                "duration_sec": row["duration_sec"],
            }
        language_names[language] = names
        durations[language] = round(total, 1)

    # 按角色、类别、分组键稳定排序，生成任务编号。
    groups: list[dict] = []
    order = sorted(
        by_group.items(),
        key=lambda item: (
            ROLE_ORDER.get(item[1]["first"]["role"], 9),
            CATEGORY_ORDER.index(item[1]["first"]["category"])
            if item[1]["first"]["category"] in CATEGORY_ORDER
            else 99,
            item[0],
        ),
    )
    task_by_key: dict[str, str] = {}
    for index, (key, bucket) in enumerate(order, 1):
        task_id = f"{prefix}{index:03d}"
        first = bucket["first"]
        names = sorted(bucket["names"])
        languages = sorted(bucket["languages"])
        numeric_values = []
        for name in names:
            value = parse_name(name)["number"]
            if value is not None:
                numeric_values.append(value)
        note_bits = []
        if numeric_values:
            note_bits.append(
                f"数字档位 {min(numeric_values)}–{max(numeric_values)}，共 {len(names)} 条流；填模板即可覆盖"
            )
        elif len(names) > 1:
            note_bits.append(f"同一句 {len(names)} 个变体，译文共用；每条流仍需分别合成")
        if any(name in OFFICIAL_CN_NAMES for name in names):
            note_bits.append("其中包含可选用陆战中文原音的流")
        first_language_rows = [
            row for row in bucket["rows"] if row["language"] == languages[0]
        ]
        indexes = sorted(
            row["stream_index"] for row in first_language_rows if row["stream_index"]
        )
        group = {
            "task_id": task_id,
            "group_key": key,
            "role": first["role"],
            "role_label": first["role_label"],
            "category": first["category"],
            "variant_count": len(names),
            "languages": ",".join(languages),
            "language_count": len(languages),
            "sample_event": names[0],
            "template": first["template"],
            "draft": first["draft"],
            "text": "",
            "note": "；".join(note_bits),
            "numeric_min": min(numeric_values) if numeric_values else "",
            "numeric_max": max(numeric_values) if numeric_values else "",
            "examples": " | ".join(names[:3]),
            "stream_indexes": ",".join(str(value) for value in indexes[:6])
            + ("…" if len(indexes) > 6 else ""),
        }
        groups.append(group)
        task_by_key[key] = task_id
        for row in bucket["rows"]:
            row["task_id"] = task_id

    # 每个唯一流名一条，附带各语言 bank / 流序号 / 时长。
    name_rows: list[dict] = []
    for name, entry in sorted(by_name.items()):
        row = {
            "event": name,
            "role": entry["role_label"],
            "category": entry["category"],
            "task_id": task_by_key.get(parse_name(name)["group_key"], ""),
            "template": entry["template"],
            "draft": entry["draft"],
            "languages": ",".join(sorted(entry["languages"])),
            "language_count": len(entry["languages"]),
            "text": "",
        }
        for language in sorted(sources):
            info = entry["by_language"].get(language)
            row[f"{language}_index"] = info["index"] if info else ""
            row[f"{language}_duration"] = info["duration_sec"] if info else ""
        name_rows.append(row)

    union = set().union(*language_names.values()) if language_names else set()
    intersection = (
        set.intersection(*language_names.values()) if language_names else set()
    )
    numeric_streams = sum(
        1 for row in rows if parse_name(row["event"])["number"] is not None
    )
    return {
        "scope": scope_label,
        "languages": {
            language: {
                "bank": str(sources[language].get("bank") or ""),
                "streams": len(language_names.get(language, set())),
                "duration_sec": durations.get(language, 0.0),
            }
            for language in sources
        },
        "union": len(union),
        "intersection": len(intersection),
        "groups": groups,
        "names": name_rows,
        "rows": rows,
        "stats": {
            "streams": len(rows),
            "unique_names": len(union),
            "common_names": len(intersection),
            "groups": len(groups),
            "numeric_groups": sum(1 for group in groups if group["template"]),
            "phrase_groups": sum(1 for group in groups if not group["template"]),
            "numeric_streams": numeric_streams,
            "phrase_streams": len(rows) - numeric_streams,
        },
    }


def group_fields() -> list[str]:
    return [
        "task_id",
        "role_label",
        "category",
        "variant_count",
        "language_count",
        "languages",
        "sample_event",
        "template",
        "draft",
        "text",
        "numeric_min",
        "numeric_max",
        "note",
        "examples",
        "stream_indexes",
        "group_key",
    ]


def stream_fields() -> list[str]:
    return [
        "task_id",
        "event_id",
        "event",
        "bank",
        "language",
        "source_file",
        "source_path",
        "stream_index",
        "duration_sec",
        "role_label",
        "category",
        "draft",
        "text",
        "status",
        "note",
    ]


def name_fields(languages: list[str]) -> list[str]:
    fields = [
        "task_id",
        "event",
        "role",
        "category",
        "languages",
        "language_count",
        "template",
        "draft",
        "text",
    ]
    for language in languages:
        fields.append(f"{language}_index")
        fields.append(f"{language}_duration")
    return fields


def csv_text(rows: list[dict], fields: list[str], *, bom: bool = True) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" if bom else "") + buffer.getvalue()


def glossary_rows() -> list[dict]:
    return [
        {"term": term, "chinese": chinese, "example": example, "note": note}
        for term, chinese, example, note in GLOSSARY
    ]


def readme_text(result: dict, *, title: str = "翻译任务") -> str:
    languages = result.get("languages", {})
    stats = result.get("stats", {})
    scope = str(result.get("scope") or "本地化语音表")
    naval = "海战" in scope
    lines = [
        f"WT Voice Forge · {scope}翻译任务",
        "",
        "一、这份任务是什么",
        (
            "海战没有官方中文语音表（_crew_dialogs_naval_zh 不存在），所以逐条流都要"
            "先翻译成中文，再用 GPT-SoVITS 合成。"
            if naval
            else "这张语音表没有完整的中文原音，缺少的流要翻译后合成。"
        ),
        "这里把流名整理成可填写的任务，不是已经完成的译文。",
        "",
        "二、统计",
        f"- 语音表：{len(languages)} 张",
        f"- 流名并集：{stats.get('unique_names', 0)} 条；各语言共有：{stats.get('common_names', 0)} 条",
        f"- 翻译任务：{stats.get('groups', 0)} 个（数字模板 {stats.get('numeric_groups', 0)} 个，",
        f"  文本短句 {stats.get('phrase_groups', 0)} 个）",
        f"- 其中数字档位流 {stats.get('numeric_streams', 0)} 条，文本短句流 {stats.get('phrase_streams', 0)} 条",
        "",
    ]
    for language, info in languages.items():
        lines.append(
            f"- {language}: {info.get('streams', 0)} 条，约 {info.get('duration_sec', 0) / 60:.1f} 分钟"
        )
    sample_bank = next(
        (
            info.get("bank")
            for language, info in languages.items()
            if language in ("en", "en_us") and info.get("bank")
        ),
        next(
            (info.get("bank") for info in languages.values() if info.get("bank")),
            "_crew_dialogs_naval_en",
        ),
    )
    lines += [
        "",
        "三、怎么用",
        f"1. 先填《{title}_分组任务.csv》的 text 列。数字模板行可以只填 template，",
        "   例如“深度 {n} 米”；同一条 task_id 的 v1/v2 变体共用一份译文。",
        f"2. 逐条流名的最终文本在《{title}_流名索引.csv》和《按语言/*.csv》里。",
        "   如果分组表已填好，可以把模板套到逐条表；最终导入仍然用逐条表的 text 列。",
        "3. 在应用里：声音目录 → 新建项目 → 导入参考音频 → 选对应的本地化语音表 →",
        "   “加入这张语音表全部台词” → 逐条听“原音”、填中文 → 合成 → 构建。",
        "4. 翻译表可以按 event_id 导回项目。导入格式与项目“导出对照表”一致：",
        "   event_id,event,bank,source_file,source_path,text,status；额外列会被忽略。",
        "",
        "四、听原音的方法",
        "- 应用内：加入台词后，每行点“原音”即可听游戏里的原声。",
        "- 命令行（把路径换成自己的战雷目录）：",
        f'  vgmstream-cli -s <流序号> -o "%TEMP%\\line.wav" "D:\\SteamLibrary\\steamapps\\common\\War Thunder\\sound\\{sample_bank}.assets.bank"',
        f"- 流序号见《{title}_流名索引.csv》的 <语言>_index 列。",
        "",
        "五、注意",
        "- 自动草稿只根据流名推断，例如 angle_10 → “角度 10”，不是听写结果；",
        "  必须听原音确认语气、数字读法（方位通常读三位数）和省略成分。",
        "- 同一句有多个变体（v1/v2/...）时译文可以共用，但每条流仍需单独合成，",
        "  因为游戏会按不同流名播放不同时长/语气的版本。",
        "- 部分通用移动/目标语音在陆战中文表里有同名原音，可选择复用，但音色",
        "  可能与当前语音套不一致；本任务默认仍按“待翻译”处理。",
        "- 翻译完成后仍需在游戏内实听，确认舰种、武器和损管术语符合战雷习惯。",
        "",
        "六、文件",
        f"- {title}_分组任务.csv：按语义分组，先翻译这张",
        f"- {title}_流名索引.csv：{stats.get('unique_names', 0)} 条唯一流名，含各语言流序号/时长",
        f"- 按语言/*.csv：{len(languages)} 张表的逐条任务，可直接填 text 后导入项目",
        f"- {title}_术语表.csv：流名常见词的中文对照",
        f"- {title}_统计.json：机器可读的统计",
    ]
    return "\n".join(lines) + "\n"
