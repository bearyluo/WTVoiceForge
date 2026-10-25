"""官方 FMOD 工程目录解析。

M0 负责 Event → Bank 的关系；M1 增加完整的 Event → AudioFile → Assets 映射、
时长、磁盘缓存和分页查询。旧 build_catalog() 保持原行为，供旧接口兼容。
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app import banks

NAME_RE = re.compile(r'<property name="name">\s*<value>(.*?)</value>', re.S)
BANK_REL_RE = re.compile(
    r'<relationship name="banks">\s*<destination>\{([^}]+)\}</destination>', re.S
)

CATALOG_SCHEMA = 2
AUDIO_REF_RE = re.compile(
    r'<relationship name="audioFile">\s*<destination>\{([^}]+)\}</destination>', re.S
)
ASSET_PATH_RE = re.compile(r'<property name="assetPath">\s*<value>(.*?)</value>', re.S)
LENGTH_RE = re.compile(r'<property name="length">\s*<value>([0-9.]+)</value>', re.S)
CHANNEL_RE = re.compile(r'<property name="channelCount">\s*<value>([0-9]+)</value>', re.S)
RATE_RE = re.compile(r'<property name="frequencyInKHz">\s*<value>([0-9.]+)</value>', re.S)

Progress = Callable[[str, int, int | None, str], None]


@dataclass
class CatalogEvent:
    event: str
    bank: str
    bank_category: str
    bank_label: str
    source_file: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CatalogBank:
    name: str
    event_count: int
    category: str
    label: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Catalog:
    kit_path: str
    fspro: str
    generated_at: str
    bank_count: int
    event_count: int
    returned_events: int
    banks: list[CatalogBank] = field(default_factory=list)
    events: list[CatalogEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    asset_mapping: bool = False
    unassigned_event_count: int = 0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["truncated"] = self.returned_events < self.event_count
        return payload


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_bank_names(kit_path: str | Path) -> dict[str, str]:
    root = Path(kit_path)
    bank_dir = root / "Metadata" / "Bank"
    names: dict[str, str] = {}
    if not bank_dir.is_dir():
        return names
    for path in sorted(bank_dir.glob("*.xml")):
        text = _read(path)
        match = NAME_RE.search(text)
        if not match:
            continue
        guid = path.stem.strip("{}")
        names[guid] = html.unescape(match.group(1).strip())
    return names


def parse_events(kit_path: str | Path, bank_names: dict[str, str]) -> list[CatalogEvent]:
    root = Path(kit_path)
    event_dir = root / "Metadata" / "Event"
    events: list[CatalogEvent] = []
    if not event_dir.is_dir():
        return events

    for path in sorted(event_dir.glob("*.xml")):
        text = _read(path)
        match = NAME_RE.search(text)
        if not match:
            continue
        event_name = html.unescape(match.group(1).strip())
        bank_match = BANK_REL_RE.search(text)
        bank_guid = bank_match.group(1) if bank_match else ""
        if bank_guid:
            bank_name = bank_names.get(bank_guid, "unknown")
            meta = banks.classify_bank(bank_name)
            bank_category = meta["category"]
            bank_label = meta["category_label"]
        else:
            # 官方工程里有约 1.8k 个事件没有 banks 关系（嵌套/未分配事件），
            # 它们不直接对应某个可替换 bank，单独计数，不混进覆盖率。
            bank_name = "unassigned"
            bank_category = "unassigned"
            bank_label = "未分配 bank（嵌套事件）"
        events.append(
            CatalogEvent(
                event=event_name,
                bank=bank_name,
                bank_category=bank_category,
                bank_label=bank_label,
                source_file=path.name,
            )
        )
    return events


def build_catalog(kit_path: str | Path, limit: int | None = 200) -> Catalog:
    root = Path(kit_path).expanduser()
    errors: list[str] = []
    if not root.is_dir():
        errors.append(f"目录不存在：{root}")
        return Catalog(
            kit_path=str(root),
            fspro="",
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            bank_count=0,
            event_count=0,
            returned_events=0,
            errors=errors,
        )

    if not (root / "Metadata" / "Bank").is_dir():
        errors.append("缺少 Metadata/Bank")
    if not (root / "Metadata" / "Event").is_dir():
        errors.append("缺少 Metadata/Event")
    if not (root / "Assets").is_dir():
        errors.append("缺少 Assets")

    fspro = next(iter(sorted(root.glob("*.fspro"))), None)
    if fspro is None:
        errors.append("缺少 *.fspro")

    bank_names = parse_bank_names(root)
    all_events = parse_events(root, bank_names)
    events = [event for event in all_events if event.bank != "unassigned"]
    unassigned_event_count = len(all_events) - len(events)
    counts = Counter(event.bank for event in events)

    catalog_banks: list[CatalogBank] = []
    for bank_name in sorted(set(bank_names.values())):
        meta = banks.classify_bank(bank_name)
        catalog_banks.append(
            CatalogBank(
                name=bank_name,
                event_count=counts.get(bank_name, 0),
                category=meta["category"],
                label=meta["category_label"],
            )
        )
    for bank_name, count in sorted(counts.items()):
        if bank_name not in {item.name for item in catalog_banks}:
            meta = banks.classify_bank(bank_name)
            catalog_banks.append(
                CatalogBank(
                    name=bank_name,
                    event_count=count,
                    category=meta["category"],
                    label=meta["category_label"],
                )
            )

    catalog_banks.sort(key=lambda item: (-item.event_count, item.name.lower()))
    events.sort(key=lambda item: (item.bank.lower(), item.event.lower()))
    total = len(events)
    if limit is not None and limit > 0:
        returned = events[:limit]
    else:
        returned = events

    return Catalog(
        kit_path=str(root.resolve()),
        fspro=str(fspro) if fspro else "",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        bank_count=len(bank_names),
        event_count=total,
        returned_events=len(returned),
        banks=catalog_banks,
        events=returned,
        errors=errors,
        asset_mapping=False,
        unassigned_event_count=unassigned_event_count,
    )


def save_catalog(catalog: Catalog, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_catalog(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ======================================================================
# M1：完整目录（事件 → AudioFile → Assets）+ 缓存 + 分页查询
# ======================================================================


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _count_xml(directory: Path) -> int:
    try:
        return sum(1 for _ in directory.glob("*.xml"))
    except OSError:
        return 0


def _dir_mtime_ns(directory: Path) -> int:
    try:
        return directory.stat().st_mtime_ns
    except OSError:
        return 0


def _clean_asset_path(raw: str) -> str:
    value = html.unescape(raw.strip()).replace("\\", "/")
    if re.match(r"^[A-Za-z]:", value):
        return ""
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def fingerprint_kit(kit_path: str | Path) -> dict:
    root = Path(kit_path).expanduser()
    fspro = next(iter(sorted(root.glob("*.fspro"))), None) if root.is_dir() else None
    dirs = {
        "bank": root / "Metadata" / "Bank",
        "event": root / "Metadata" / "Event",
        "audiofile": root / "Metadata" / "AudioFile",
        "asset": root / "Assets",
    }
    fingerprint = {
        "kit_path": str(root.resolve()) if root.is_dir() else str(root),
        "fspro_size": fspro.stat().st_size if fspro else 0,
        "fspro_mtime_ns": fspro.stat().st_mtime_ns if fspro else 0,
        "bank_count": _count_xml(dirs["bank"]),
        "event_count": _count_xml(dirs["event"]),
        "audiofile_count": _count_xml(dirs["audiofile"]),
    }
    for key, directory in dirs.items():
        fingerprint[f"{key}_mtime_ns"] = _dir_mtime_ns(directory)
    asset_count = 0
    if dirs["asset"].is_dir():
        for _dirpath, _dirnames, filenames in os.walk(dirs["asset"]):
            asset_count += len(filenames)
    fingerprint["asset_count"] = asset_count
    return fingerprint


def cache_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / "cache" / "catalog" / "catalog.json"


def save_full_catalog(data: dict, data_dir: str | Path) -> Path:
    target = cache_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def load_full_catalog(data_dir: str | Path) -> dict | None:
    target = cache_path(data_dir)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != CATALOG_SCHEMA:
        return None
    if not isinstance(raw.get("events"), list) or not isinstance(raw.get("assets"), dict):
        return None
    return raw


def _asset_index(asset_root: Path, cancel_event=None) -> dict[str, tuple[str, int]]:
    index: dict[str, tuple[str, int]] = {}
    if not asset_root.is_dir():
        return index
    seen = 0
    for dirpath, _dirnames, filenames in os.walk(asset_root):
        for name in filenames:
            seen += 1
            if cancel_event is not None and seen % 1000 == 0 and cancel_event.is_set():
                from app.jobs import JobCancelled

                raise JobCancelled
            full = Path(dirpath) / name
            try:
                relative = full.relative_to(asset_root).as_posix()
                size = full.stat().st_size
            except OSError:
                continue
            index[relative.casefold()] = (relative, size)
    return index


def _parse_audio_asset(audio_dir: Path, guid: str) -> tuple[str, dict]:
    text = _read_optional(audio_dir / f"{{{guid}}}.xml")
    if text is None:
        text = _read_optional(audio_dir / f"{guid}.xml")
    if text is None:
        return guid, {
            "path": "",
            "length": None,
            "channels": None,
            "rate_khz": None,
            "exists": False,
            "size_bytes": 0,
            "missing_metadata": True,
        }
    path_match = ASSET_PATH_RE.search(text)
    length_match = LENGTH_RE.search(text)
    channel_match = CHANNEL_RE.search(text)
    rate_match = RATE_RE.search(text)
    asset_path = _clean_asset_path(path_match.group(1)) if path_match else ""
    return guid, {
        "path": asset_path,
        "length": float(length_match.group(1)) if length_match else None,
        "channels": int(channel_match.group(1)) if channel_match else None,
        "rate_khz": float(rate_match.group(1)) if rate_match else None,
        "exists": False,
        "size_bytes": 0,
        "missing_metadata": not bool(asset_path),
    }


def build_full_catalog(
    kit_path: str | Path,
    progress: Progress | None = None,
    cancel_event=None,
) -> dict:
    """构建完整目录。调用方负责把结果写入缓存。"""

    def report(stage: str, current: int, total: int | None, message: str) -> None:
        if progress:
            progress(stage, current, total, message)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            from app.jobs import JobCancelled

            raise JobCancelled

    root = Path(kit_path).expanduser()
    errors: list[str] = []
    if not root.is_dir():
        raise ValueError(f"目录不存在：{root}")
    for name, path in (
        ("Metadata/Bank", root / "Metadata" / "Bank"),
        ("Metadata/Event", root / "Metadata" / "Event"),
        ("Metadata/AudioFile", root / "Metadata" / "AudioFile"),
        ("Assets", root / "Assets"),
    ):
        if not path.is_dir():
            errors.append(f"缺少 {name}")
    if errors:
        raise ValueError("；".join(errors))

    report("banks", 0, None, "解析 bank 名称")
    bank_names = parse_bank_names(root)
    event_paths = sorted((root / "Metadata" / "Event").glob("*.xml"))
    report("events", 0, len(event_paths), f"解析 {len(event_paths)} 个事件")

    events: list[dict] = []
    referenced: set[str] = set()
    parsed_files = 0
    for index, path in enumerate(event_paths, 1):
        check_cancel()
        text = _read_optional(path)
        if text is None:
            continue
        match = NAME_RE.search(text)
        if not match:
            continue
        parsed_files += 1
        event_name = html.unescape(match.group(1).strip())
        bank_match = BANK_REL_RE.search(text)
        bank_guid = bank_match.group(1) if bank_match else ""
        bank_name = bank_names.get(bank_guid, "unassigned") if bank_guid else "unassigned"
        if bank_guid and bank_name == "unassigned":
            bank_name = "unknown"
        if bank_name == "unassigned":
            meta = {
                "category": "unassigned",
                "category_label": "未分配 bank（嵌套事件）",
                "scope": "other",
                "language": None,
                "language_label": None,
                "label": "未分配 bank（嵌套事件）",
            }
        else:
            meta = banks.classify_bank(bank_name)
        guids = list(dict.fromkeys(AUDIO_REF_RE.findall(text)))
        referenced.update(guids)
        events.append(
            {
                "id": path.stem.strip("{}"),
                "event": event_name,
                "bank": bank_name,
                "category": meta["category"],
                "label": meta["category_label"],
                "scope": meta["scope"],
                "language": meta["language"],
                "language_label": meta["language_label"],
                "source_file": path.name,
                "asset_guids": guids,
                "asset_count": len(guids),
                "audio_refs_raw": len(AUDIO_REF_RE.findall(text)),
                "existing_asset_count": 0,
                "duration_sec": 0.0,
            }
        )
        if index % 100 == 0 or index == len(event_paths):
            report("events", index, len(event_paths), f"解析事件 {index}/{len(event_paths)}")

    report("audiofiles", 0, len(referenced), f"读取 {len(referenced)} 个素材元数据")
    assets: dict[str, dict] = {}
    audio_dir = root / "Metadata" / "AudioFile"
    guids = sorted(referenced)
    done = 0
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="audiofile") as pool:
        for start in range(0, len(guids), 256):
            check_cancel()
            batch = guids[start : start + 256]
            for guid, asset in pool.map(lambda item: _parse_audio_asset(audio_dir, item), batch):
                assets[guid] = asset
                done += 1
                if done % 128 == 0 or done == len(guids):
                    report("audiofiles", done, len(guids), f"读取素材元数据 {done}/{len(guids)}")

    report("assets", 0, None, "扫描 Assets 素材文件")
    asset_index = _asset_index(root / "Assets", cancel_event)
    report("assets", len(asset_index), len(asset_index), f"索引 {len(asset_index)} 个素材文件")

    missing_files = 0
    missing_metadata = 0
    for asset in assets.values():
        if asset["missing_metadata"]:
            missing_metadata += 1
            continue
        found = asset_index.get(asset["path"].casefold())
        if found:
            asset["exists"] = True
            asset["size_bytes"] = found[1]
        else:
            missing_files += 1

    for event in events:
        check_cancel()
        lengths = [
            assets[guid]["length"]
            for guid in event["asset_guids"]
            if guid in assets and assets[guid]["length"]
        ]
        event["duration_sec"] = round(max(lengths), 3) if lengths else 0.0
        event["existing_asset_count"] = sum(
            1 for guid in event["asset_guids"] if guid in assets and assets[guid]["exists"]
        )

    bank_map: dict[str, dict] = {}
    for bank_name in set(bank_names.values()):
        meta = banks.classify_bank(bank_name)
        bank_map[bank_name] = {
            "name": bank_name,
            "category": meta["category"],
            "label": meta["category_label"],
            "scope": meta["scope"],
            "language": meta["language"],
            "language_label": meta["language_label"],
            "event_count": 0,
            "with_assets": 0,
            "asset_refs": 0,
            "missing_assets": 0,
            "duration_sec": 0.0,
        }
    for event in events:
        if event["bank"] == "unassigned":
            continue
        summary = bank_map.setdefault(
            event["bank"],
            {
                "name": event["bank"],
                "category": event["category"],
                "label": event["label"],
                "scope": event["scope"],
                "language": event["language"],
                "language_label": event["language_label"],
                "event_count": 0,
                "with_assets": 0,
                "asset_refs": 0,
                "missing_assets": 0,
                "duration_sec": 0.0,
            },
        )
        summary["event_count"] += 1
        summary["asset_refs"] += event["asset_count"]
        summary["with_assets"] += 1 if event["existing_asset_count"] else 0
        summary["missing_assets"] += event["asset_count"] - event["existing_asset_count"]
        summary["duration_sec"] = round(summary["duration_sec"] + event["duration_sec"], 3)

    bank_list = sorted(
        bank_map.values(),
        key=lambda item: (
            banks.SCOPE_ORDER.get(item["scope"], 99),
            -item["event_count"],
            item["name"].lower(),
        ),
    )

    assigned = [event for event in events if event["bank"] != "unassigned"]
    assigned_assets = {
        guid for event in assigned for guid in event["asset_guids"]
    }
    assigned_existing = sum(
        1 for guid in assigned_assets if guid in assets and assets[guid]["exists"]
    )
    assigned_missing_files = sum(
        1
        for guid in assigned_assets
        if guid in assets
        and not assets[guid]["missing_metadata"]
        and not assets[guid]["exists"]
    )
    stats = {
        "banks": len(bank_names),
        "event_files": len(event_paths),
        "parsed_events": parsed_files,
        "assigned_events": len(assigned),
        "unassigned_events": len(events) - len(assigned),
        "events_with_refs": sum(1 for event in assigned if event["asset_count"]),
        "events_with_assets": sum(1 for event in assigned if event["existing_asset_count"]),
        "events_missing_assets": sum(
            1 for event in assigned if event["asset_count"] and not event["existing_asset_count"]
        ),
        "asset_refs": sum(event["asset_count"] for event in assigned),
        "asset_refs_raw": sum(event["audio_refs_raw"] for event in assigned),
        "unique_assets": len(assets),
        "unique_assets_assigned": len(assigned_assets),
        "existing_assets": sum(1 for asset in assets.values() if asset["exists"]),
        "existing_assets_assigned": assigned_existing,
        "missing_files": missing_files,
        "missing_files_assigned": assigned_missing_files,
        "missing_metadata": missing_metadata,
        "duration_sec": round(sum(event["duration_sec"] for event in assigned), 3),
    }
    data = {
        "schema": CATALOG_SCHEMA,
        "kit_path": str(root.resolve()),
        "fingerprint": fingerprint_kit(root),
        "generated_at": _now(),
        "stats": stats,
        "banks": bank_list,
        "events": events,
        "assets": assets,
        "errors": [],
    }
    report("done", 1, 1, "目录构建完成")
    return data


def validate_cache(data: dict | None, kit_path: str | Path) -> tuple[bool, bool, str]:
    """返回 (可用, stale, 原因)。kit 路径不同时不可用。"""
    if not data:
        return False, False, "没有缓存"
    root = Path(kit_path).expanduser()
    try:
        resolved = str(root.resolve())
    except OSError:
        resolved = str(root)
    if data.get("kit_path") != resolved:
        return False, False, "缓存属于另一个工程"
    try:
        current = fingerprint_kit(root)
    except OSError:
        return False, False, "无法读取工程"
    if current != data.get("fingerprint"):
        return True, True, "工程内容可能已变化，可重新生成"
    return True, False, ""


class CatalogStore:
    """进程内缓存。读取一次，之后查询和预览都用内存数据。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.data: dict | None = None
        self.cache_file = ""
        self.kit_path = ""
        self.stale = False
        self.reason = ""
        self.checked = False

    def status(self, kit_path: str | Path, data_dir: str | Path, recheck: bool = False) -> dict:
        root = Path(kit_path).expanduser()
        try:
            resolved = str(root.resolve())
        except OSError:
            resolved = str(root)
        with self._lock:
            target = cache_path(data_dir)
            key = str(target)
            if recheck or not self.checked or self.cache_file != key or self.kit_path != resolved:
                self.data = load_full_catalog(data_dir)
                self.cache_file = key
                self.kit_path = resolved
                self.checked = True
                self.stale = False
                self.reason = ""
                if self.data:
                    usable, stale, reason = validate_cache(self.data, kit_path)
                    if not usable:
                        self.data = None
                        self.reason = reason
                    else:
                        self.stale = stale
                        self.reason = reason
            return {
                "available": self.data is not None,
                "cached": self.data is not None,
                "stale": self.stale,
                "reason": self.reason,
                "cache_file": self.cache_file,
                "generated_at": (self.data or {}).get("generated_at", ""),
                "stats": (self.data or {}).get("stats"),
            }

    def set(self, data: dict, data_dir: str | Path) -> None:
        save_full_catalog(data, data_dir)
        with self._lock:
            self.data = data
            self.cache_file = str(cache_path(data_dir))
            self.kit_path = data.get("kit_path", "")
            self.stale = False
            self.reason = ""
            self.checked = True

    def clear(self) -> None:
        with self._lock:
            self.data = None
            self.stale = False
            self.reason = ""
            self.checked = False

    def snapshot(self) -> dict | None:
        with self._lock:
            return self.data


