"""aircraft_gui 共享素材的构建期事件隔离。

`enemy_destr_by_plane` 与 `obj_complete` 共用同一个 AudioFile
（assetPath 都是 gui/obj_complete.wav）。当用户只重配 `enemy_destr_by_plane`
的空战击杀“叮”时，直接替换那条 AudioFile 会顺带改掉 `obj_complete` 的原音。

这里在**构建副本**里为目标事件克隆一份独立 AudioFile（确定性 uuid5 生成的
新 GUID + 隔离 assetPath），只把目标事件 XML 里的 audioFile 引用指向克隆
GUID，其他事件仍指向原 GUID；官方工程不写不改。
"""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from app import cockpit

TARGET_EVENT = cockpit.PLANE_KILL_EVENT
TARGET_ASSET = cockpit.PLANE_KILL_ASSET
ISOLATED_ASSET = cockpit.PLANE_KILL_ISOLATED_ASSET

# uuid5 命名空间固定，保证同一 (原 GUID, 隔离路径) 每轮生成相同克隆 GUID，
# 不会随构建次数增长。
_NAMESPACE = uuid.UUID("5f9c2e17-8a41-4d6b-b2f1-c0d5a6e9b7a3")


def _guid(value: str) -> str:
    return str(value or "").strip().strip("{}").lower()


def clone_audio_guid(original_guid: str) -> str:
    """由原 AudioFile GUID 确定性生成隔离素材的新 GUID。"""
    return str(uuid.uuid5(_NAMESPACE, f"{_guid(original_guid)}:{ISOLATED_ASSET}"))


def _clone_audio_file(
    kit: Path, original_guid: str, cloned_guid: str, target: Path
) -> dict:
    """克隆官方 AudioFile 元数据到隔离路径，返回新资产的 catalog 条目。"""
    source = kit / "Metadata" / "AudioFile" / f"{{{original_guid}}}.xml"
    if not source.is_file():
        raise RuntimeError(f"官方工程缺少 AudioFile 元数据：{original_guid}")
    try:
        tree = ET.parse(source)
    except ET.ParseError as exc:
        raise RuntimeError(f"官方 AudioFile 元数据无法解析：{source}") from exc
    root = tree.getroot()
    meta = {"path": ISOLATED_ASSET, "length": None, "channels": None, "rate_khz": None}
    for obj in root.iter("object"):
        if obj.get("class") != "AudioFile":
            continue
        obj.set("id", f"{{{cloned_guid}}}")
        for prop in obj.findall("property"):
            name = prop.get("name")
            value = (prop.findtext("value") or "").strip()
            if name == "assetPath":
                value = ISOLATED_ASSET
            elif name == "length":
                meta["length"] = float(value) if value else None
            elif name == "channelCount":
                meta["channels"] = int(value) if value else None
            elif name == "frequencyInKHz":
                meta["rate_khz"] = float(value) if value else None
            else:
                continue
            node = prop.find("value")
            if node is not None:
                node.text = value
    ET.indent(root, space="\t")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    meta["exists"] = True
    meta["size_bytes"] = 0
    meta["missing_metadata"] = False
    return meta


def _rewrite_event_reference(event_path: Path, original_guid: str, cloned_guid: str) -> int:
    """把事件 XML 里指向原 GUID 的 SingleSound.audioFile 改为克隆 GUID。"""
    try:
        tree = ET.parse(event_path)
    except ET.ParseError as exc:
        raise RuntimeError(f"目标事件 XML 无法解析：{event_path}") from exc
    root = tree.getroot()
    changed = 0
    for obj in root.iter("object"):
        if obj.get("class") != "SingleSound":
            continue
        relation = obj.find("relationship[@name='audioFile']")
        if relation is None:
            continue
        destination = (relation.findtext("destination") or "").strip().strip("{}").lower()
        if destination != _guid(original_guid):
            continue
        relation.find("destination").text = f"{{{cloned_guid}}}"
        changed += 1
    if not changed:
        return 0
    ET.indent(root, space="\t")
    event_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return changed


def is_target_source(source: dict) -> bool:
    """判断一条台词的来源是否是空战击杀提示音事件。"""
    source = source or {}
    event = str(source.get("event") or "").strip()
    event_id = str(source.get("event_id") or "").strip()
    return event == TARGET_EVENT or event_id == f"fx:{cockpit.BANK}:{TARGET_EVENT}"


def find_target_event(data: dict) -> tuple[dict, str]:
    """在 catalog 里定位目标事件并返回 (事件, 原 GUID)。缺失则抛错。"""
    for event in data.get("events", []):
        if event.get("bank") != cockpit.BANK or event.get("event") != TARGET_EVENT:
            continue
        guids = event.get("asset_guids") or []
        original_guid = _guid(guids[0]) if guids else ""
        if not original_guid:
            raise RuntimeError(f"{TARGET_EVENT} 事件没有可用的 AudioFile GUID")
        return event, original_guid
    raise RuntimeError(f"事件目录里缺少目标事件 {TARGET_EVENT}，无法隔离其共享素材")


def apply(
    data: dict,
    base: str | Path,
    assets_dir: str | Path,
    kit: str | Path,
    mapping: dict,
) -> dict:
    """在构建副本里为目标事件克隆独立素材，并更新本次 catalog 副本。

    只改 build/base 与本次 catalog 副本；官方工程只读。返回隔离结果元数据。
    """
    kit_path = Path(kit).expanduser().resolve()
    base_path = Path(base)
    assets = Path(assets_dir)

    event, original_guid = find_target_event(data)
    source_file = str(event.get("source_file") or "").strip()
    if not source_file:
        raise RuntimeError(f"{TARGET_EVENT} 事件缺少 source_file，无法定位事件 XML")

    event_path = base_path / "Metadata" / "Event" / source_file
    if not event_path.is_file():
        raise RuntimeError(f"构建副本缺少目标事件 XML：{source_file}")

    # 只有用户确实勾选了目标事件时才做隔离；否则保持原工程引用不变。
    if ISOLATED_ASSET not in mapping:
        return {"applied": False, "original_guid": original_guid, "cloned_guid": ""}

    original_asset = data.get("assets", {}).get(original_guid) or {}
    if str(original_asset.get("path") or "").strip() != TARGET_ASSET:
        raise RuntimeError(
            f"{TARGET_EVENT} 的源素材不是 {TARGET_ASSET}，拒绝隔离共享音效"
        )

    cloned_guid = clone_audio_guid(original_guid)
    cloned_meta = _clone_audio_file(
        kit_path, original_guid, cloned_guid,
        base_path / "Metadata" / "AudioFile" / f"{{{cloned_guid}}}.xml",
    )

    if _rewrite_event_reference(event_path, original_guid, cloned_guid) == 0:
        raise RuntimeError(
            f"{TARGET_EVENT} 事件里没找到指向原 GUID 的 SingleSound，无法隔离"
        )

    # 更新本次 catalog 副本：目标事件改用克隆 GUID，并登记克隆资产。
    data.setdefault("assets", {})[cloned_guid] = cloned_meta
    event["asset_guids"] = [cloned_guid]

    # 先把原音放进隔离 assets 目录，让后续“已存在则跳过”机制成立；
    # 替换阶段会用合成音频覆盖它。
    source_audio = kit_path / "Assets" / TARGET_ASSET
    if source_audio.is_file():
        target_audio = assets / ISOLATED_ASSET
        target_audio.parent.mkdir(parents=True, exist_ok=True)
        target_audio.write_bytes(source_audio.read_bytes())

    return {"applied": True, "original_guid": original_guid, "cloned_guid": cloned_guid}
