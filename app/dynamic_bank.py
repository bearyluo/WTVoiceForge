"""游戏里有、官方 FMOD 工程没有 AudioTable 的乘员语音表。

官方工程只为 14 张表提供了 AudioTable 元数据，但游戏本体有 61 张
空战/陆战/海战语音表。这里不给工程补素材，而是把游戏 bank 里的原始流
解到临时工程目录，补一个只有 sourceDirectory 的 AudioTable，再让 FMOD
按同一套流名重新打包。这样所有语言都能走同一条构建链。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from app import config, localized, vgmstream

DYNAMIC_ROOT = "dialogs_dynamic"
BANK_RE = re.compile(r"_crew_dialogs_(common|ground|naval)(?:_(sm))?_([a-z_]+)")
KIT_CREW_FOLDER = "{3bdf5780-3395-4ae2-b2de-c410ce32dd4d}"
SCOPE_NAMES = {"common": "通用/空勤", "ground": "陆战", "naval": "海战"}
CACHE_VERSION = 1
CACHE_KEEP = 2
CACHE_BUDGET = 4 * 1024 * 1024 * 1024
_NS = uuid.UUID("6f0f5f3e-2a0f-4f8d-9a4e-2d0b3c4a5e6f")


class DynamicBankError(RuntimeError):
    pass


def parse_bank(bank: str) -> tuple[str, str, bool] | None:
    """返回 (scope, language, 是否 ground_sm 补充表)。"""
    match = BANK_RE.fullmatch(str(bank or "").strip())
    if not match:
        return None
    return match.group(1), match.group(3), bool(match.group(2))


def is_crew_bank(bank: str) -> bool:
    return parse_bank(bank) is not None


def is_dynamic_bank(kit: str | Path, bank: str) -> bool:
    tables = localized.tables(kit)
    return is_crew_bank(bank) and bank not in tables


def language_label(language: str, supplement: bool = False) -> str:
    # 游戏里的 ground_sm_uk 是英国补充表，不是乌克兰语。
    if language == "uk" and supplement:
        return "英军英语（UK）"
    return localized.LANG_LABELS.get(language, language)


def label(bank: str) -> str:
    parsed = parse_bank(bank)
    if not parsed:
        return str(bank)
    scope, language, supplement = parsed
    scope_label = SCOPE_NAMES.get(scope, scope)
    if supplement:
        scope_label = f"{scope_label}补充"
    return f"{language_label(language, supplement)} · {scope_label}"


def source_rel(bank: str) -> str:
    return f"{DYNAMIC_ROOT}/{bank}"


def asset_rel(bank: str, index: int, name: str) -> str:
    return f"{source_rel(bank)}/{int(index)}/{name}.wav"


def _game_bank(game_path: str | Path | None, bank: str) -> Path:
    path = localized.game_bank_path(game_path, bank)
    if not path:
        raise DynamicBankError(f"游戏里找不到语音表：{bank}")
    return path


def game_rows(
    bank: str,
    game_path: str | Path | None,
) -> list[dict]:
    """按流名分组返回游戏语音表台词，保留同名多个变体。"""
    parsed = parse_bank(bank)
    if not parsed:
        raise DynamicBankError(f"不是乘员语音表：{bank}")
    scope, language, supplement = parsed
    items = localized.game_streams(_game_bank(game_path, bank))
    if not items:
        raise DynamicBankError(f"读不到游戏语音表：{bank}")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        grouped[str(item["name"])].append(item)
    result: list[dict] = []
    for name in sorted(grouped):
        variants = grouped[name]
        assets = [
            {
                "guid": "",
                "path": asset_rel(bank, item["index"], name),
                "file": f"{name}.wav",
                "duration_sec": float(item.get("duration_sec") or 0),
                "game_index": int(item["index"]),
            }
            for item in variants
        ]
        duration = max(float(item.get("duration_sec") or 0) for item in variants)
        result.append(
            {
                "event_id": f"at:{bank}:{name}",
                "event": name,
                "bank": bank,
                "label": label(bank),
                "scope": scope,
                "language": language,
                "localized": True,
                "game_only": True,
                "supplemental": supplement,
                "source_file": f"{name}.wav",
                "target_file": f"{name}.wav",
                "duration_sec": round(duration, 3),
                "assets": assets,
            }
        )
    return result


def _bank_xml(base: Path, bank: str) -> Path | None:
    root = base / "Metadata" / "Bank"
    if not root.is_dir():
        return None
    for item in root.glob("*.xml"):
        try:
            tree = ET.parse(item)
        except (ET.ParseError, OSError):
            continue
        for obj in tree.getroot().findall("object"):
            if obj.get("class") != "Bank":
                continue
            name = obj.findtext("property[@name='name']/value")
            if (name or "").strip() == bank:
                return item
    return None


def _new_xml(base: Path, bank: str) -> Path:
    folder = base / "Metadata" / "Bank"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{{{uuid.uuid5(_NS, bank)}}}.xml"
    root = ET.Element("objects", {"serializationModel": "Studio.02.02.00"})
    bank_obj = ET.SubElement(root, "object", {"class": "Bank", "id": f"{{{uuid.uuid5(_NS, bank + '/bank')}}}"})
    bank_name = ET.SubElement(bank_obj, "property", {"name": "name"})
    ET.SubElement(bank_name, "value").text = bank
    folder_rel = ET.SubElement(bank_obj, "relationship", {"name": "folder"})
    ET.SubElement(folder_rel, "destination").text = KIT_CREW_FOLDER
    _add_table(bank_obj, root, bank)
    _write(path, root)
    return path


def _add_table(bank_obj: ET.Element, root: ET.Element, bank: str) -> None:
    table_id = f"{{{uuid.uuid5(_NS, bank + '/table')}}}"
    rel = ET.SubElement(bank_obj, "relationship", {"name": "audioTable"})
    ET.SubElement(rel, "destination").text = table_id
    table = ET.SubElement(root, "object", {"class": "AudioTable", "id": table_id})
    name = ET.SubElement(table, "property", {"name": "name"})
    ET.SubElement(name, "value").text = "Audio Table"
    source = ET.SubElement(table, "property", {"name": "sourceDirectory"})
    ET.SubElement(source, "value").text = f"Assets/{source_rel(bank)}"
    include = ET.SubElement(table, "property", {"name": "includeSubDirectories"})
    ET.SubElement(include, "value").text = "true"


def _write(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="\t")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def ensure_metadata(base: str | Path, bank: str) -> str:
    """给临时 FMOD 工程补 AudioTable；返回 Assets 下的源目录。"""
    base_path = Path(base)
    path = _bank_xml(base_path, bank)
    if path is None:
        _new_xml(base_path, bank)
        return source_rel(bank)
    tree = ET.parse(path)
    root = tree.getroot()
    bank_obj = None
    for obj in root.findall("object"):
        if obj.get("class") != "Bank":
            continue
        name = obj.findtext("property[@name='name']/value")
        if (name or "").strip() == bank:
            bank_obj = obj
            break
    if bank_obj is None:
        raise DynamicBankError(f"工程里找不到语音表 bank：{bank}")
    table_id = None
    rel = bank_obj.find("relationship[@name='audioTable']")
    if rel is not None:
        table_id = (rel.findtext("destination") or "").strip()
    table = None
    if table_id:
        for obj in root.findall("object"):
            if obj.get("class") == "AudioTable" and obj.get("id") == table_id:
                table = obj
                break
    if table is None:
        if rel is not None:
            bank_obj.remove(rel)
        _add_table(bank_obj, root, bank)
    else:
        source = table.find("property[@name='sourceDirectory']/value")
        if source is None:
            source = ET.SubElement(table, "property", {"name": "sourceDirectory"})
            ET.SubElement(source, "value")
            source = source.find("value")
        source.text = f"Assets/{source_rel(bank)}"
        include = table.find("property[@name='includeSubDirectories']/value")
        if include is not None:
            include.text = "true"
    _write(path, root)
    return source_rel(bank)


def source_audio(
    bank: str,
    name: str,
    game_path: str | Path | None,
    data_dir: str | Path,
) -> Path | None:
    """从游戏 bank 取一条流的原音，缓存后供试听。"""
    path = localized.game_bank_path(game_path, bank)
    if not path:
        return None
    for item in localized.game_streams(path):
        if str(item["name"]) != str(name):
            continue
        index = int(item["index"])
        cache = (
            Path(data_dir)
            / "cache"
            / "localized"
            / bank
            / f"{index:04d}_{name}.wav"
        )
        if not cache.is_file():
            cache.parent.mkdir(parents=True, exist_ok=True)
            localized.extract_stream(path, index, cache)
        return cache
    return None


def cache_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / "cache" / "dynamic"


def _vgmstream_stamp() -> dict:
    exe = vgmstream.find()
    if not exe:
        return {}
    try:
        stat = exe.stat()
    except OSError:
        return {"path": str(exe), "version": vgmstream.VERSION}
    return {
        "path": str(exe),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "version": vgmstream.VERSION,
    }


def cache_key(game_path: str | Path | None, bank: str) -> str:
    meta = {
        "cache_version": CACHE_VERSION,
        "app_version": config.APP_VERSION,
        "bank": signature(game_path, bank),
        "vgmstream": _vgmstream_stamp(),
    }
    return hashlib.sha256(
        json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]


def _cache_ready(cache: Path, key: str) -> bool:
    if not (cache / "streams").is_dir():
        return False
    try:
        meta = json.loads((cache / ".source.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(meta, dict)
        and meta.get("cache_version") == CACHE_VERSION
        and meta.get("app_version") == config.APP_VERSION
        and meta.get("key") == key
    )


def _tree_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _prune_cache(bank_root: Path, keep: str) -> None:
    try:
        entries = [
            item
            for item in bank_root.iterdir()
            if item.is_dir() and not item.name.startswith(".") and item.name != keep
        ]
    except OSError:
        return
    entries.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    for item in entries[max(0, CACHE_KEEP - 1) :]:
        shutil.rmtree(item, ignore_errors=True)
    now = time.time()
    for item in bank_root.glob(".*"):
        if not item.is_dir():
            continue
        try:
            if now - item.stat().st_mtime > 86400:
                shutil.rmtree(item, ignore_errors=True)
        except OSError:
            continue


def _prune_budget(root: Path, keep: Path | None = None) -> None:
    try:
        entries = [item for item in root.glob("*/*") if item.is_dir()]
    except OSError:
        return
    sized = []
    total = 0
    for item in entries:
        try:
            size = _tree_bytes(item)
            total += size
            sized.append((item.stat().st_mtime, size, item))
        except OSError:
            continue
    if total <= CACHE_BUDGET:
        return
    sized.sort(key=lambda item: item[0])
    for _mtime, size, item in sized:
        if keep is not None and item == keep:
            continue
        if total <= CACHE_BUDGET:
            break
        shutil.rmtree(item, ignore_errors=True)
        total -= size


def extract_originals(
    assets_dir: str | Path,
    bank: str,
    game_path: str | Path | None,
    *,
    job=None,
    data_dir: str | Path | None = None,
) -> int:
    """把整张游戏 bank 解到构建素材目录，同名变体保留在序号子目录。

    传入 data_dir 时按 bank 大小/修改时间、应用版本和 vgmstream 版本缓存；
    同一版本再次构建直接复制缓存，保留最近两个版本。
    """
    game_bank = _game_bank(game_path, bank)
    root = Path(assets_dir) / source_rel(bank)
    rows = localized.game_streams(game_bank)
    if not rows:
        raise DynamicBankError(f"读不到游戏语音表：{bank}")
    relative_targets = {
        int(row["index"]): Path(str(int(row["index"]))) / f"{row['name']}.wav"
        for row in rows
    }

    cache: Path | None = None
    key = ""
    if data_dir:
        key = cache_key(game_path, bank)
        bank_root = cache_root(data_dir) / bank
        cache = bank_root / key
        if _cache_ready(cache, key):
            if job is not None:
                job.update(
                    message=f"复用原音缓存：{label(bank)}（{len(rows)} 条流）"
                )
            shutil.rmtree(root, ignore_errors=True)
            shutil.copytree(cache / "streams", root)
            return len(rows)
        bank_root.mkdir(parents=True, exist_ok=True)

    if cache is not None:
        assert data_dir is not None
        staging = Path(
            tempfile.mkdtemp(prefix=f".{key}-", dir=str(cache.parent))
        )
        try:
            targets = {
                index: staging / "streams" / rel
                for index, rel in relative_targets.items()
            }
            if job is not None:
                job.update(message=f"解出原音：{label(bank)}（{len(targets)} 条流）")
            localized.extract_indexes(game_bank, targets, temp_root=staging)
            meta = {
                "cache_version": CACHE_VERSION,
                "app_version": config.APP_VERSION,
                "key": key,
                "bank": bank,
                "signature": signature(game_path, bank),
                "vgmstream": _vgmstream_stamp(),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "files": len(targets),
            }
            (staging / ".source.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if cache.exists():
                shutil.rmtree(cache, ignore_errors=True)
            os.replace(staging, cache)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        shutil.copytree(cache / "streams", root)
        _prune_cache(cache.parent, key)
        _prune_budget(cache_root(data_dir), keep=cache)
        return len(rows)

    targets = {index: root / rel for index, rel in relative_targets.items()}
    if job is not None:
        job.update(message=f"解出原音：{label(bank)}（{len(targets)} 条流）")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    localized.extract_indexes(game_bank, targets, temp_root=root.parent)
    return len(targets)


def mirror_to_project(assets_dir: str | Path, base: str | Path, bank: str) -> None:
    source = Path(assets_dir) / source_rel(bank)
    target = Path(base) / "Assets" / source_rel(bank)
    shutil.rmtree(target, ignore_errors=True)
    if not source.is_dir():
        return
    shutil.copytree(source, target)


def signature(game_path: str | Path | None, bank: str) -> dict:
    path = _game_bank(game_path, bank)
    stat = path.stat()
    return {"bank": bank, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def write_signature(assets_dir: str | Path, bank: str, data: dict) -> None:
    root = Path(assets_dir) / source_rel(bank)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".source.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
