"""战雷目录探测与只读状态读取。"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app import banks

GAME_DIR_NAME = "War Thunder"


@dataclass
class GameInstall:
    path: str
    aces_exe: str
    config_blk: str
    sound_dir: str
    bank_count: int
    mod_file_count: int
    enable_mod: bool | None
    fmod_sound_enable: bool | None
    has_sound_section: bool
    aces_modified: str
    config_modified: str

    def to_dict(self) -> dict:
        return asdict(self)


def _children_map(path: Path) -> dict[str, Path]:
    try:
        return {child.name.lower(): child for child in path.iterdir()}
    except OSError:
        return {}


def _modified(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def find_aces_exe(root: str | Path) -> Path | None:
    """战雷根目录没有 aces.exe；实际在 win64/win32/win-arm64ec 下。"""
    base = Path(root)
    for relative in ("win64/aces.exe", "win32/aces.exe", "win-arm64ec/aces.exe", "aces.exe"):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def is_warthunder_dir(path: str | Path) -> bool:
    root = Path(path).expanduser()
    if not root.is_dir():
        return False
    children = _children_map(root)
    sound = children.get("sound")
    config = children.get("config.blk")
    return bool(
        find_aces_exe(root)
        and sound
        and sound.is_dir()
        and config
        and config.is_file()
    )


def read_config_blk(path: str | Path) -> dict:
    root = Path(path).expanduser()
    config_path = root if root.is_file() else root / "config.blk"
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "path": str(config_path),
            "error": str(exc),
            "has_sound_section": False,
            "enable_mod": None,
            "fmod_sound_enable": None,
        }

    match = re.search(r"sound\s*\{(.*?)\}", text, re.S | re.I)
    section = match.group(1) if match else text

    def flag(name: str) -> bool | None:
        found = re.search(rf"\b{re.escape(name)}\s*:\s*b\s*=\s*(yes|no)\b", section, re.I)
        return None if not found else found.group(1).lower() == "yes"

    return {
        "path": str(config_path),
        "error": "",
        "has_sound_section": bool(match),
        "enable_mod": flag("enable_mod"),
        "fmod_sound_enable": flag("fmod_sound_enable"),
        "use_loops_gun_sound": flag("useLoopsForGunSound"),
        "use_loops_gm_gun_sound": flag("useLoopsForGMGunSound"),
    }


def scan_game(path: str | Path) -> GameInstall:
    root = Path(path).expanduser().resolve()
    config = read_config_blk(root)
    aces = find_aces_exe(root)
    bank_items = banks.scan_banks(root / "sound")
    mod_dir = root / "sound" / "mod"
    mod_file_count = 0
    if mod_dir.is_dir():
        try:
            mod_file_count = sum(1 for item in mod_dir.rglob("*") if item.is_file())
        except OSError:
            mod_file_count = 0

    return GameInstall(
        path=str(root),
        aces_exe=str(aces) if aces else "",
        config_blk=str(root / "config.blk"),
        sound_dir=str(root / "sound"),
        bank_count=len(bank_items),
        mod_file_count=mod_file_count,
        enable_mod=config.get("enable_mod"),
        fmod_sound_enable=config.get("fmod_sound_enable"),
        has_sound_section=bool(config.get("has_sound_section")),
        aces_modified=_modified(aces),
        config_modified=_modified(root / "config.blk"),
    )


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        if not path:
            continue
        try:
            key = str(path.expanduser().resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg
        except ImportError:
            winreg = None  # type: ignore[assignment]
        if winreg is not None:
            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        for value_name in ("SteamPath", "InstallPath"):
                            try:
                                value, _ = winreg.QueryValueEx(key, value_name)
                            except OSError:
                                continue
                            if value:
                                roots.append(Path(value))
                except OSError:
                    continue

    for base in (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        "C:/Program Files (x86)",
        "C:/Program Files",
    ):
        if base:
            roots.append(Path(base) / "Steam")
    return _dedupe_paths(roots)


def _steam_libraries(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _dedupe_paths(libraries)

    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\")
        libraries.append(Path(raw))
    return _dedupe_paths(libraries)


def candidate_paths(extra_paths: Iterable[str | Path] | None = None) -> list[Path]:
    candidates: list[Path] = []

    for env_name in ("WT_PATH", "WAR_THUNDER_PATH"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))

    for steam_root in _steam_roots():
        for library in _steam_libraries(steam_root):
            candidates.append(library / "steamapps" / "common" / GAME_DIR_NAME)

    for drive in ("C:", "D:", "E:", "F:", "G:"):
        for base in ("SteamLibrary", "Steam", "Games", "Program Files (x86)/Steam"):
            candidates.append(Path(f"{drive}/{base}/steamapps/common/{GAME_DIR_NAME}"))
        candidates.append(Path(f"{drive}/War Thunder"))

    if extra_paths:
        for item in extra_paths:
            if item:
                candidates.append(Path(item))

    return _dedupe_paths(candidates)


def detect_installs(extra_paths: Iterable[str | Path] | None = None) -> list[GameInstall]:
    installs: list[GameInstall] = []
    for path in candidate_paths(extra_paths):
        if is_warthunder_dir(path):
            installs.append(scan_game(path))
    return installs
