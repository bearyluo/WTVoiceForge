"""数据目录磁盘占用统计。

扫描在后台线程执行，页面先拿到缓存结果或“统计中”，不会阻塞 8765 服务。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

ARCHIVE_SUFFIXES = {".7z", ".zip", ".rar", ".tar", ".gz", ".tgz", ".part"}
SCAN_TTL_SECONDS = 60.0
CLEAN_IDS = ("archives", "cache", "webview", "logs")
# 只清 WebView2 的缓存目录；Local Storage / 登录态等资料保留。
WEBVIEW_CACHE_DIRS = (
    "EBWebView/Default/Cache",
    "EBWebView/Default/Code Cache",
    "EBWebView/Default/GPUCache",
    "EBWebView/Default/DawnGraphiteCache",
    "EBWebView/Default/DawnWebGPUCache",
    "EBWebView/Default/Shared Dictionary",
    "EBWebView/Default/GrShaderCache",
    "EBWebView/Default/ShaderCache",
    "EBWebView/GrShaderCache",
    "EBWebView/ShaderCache",
    "EBWebView/GraphiteDawnCache",
    "EBWebView/component_crx_cache",
    "EBWebView/CertificateRevocation",
    "EBWebView/Crashpad/reports",
)
_WEBVIEW_CACHE_PREFIXES = tuple(tuple(Path(item).parts) for item in WEBVIEW_CACHE_DIRS)

CATEGORIES = (
    {
        "id": "gpt-sovits",
        "name": "GPT-SoVITS 语音引擎",
        "description": "中文配音和音色克隆的运行库；主要是 PyTorch/CUDA 和预训练模型，删掉后不能合成中文。",
        "kind": "required",
        "icon": "i-cpu",
        "paths": ["components/gpt-sovits"],
    },
    {
        "id": "rvc",
        "name": "RVC 换声引擎",
        "description": "原语言只换音色的可选运行库；不做换声可以删除，GPT 配音不受影响，之后能重新下载。",
        "kind": "optional",
        "icon": "i-wave",
        "paths": ["components/rvc-nvidia"],
    },
    {
        "id": "voice-projects",
        "name": "声音项目",
        "description": "参考音频、训练权重、合成台词和试听缓存；删项目前先确认不再需要。",
        "kind": "data",
        "icon": "i-mic",
        "paths": ["voice_projects"],
    },
    {
        "id": "packs",
        "name": "语音包与备份",
        "description": "导入的社区包、安装前的 bank 备份、解包缓存和导出的安装包。",
        "kind": "data",
        "icon": "i-box",
        "paths": ["packs", "语音包导出", "unpacked"],
    },
    {
        "id": "archives",
        "name": "安装包残留",
        "description": "安装时勾选“保留压缩包”留下的 .7z/.zip/.part；删掉不影响已经解压的运行时。不会碰运行库里的 Python 标准库。",
        "kind": "cleanable",
        "icon": "i-upload",
        "paths": [],
    },
    {
        "id": "cache",
        "name": "临时缓存",
        "description": "目录索引、试听和解包缓存；可以安全清理，软件会在下次使用时重建。",
        "kind": "cleanable",
        "icon": "i-database",
        "paths": ["cache"],
    },
    {
        "id": "webview",
        "name": "窗口缓存",
        "description": "内嵌浏览窗口的配置与网页缓存；只清网页缓存，登录和本地数据保留。",
        "kind": "cleanable",
        "icon": "i-grid",
        "paths": ["webview"],
    },
    {
        "id": "logs",
        "name": "日志",
        "description": "启动和任务日志，只用于排查问题；可以清理。",
        "kind": "cleanable",
        "icon": "i-sliders",
        "paths": ["logs"],
    },
    {
        "id": "other",
        "name": "其他数据",
        "description": "配置文件、数据库和未归类的文件。",
        "kind": "muted",
        "icon": "i-folder",
        "paths": [],
    },
)

_BY_ID = {item["id"]: item for item in CATEGORIES}
_lock = threading.Lock()
_cache: dict | None = None
_cache_dir = ""
_scan_thread: threading.Thread | None = None
_generation = 0


def classify(parts: tuple[str, ...]) -> str:
    if not parts:
        return "other"
    suffix = Path(parts[-1]).suffix.lower()
    if parts[0] == "components":
        # 只把组件目录顶层的下载残留算作可清理压缩包；
        # runtime/python39.zip 是 Python 标准库，删掉会让 GPT/RVC 无法启动。
        relative = parts[1:]
        if len(relative) <= 2 and suffix in ARCHIVE_SUFFIXES:
            return "archives"
        if relative:
            if relative[0] == "gpt-sovits":
                return "gpt-sovits"
            if relative[0] == "rvc-nvidia":
                return "rvc"
        return "other"
    if parts[0] == "voice_projects":
        return "voice-projects"
    if parts[0] in {"packs", "语音包导出", "unpacked"}:
        return "packs"
    if parts[0] == "cache":
        return "cache"
    if parts[0] == "webview":
        return "webview"
    if parts[0] == "logs":
        return "logs"
    return "other"


def _disk_usage(path: Path) -> tuple[int, int]:
    target = path if path.exists() else path.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return 0, 0
    return usage.total, usage.free


def _is_webview_cache(parts: tuple[str, ...]) -> bool:
    return any(parts[: len(prefix)] == prefix for prefix in _WEBVIEW_CACHE_PREFIXES)


def _cleanable_summary(
    buckets: dict[str, dict], webview_bytes: int = 0, webview_files: int = 0
) -> dict:
    counts = {
        "archives": buckets["archives"],
        "cache": buckets["cache"],
        "webview": {"bytes": webview_bytes, "files": webview_files},
        "logs": buckets["logs"],
    }
    return {
        "bytes": sum(int(item["bytes"]) for item in counts.values()),
        "files": sum(int(item["files"]) for item in counts.values()),
        "ids": [key for key, item in counts.items() if item["bytes"]],
        "items": [
            {"id": key, "bytes": int(item["bytes"]), "files": int(item["files"])}
            for key, item in counts.items()
            if item["bytes"]
        ],
    }


def scan(data_dir: str | Path) -> dict:
    root = Path(data_dir)
    buckets = {item["id"]: {"bytes": 0, "files": 0} for item in CATEGORIES}
    webview_cache_bytes = 0
    webview_cache_files = 0
    total_bytes = 0
    total_files = 0
    if root.is_dir():
        # scandir 的 DirEntry.stat 会复用目录枚举结果；对几万个小文件比
        # os.walk + Path.stat 快几十倍，避免第一次打开环境页等半分钟。
        stack: list[tuple[str, tuple[str, ...]]] = [(str(root), ())]
        while stack:
            current, parent_parts = stack.pop()
            try:
                entries = os.scandir(current)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append((entry.path, parent_parts + (entry.name,)))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    parts = parent_parts + (entry.name,)
                    category = classify(parts)
                    bucket = buckets[category]
                    bucket["bytes"] += size
                    bucket["files"] += 1
                    if category == "webview" and _is_webview_cache(parts):
                        webview_cache_bytes += size
                        webview_cache_files += 1
                    total_bytes += size
                    total_files += 1

    disk_total, disk_free = _disk_usage(root)
    items = []
    for definition in CATEGORIES:
        bucket = buckets[definition["id"]]
        items.append(
            {
                **{key: value for key, value in definition.items() if key != "paths"},
                "bytes": bucket["bytes"],
                "files": bucket["files"],
                "share": round(bucket["bytes"] / total_bytes * 100, 2) if total_bytes else 0.0,
            }
        )
    return {
        "ok": True,
        "data_dir": str(root),
        "total_bytes": total_bytes,
        "total_files": total_files,
        "disk_total_bytes": disk_total,
        "disk_free_bytes": disk_free,
        "scanned_at": time.time(),
        "scanning": False,
        "items": items,
        "cleanable": _cleanable_summary(
            buckets, webview_cache_bytes, webview_cache_files
        ),
    }


def _empty(data_dir: Path) -> dict:
    disk_total, disk_free = _disk_usage(data_dir)
    items = [
        {
            **{key: value for key, value in item.items() if key != "paths"},
            "bytes": 0,
            "files": 0,
            "share": 0.0,
        }
        for item in CATEGORIES
    ]
    return {
        "ok": True,
        "data_dir": str(data_dir),
        "total_bytes": 0,
        "total_files": 0,
        "disk_total_bytes": disk_total,
        "disk_free_bytes": disk_free,
        "scanned_at": 0,
        "scanning": True,
        "items": items,
        "cleanable": _cleanable_summary(
            {item["id"]: {"bytes": 0, "files": 0} for item in CATEGORIES}
        ),
    }


def _worker(root: Path, generation: int) -> None:
    global _cache, _cache_dir, _scan_thread
    try:
        result = scan(root)
    except Exception as exc:  # 统计失败不应影响 app
        result = {
            **_empty(root),
            "ok": False,
            "scanning": False,
            "error": str(exc),
        }
    with _lock:
        if generation != _generation:
            # 扫描期间清理过文件；这份结果已经过期，丢弃后由下一次轮询重扫。
            if _scan_thread is threading.current_thread():
                _scan_thread = None
            return
        _cache = result
        _cache_dir = str(root)
        _scan_thread = None


def usage(data_dir: str | Path, *, force: bool = False) -> dict:
    """Return the latest snapshot; start a background scan when needed."""
    global _cache, _cache_dir, _scan_thread, _generation
    root = Path(data_dir)
    now = time.time()
    with _lock:
        if _cache and _cache_dir != str(root):
            _cache = None
        # 正在扫描时始终返回 scanning，不能让旧缓存按 TTL 被当成“已完成”；
        # 否则强制刷新后前端会停止轮询，看不到新结果。
        if _scan_thread and _scan_thread.is_alive():
            current = _cache if _cache and _cache_dir == str(root) else _empty(root)
            return {**current, "scanning": True, "cached": bool(_cache)}
        fresh = (
            _cache is not None
            and not force
            and now - float(_cache.get("scanned_at") or 0) < SCAN_TTL_SECONDS
        )
        if fresh:
            return {**_cache, "scanning": False, "cached": True}
        _generation += 1
        _scan_thread = threading.Thread(
            target=_worker,
            args=(root, _generation),
            name="storage-scan",
            daemon=True,
        )
        _scan_thread.start()
        current = _cache if _cache and _cache_dir == str(root) else _empty(root)
        return {**current, "scanning": True, "cached": bool(_cache)}


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _tree(folder: Path) -> tuple[list[Path], list[tuple[Path, int]]]:
    dirs: list[Path] = []
    files: list[tuple[Path, int]] = []
    if not folder.is_dir():
        return dirs, files
    stack = [folder]
    while stack:
        current = stack.pop()
        dirs.append(current)
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        files.append((Path(entry.path), entry.stat(follow_symlinks=False).st_size))
                except OSError:
                    continue
    return dirs, files


def _purge(folder: Path, root: Path, *, remove_root: bool = False) -> dict:
    if not folder.is_dir() or not _within(folder, root):
        return {"bytes": 0, "files": 0, "skipped": 0}
    dirs, files = _tree(folder)
    freed = 0
    removed = 0
    skipped = 0
    for path, size in files:
        try:
            path.unlink()
        except OSError:
            skipped += 1
            continue
        freed += size
        removed += 1
    for current in reversed(dirs):
        if current == folder and not remove_root:
            continue
        try:
            os.rmdir(current)
        except OSError:
            pass
    return {"bytes": freed, "files": removed, "skipped": skipped}


def _invalidate() -> None:
    global _cache, _generation
    with _lock:
        _generation += 1
        _cache = None


def invalidate() -> None:
    """删除了数据目录里的文件后调用，让下一次统计重新扫描。"""
    _invalidate()


def _is_component_archive(path: Path, components: Path) -> bool:
    try:
        relative = path.relative_to(components)
    except ValueError:
        return False
    if path.suffix.lower() not in ARCHIVE_SUFFIXES:
        return False
    parts = relative.parts
    if len(parts) > 2:
        return False
    if any(part.lower() == "runtime" for part in parts[:-1]):
        return False
    if path.name.lower().startswith("python") and path.suffix.lower() == ".zip":
        return False
    return True


def cleanup(
    data_dir: str | Path,
    categories: list[str] | tuple[str, ...] | None = None,
    *,
    allow_archives: bool = True,
) -> dict:
    """只清理可重建的缓存、日志、失败压缩包和 WebView 缓存。"""
    root = Path(data_dir)
    requested = list(dict.fromkeys(categories or CLEAN_IDS))
    unknown = [item for item in requested if item not in CLEAN_IDS]
    if unknown:
        raise ValueError("未知清理项：" + "、".join(unknown))

    details: list[dict] = []
    skipped: list[str] = []
    try:
        if "cache" in requested:
            result = _purge(root / "cache", root)
            details.append({"id": "cache", **result})
        if "logs" in requested:
            result = _purge(root / "logs", root)
            details.append({"id": "logs", **result})
        if "webview" in requested:
            total = {"bytes": 0, "files": 0, "skipped": 0}
            for relative in WEBVIEW_CACHE_DIRS:
                result = _purge(root / "webview" / relative, root, remove_root=True)
                for key in total:
                    total[key] += result[key]
            details.append({"id": "webview", **total})
        if "archives" in requested:
            if not allow_archives:
                details.append({"id": "archives", "bytes": 0, "files": 0, "skipped": 0})
                skipped.append("组件下载任务正在运行，压缩包残留已跳过")
            else:
                total = {"bytes": 0, "files": 0, "skipped": 0}
                components = root / "components"
                if components.is_dir():
                    _, files = _tree(components)
                    for path, size in files:
                        if not _is_component_archive(path, components):
                            continue
                        try:
                            path.unlink()
                        except OSError:
                            total["skipped"] += 1
                            continue
                        total["bytes"] += size
                        total["files"] += 1
                details.append({"id": "archives", **total})
    finally:
        _invalidate()

    blocked = sum(item["skipped"] for item in details)
    if blocked:
        skipped.append(f"{blocked} 个文件正在使用或没有权限，已保留")
    return {
        "ok": True,
        "data_dir": str(root),
        "freed_bytes": sum(item["bytes"] for item in details),
        "removed_files": sum(item["files"] for item in details),
        "skipped_files": blocked,
        "categories": details,
        "skipped": skipped,
    }
