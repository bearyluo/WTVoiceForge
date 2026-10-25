"""vgmstream 运行时：发现、下载、校验与安全解压。

版本固定为官方 r2117（2026-05）。源码模式缺失时自动下载到数据目录；
打包版优先使用随 exe 分发的 vendor/vgmstream。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Callable

from app import config
from app.jobs import JobCancelled

VERSION = "r2117"
ZIP_NAME = "vgmstream-win64.zip"
URL = f"https://github.com/vgmstream/vgmstream/releases/download/{VERSION}/{ZIP_NAME}"
SIZE = 4_468_180
SHA256 = "6c4a8a3813864fefed081bbd337dbc0ad93bf88e0b92f5db98d7ab258b22dc6c"
USER_AGENT = f"WTVoiceForge/{config.APP_VERSION}"
CHUNK = 1024 * 1024
MAX_EXTRACT_BYTES = 256 * 1024 * 1024
EXE_NAME = "vgmstream-cli.exe"

Progress = Callable[[str, int, int | None, str], None]


class VgmstreamError(RuntimeError):
    pass


def install_dir() -> Path:
    cfg = config.load_config()
    base = Path(cfg.data_dir).expanduser() if cfg.data_dir else config.DEFAULT_DATA_DIR
    return base / "components" / "vgmstream"


def candidate_paths() -> list[Path]:
    roots = [config.APP_ROOT, config.BASE_DIR]
    paths = [root / "vendor" / "vgmstream" / EXE_NAME for root in roots]
    paths.extend(
        [
            install_dir() / EXE_NAME,
            install_dir() / "vgmstream-win64" / EXE_NAME,
        ]
    )
    return paths


def find() -> Path | None:
    seen: set[str] = set()
    for path in candidate_paths():
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    which = shutil.which("vgmstream-cli") or shutil.which("vgmstream-cli.exe")
    if which:
        return Path(which)
    return None


def is_bundled(path: Path | None) -> bool:
    if not path:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for root in (config.APP_ROOT, config.BASE_DIR):
        vendor = (root / "vendor" / "vgmstream").resolve()
        try:
            resolved.relative_to(vendor)
            return True
        except ValueError:
            continue
    return False


def version(path: Path | None = None) -> str:
    target = path or find()
    if not target:
        return ""
    try:
        completed = subprocess.run(
            [str(target), "-h"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    match = re.search(r"vgmstream CLI decoder\s+(\S+)", text)
    return match.group(1).strip() if match else ""


def status() -> dict:
    path = find()
    return {
        "available": bool(path),
        "path": str(path) if path else "",
        "bundled": is_bundled(path),
        "install_dir": str(install_dir()),
        "version": VERSION,
        "download": {
            "url": URL,
            "filename": ZIP_NAME,
            "size": SIZE,
            "sha256": SHA256,
        },
    }


def command(exe: str | Path, bank: str | Path, out_dir: str | Path) -> list[str]:
    """使用 ?s（子流序号）保证重名流不会互相覆盖。"""
    return [
        str(exe),
        "-S",
        "0",
        "-o",
        str(Path(out_dir) / "?s_?n.wav"),
        str(bank),
    ]


# ---------- 下载 / 校验 ----------


def _check_cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled


def _emit(progress: Progress | None, stage: str, current: int, total: int | None, message: str) -> None:
    if progress:
        progress(stage, current, total, message)


def _sha256_file(path: Path, progress: Progress | None = None, cancel_event=None) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    done = 0
    with path.open("rb") as handle:
        while True:
            _check_cancel(cancel_event)
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
            _emit(progress, "verify", done, size, "校验 vgmstream")
    return digest.hexdigest().lower()


def _download(url: str, part: Path, expected: int, progress: Progress | None = None, cancel_event=None) -> None:
    existing = part.stat().st_size if part.exists() else 0
    if existing > expected:
        part.unlink(missing_ok=True)
        existing = 0
    if existing == expected:
        _emit(progress, "download", existing, expected, "已下载 vgmstream")
        return

    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(url, headers=headers)
    mode = "ab" if existing else "wb"
    received = existing
    _emit(progress, "download", received, expected, "下载 vgmstream")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status_code = getattr(response, "status", 200)
            if existing and status_code != 206:
                mode = "wb"
                received = 0
                _emit(progress, "download", 0, expected, "下载 vgmstream")
            with part.open(mode) as handle:
                while True:
                    _check_cancel(cancel_event)
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if received > expected:
                        raise VgmstreamError("下载体积超过清单记录，已中止")
                    _emit(progress, "download", received, expected, "下载 vgmstream")
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and existing == expected:
            return
        raise VgmstreamError(f"下载 vgmstream 失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise VgmstreamError(f"下载 vgmstream 失败：{exc.reason}") from exc

    if received != expected:
        raise VgmstreamError(
            f"vgmstream 下载不完整：{received}/{expected} 字节，可重试续传"
        )


# ---------- 解压 ----------


def _safe_member_path(name: str, destination: Path) -> Path:
    raw = name.replace("\\", "/")
    if not raw or raw.endswith("/"):
        raise VgmstreamError(f"压缩包包含非法条目：{name!r}")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise VgmstreamError(f"压缩包包含绝对路径：{name}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts) or not parts:
        raise VgmstreamError(f"压缩包包含路径穿越：{name}")
    target = destination.joinpath(*parts)
    try:
        target.resolve().relative_to(destination.resolve())
    except ValueError as exc:
        raise VgmstreamError(f"压缩包条目逃出目标目录：{name}") from exc
    return target


def safe_extract_zip(archive: Path, destination: Path) -> int:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    extracted = 0
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == stat.S_IFLNK:
                raise VgmstreamError(f"压缩包包含符号链接：{info.filename}")
            total += info.file_size
            if total > MAX_EXTRACT_BYTES:
                raise VgmstreamError("压缩包解压后过大，已中止")
            target = _safe_member_path(info.filename, destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            extracted += 1
    return extracted


def _merge_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _merge_tree(child, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def install_to(
    destination: str | Path,
    progress: Progress | None = None,
    cancel_event=None,
    force: bool = False,
) -> Path:
    """下载并解压到指定目录，返回 vgmstream-cli.exe。幂等。"""
    destination = Path(destination).expanduser().resolve()
    exe = destination / EXE_NAME
    if exe.is_file() and not force:
        return exe

    destination.mkdir(parents=True, exist_ok=True)
    part = destination / (ZIP_NAME + ".part")
    archive = destination / ZIP_NAME
    try:
        if not (archive.is_file() and archive.stat().st_size == SIZE):
            _download(URL, part, SIZE, progress=progress, cancel_event=cancel_event)
            _check_cancel(cancel_event)
            _emit(progress, "verify", 0, SIZE, "校验 vgmstream")
            digest = _sha256_file(part, progress=progress, cancel_event=cancel_event)
            if digest != SHA256:
                part.unlink(missing_ok=True)
                raise VgmstreamError("vgmstream 校验失败（SHA-256 不匹配）")
            part.replace(archive)
        else:
            _check_cancel(cancel_event)
            _emit(progress, "verify", 0, SIZE, "校验 vgmstream")
            if _sha256_file(archive, progress=progress, cancel_event=cancel_event) != SHA256:
                archive.unlink(missing_ok=True)
                raise VgmstreamError("vgmstream 校验失败（SHA-256 不匹配）")

        _check_cancel(cancel_event)
        _emit(progress, "extract", 0, None, "解压 vgmstream")
        temp = destination / f".extract-{uuid.uuid4().hex[:8]}"
        try:
            safe_extract_zip(archive, temp)
            _check_cancel(cancel_event)
            _merge_tree(temp, destination)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        if not exe.is_file():
            raise VgmstreamError("解压完成但没有找到 vgmstream-cli.exe")
        _emit(progress, "extract", 1, 1, "vgmstream 就绪")
        return exe
    finally:
        # 失败/取消时保留 .part，下次可以续传；成功后它已被改名为 archive。
        if archive.exists() and exe.is_file() and not force:
            archive.unlink(missing_ok=True)


def ensure(job=None) -> Path:
    """确保 vgmstream 可用；有 job 时把进度写入 job。"""
    found = find()
    if found:
        return found

    def report(stage: str, current: int, total: int | None, message: str) -> None:
        if job is None:
            return
        job.update(stage=stage, current=current, total=total, message=message)

    exe = install_to(
        install_dir(),
        progress=report,
        cancel_event=job.cancel_event if job is not None else None,
    )
    if job is not None:
        job.update(message="vgmstream 已就绪")
    return exe
