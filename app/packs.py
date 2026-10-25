"""成品语音包导入、安装与还原。

这里只处理“已经做好的语音包”：`.wtvoice`、普通 zip（含 .bank）或单个
`.bank`。安装只写游戏目录的 `sound/mod/`，覆盖前自动备份到应用数据目录；
制作语音包仍由“声音目录 → 构建语音包”负责，两边不混在一起。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app import components, config, game, localized, vgmstream

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_UNCOMPRESSED = 1536 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
PACK_EXTENSIONS = {".wtvoice", ".zip", ".bank"}
ARCHIVE_EXTENSIONS = {".rar", ".7z"}
BANK_SUFFIX = ".bank"
TOKEN_RE = re.compile(r"[0-9a-f]{12}")
PROTECTED_BANKS = {
    "masterbank.bank",
    "masterbank.assets.bank",
    "masterbank.strings.bank",
}
CONFIG_KEYS = {
    "enable_mod": "enable_mod",
    "fmod_sound_enable": "fmod_sound_enable",
    "use_loops_gun_sound": "useLoopsForGunSound",
    "use_loops_gm_gun_sound": "useLoopsForGMGunSound",
}
RISKY_BANKS = {
    "masterbank.bank",
    "masterbank.assets.bank",
    "masterbank.strings.bank",
    "hangar.bank",
}
EXECUTABLE_SUFFIXES = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".scr", ".com"}


def _is_protected(name: str) -> bool:
    return Path(name).name.lower() in PROTECTED_BANKS


def _config_flags(info: dict) -> dict:
    return {key: info.get(key) for key in CONFIG_KEYS}


class PackError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pack_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / "packs"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> str:
    text = str(name or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text.split("/")[0]:
        raise PackError(f"语音包里有非法路径：{name}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise PackError(f"语音包里有非法路径：{name}")
    return "/".join(parts)


def _zip_banks(path: Path) -> tuple[list[dict], dict | None, list[str], list[str], str]:
    warnings: list[str] = []
    banks: list[dict] = []
    extras: list[str] = []
    manifest: dict | None = None
    seen: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackError("语音包文件数量异常，已拒绝")
        total = sum(max(0, item.file_size) for item in infos)
        if total > MAX_UNCOMPRESSED:
            raise PackError("语音包解压后过大，已拒绝")
        for info in infos:
            if info.is_dir():
                continue
            member = _safe_member(info.filename)
            if info.flag_bits & 0x1:
                raise PackError("语音包包含加密文件，暂不支持")
            if member.lower() == "manifest.json":
                with archive.open(info) as handle:
                    raw = handle.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) <= MAX_MANIFEST_BYTES:
                    try:
                        value = json.loads(raw.decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        warnings.append("manifest.json 不是有效 JSON，已忽略")
                    else:
                        if isinstance(value, dict):
                            manifest = value
                continue
            if not member.lower().endswith(BANK_SUFFIX):
                if Path(member).name.lower() not in {"readme.txt", "readme.md"}:
                    extras.append(member)
                continue
            name = Path(member).name
            key = name.lower()
            if key in seen:
                raise PackError(f"语音包里有重名 bank：{name}")
            seen.add(key)
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            banks.append(
                {
                    "name": name,
                    "member": member,
                    "size": size,
                    "sha256": digest.hexdigest(),
                    "protected": _is_protected(name),
                }
            )
    layout = _layout_of([bank["member"] for bank in banks])
    warnings.extend(_layout_warnings(layout))
    if extras:
        warnings.append(f"包内还有 {len(extras)} 个非 bank 文件，已忽略，不会执行")
        executables = [item for item in extras if Path(item).suffix.lower() in EXECUTABLE_SUFFIXES]
        if executables:
            shown = "、".join(executables[:3])
            warnings.append(f"包内包含可执行文件：{shown}，已忽略；不要运行来源不明的程序")
    warnings.extend(_pair_warnings(banks))
    return banks, manifest, warnings, extras, layout


def _seven_zip() -> Path:
    info = components.find_extractor()
    if not info or info.get("kind") != "7z":
        raise PackError(
            "导入 RAR/7z 需要本机安装 7-Zip；也可以先手动解压成 zip 或直接拖 .bank 进来"
        )
    return Path(info["path"])


def _archive_entries(exe: Path, archive: Path) -> list[dict]:
    try:
        completed = subprocess.run(
            [str(exe), "l", "-slt", "-ba", "-sccUTF-8", str(archive)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackError(f"无法读取压缩包：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "")[-300:]
        raise PackError(f"无法读取压缩包：{detail.strip()}")
    entries: list[dict] = []
    for block in (completed.stdout or "").replace("\r\n", "\n").split("\n\n"):
        data: dict[str, str] = {}
        for line in block.splitlines():
            if " = " not in line:
                continue
            key, value = line.split(" = ", 1)
            data[key.strip()] = value
        if data.get("Path"):
            entries.append(data)
    return entries


def _normalize_archive(archive: Path, folder: Path) -> tuple[Path, int]:
    """把 RAR/7z 解到临时目录，转成可校验的 zip，返回 (zip, 忽略文件数)。"""
    exe = _seven_zip()
    entries = _archive_entries(exe, archive)
    if not entries:
        raise PackError("压缩包是空的或格式无法识别")
    if len(entries) > MAX_ENTRIES:
        raise PackError("压缩包文件数量异常，已拒绝")
    total = 0
    for entry in entries:
        rel = str(entry.get("Path") or "").replace("\\", "/").strip()
        _safe_member(rel)
        if str(entry.get("Encrypted") or "").strip() == "+":
            raise PackError("压缩包包含加密文件，暂不支持")
        if str(entry.get("Attributes") or "").strip().upper().startswith("D"):
            continue
        try:
            size = int(str(entry.get("Size") or "0").strip() or "0")
        except ValueError:
            size = 0
        if size < 0:
            raise PackError("压缩包里有异常文件大小")
        total += size
        if total > MAX_UNCOMPRESSED:
            raise PackError("压缩包解压后过大，已拒绝")

    extracted = folder / "extracted"
    shutil.rmtree(extracted, ignore_errors=True)
    extracted.mkdir(parents=True)
    try:
        completed = subprocess.run(
            [
                str(exe),
                "x",
                "-y",
                "-bd",
                "-bso0",
                "-bsp0",
                f"-o{extracted}",
                str(archive),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackError(f"解压失败：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "")[-300:]
        raise PackError(f"解压失败：{detail.strip()}")

    kept: list[tuple[str, Path]] = []
    extras = 0
    extracted_total = 0
    for item in sorted(extracted.rglob("*")):
        if item.is_symlink():
            raise PackError(f"压缩包包含符号链接，已拒绝：{item.name}")
        if not item.is_file():
            continue
        extracted_total += item.stat().st_size
        if extracted_total > MAX_UNCOMPRESSED:
            raise PackError("压缩包解压后过大，已拒绝")
        rel = item.relative_to(extracted).as_posix()
        _safe_member(rel)
        name = Path(rel).name.lower()
        if name == "manifest.json" or name.endswith(BANK_SUFFIX) or name in {
            "readme.txt",
            "readme.md",
        }:
            kept.append((rel, item))
        else:
            extras += 1
    if not any(Path(rel).name.lower().endswith(BANK_SUFFIX) for rel, _ in kept):
        raise PackError("压缩包里没有 .bank 文件")
    normalized = folder / "normalized.zip"
    with zipfile.ZipFile(normalized, "w", zipfile.ZIP_STORED) as target:
        for rel, item in kept:
            target.write(item, rel)
    shutil.rmtree(extracted, ignore_errors=True)
    return normalized, extras


def _layout_of(members: list[str]) -> str:
    if not members:
        return "empty"
    prefixes = set()
    for member in members:
        parent = "/".join(member.split("/")[:-1]).lower()
        prefixes.add(parent)
    if len(prefixes) > 1:
        return "mixed"
    prefix = next(iter(prefixes))
    if prefix in ("", "."):
        return "flat"
    if prefix == "sound/mod":
        return "sound_mod"
    if prefix == "mod":
        return "mod"
    if prefix == "banks":
        return "banks"
    return "nested"


def _layout_warnings(layout: str) -> list[str]:
    if layout == "sound_mod":
        return ["按社区 sound/mod 结构打包，解压后可直接合并到游戏目录"]
    if layout == "mod":
        return ["按社区 mod 文件夹结构打包；应用会统一安装到 sound/mod"]
    if layout == "banks":
        return ["旧版 .wtvoice 的 banks/ 结构，应用仍可直接安装"]
    if layout == "flat":
        return ["bank 直接放在压缩包根目录，应用会安装到 sound/mod"]
    if layout in ("nested", "mixed"):
        return ["bank 位于多层目录或目录不统一，安装时只使用文件名"]
    return []


def _pair_warnings(banks: list[dict]) -> list[str]:
    warnings: list[str] = []
    names = {bank["name"].lower() for bank in banks}
    for bank in banks:
        lower = bank["name"].lower()
        if lower == "hangar.bank":
            warnings.append("hangar.bank 是旧版 bank，社区反馈可能导致爆音；官方工程已不再提供")
        if lower.endswith(".assets.bank"):
            base = lower[: -len(".assets.bank")] + ".bank"
            if base not in names:
                warnings.append(f"{bank['name']} 缺少同名 {base}，可能只有音频没有事件定义")
            continue
        if lower.endswith(".strings.bank") or lower.endswith(".streams.bank"):
            continue
        if not lower.endswith(".bank"):
            continue
        base = lower[: -len(".bank")]
        if f"{base}.assets.bank" not in names and f"{base}.streams.bank" not in names:
            warnings.append(f"{bank['name']} 没有同名 .assets.bank，可能只有事件定义没有音频")
    return warnings


def _parse_file(path: Path, source_type: str, display_name: str = "") -> dict:
    warnings: list[str] = []
    manifest: dict | None = None
    extras: list[str] = []
    layout = "single"
    if source_type == "bank":
        bank_name = Path(display_name).name if display_name else path.name
        if not bank_name.lower().endswith(BANK_SUFFIX):
            bank_name = path.name
        banks = [
            {
                "name": bank_name,
                "member": "",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "protected": _is_protected(bank_name),
            }
        ]
        name = Path(bank_name).stem
        warnings.extend(_pair_warnings(banks))
    else:
        banks, manifest, warnings, extras, layout = _zip_banks(path)
        if not banks:
            raise PackError("语音包里没有 .bank 文件")
        if not manifest:
            warnings.append("缺少 manifest.json，按普通 zip 安装")
        if manifest:
            label = manifest.get("name")
            name = str(label).strip() if isinstance(label, str) and label.strip() else path.stem
            if manifest.get("format") not in (None, "wtvoice"):
                warnings.append("manifest format 不是 wtvoice，仍按 bank 安装")
            listed = manifest.get("banks")
            if isinstance(listed, list):
                expected = {str(item) for item in listed}
                actual = {item["name"] for item in banks}
                missing = expected - actual
                if missing:
                    warnings.append(f"清单里有 {len(missing)} 个 bank 不在包内")
        else:
            name = path.stem
    protected = [bank for bank in banks if bank.get("protected")]
    lower_names = [bank["name"].lower() for bank in banks]
    wave_like = bool(
        protected
        and any("crew_dialogs" in item for item in lower_names)
        and any(item.startswith("masterbank") for item in lower_names)
    )
    if protected:
        warnings.append(
            "包含 masterbank 核心 bank（社区降噪包常见）；默认拒绝安装，"
            "确认用途后勾选“允许安装 masterbank”"
        )
    if wave_like:
        warnings.append(
            "检测到 W.A.V.E. 风格降噪包；旧版可能导致枪炮/引擎声消失或卡顿，"
            "建议使用 W.A.V.E. 4.0"
        )
    return {
        "name": name,
        "source_type": source_type,
        "layout": layout,
        "extra_files": len(extras),
        "requires_opt_in": bool(protected),
        "wave_like": wave_like,
        "bank_count": len(banks),
        "banks": sorted(banks, key=lambda item: item["name"]),
        "manifest": manifest,
        "warnings": warnings,
    }


def receive_upload(
    data_dir: str | Path,
    filename: str,
    stream,
    length: int,
) -> dict:
    safe_name = Path(str(filename or "")).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in PACK_EXTENSIONS | ARCHIVE_EXTENSIONS:
        raise PackError("只支持 .wtvoice、.zip、.rar、.7z 或 .bank")
    if length <= 0:
        raise PackError("上传内容为空")
    if length > MAX_UPLOAD_BYTES:
        raise PackError("语音包超过 1 GB，已拒绝")
    token = uuid.uuid4().hex[:12]
    folder = _pack_root(data_dir) / "uploads" / token
    folder.mkdir(parents=True, exist_ok=False)
    archive = folder / f"source{suffix}"
    digest = hashlib.sha256()
    written = 0
    try:
        with archive.open("wb") as handle:
            while True:
                chunk = stream.read(min(1024 * 1024, length - written))
                if not chunk:
                    break
                written += len(chunk)
                if written > length:
                    raise PackError("上传内容超过声明大小")
                digest.update(chunk)
                handle.write(chunk)
        if written != length:
            raise PackError("上传中断，文件不完整")
        if suffix in ARCHIVE_EXTENSIONS:
            normalized, ignored = _normalize_archive(archive, folder)
            info = _parse_file(normalized, "zip", safe_name)
            info["archive"] = normalized.name
            info["source_format"] = suffix.lstrip(".")
            if ignored:
                info["warnings"].insert(
                    0,
                    f"已从 {suffix.lstrip('.').upper()} 解出并转为可校验的 zip；"
                    f"忽略 {ignored} 个非 bank 文件，不会执行",
                )
            else:
                info["warnings"].insert(
                    0, f"已从 {suffix.lstrip('.').upper()} 解出并转为可校验的 zip"
                )
            archive.unlink(missing_ok=True)
        else:
            info = _parse_file(archive, suffix.lstrip("."), safe_name)
        info.update(
            {
                "token": token,
                "original_name": safe_name,
                "archive": archive.name,
                "size": written,
                "sha256": digest.hexdigest(),
                "created_at": _now(),
            }
        )
        _write_json(folder / "pack.json", info)
        return {"token": token, "pack": _public(info)}
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def register_archive(
    data_dir: str | Path,
    archive_path: str | Path,
    original_name: str = "",
) -> dict:
    """把本机已有语音包登记为可安装项，不经过浏览器上传。

    制作页“一键安装”直接使用构建产物；相同文件重复安装时复用原来的
    解析编号，安装记录会覆盖而不是越积越多。
    """
    source = Path(archive_path)
    if not source.is_file():
        raise PackError("语音包文件不存在，请重新构建")
    safe_name = Path(str(original_name or source.name)).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in PACK_EXTENSIONS:
        safe_name = source.name
        suffix = source.suffix.lower()
    if suffix not in PACK_EXTENSIONS:
        raise PackError("只支持 .wtvoice、.zip 或 .bank")
    size = source.stat().st_size
    if size <= 0:
        raise PackError("语音包内容为空")
    if size > MAX_UPLOAD_BYTES:
        raise PackError("语音包超过 1 GB，已拒绝")
    digest = sha256_file(source)

    uploads = _pack_root(data_dir) / "uploads"
    if uploads.is_dir():
        for folder in sorted(uploads.iterdir()):
            if not folder.is_dir() or not TOKEN_RE.fullmatch(folder.name):
                continue
            try:
                existing = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(existing, dict):
                continue
            if (
                existing.get("sha256") != digest
                or existing.get("original_name") != safe_name
                or not _archive_path(data_dir, existing).is_file()
            ):
                continue
            existing["size"] = size
            existing["source_type"] = suffix.lstrip(".")
            return {"token": existing["token"], "pack": _public(existing), "reused": True}

    token = uuid.uuid4().hex[:12]
    folder = uploads / token
    folder.mkdir(parents=True, exist_ok=False)
    archive = folder / f"source{suffix}"
    try:
        try:
            os.link(source, archive)
        except OSError:
            shutil.copy2(source, archive)
        info = _parse_file(archive, suffix.lstrip("."), safe_name)
        info.update(
            {
                "token": token,
                "original_name": safe_name,
                "archive": archive.name,
                "size": size,
                "sha256": digest,
                "created_at": _now(),
            }
        )
        _write_json(folder / "pack.json", info)
        return {"token": token, "pack": _public(info), "reused": False}
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _public(info: dict) -> dict:
    return {
        key: value
        for key, value in info.items()
        if key not in {"archive"}
    }


def discard_upload(data_dir: str | Path, token: str) -> dict:
    token = str(token or "").strip()
    if not TOKEN_RE.fullmatch(token):
        raise PackError("无效的语音包编号")
    installed = _load_installed(data_dir)
    if any(entry.get("token") == token for entry in installed.get("packs", [])):
        raise PackError("这个语音包已经安装，请先卸载再删除解析文件")
    folder = _pack_root(data_dir) / "uploads" / token
    if not folder.is_dir():
        return {"discarded": False}
    shutil.rmtree(folder)
    return {"discarded": True}


def load_pack(data_dir: str | Path, token: str) -> dict:
    token = str(token or "").strip()
    if not TOKEN_RE.fullmatch(token):
        raise PackError("无效的语音包编号")
    path = _pack_root(data_dir) / "uploads" / token / "pack.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError("语音包解析结果不存在，请重新导入") from exc
    if not isinstance(data, dict) or not isinstance(data.get("banks"), list):
        raise PackError("语音包解析结果损坏，请重新导入")
    return data


def _archive_path(data_dir: str | Path, pack: dict) -> Path:
    return _pack_root(data_dir) / "uploads" / pack["token"] / pack["archive"]


def game_running() -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq aces.exe", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "aces.exe" in (completed.stdout or "").lower()


def _valid_game(cfg: config.AppConfig) -> Path:
    path = Path(cfg.game_path).expanduser() if cfg.game_path else None
    if not path or not game.is_warthunder_dir(path):
        raise PackError("请先在“工作台”选择有效的战雷目录")
    return path.resolve()


def inspect_game(
    cfg: config.AppConfig,
    data_dir: str | Path,
    pack: dict,
) -> dict:
    try:
        root = _valid_game(cfg)
    except PackError as exc:
        return {
            "valid": False,
            "reason": str(exc),
            "running": game_running(),
            "enable_mod": None,
            "config": {key: None for key in CONFIG_KEYS},
            "mod_dir": "",
            "conflicts": [],
        }
    mod_dir = root / "sound" / "mod"
    config_info = game.read_config_blk(root)
    installed = _load_installed(data_dir)
    owners = installed.get("bank_owner", {})
    owner_names = {
        item.get("id"): item.get("name", "未命名语音包")
        for item in installed.get("packs", [])
    }
    conflicts = []
    for bank in pack.get("banks", []):
        target = mod_dir / bank["name"]
        base = root / "sound" / bank["name"]
        same = False
        if target.is_file():
            try:
                same = sha256_file(target) == bank.get("sha256")
            except OSError:
                same = False
        owner = owners.get(bank["name"], "")
        conflicts.append(
            {
                "name": bank["name"],
                "base_present": base.is_file(),
                "mod_present": target.is_file(),
                "mod_same": same,
                "owner": owner,
                "owner_name": owner_names.get(owner, ""),
            }
        )
    return {
        "valid": True,
        "path": str(root),
        "mod_dir": str(mod_dir),
        "running": game_running(),
        "enable_mod": config_info.get("enable_mod"),
        "config": _config_flags(config_info),
        "has_sound_section": config_info.get("has_sound_section"),
        "conflicts": conflicts,
    }


def _set_config_flags(
    config_path: Path,
    changes: dict[str, bool],
    backup_dir: Path | None = None,
) -> Path:
    if not changes:
        return config_path
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PackError(f"读取 config.blk 失败：{exc}") from exc
    if backup_dir:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, backup_dir / "config.blk")
    missing: list[str] = []
    for name, enabled in changes.items():
        value = "yes" if enabled else "no"
        pattern = re.compile(
            rf"(\b{re.escape(name)}\s*:\s*b\s*=\s*)(yes|no)\b", re.I
        )
        if pattern.search(text):
            text = pattern.sub(lambda match: match.group(1) + value, text, count=1)
        else:
            missing.append(f"  {name}:b={value}")
    if missing:
        section = re.search(r"(sound\s*\{)", text, re.I)
        if section:
            insert = section.end()
            text = text[:insert] + "\n" + "\n".join(missing) + text[insert:]
        else:
            text = text.rstrip() + "\n\nsound{\n" + "\n".join(missing) + "\n}\n"
    temp = config_path.with_name(config_path.name + ".wtvoice.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, config_path)
    return config_path


def set_enable_mod(cfg: config.AppConfig, data_dir: str | Path, enabled: bool) -> dict:
    root = _valid_game(cfg)
    if game_running():
        raise PackError("游戏正在运行，请先关闭游戏再修改 config.blk")
    backup = (
        _pack_root(data_dir)
        / "backups"
        / f"config_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"
    )
    _set_config_flags(root / "config.blk", {"enable_mod": enabled}, backup)
    return {"enable_mod": enabled, "backup": str(backup / "config.blk")}


def _config_changes(enable_mod: bool, config: dict | None) -> dict[str, bool]:
    changes: dict[str, bool] = {}
    if enable_mod:
        changes["enable_mod"] = True
    if isinstance(config, dict):
        for key, actual in CONFIG_KEYS.items():
            value = config.get(key)
            if isinstance(value, bool):
                changes[actual] = value
    return changes


def _installed_path(data_dir: str | Path) -> Path:
    return _pack_root(data_dir) / "installed.json"


def _load_installed(data_dir: str | Path) -> dict:
    try:
        data = json.loads(_installed_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "packs": [], "bank_owner": {}}
    if not isinstance(data, dict):
        return {"schema": 1, "packs": [], "bank_owner": {}}
    data.setdefault("packs", [])
    data.setdefault("bank_owner", {})
    return data


def _save_installed(data_dir: str | Path, data: dict) -> None:
    _write_json(_installed_path(data_dir), data)


def install(
    cfg: config.AppConfig,
    data_dir: str | Path,
    token: str,
    enable_mod: bool = False,
    config: dict | None = None,
    allow_masterbank: bool = False,
) -> dict:
    root = _valid_game(cfg)
    if game_running():
        raise PackError("游戏正在运行，请先关闭游戏再安装语音包")
    changes = _config_changes(enable_mod, config)
    pack = load_pack(data_dir, token)
    protected = [bank["name"] for bank in pack.get("banks", []) if bank.get("protected")]
    if protected and not allow_masterbank:
        raise PackError(
            "这个包含 masterbank 核心 bank（常见于 W.A.V.E. 降噪包），"
            "默认拒绝安装；确认用途后在安装页勾选“允许安装 masterbank”"
        )
    archive = _archive_path(data_dir, pack)
    if not archive.is_file():
        raise PackError("语音包源文件不存在，请重新导入")
    mod_dir = root / "sound" / "mod"
    mod_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    backup_root = _pack_root(data_dir) / "backups" / f"{stamp}_{pack['token']}_{suffix}"
    backup_banks = backup_root / "banks"
    previous = _load_installed(data_dir)
    previous_entry = next(
        (item for item in previous.get("packs", []) if item.get("token") == pack["token"]),
        None,
    )
    previous_banks = {
        item.get("name"): item for item in (previous_entry or {}).get("banks", [])
    }
    records = []
    for bank in pack["banks"]:
        target = mod_dir / bank["name"]
        backup = ""
        old = previous_banks.get(bank["name"], {})
        if target.is_file():
            current = ""
            try:
                current = sha256_file(target)
            except OSError:
                current = ""
            if old and current == old.get("sha256"):
                # 重装同一个包：目标文件本来就是本工具写的，沿用最初的备份。
                backup = old.get("backup", "")
            else:
                backup_banks.mkdir(parents=True, exist_ok=True)
                backup_file = backup_banks / bank["name"]
                shutil.copy2(target, backup_file)
                backup = backup_file.relative_to(data_dir).as_posix()
        elif old:
            backup = old.get("backup", "")
        owner = previous.get("bank_owner", {}).get(bank["name"], "")
        if previous_entry and owner == previous_entry.get("id"):
            owner = ""
        records.append(
            {
                "name": bank["name"],
                "sha256": bank["sha256"],
                "backup": backup,
                "previous_owner": owner,
            }
        )

    try:
        if pack["source_type"] == "bank":
            _copy_banks_from_file(archive, pack, mod_dir)
        else:
            with zipfile.ZipFile(archive) as zipped:
                _copy_banks_from_zip(zipped, pack, mod_dir)
    except Exception:
        _rollback_banks(mod_dir, records, data_dir)
        raise

    config_backup = ""
    try:
        if changes:
            _set_config_flags(root / "config.blk", changes, backup_root)
            config_backup = (backup_root / "config.blk").relative_to(data_dir).as_posix()
    except Exception:
        _rollback_banks(mod_dir, records, data_dir)
        backed_config = backup_root / "config.blk"
        if backed_config.is_file():
            try:
                shutil.copy2(backed_config, root / "config.blk")
            except OSError:
                pass
        raise

    installed = previous
    if previous_entry:
        installed["packs"] = [
            item for item in installed.get("packs", []) if item.get("id") != previous_entry.get("id")
        ]
        for name, owner in list(installed.get("bank_owner", {}).items()):
            if owner == previous_entry.get("id"):
                installed["bank_owner"].pop(name, None)
    pack_id = "pk_" + uuid.uuid4().hex[:12]
    entry = {
        "id": pack_id,
        "token": pack["token"],
        "name": pack["name"],
        "source_type": pack["source_type"],
        "installed_at": _now(),
        "banks": records,
        "backup": backup_root.relative_to(data_dir).as_posix(),
        "config_backup": config_backup,
        "enable_mod": bool(changes.get("enable_mod")),
        "config_flags": {key: value for key, value in changes.items()},
        "masterbank": bool(protected),
    }
    installed["packs"].insert(0, entry)
    for record in records:
        installed["bank_owner"][record["name"]] = pack_id
    _save_installed(data_dir, installed)
    return entry


def _copy_banks_from_file(archive: Path, pack: dict, mod_dir: Path) -> None:
    for bank in pack["banks"]:
        temp = mod_dir / f".uploading-{uuid.uuid4().hex[:8]}.bank"
        try:
            shutil.copy2(archive, temp)
            if sha256_file(temp) != bank["sha256"]:
                raise PackError(f"{bank['name']} 校验失败，安装已中止")
            os.replace(temp, mod_dir / bank["name"])
        finally:
            try:
                temp.unlink()
            except OSError:
                pass


def _copy_banks_from_zip(zipped: zipfile.ZipFile, pack: dict, mod_dir: Path) -> None:
    for bank in pack["banks"]:
        temp = mod_dir / f".uploading-{uuid.uuid4().hex[:8]}.bank"
        try:
            with zipped.open(bank["member"]) as source, temp.open("wb") as handle:
                shutil.copyfileobj(source, handle, 1024 * 1024)
            if sha256_file(temp) != bank["sha256"]:
                raise PackError(f"{bank['name']} 校验失败，安装已中止")
            os.replace(temp, mod_dir / bank["name"])
        finally:
            try:
                temp.unlink()
            except OSError:
                pass


def _rollback_banks(mod_dir: Path, records: list[dict], data_dir: str | Path) -> None:
    for record in records:
        target = mod_dir / record["name"]
        backup = record.get("backup")
        if backup:
            source = Path(data_dir) / backup
            if source.is_file():
                shutil.copy2(source, target)
        else:
            try:
                target.unlink()
            except OSError:
                pass


def _contents_cache(data_dir: str | Path, bank: dict) -> Path:
    digest = str(bank.get("sha256") or "")[:40] or "unknown"
    return Path(data_dir) / "cache" / "pack_contents" / f"{digest}.json"


def _pack_bank_streams(data_dir: str | Path, pack: dict, bank: dict) -> dict:
    cache = _contents_cache(data_dir, bank)
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = None
    if (
        isinstance(cached, dict)
        and cached.get("vgmstream") == vgmstream.VERSION
        and isinstance(cached.get("streams"), list)
    ):
        return cached

    archive = _archive_path(data_dir, pack)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="wtv_pack_contents_", dir=str(cache.parent)))
    try:
        if pack.get("source_type") == "bank":
            source = archive
        else:
            member = str(bank.get("member") or "")
            if not member:
                raise PackError(f"{bank.get('name')} 缺少包内路径")
            source = temp / Path(str(bank.get("name") or "bank.bank")).name
            with zipfile.ZipFile(archive) as zipped, zipped.open(member) as stream:
                with source.open("wb") as handle:
                    shutil.copyfileobj(stream, handle, 1024 * 1024)
        try:
            streams = [
                {
                    "index": int(item.get("index") or 0),
                    "name": str(item.get("name") or ""),
                    "duration_sec": float(item.get("duration_sec") or 0),
                }
                for item in localized.game_streams(source)
            ]
        except localized.LocalizedError as exc:
            return {
                "bank": bank.get("name"),
                "streams": [],
                "stream_total": 0,
                "event_total": 0,
                "duration_sec": 0.0,
                "error": str(exc),
            }
        data = {
            "vgmstream": vgmstream.VERSION,
            "bank": bank.get("name"),
            "streams": streams,
            "stream_total": len(streams),
            "event_total": len({item["name"] for item in streams}),
            "duration_sec": round(sum(item["duration_sec"] for item in streams), 2),
        }
        temporary = cache.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, cache)
        return data
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def contents(data_dir: str | Path, token: str) -> dict:
    """只读查看包内 bank 的流名/时长，以及 manifest 里的台词映射。"""
    pack = load_pack(data_dir, token)
    manifest = pack.get("manifest") if isinstance(pack.get("manifest"), dict) else {}
    mapping = manifest.get("mapping") if isinstance(manifest.get("mapping"), list) else []
    lines = []
    for row in mapping[:20000]:
        if not isinstance(row, dict):
            continue
        lines.append(
            {
                "event": str(row.get("event") or row.get("event_id") or ""),
                "bank": str(row.get("bank") or ""),
                "text": str(row.get("text") or ""),
                "target_file": str(row.get("target_file") or ""),
            }
        )
    banks = []
    for bank in pack.get("banks", []):
        info = _pack_bank_streams(data_dir, pack, bank)
        banks.append(
            {
                "name": bank.get("name"),
                "size": bank.get("size"),
                "sha256": bank.get("sha256"),
                **info,
            }
        )
    return {
        "name": pack.get("name"),
        "token": pack.get("token"),
        "source_type": pack.get("source_type"),
        "layout": pack.get("layout"),
        "bank_count": len(banks),
        "banks": banks,
        "lines": lines,
        "lines_total": len(mapping),
        "note": (
            "流名和时长由本机 vgmstream 只读解析；manifest 里的台词映射是作者写入的。"
            "社区包若没有 manifest，只能看到 bank 内的流名。"
        ),
    }


def conflicts(data_dir: str | Path, token: str = "") -> dict:
    """报告多个包共用同一 bank 时的覆盖关系；不合并编译后的 bank。"""
    data = _load_installed(data_dir)
    target = load_pack(data_dir, token) if str(token or "").strip() else None
    owners: dict[str, list[dict]] = {}
    for entry in data.get("packs", []):
        for bank in entry.get("banks", []):
            name = str(bank.get("name") or "")
            if not name:
                continue
            owners.setdefault(name, []).append(
                {
                    "pack": entry.get("name"),
                    "pack_id": entry.get("id"),
                    "token": entry.get("token"),
                    "sha256": bank.get("sha256"),
                }
            )
    if target:
        names = [str(bank.get("name") or "") for bank in target.get("banks", [])]
        target_sha = {
            str(bank.get("name") or ""): bank.get("sha256")
            for bank in target.get("banks", [])
        }
        rows = []
        for name in names:
            current = owners.get(name, [])
            others = [
                item for item in current if item.get("token") != target.get("token")
            ]
            same_audio = bool(
                target_sha.get(name)
                and any(item.get("sha256") == target_sha[name] for item in current)
            )
            if others and same_audio:
                action = "same_audio_shared"
            elif others:
                action = "overwrite"
            elif current:
                action = "reinstall"
            else:
                action = "new"
            if action in {"overwrite", "same_audio_shared"}:
                rows.append(
                    {"bank": name, "action": action, "owners": others}
                )
        return {
            "merge_supported": False,
            "note": (
                "编译后的 bank 没有可靠的合并写入器，应用不会把两个包的音频合成一个 bank。"
                "安装会整张覆盖同名 bank，并保留备份和原所有者记录。"
            ),
            "banks": rows,
            "overwrite_count": sum(
                1 for row in rows if row["action"] == "overwrite"
            ),
            "shared_count": sum(
                1 for row in rows if row["action"] == "same_audio_shared"
            ),
        }
    rows = [
        {"bank": name, "owners": entries}
        for name, entries in sorted(owners.items())
        if len(entries) > 1
    ]
    return {
        "merge_supported": False,
        "note": "同一 bank 被多个已安装包记录时，只有最后安装的那份生效。",
        "banks": rows,
    }


def list_installed(cfg: config.AppConfig, data_dir: str | Path) -> dict:
    data = _load_installed(data_dir)
    try:
        root = _valid_game(cfg)
        mod_dir = root / "sound" / "mod"
        config_info = game.read_config_blk(root)
        valid = True
        reason = ""
    except PackError as exc:
        root = None
        mod_dir = None
        config_info = {"enable_mod": None}
        valid = False
        reason = str(exc)
    packs = []
    for entry in data.get("packs", []):
        banks = []
        healthy = True
        for record in entry.get("banks", []):
            target = mod_dir / record["name"] if mod_dir else None
            stale = False
            if not target or not target.is_file():
                status = "missing"
            else:
                try:
                    status = "ok" if sha256_file(target) == record.get("sha256") else "overwritten"
                except OSError:
                    status = "missing"
                base = root / "sound" / record["name"] if root else None
                if base and base.is_file():
                    try:
                        stale = base.stat().st_mtime > target.stat().st_mtime + 1
                    except OSError:
                        stale = False
            if status != "ok":
                healthy = False
            banks.append({**record, "status": status, "stale": stale})
        packs.append({**entry, "banks": banks, "healthy": healthy})
    untracked = []
    risky = []
    if mod_dir and mod_dir.is_dir():
        known = {record["name"] for entry in data.get("packs", []) for record in entry.get("banks", [])}
        try:
            for item in sorted(mod_dir.iterdir()):
                if not item.is_file():
                    continue
                lower = item.name.lower()
                if lower in RISKY_BANKS and item.name not in known:
                    risky.append(
                        {
                            "name": item.name,
                            "reason": "社区反馈：mod 里的 masterbank / 旧 hangar.bank 可能导致无声、爆音或卡顿",
                        }
                    )
                if lower.endswith(BANK_SUFFIX) and item.name not in known:
                    untracked.append({"name": item.name, "size": item.stat().st_size})
        except OSError:
            pass
    return {
        "valid": valid,
        "reason": reason,
        "game": str(root) if root else "",
        "mod_dir": str(mod_dir) if mod_dir else "",
        "enable_mod": config_info.get("enable_mod"),
        "config": _config_flags(config_info),
        "running": game_running(),
        "packs": packs,
        "untracked": untracked,
        "risky": risky,
    }


def _original_config_backup(data_dir: str | Path) -> Path | None:
    backups = _pack_root(data_dir) / "backups"
    try:
        candidates = [
            item / "config.blk"
            for item in backups.iterdir()
            if item.is_dir() and (item / "config.blk").is_file()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    # 同一秒内连续安装两个包时目录名的时间戳相同，按备份文件写入时间取最早。
    try:
        return min(candidates, key=lambda item: (item.stat().st_mtime_ns, item.as_posix()))
    except OSError:
        return min(candidates, key=lambda item: item.as_posix())


def _restore_original_config(root: Path, data_dir: str | Path) -> str:
    backup = _original_config_backup(data_dir)
    if not backup:
        return ""
    shutil.copy2(backup, root / "config.blk")
    return str(backup)


def restore_all(cfg: config.AppConfig, data_dir: str | Path) -> dict:
    """按安装顺序倒着卸载所有语音包，并把 config.blk 还原到最早备份。"""
    root = _valid_game(cfg)
    if game_running():
        raise PackError("游戏正在运行，请先关闭游戏再还原原版音效")
    initial = _load_installed(data_dir).get("packs", [])
    removed_packs = 0
    removed_banks = 0
    restored_banks = 0
    skipped = 0
    missing = 0
    errors: list[dict] = []
    config_restored = ""
    for entry in list(initial):
        try:
            result = uninstall(cfg, data_dir, entry.get("id", ""))
        except PackError as exc:
            errors.append({"name": entry.get("name") or "未命名语音包", "error": str(exc)})
            continue
        removed_packs += 1
        removed_banks += int(result.get("removed") or 0)
        restored_banks += int(result.get("restored") or 0)
        skipped += int(result.get("skipped") or 0)
        missing += int(result.get("missing") or 0)
        if result.get("config_restored"):
            config_restored = str(result["config_restored"])
    remaining = _load_installed(data_dir).get("packs", [])
    return {
        "removed_packs": removed_packs,
        "remaining_packs": len(remaining),
        "removed": removed_banks,
        "restored": restored_banks,
        "skipped": skipped,
        "missing": missing,
        "errors": errors,
        "config_restored": config_restored,
        "untracked": [
            item.get("name")
            for item in list_installed(cfg, data_dir).get("untracked", [])
        ],
    }


def uninstall(cfg: config.AppConfig, data_dir: str | Path, pack_id: str) -> dict:
    root = _valid_game(cfg)
    if game_running():
        raise PackError("游戏正在运行，请先关闭游戏再卸载语音包")
    data = _load_installed(data_dir)
    pack_id = str(pack_id or "").strip()
    entry = next((item for item in data.get("packs", []) if item.get("id") == pack_id), None)
    if not entry:
        raise PackError("没有这条安装记录")
    owners = data.get("bank_owner", {})
    covering = [
        record.get("name", "")
        for record in entry.get("banks", [])
        if owners.get(record.get("name", "")) not in ("", pack_id)
    ]
    if covering:
        raise PackError(
            "这个语音包的 bank 已被后安装的语音包覆盖，"
            "请先卸载最新的那个语音包再试（" + "、".join(covering[:3]) + "）"
        )
    mod_dir = root / "sound" / "mod"
    removed = 0
    restored = 0
    skipped = 0
    missing = 0
    for record in entry.get("banks", []):
        target = mod_dir / record["name"]
        if not target.is_file():
            missing += 1
            continue
        try:
            current = sha256_file(target)
        except OSError:
            missing += 1
            continue
        if current != record.get("sha256"):
            skipped += 1
            continue
        try:
            target.unlink()
        except OSError:
            skipped += 1
            continue
        removed += 1
        backup = record.get("backup")
        if backup:
            source = Path(data_dir) / backup
            if source.is_file():
                shutil.copy2(source, target)
                restored += 1
    data["packs"] = [item for item in data.get("packs", []) if item.get("id") != pack_id]
    for record in entry.get("banks", []):
        name = record.get("name", "")
        if data.get("bank_owner", {}).get(name) != pack_id:
            continue
        previous_owner = record.get("previous_owner", "")
        if previous_owner:
            data["bank_owner"][name] = previous_owner
        else:
            data["bank_owner"].pop(name, None)
    for name, owner in list(data.get("bank_owner", {}).items()):
        if owner == pack_id:
            data["bank_owner"].pop(name, None)
    _save_installed(data_dir, data)
    config_restored = ""
    if not data.get("packs"):
        try:
            config_restored = _restore_original_config(root, data_dir)
        except OSError:
            config_restored = ""
    return {
        "removed": removed,
        "restored": restored,
        "skipped": skipped,
        "missing": missing,
        "config_restored": config_restored,
    }
