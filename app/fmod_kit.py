"""官方 FMOD 工程与 FMOD Studio CLI 检测。"""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

KIT_GLOBS = (
    "fmod_studio_warthunder_for_modders*",
    "*fmod*studio*warthunder*modders*",
    "*warthunder*for*modders*",
)


@dataclass
class KitInfo:
    path: str
    fspro: str
    valid: bool
    bank_xml_count: int
    event_xml_count: int
    asset_dir_count: int
    top_assets: list[str] = field(default_factory=list)
    modified: str = ""
    size_mb: float | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _modified(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _find_fspro(root: Path) -> Path | None:
    try:
        return next(iter(sorted(root.glob("*.fspro"))), None)
    except OSError:
        return None


def validate_kit(path: str | Path) -> bool:
    return inspect_kit(path).valid


def inspect_kit(path: str | Path, compute_size: bool = False) -> KitInfo:
    root = Path(path).expanduser()
    errors: list[str] = []
    fspro = _find_fspro(root) if root.is_dir() else None
    metadata = root / "Metadata"
    assets = root / "Assets"

    if not root.is_dir():
        errors.append("目录不存在")
    if fspro is None:
        errors.append("缺少 *.fspro")
    if not (metadata / "Bank").is_dir():
        errors.append("缺少 Metadata/Bank")
    if not (metadata / "Event").is_dir():
        errors.append("缺少 Metadata/Event")
    if not assets.is_dir():
        errors.append("缺少 Assets")

    bank_count = _count_xml(metadata / "Bank")
    event_count = _count_xml(metadata / "Event")
    asset_dirs: list[str] = []
    if assets.is_dir():
        try:
            asset_dirs = sorted(item.name for item in assets.iterdir() if item.is_dir())
        except OSError:
            asset_dirs = []
    top_assets = asset_dirs[:16]

    size_mb: float | None = None
    if compute_size and root.is_dir():
        total = 0
        try:
            for item in root.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
        except OSError:
            pass
        size_mb = round(total / 1024 / 1024, 1)

    return KitInfo(
        path=str(root.resolve()) if root.is_dir() else str(root),
        fspro=str(fspro) if fspro else "",
        valid=not errors,
        bank_xml_count=bank_count,
        event_xml_count=event_count,
        asset_dir_count=len(asset_dirs),
        top_assets=top_assets,
        modified=_modified(root) if root.is_dir() else "",
        size_mb=size_mb,
        errors=errors,
    )


def _count_xml(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    try:
        return sum(1 for item in directory.glob("*.xml") if item.is_file())
    except OSError:
        return 0


def _candidate_kit_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    if (root / "sounds.fspro").is_file() or _find_fspro(root):
        found.append(root)
    try:
        for pattern in KIT_GLOBS:
            for item in root.glob(pattern):
                if item.is_dir():
                    found.append(item)
    except OSError:
        pass
    return found


def find_kits(extra_paths: Iterable[str | Path] | None = None) -> list[KitInfo]:
    candidates: list[Path] = []
    for env_name in ("WT_FMOD_KIT", "FMOD_KIT_PATH"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    if extra_paths:
        for item in extra_paths:
            if item:
                candidates.append(Path(item))

    common_roots = [
        Path.home() / "Documents",
        Path.home() / "Downloads",
        Path("D:/"),
        Path("E:/"),
        Path("C:/"),
    ]
    for root in common_roots:
        candidates.extend(_candidate_kit_dirs(root))

    seen: set[str] = set()
    result: list[KitInfo] = []
    for candidate in candidates:
        for kit_dir in _candidate_kit_dirs(candidate):
            try:
                key = str(kit_dir.resolve()).lower()
            except OSError:
                key = str(kit_dir).lower()
            if key in seen:
                continue
            seen.add(key)
            info = inspect_kit(kit_dir)
            if info.valid:
                result.append(info)

    result.sort(key=lambda info: info.modified, reverse=True)
    return result[:8]


def find_fmod_studio() -> list[dict]:
    candidates: list[Path] = []
    for env_name in ("FMOD_STUDIO_PATH",):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    which = shutil.which("fmodstudiocl.exe") or shutil.which("fmodstudiocl")
    if which:
        candidates.append(Path(which))

    roots = [
        Path("C:/Program Files/FMOD SoundSystem"),
        Path("C:/Program Files (x86)/FMOD SoundSystem"),
        Path("D:/Program Files/FMOD SoundSystem"),
        Path("D:/FMOD SoundSystem"),
        Path("E:/FMOD SoundSystem"),
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            candidates.extend(root.glob("FMOD Studio */fmodstudiocl.exe"))
            candidates.extend(root.glob("*/fmodstudiocl.exe"))
        except OSError:
            continue

    seen: set[str] = set()
    result: list[dict] = []
    for item in candidates:
        try:
            if not item.is_file():
                continue
            key = str(item.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "path": str(item),
                "version": item.parent.name,
                "modified": _modified(item),
            }
        )
    result.sort(key=lambda item: item["version"], reverse=True)
    return result


def build_kit(*args, **kwargs):
    raise NotImplementedError("FMOD 构建将在 M3 接入；M0 只做工程检测与目录解析。")