store = CatalogStore()


def _facets(data: dict) -> dict:
    categories: dict[str, dict] = {}
    languages: dict[str, dict] = {}
    scopes: Counter = Counter()
    for event in data["events"]:
        if event["bank"] == "unassigned":
            continue
        category = event["category"]
        item = categories.setdefault(
            category,
            {"key": category, "label": event["label"], "count": 0},
        )
        item["count"] += 1
        if event["language"]:
            language = event["language"]
            language_item = languages.setdefault(
                language,
                {"key": language, "label": event["language_label"] or language, "count": 0},
            )
            language_item["count"] += 1
        scopes[event["scope"]] += 1
    return {
        "banks": data["banks"],
        "categories": sorted(categories.values(), key=lambda item: (-item["count"], item["key"])),
        "languages": sorted(languages.values(), key=lambda item: (-item["count"], item["key"])),
        "scopes": [{"key": key, "count": count} for key, count in scopes.most_common()],
    }


def query_catalog(
    data: dict,
    *,
    bank: str = "",
    category: str = "",
    language: str = "",
    q: str = "",
    has_assets: str = "",
    include_unassigned: bool = False,
    page: int = 1,
    page_size: int = 50,
    sort: str = "bank",
    order: str = "asc",
) -> dict:
    bank = bank.strip()
    category = category.strip()
    language = language.strip()
    needle = q.strip().casefold()
    has_assets = has_assets.strip()

    filtered = []
    for event in data["events"]:
        if event["bank"] == "unassigned" and not include_unassigned:
            continue
        if bank and event["bank"] != bank:
            continue
        if category and event["category"] != category:
            continue
        if language and event["language"] != language:
            continue
        if needle:
            haystack = f"{event['event']} {event['bank']}".casefold()
            if needle not in haystack:
                continue
        existing = event["existing_asset_count"]
        if has_assets == "assets" and not existing:
            continue
        if has_assets == "missing" and not (event["asset_count"] and not existing):
            continue
        if has_assets == "none" and event["asset_count"]:
            continue
        filtered.append(event)

    reverse = order.lower() == "desc"
    if sort == "duration":
        filtered.sort(key=lambda item: item["duration_sec"], reverse=reverse)
    elif sort == "event":
        filtered.sort(key=lambda item: (item["event"].lower(), item["bank"].lower()), reverse=reverse)
    else:
        filtered.sort(
            key=lambda item: (item["bank"].lower(), item["event"].lower()),
            reverse=reverse,
        )

    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(200, int(page_size)))
    except (TypeError, ValueError):
        page_size = 50
    total = len(filtered)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    start = (page - 1) * page_size
    page_events = filtered[start : start + page_size]

    items = []
    for event in page_events:
        assets = []
        for guid in event["asset_guids"][:20]:
            asset = data["assets"].get(guid)
            if not asset:
                continue
            assets.append({"guid": guid, **asset})
        items.append({**event, "assets": assets, "truncated_assets": len(event["asset_guids"]) > 20})

    return {
        "stats": data["stats"],
        "facets": _facets(data),
        "page": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
        },
        "filtered": total,
        "events": items,
    }

