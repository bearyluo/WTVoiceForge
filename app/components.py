"""AI 运行时组件：官方 RVC 整合包的下载、校验、解压与启用。

只从 Hugging Face 官方仓库或其镜像下载原始整合包，不重新打包、不托管第三方文件。
下载支持断点续传；完成后按 SHA-256 校验，再解压并自动写入 config.rvc_path。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app import config
from app.engines import gpt_sovits, rvc

CHUNK_SIZE = 4 * 1024 * 1024
MIN_EXTRA_BYTES = 512 * 1024 * 1024
SLIM_PATHS = ("tools/asr", "tools/uvr5")
SLIM_SAVE_BYTES = 1_900_000_000
USER_AGENT = f"WTVoiceForge/{config.APP_VERSION}"

# 官方仓库：https://huggingface.co/lj1995/VoiceConversionWebUI
# size / sha256 为该仓库 LFS 元数据（2026-10 核对）。
CATALOG: tuple[dict, ...] = (
    {
        "id": "gpt-sovits",
        "name": "GPT-SoVITS · NVIDIA 整合包",
        "version": "v4 · 2025-05-29",
        "filename": "GPT-SoVITS-v4-20250529.7z",
        "repo": "lj1995/GPT-SoVITS-windows-package",
        "kind": "gpt_sovits",
        "size": 7_493_794_100,
        "sha256": "6461419c7c81f7c23a08e467c040eb25197bf8291ec1107e0104c46354b77146",
        "backend": "cuda",
        "backend_label": "CUDA",
        "recommend": "NVIDIA 6GB 显存起（RTX 3060 可用）",
        "note": "文本转语音 / 音色克隆；压缩包约 7.5GB，解压后更大。",
        "homepage": "https://huggingface.co/lj1995/GPT-SoVITS-windows-package",
    },
    {
        "id": "rvc-nvidia",
        "name": "RVC · NVIDIA 稳定版",
        "version": "2024-06-04",
        "filename": "RVC20240604Nvidia.7z",
        "repo": "lj1995/VoiceConversionWebUI",
        "kind": "rvc",
        "size": 5_599_416_462,
        "sha256": "86dfc8ef3d00274a7f0a18154591d0ef1af9bb05d998c1fb76d415103a749eff",
        "backend": "cuda",
        "backend_label": "CUDA",
        "recommend": "GTX 10 系 ~ RTX 40 系",
        "note": "内置运行环境；换声 2GB 起，轻量训练建议 4GB 显存起。",
        "homepage": "https://huggingface.co/lj1995/VoiceConversionWebUI",
    },
    {
        "id": "rvc-nvidia50",
        "name": "RVC · NVIDIA RTX 50 系",
        "version": "2026-07-18",
        "filename": "RVC20260718Nvidia50x0.7z",
        "repo": "lj1995/VoiceConversionWebUI",
        "kind": "rvc",
        "size": 7_606_183_207,
        "sha256": "d258bc0cbcce07136f05781f04aafb6f5e02c593f88c8fe8c62a4ab683751467",
        "backend": "cuda",
        "backend_label": "CUDA 12.8",
        "recommend": "RTX 50 系",
        "note": "RTX 50 系必须使用这一版；旧包不支持新架构。",
        "homepage": "https://huggingface.co/lj1995/VoiceConversionWebUI",
    },
    {
        "id": "rvc-amd",
        "name": "RVC · AMD / Intel（DirectML）",
        "version": "2026-07-18",
        "filename": "RVC20260718AMD_Intel.7z",
        "repo": "lj1995/VoiceConversionWebUI",
        "kind": "rvc",
        "size": 5_575_283_376,
        "sha256": "cdd7938a9be24566faf936da6092488ff18fb36539fe661172a70978cc76fb04",
        "backend": "dml",
        "backend_label": "DirectML",
        "recommend": "RX 6000/7000、Intel Arc",
        "note": "换声 4GB 起；A 卡训练为 Beta（6GB 起），4GB 不保证训练。",
        "homepage": "https://huggingface.co/lj1995/VoiceConversionWebUI",
    },
)

_BY_ID = {item["id"]: item for item in CATALOG}
ACTIVE_STATES = ("queued", "downloading", "verifying", "extracting")


class ComponentError(RuntimeError):
    pass


class ComponentBusy(ComponentError):
    pass


class _Cancelled(Exception):
    pass


def component(component_id: str) -> dict:
    item = _BY_ID.get(component_id)
    if not item:
        raise ComponentError(f"未知组件：{component_id}")
    return item


def download_urls(item: dict, source: str = "auto") -> list[str]:
    relative = item["filename"]
    repo = item.get("repo", "lj1995/VoiceConversionWebUI")
    official = f"https://huggingface.co/{repo}/resolve/main/{relative}?download=true"
    mirror = f"https://hf-mirror.com/{repo}/resolve/main/{relative}?download=true"
    if source == "official":
        return [official]
    if source == "mirror":
        return [mirror]
    return [mirror, official]


def default_dir(cfg: config.AppConfig | None = None) -> Path:
    cfg = cfg or config.load_config()
    if cfg.components_dir:
        return Path(cfg.components_dir).expanduser()
    base = Path(cfg.data_dir).expanduser() if cfg.data_dir else config.DEFAULT_DATA_DIR
    return base / "components"


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


def required_bytes(item: dict) -> int:
    # 压缩包和解压目录会同时存在；整合包含 Python/torch，解压后通常明显大于压缩包。
    # 按 2.5 倍再加 512MB 余量，避免解压到一半磁盘满。
    return int(item["size"] * 2.5) + MIN_EXTRA_BYTES


def _target_root(target: str | Path | None) -> Path:
    root = Path(target).expanduser() if target else default_dir()
    try:
        return root.resolve()
    except OSError:
        return root.absolute()


def preflight(component_id: str, target: str | Path | None = None, slim: bool = False) -> dict:
    item = component(component_id)
    root = _target_root(target)
    free = free_bytes(root)
    need = required_bytes(item)
    extractor = find_extractor()
    slim_ok = bool(slim) and item.get("kind") == "gpt_sovits"
    warnings: list[str] = []
    if not extractor or not extractor.get("available"):
        warnings.append("未找到解压工具，需要 7-Zip 或 Windows 10/11 自带的 tar。")
    if free and free < need:
        warnings.append(
            f"磁盘空间不足：还差约 {(need - free) / 1024 ** 3:.1f} GB，请更换安装位置或清理磁盘。"
        )
    if slim_ok:
        warnings.append("精简安装仍会先完整解压，装好后删除语音识别和人声分离组件。")
    return {
        "ok": bool(extractor) and (not free or free >= need),
        "component": item["id"],
        "name": item["name"],
        "filename": item["filename"],
        "target": str(root),
        "download_bytes": int(item["size"]),
        "required_bytes": need,
        "free_bytes": free,
        "shortfall_bytes": max(0, need - free) if free else 0,
        "extractor": extractor,
        "slim": slim_ok,
        "slim_estimate_bytes": SLIM_SAVE_BYTES if slim_ok else 0,
        "archive_present": (root / item["id"] / item["filename"]).is_file(),
        "warnings": warnings,
    }


def find_extractor() -> dict | None:
    """优先 7-Zip；没有就用 Windows 10+ 自带的 bsdtar。"""
    seven_zip_candidates = [
        config.APP_ROOT / "vendor" / "7zip" / "7zr.exe",
        config.APP_ROOT / "vendor" / "7zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ]
    which_7z = shutil.which("7z") or shutil.which("7za") or shutil.which("7zr")
    if which_7z:
        seven_zip_candidates.append(Path(which_7z))
    for candidate in seven_zip_candidates:
        if candidate.is_file():
            return {"available": True, "kind": "7z", "name": "7-Zip", "path": str(candidate)}

    if os.name == "nt":
        tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        if tar.is_file():
            return {"available": True, "kind": "tar", "name": "Windows tar", "path": str(tar)}
    else:
        tar = shutil.which("bsdtar") or shutil.which("tar")
        if tar:
            return {"available": True, "kind": "tar", "name": "tar", "path": tar}
    return None


def recommended_rvc(summary: dict | None = None) -> str:
    """按显卡挑官方 RVC 整合包；CPU 也可安装，只是很慢。"""
    if summary is None:
        from app import gpu

        summary = gpu.detect_summary()
    if summary.get("has_nvidia"):
        names = " ".join(str(item.get("name") or "") for item in summary.get("gpus") or [])
        if re.search(r"RTX\s*50", names, re.IGNORECASE):
            return "rvc-nvidia50"
        return "rvc-nvidia"
    if summary.get("has_amd") or summary.get("has_intel"):
        return "rvc-amd"
    return "rvc-nvidia"


def public_catalog() -> list[dict]:
    result = []
    for item in CATALOG:
        result.append(
            {
                "id": item["id"],
                "name": item["name"],
                "version": item["version"],
                "kind": item.get("kind", "rvc"),
                "size": item["size"],
                "sha256": item["sha256"],
                "backend": item["backend"],
                "backend_label": item["backend_label"],
                "recommend": item["recommend"],
                "note": item["note"],
                "homepage": item["homepage"],
                "filename": item["filename"],
            }
        )
    return result


class Job:
    def __init__(
        self,
        item: dict,
        target: Path,
        source: str,
        keep_archive: bool,
        slim: bool = False,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.component = item["id"]
        self.name = item["name"]
        self.filename = item["filename"]
        self.target = str(target)
        self.source = source
        self.keep_archive = keep_archive
        self.slim = bool(slim) and item.get("kind") == "gpt_sovits"
        self.slim_saved = 0
        self.state = "queued"
        self.message = "准备下载"
        self.error = ""
        self.total = int(item["size"])
        self.received = 0
        self.phase_percent: float | None = None
        self.speed = 0.0
        self.eta: int | None = None
        self.rvc_path = ""
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.cancel_event = threading.Event()
        self._last_time = time.monotonic()
        self._last_received = 0

    def progress(self, received: int) -> None:
        now = time.monotonic()
        elapsed = now - self._last_time
        if elapsed >= 0.5:
            inst = max(0, received - self._last_received) / elapsed
            self.speed = inst if self.speed <= 0 else self.speed * 0.65 + inst * 0.35
            remaining = max(0, self.total - received)
            self.eta = int(remaining / self.speed) if self.speed > 65536 else None
            self._last_time = now
            self._last_received = received
        self.received = received

    def snapshot(self) -> dict:
        if self.total:
            percent = min(100.0, self.received * 100.0 / self.total)
        else:
            percent = 0.0
        if self.state == "done":
            percent = 100.0
        elapsed = (self.finished_at or time.time()) - self.started_at
        return {
            "id": self.id,
            "component": self.component,
            "name": self.name,
            "filename": self.filename,
            "state": self.state,
            "active": self.state in ACTIVE_STATES,
            "message": self.message,
            "error": self.error,
            "target": self.target,
            "source": self.source,
            "total": self.total,
            "received": self.received,
            "percent": round(percent, 2),
            "phase_percent": round(self.phase_percent, 2) if self.phase_percent is not None else None,
            "speed": round(self.speed),
            "eta": self.eta,
            "rvc_path": self.rvc_path,
            "keep_archive": self.keep_archive,
            "slim": self.slim,
            "slim_saved": self.slim_saved,
            "elapsed": int(elapsed),
            "finished": self.finished_at is not None,
        }


class Manager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._job: Job | None = None
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict | None:
        with self._lock:
            return self._job.snapshot() if self._job else None

    def start(
        self,
        component_id: str,
        target: str | Path | None = None,
        keep_archive: bool = False,
        source: str = "auto",
        slim: bool = False,
    ) -> dict:
        item = component(component_id)
        if source not in ("auto", "official", "mirror"):
            raise ComponentError("下载源只能是 auto / official / mirror")
        target_root = _target_root(target)

        with self._lock:
            if self._job and self._job.state in ACTIVE_STATES:
                raise ComponentBusy(f"已有组件任务在运行：{self._job.name}")
            free = free_bytes(target_root)
            need = required_bytes(item)
            if free and free < need:
                raise ComponentError(
                    f"磁盘空间不足：需要约 {need / 1024 ** 3:.1f} GB，"
                    f"当前可用 {free / 1024 ** 3:.1f} GB。"
                )
            job = Job(item, target_root, source, keep_archive, slim)
            self._job = job
            self._thread = threading.Thread(
                target=self._run,
                args=(job, item),
                name=f"component-{item['id']}",
                daemon=True,
            )
            self._thread.start()
            return job.snapshot()

    def cancel(self) -> dict | None:
        with self._lock:
            job = self._job
            if job and job.state in ACTIVE_STATES:
                job.cancel_event.set()
                job.message = "正在取消…"
            return job.snapshot() if job else None

    # ---------- worker ----------

    def _run(self, job: Job, item: dict) -> None:
        dest = Path(job.target) / item["id"]
        archive = dest / item["filename"]
        part = archive.with_name(archive.name + ".part")
        extractor = find_extractor()
        try:
            dest.mkdir(parents=True, exist_ok=True)
            if not extractor:
                raise ComponentError(
                    "未找到解压工具。请安装 7-Zip，或使用 Windows 10/11（自带 tar 解压）。"
                )

            urls = download_urls(item, job.source)
            verified = False
            last_error = ""

            # 上次解压失败时，压缩包可能已经完整下载并通过校验，直接复用。
            if archive.exists():
                job.state = "verifying"
                job.message = "校验已下载的压缩包"
                job.phase_percent = 0.0
                if self._verify(job, archive, item["sha256"]):
                    verified = True
                    part.unlink(missing_ok=True)
                else:
                    archive.unlink(missing_ok=True)

            for index, url in enumerate(urls if not verified else []):
                if job.cancel_event.is_set():
                    raise _Cancelled
                source_label = "官方源" if "huggingface.co" in url else "镜像源"
                job.state = "downloading"
                job.message = f"从{source_label}下载 {item['name']}"
                try:
                    self._download(job, url, part, item["size"])
                except _Cancelled:
                    raise
                except Exception as exc:  # 网络错误：保留分片，尝试下一个源
                    last_error = f"{source_label}下载失败：{exc}"
                    job.message = last_error
                    if index == len(urls) - 1:
                        raise ComponentError(last_error) from exc
                    continue

                if job.cancel_event.is_set():
                    raise _Cancelled
                job.state = "verifying"
                job.message = "校验 SHA-256 完整性"
                job.phase_percent = 0.0
                if self._verify(job, part, item["sha256"]):
                    verified = True
                    break
                part.unlink(missing_ok=True)
                last_error = f"{source_label}文件校验失败（可能被代理篡改或下载损坏）"
                job.message = last_error
                if index == len(urls) - 1:
                    raise ComponentError(last_error)

            if not verified:
                raise ComponentError(last_error or "没有可用的下载源")

            if part.exists():
                part.replace(archive)
            job.phase_percent = None
            job.message = "解压运行时"
            job.state = "extracting"
            self._extract(job, extractor, archive, dest)

            if job.cancel_event.is_set():
                raise _Cancelled

            if item.get("kind") == "gpt_sovits":
                root = gpt_sovits.locate_install(dest)
                if not root:
                    raise ComponentError(
                        "解压完成，但没有找到 GPT-SoVITS 主目录（应包含 api_v2.py 和 runtime/python.exe）。"
                    )
                root = root.resolve()
                if job.slim:
                    job.message = "精简安装：移除语音识别和人声分离组件"
                    job.slim_saved = _prune_slim(root)
                config.update_config(
                    gpt_sovits_path=str(root),
                    components_dir=str(Path(job.target)),
                )
                job.rvc_path = str(root)
                job.message = (
                    "安装完成，已自动启用 GPT-SoVITS（精简版）"
                    if job.slim
                    else "安装完成，已自动启用 GPT-SoVITS"
                )
            else:
                rvc_root = rvc.locate_rvc(dest)
                if not rvc_root:
                    raise ComponentError(
                        "解压完成，但没有找到 RVC 主目录（应包含 webui.py 和 configs/assets）。"
                    )
                rvc_root = rvc_root.resolve()
                config.update_config(rvc_path=str(rvc_root), components_dir=str(Path(job.target)))
                job.rvc_path = str(rvc_root)
                job.message = "安装完成，已自动启用 RVC"
            if not job.keep_archive:
                archive.unlink(missing_ok=True)
            job.phase_percent = 100.0
            job.state = "done"
        except _Cancelled:
            job.state = "cancelled"
            job.error = ""
            job.message = "已取消；已下载的分片会保留，下次可续传"
        except Exception as exc:
            job.state = "error"
            job.error = str(exc)
            job.message = str(exc)
        finally:
            job.finished_at = time.time()
            job.phase_percent = None

    def _download(self, job: Job, url: str, part: Path, expected: int) -> None:
        existing = part.stat().st_size if part.exists() else 0
        if existing > expected:
            part.unlink()
            existing = 0
        if existing == expected:
            job.progress(existing)
            return

        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        req = urllib.request.Request(url, headers=headers)
        mode = "ab" if existing else "wb"
        received = existing
        job.progress(received)

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                status = getattr(response, "status", 200)
                if existing and status != 206:
                    # 服务器不支持续传：从头开始。
                    mode = "wb"
                    received = 0
                    job.progress(0)
                with open(part, mode) as handle:
                    while True:
                        if job.cancel_event.is_set():
                            raise _Cancelled
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        received += len(chunk)
                        job.progress(received)
                        if received > expected:
                            raise ComponentError("下载体积超过清单记录，已中止。")
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing == expected:
                return
            raise ComponentError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ComponentError(str(exc.reason)) from exc

        if received != expected:
            raise ComponentError(
                f"下载不完整：{received / 1024 ** 3:.2f} / {expected / 1024 ** 3:.2f} GB，可重试续传。"
            )

    def _verify(self, job: Job, part: Path, expected_sha: str) -> bool:
        if not part.exists():
            return False
        digest = hashlib.sha256()
        size = part.stat().st_size
        done = 0
        with open(part, "rb") as handle:
            while True:
                if job.cancel_event.is_set():
                    raise _Cancelled
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                done += len(chunk)
                if size:
                    job.phase_percent = done * 100.0 / size
        return digest.hexdigest().lower() == expected_sha.lower()

    def _extract(self, job: Job, extractor: dict, archive: Path, dest: Path) -> None:
        if extractor["kind"] == "7z":
            command = [
                extractor["path"],
                "x",
                str(archive),
                f"-o{dest}",
                "-y",
                "-bsp1",
                "-bso0",
                "-bse1",
            ]
        else:
            command = [extractor["path"], "-xf", str(archive), "-C", str(dest)]

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            creationflags=creationflags,
        )

        def read_progress() -> None:
            assert process.stdout is not None
            buffer = b""
            while True:
                chunk = process.stdout.read(512)
                if not chunk:
                    break
                buffer += chunk
                for match in re.findall(rb"(\d{1,3})%", buffer[-4096:]):
                    value = int(match)
                    if 0 <= value <= 100:
                        job.phase_percent = float(value)
                if len(buffer) > 8192:
                    buffer = buffer[-2048:]

        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        while process.poll() is None:
            if job.cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                reader.join(timeout=2)
                if process.stdout:
                    process.stdout.close()
                raise _Cancelled
            time.sleep(0.25)
        reader.join(timeout=3)
        if process.stdout:
            process.stdout.close()
        if process.returncode != 0:
            raise ComponentError(f"解压失败（{extractor['name']} 退出码 {process.returncode}）")


manager = Manager()


def _tree_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    stack = [path]
    while stack:
        current = stack.pop()
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
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def _force_write(func, path, exc) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _prune_slim(root: Path) -> int:
    root = root.resolve()
    saved = 0
    for relative in SLIM_PATHS:
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_dir():
            saved += _tree_size(target)
            shutil.rmtree(target, onexc=_force_write)
    return saved


def _component_bases(cfg: config.AppConfig) -> list[Path]:
    bases: list[Path] = []
    for candidate in (
        default_dir(cfg),
        Path(cfg.data_dir).expanduser() / "components" if cfg.data_dir else None,
        config.DEFAULT_DATA_DIR / "components",
    ):
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if resolved not in bases:
            bases.append(resolved)
    return bases


def _locate_installed(kind: str, cfg: config.AppConfig) -> Path | None:
    if kind == "gpt" and cfg.gpt_sovits_path:
        return gpt_sovits.locate_install(Path(cfg.gpt_sovits_path).expanduser())
    if kind == "rvc" and cfg.rvc_path:
        return rvc.locate_rvc(Path(cfg.rvc_path).expanduser())
    for base in _component_bases(cfg):
        if kind == "gpt":
            found = gpt_sovits.locate_install(base / "gpt-sovits")
            if found:
                return found
            continue
        for item in CATALOG:
            if item.get("kind", "rvc") != "rvc":
                continue
            found = rvc.locate_rvc(base / item["id"])
            if found:
                return found
    return None


def _valid_component_folder(kind: str, folder: Path) -> bool:
    if folder.is_symlink():
        return False
    name = folder.name.lower()
    if kind == "gpt":
        return name == "gpt-sovits"
    item = _BY_ID.get(folder.name)
    return name.startswith("rvc-") or bool(
        item and item.get("kind", "rvc") == "rvc"
    )


def _managed_folder(
    kind: str, root: Path, cfg: config.AppConfig
) -> tuple[Path | None, str]:
    for base in _component_bases(cfg):
        try:
            relative = root.resolve().relative_to(base)
        except (OSError, ValueError):
            continue
        if not relative.parts:
            continue
        folder = base / relative.parts[0]
        if folder.is_symlink():
            return None, "安装目录是快捷链接，已拒绝自动删除。"
        if not _valid_component_folder(kind, folder):
            return None, "安装目录结构不符合预期，已拒绝自动删除。"
        return folder, ""
    return None, "这个运行时在应用管理的 components 目录之外，不能自动删除。"


def uninstall_targets(cfg: config.AppConfig | None = None) -> dict:
    cfg = cfg or config.load_config()
    targets: dict[str, dict] = {}
    for kind, label in (("gpt", "GPT-SoVITS"), ("rvc", "RVC")):
        root = _locate_installed(kind, cfg)
        if not root:
            targets[kind] = {"available": False, "path": "", "folder": "", "reason": ""}
            continue
        folder, reason = _managed_folder(kind, root, cfg)
        targets[kind] = {
            "available": folder is not None,
            "path": str(root),
            "folder": str(folder) if folder else "",
            "reason": reason,
            "label": label,
        }
    return targets


def uninstall(component_id: str) -> dict:
    component_id = str(component_id or "").strip()
    known = _BY_ID.get(component_id)
    if component_id == "gpt-sovits" or (known and known.get("kind") == "gpt_sovits"):
        kind = "gpt"
    elif (
        component_id == "rvc"
        or component_id.startswith("rvc")
        or (known and known.get("kind", "rvc") == "rvc")
    ):
        kind = "rvc"
    else:
        raise ComponentError("只能卸载 GPT-SoVITS 或 RVC 运行时")

    with manager._lock:
        if manager._job and manager._job.state in ACTIVE_STATES:
            raise ComponentBusy(f"组件任务正在运行：{manager._job.name}，请先取消或等待完成")

    cfg = config.load_config()
    root = _locate_installed(kind, cfg)
    if not root:
        raise ComponentError("没有检测到已安装的运行时")
    folder, reason = _managed_folder(kind, root, cfg)
    if not folder:
        raise ComponentError(reason)

    if kind == "gpt":
        gpt_sovits.stop_server()
    size = _tree_size(folder)
    try:
        shutil.rmtree(folder, onexc=_force_write)
    except OSError as exc:
        raise ComponentError(
            f"删除运行时失败：{exc}。请关闭正在使用该目录的程序后重试。"
        ) from exc
    if folder.exists():
        raise ComponentError("运行时目录仍有文件被占用，请关闭相关程序后重试。")

    if kind == "gpt":
        config.update_config(gpt_sovits_path="")
    else:
        config.update_config(rvc_path="")
    return {
        "component": component_id,
        "kind": kind,
        "name": "GPT-SoVITS" if kind == "gpt" else "RVC",
        "removed_path": str(folder),
        "runtime_path": str(root),
        "freed_bytes": size,
    }


def describe() -> dict:
    cfg = config.load_config()
    target = default_dir(cfg)
    status = rvc.detect_rvc(cfg.rvc_path or None)
    extractor = find_extractor()
    free = free_bytes(target)
    return {
        "ok": True,
        "default_dir": str(target),
        "free_bytes": free,
        "required_bytes": {item["id"]: required_bytes(item) for item in CATALOG},
        "catalog": public_catalog(),
        "extractor": extractor,
        "job": manager.snapshot(),
        "rvc": status.to_dict(),
        "rvc_component": recommended_rvc(),
        "gpt_sovits": gpt_sovits.status(),
        "uninstall": uninstall_targets(cfg),
    }
