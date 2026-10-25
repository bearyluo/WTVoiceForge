"""旧版官方 FMOD 工程的只读兼容补丁。

2026-06-30 官方工程加入了 `enemy_kill_assist` 事件和 `gui/ui_kill_assist.wav`
素材；此前发布的工程没有它们。用旧工程重建 `aircraft_gui` 时，游戏里的
助攻提示音会整条消失。注意：助攻提示音和空战击杀提示音 `enemy_destr_by_plane`
不是一回事，两者不能混用；旧事件 `enemy_destroyed` 的实际用途未经确认。

这里只在工程确实缺少这些文件时，把官方事件元数据补进**构建目录**，音频按
流名从用户本机游戏 bank 解出。官方工程和游戏目录都不写入，也不分发游戏音频。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from app import cockpit, localized, voice

KILL_EVENT = cockpit.KILL_ASSIST_EVENT
KILL_STREAM = cockpit.KILL_ASSIST_STREAM
KILL_ASSET = cockpit.KILL_ASSIST_ASSET
KILL_EVENT_FILE = "{3ed0e256-0a6a-4de0-9232-e01486d3e535}.xml"
KILL_AUDIO_FILE = "{a85c6494-752a-4853-a498-eb87a29e4a16}.xml"

KILL_EVENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="Studio.02.02.00">
\t<object class="Event" id="{3ed0e256-0a6a-4de0-9232-e01486d3e535}">
\t\t<property name="name">
\t\t\t<value>enemy_kill_assist</value>
\t\t</property>
\t\t<property name="outputFormat">
\t\t\t<value>1</value>
\t\t</property>
\t\t<relationship name="folder">
\t\t\t<destination>{89e1a698-0cd8-4190-a5c1-93b2b2a1e316}</destination>
\t\t</relationship>
\t\t<relationship name="mixer">
\t\t\t<destination>{96b884ee-3217-4c59-908e-d92263f350ce}</destination>
\t\t</relationship>
\t\t<relationship name="masterTrack">
\t\t\t<destination>{2dbcd60e-1c78-45f3-80d1-5b6cf7e7c4cb}</destination>
\t\t</relationship>
\t\t<relationship name="mixerInput">
\t\t\t<destination>{274434fa-ea9f-4854-b2f3-ce62c9c307e8}</destination>
\t\t</relationship>
\t\t<relationship name="automatableProperties">
\t\t\t<destination>{9da8c242-a1a0-4daa-a7c9-1f990b68609f}</destination>
\t\t</relationship>
\t\t<relationship name="markerTracks">
\t\t\t<destination>{76b399ce-7759-48b3-ae28-fb54da626da4}</destination>
\t\t</relationship>
\t\t<relationship name="groupTracks">
\t\t\t<destination>{c07ce245-6847-40c8-8ccd-52d6db578474}</destination>
\t\t</relationship>
\t\t<relationship name="timeline">
\t\t\t<destination>{64e3b668-e46a-46af-8768-856bfcafeede}</destination>
\t\t</relationship>
\t\t<relationship name="banks">
\t\t\t<destination>{d5bab5d0-c653-4e3b-a5f0-e5711c067d7d}</destination>
\t\t</relationship>
\t</object>
\t<object class="EventMixer" id="{96b884ee-3217-4c59-908e-d92263f350ce}">
\t\t<relationship name="masterBus">
\t\t\t<destination>{c93fbc53-7b7d-403b-97a3-e8df9930722f}</destination>
\t\t</relationship>
\t</object>
\t<object class="MasterTrack" id="{2dbcd60e-1c78-45f3-80d1-5b6cf7e7c4cb}">
\t\t<relationship name="mixerGroup">
\t\t\t<destination>{c93fbc53-7b7d-403b-97a3-e8df9930722f}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerInput" id="{274434fa-ea9f-4854-b2f3-ce62c9c307e8}">
\t\t<relationship name="effectChain">
\t\t\t<destination>{f7937f6e-5595-4b1c-a623-2c1faea9c84f}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{22524966-dd41-4314-9b8f-8369d8bc567d}</destination>
\t\t</relationship>
\t\t<relationship name="output">
\t\t\t<destination>{2c8de983-ea41-45fa-b1bc-87cd31edc8ad}</destination>
\t\t</relationship>
\t</object>
\t<object class="EventAutomatableProperties" id="{9da8c242-a1a0-4daa-a7c9-1f990b68609f}">
\t\t<property name="maxVoices">
\t\t\t<value>1</value>
\t\t</property>
\t\t<property name="priority">
\t\t\t<value>4</value>
\t\t</property>
\t</object>
\t<object class="MarkerTrack" id="{76b399ce-7759-48b3-ae28-fb54da626da4}" />
\t<object class="GroupTrack" id="{c07ce245-6847-40c8-8ccd-52d6db578474}">
\t\t<relationship name="modules">
\t\t\t<destination>{40595c15-62f5-4686-8208-d82aa38fcb46}</destination>
\t\t</relationship>
\t\t<relationship name="mixerGroup">
\t\t\t<destination>{4106168d-5043-4976-b0ee-58964a44c4a1}</destination>
\t\t</relationship>
\t</object>
\t<object class="Timeline" id="{64e3b668-e46a-46af-8768-856bfcafeede}">
\t\t<relationship name="modules">
\t\t\t<destination>{40595c15-62f5-4686-8208-d82aa38fcb46}</destination>
\t\t</relationship>
\t</object>
\t<object class="EventMixerMaster" id="{c93fbc53-7b7d-403b-97a3-e8df9930722f}">
\t\t<relationship name="effectChain">
\t\t\t<destination>{aaaf18f7-dc80-4270-a55c-d5481797ae4a}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{60cd150d-db9a-468b-9542-9b7575ac7db1}</destination>
\t\t</relationship>
\t\t<relationship name="mixer">
\t\t\t<destination>{96b884ee-3217-4c59-908e-d92263f350ce}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusEffectChain" id="{f7937f6e-5595-4b1c-a623-2c1faea9c84f}">
\t\t<relationship name="effects">
\t\t\t<destination>{3f7dfa23-b480-4fec-8cf7-211d3d8c0629}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{22524966-dd41-4314-9b8f-8369d8bc567d}" />
\t<object class="SingleSound" id="{40595c15-62f5-4686-8208-d82aa38fcb46}">
\t\t<property name="length">
\t\t\t<value>2</value>
\t\t</property>
\t\t<relationship name="audioFile">
\t\t\t<destination>{a85c6494-752a-4853-a498-eb87a29e4a16}</destination>
\t\t</relationship>
\t</object>
\t<object class="EventMixerGroup" id="{4106168d-5043-4976-b0ee-58964a44c4a1}">
\t\t<property name="name">
\t\t\t<value>layer00</value>
\t\t</property>
\t\t<relationship name="effectChain">
\t\t\t<destination>{6b991375-5b6e-453a-a7ae-83ddce6e4425}</destination>
\t\t</relationship>
\t\t<relationship name="panner">
\t\t\t<destination>{da70d887-2e5e-4693-9d3a-cbf33545c461}</destination>
\t\t</relationship>
\t\t<relationship name="output">
\t\t\t<destination>{c93fbc53-7b7d-403b-97a3-e8df9930722f}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusEffectChain" id="{aaaf18f7-dc80-4270-a55c-d5481797ae4a}">
\t\t<relationship name="effects">
\t\t\t<destination>{66b85ef7-326f-4194-a327-1ccde94465fb}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{60cd150d-db9a-468b-9542-9b7575ac7db1}" />
\t<object class="MixerBusFader" id="{3f7dfa23-b480-4fec-8cf7-211d3d8c0629}" />
\t<object class="MixerBusEffectChain" id="{6b991375-5b6e-453a-a7ae-83ddce6e4425}">
\t\t<relationship name="effects">
\t\t\t<destination>{35219eac-3cd9-47f5-a0a1-94387088f4ab}</destination>
\t\t\t<destination>{a0bf7616-9a1a-41c8-8f04-7b4412dca7b5}</destination>
\t\t</relationship>
\t</object>
\t<object class="MixerBusPanner" id="{da70d887-2e5e-4693-9d3a-cbf33545c461}" />
\t<object class="MixerBusFader" id="{66b85ef7-326f-4194-a327-1ccde94465fb}" />
\t<object class="MixerBusFader" id="{35219eac-3cd9-47f5-a0a1-94387088f4ab}" />
\t<object class="GainEffect" id="{a0bf7616-9a1a-41c8-8f04-7b4412dca7b5}" />
</objects>
"""

KILL_AUDIO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="Studio.02.02.00">
\t<object class="AudioFile" id="{a85c6494-752a-4853-a498-eb87a29e4a16}">
\t\t<property name="assetPath">
\t\t\t<value>gui/ui_kill_assist.wav</value>
\t\t</property>
\t\t<property name="frequencyInKHz">
\t\t\t<value>48</value>
\t\t</property>
\t\t<property name="channelCount">
\t\t\t<value>2</value>
\t\t</property>
\t\t<property name="length">
\t\t\t<value>2</value>
\t\t</property>
\t\t<relationship name="masterAssetFolder">
\t\t\t<destination>{81dc2e2f-1fe8-4962-9976-ac27d7c0b961}</destination>
\t\t</relationship>
\t</object>
</objects>
"""


def missing_parts(kit: str | Path) -> list[str]:
    """返回旧版工程缺少的部分：event / audio_meta / audio。"""
    root = Path(kit)
    missing = []
    if not (root / "Metadata" / "Event" / KILL_EVENT_FILE).is_file():
        missing.append("event")
    if not (root / "Metadata" / "AudioFile" / KILL_AUDIO_FILE).is_file():
        missing.append("audio_meta")
    if not (root / "Assets" / KILL_ASSET).is_file():
        missing.append("audio")
    return missing


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _extract_audio(game_path: str | Path | None, target: Path) -> str:
    bank = localized.game_bank_path(game_path, cockpit.BANK)
    if not bank:
        return "没有找到游戏里的 aircraft_gui 语音 bank"
    try:
        streams = [
            item
            for item in localized.game_streams(bank)
            if str(item.get("name") or "") == KILL_STREAM
        ]
    except localized.LocalizedError as exc:
        return f"无法读取游戏 aircraft_gui bank：{exc}"
    if len(streams) != 1:
        return f"游戏 bank 里的 {KILL_STREAM} 流数量异常（{len(streams)} 条）"
    try:
        localized.extract_stream(bank, int(streams[0]["index"]), target)
    except localized.LocalizedError as exc:
        return str(exc)
    return ""


def restore_aircraft_gui(
    base: str | Path,
    kit: str | Path,
    assets_dir: str | Path,
    game_path: str | Path | None,
) -> dict:
    """在构建目录里补回旧版工程缺失的助攻提示音。

    返回 {"restored": [...], "error": "..."}。只改构建目录；源工程和游戏目录只读。
    """
    root = Path(kit)
    base_path = Path(base)
    assets = Path(assets_dir)
    missing = missing_parts(root)
    target_audio = assets / KILL_ASSET
    if not missing:
        # 事件目录可能还是旧的，没把这条素材算进本次构建；这里兜底放一份。
        if not target_audio.is_file():
            source = root / "Assets" / KILL_ASSET
            if source.is_file():
                target_audio.parent.mkdir(parents=True, exist_ok=True)
                target_audio.write_bytes(source.read_bytes())
        return {"restored": [], "error": ""}
    restored: list[str] = []

    if "audio" in missing:
        # 构建目录每次都会清空，旧工程每次都要从游戏 bank 解一遍。
        error = _extract_audio(game_path, target_audio)
        if error:
            return {"restored": [], "error": f"助攻提示音无法补回：{error}"}
        restored.append("audio")
    elif not target_audio.is_file():
        source = root / "Assets" / KILL_ASSET
        if source.is_file():
            target_audio.parent.mkdir(parents=True, exist_ok=True)
            target_audio.write_bytes(source.read_bytes())

    if "event" in missing:
        _write_text(base_path / "Metadata" / "Event" / KILL_EVENT_FILE, KILL_EVENT_XML)
        restored.append("event")
    if "audio_meta" in missing:
        _write_text(
            base_path / "Metadata" / "AudioFile" / KILL_AUDIO_FILE,
            KILL_AUDIO_XML,
        )
        restored.append("audio_meta")
    return {"restored": restored, "error": ""}


def adjust_kill_assist_length(base: str | Path, mapping: dict) -> int:
    """替换成语音后同步事件长度；事件目录可能还没有这条，故单独处理。"""
    item = mapping.get(KILL_ASSET)
    if not item:
        return 0
    path = Path(base) / "Metadata" / "Event" / KILL_EVENT_FILE
    if not path.is_file():
        return 0
    duration = voice.wav_duration(Path(str(item.get("audio") or "")))
    if duration <= 0:
        return 0
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return 0
    root = tree.getroot()
    target = cockpit.KILL_ASSIST_AUDIO_GUID
    changed = 0
    for obj in root.iter("object"):
        if obj.get("class") != "SingleSound":
            continue
        relation = obj.find("relationship[@name='audioFile']")
        if relation is None:
            continue
        destination = (relation.findtext("destination") or "").strip().strip("{}").lower()
        if destination != target:
            continue
        value = obj.find("property[@name='length']/value")
        if value is None:
            continue
        value.text = f"{duration:.6f}"
        changed += 1
    if changed:
        ET.indent(root, space="\t")
        path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return changed
