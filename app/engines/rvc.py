"""RVC 目录检测。

识别官方整合包和源码版共有的入口特征，不导入 torch。
训练/推理通过整合包自带 Python 和桥接脚本执行；A 卡训练另见 rvc_dml.py（Beta）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable

from app import config, gpu
from app.engines.base import EngineStatus

ENTRY_FILES = ("webui.py", "infer-web.py", "go-webui.bat", "go-web.bat")
STRUCTURE_DIRS = ("configs", "assets", "infer_pack", "train", "infer", ".venv", "runtime", "python")
COMMON_DIRS = (
    "RVC",
    "rvc",
    "RVC-WebUI",
    "Retrieval-based-Voice-Conversion-WebUI",
)
NAME_HINTS = ("rvc", "voice-conversion", "voice_conversion", "voice conversion")
SCAN_PARENTS = ("AI", "Tools", "Software", "Download", "Downloads", "MovedFromC", "app")
SKIP_CHILDREN = {
    ".git",
    ".venv",
    "venv",
    "assets",
    "configs",
    "infer_pack",
    "train",
    "infer",
    "logs",
    "weights",
    "runtime",
    "python",
    "__pycache__",
    "node_modules",
}

BACKEND_LABELS = {"cuda": "CUDA", "dml": "DirectML", "cpu": "CPU"}


def is_rvc_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_entry = any((path / name).is_file() for name in ENTRY_FILES)
    # RVC 根目录必须有 configs/config.py 或 infer/ 模块；GPT-SoVITS
    # 也有 webui.py、go-webui.bat 和 runtime/，不能只靠入口文件名判断。
    has_marker = (
        (path / "configs" / "config.py").is_file()
        or (path / "infer" / "modules" / "vc" / "modules.py").is_file()
        or (path / "infer_pack").is_dir()
    )
    has_structure = any((path / name).is_dir() for name in STRUCTURE_DIRS)
    return has_entry and has_marker and has_structure


def _children(path: Path) -> list[Path]:
    try:
        return sorted(
            (item for item in path.iterdir() if item.is_dir()),
            key=lambda item: item.name.lower(),
        )
    except OSError:
        return []


def locate_rvc(root: str | Path, max_depth: int = 2) -> Path | None:
    """在选中目录及其浅层子目录里找 RVC 主目录。"""
    root = Path(root)
    if is_rvc_dir(root):
        return root
    if not root.is_dir():
        return None
    children = _children(root)
    for child in children:
        if child.name.lower() in SKIP_CHILDREN:
            continue
        if is_rvc_dir(child):
            return child
    if max_depth >= 2:
        for child in children:
            if child.name.lower() in SKIP_CHILDREN:
                continue
            grandchildren = _children(child)
            if len(grandchildren) > 200:
                continue
            for grandchild in grandchildren:
                if is_rvc_dir(grandchild):
                    return grandchild
    return None


def diagnose(path: str | Path) -> list[str]:
    root = Path(path)
    if not root.exists():
        return ["目录不存在"]
    if not root.is_dir():
        return ["不是目录"]
    missing: list[str] = []
    if not any((root / name).is_file() for name in ENTRY_FILES):
        missing.append("入口文件 webui.py / infer-web.py / go-webui.bat")
    if not (
        (root / "configs" / "config.py").is_file()
        or (root / "infer" / "modules" / "vc" / "modules.py").is_file()
        or (root / "infer_pack").is_dir()
    ):
        missing.append("RVC 的 configs/config.py 或 infer/ 模块")
    if not any((root / name).is_dir() for name in STRUCTURE_DIRS):
        missing.append("configs / assets 等运行目录")
    return missing


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _scan_parent(parent: Path) -> list[Path]:
    found: list[Path] = []
    if not parent.is_dir():
        return found
    try:
        for item in parent.iterdir():
            if not item.is_dir():
                continue
            lowered = item.name.lower()
            if any(hint in lowered for hint in NAME_HINTS):
                found.append(item)
    except OSError:
        pass
    return found


def _candidates(explicit: str | Path | None = None) -> list[Path]:
    if explicit:
        return [Path(explicit)]

    candidates: list[Path] = []
    env_home = os.environ.get("RVC_HOME")
    if env_home:
        candidates.append(Path(env_home))

    bases = [Path("D:/"), Path("C:/"), Path("E:/")]
    for base in bases:
        for name in COMMON_DIRS:
            candidates.append(base / name)
        candidates.extend(_scan_parent(base))
        for parent_name in SCAN_PARENTS:
            candidates.extend(_scan_parent(base / parent_name))

    # 应用自己的组件目录：内置下载会把 RVC 解到 data/components 下，
    # 这里必须优先找，否则换一个工作盘/启动方式就识别不到已安装的运行时。
    try:
        cfg = config.load_config()
        data_dir = (
            Path(cfg.data_dir).expanduser() if cfg.data_dir else config.DEFAULT_DATA_DIR
        )
        components_dir = (
            Path(cfg.components_dir).expanduser()
            if cfg.components_dir
            else data_dir / "components"
        )
        candidates.append(components_dir)
        candidates.extend(_children(components_dir))
    except OSError:
        pass

    home = Path.home()
    for folder in ("Downloads", "Desktop", "Documents"):
        candidates.extend(_scan_parent(home / folder))
    candidates.extend(
        [
            home / "RVC",
            config.BASE_DIR / "RVC",
            config.BASE_DIR.parent / "RVC",
            config.BASE_DIR / "Retrieval-based-Voice-Conversion-WebUI",
        ]
    )
    return _dedupe(candidates)


def _status_for(path: Path, backend: str, backend_label: str) -> EngineStatus:
    notes = ["已接入换声训练与批量换声。"]
    if backend == "dml":
        notes.append("当前显卡走 DirectML：换声可用，训练为 Beta（6GB 起）。")
    elif backend == "cpu":
        notes.append("当前未检测到可用独显：可试听/少量换声，训练不建议。")
    return EngineStatus(
        name="RVC",
        available=True,
        path=str(path.resolve()),
        backend=backend,
        backend_label=backend_label,
        detail="检测到 RVC 主目录。",
        notes=notes,
    )


def detect_rvc(path: str | Path | None = None) -> EngineStatus:
    summary = gpu.detect_summary()
    backend = summary.get("recommended_backend", "cpu")
    backend_label = BACKEND_LABELS.get(backend, backend.upper())

    for candidate in _candidates(path):
        located = locate_rvc(candidate)
        if located:
            return _status_for(located, backend, backend_label)

    missing = diagnose(path) if path else []
    detail = (
        "没有识别到 RVC 主目录。需要 webui.py（或 go-webui.bat）以及 configs/assets。"
        if path
        else "未找到 RVC。可以直接下载官方整合包，或选择已有的 RVC 目录。"
    )
    notes = []
    if not path:
        notes.append("内置下载会从 Hugging Face 拉取官方整合包并校验 SHA-256。")
    return EngineStatus(
        name="RVC",
        available=False,
        backend=backend,
        backend_label=backend_label,
        detail=detail,
        notes=notes,
        missing=missing,
    )


class RvcError(RuntimeError):
    pass


def find_install(path: str | Path | None = None) -> Path | None:
    configured = path or config.load_config().rvc_path
    if configured:
        located = locate_rvc(configured)
        if located:
            return located
    for candidate in _candidates(None):
        located = locate_rvc(candidate)
        if located:
            return located
    return None


def runtime_python(root: str | Path) -> Path:
    root = Path(root)
    for name in ("runtime/python.exe", "python.exe", "runtime/python"):
        path = root / name
        if path.is_file():
            return path
    raise RvcError("RVC 整合包里没有找到 python.exe")


def bridge_path(name: str) -> Path:
    path = Path(__file__).resolve().parent / "rvc_bridge" / name
    if not path.is_file():
        raise RvcError(f"缺少 RVC 桥接脚本：{name}")
    return path


def _env(root: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    return env


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.kill()


def _run_bridge(job, root: Path, script: str, payload: dict, timeout: int) -> dict:
    from app import jobs

    config_path = Path(tempfile.mkdtemp(prefix="wtv-rvc-")) / "job.json"
    config_path.write_text(
        json.dumps({"root": str(root), **payload}, ensure_ascii=False),
        encoding="utf-8",
    )
    command = [str(runtime_python(root)), str(bridge_path(script)), str(config_path)]
    process = subprocess.Popen(
        command,
        cwd=str(root),
        env=_env(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: list[str] = []
    result: dict = {}
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            if job.cancel_event.is_set():
                _kill_tree(process)
                raise jobs.JobCancelled
            line = raw.rstrip()
            if not line:
                continue
            tail.append(line)
            tail = tail[-40:]
            data = None
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    data = None
            if isinstance(data, dict):
                kind = data.get("type")
                if kind == "error":
                    raise RvcError(str(data.get("error") or "RVC 任务失败"))
                if kind == "stage":
                    stage = str(data.get("stage") or "")
                    labels = {
                        "preprocess": "数据切分",
                        "f0": "提取音高",
                        "feature": "提取音色特征",
                        "train": "训练换声模型",
                        "index": "构建检索索引",
                        "infer": "批量换声",
                    }
                    job.update(message=labels.get(stage, stage or "RVC 处理中"))
                    if data.get("total"):
                        job.update(total=int(data["total"]), current=0)
                if kind == "progress":
                    total = int(data.get("total") or 0)
                    done = int(data.get("done") or 0)
                    job.update(
                        total=total,
                        current=done,
                        message=f"换声 {done}/{total}：{data.get('file', '')}",
                    )
                    if total:
                        job.update(percent=min(99, done / total * 100))
                if kind == "done":
                    result = data
            else:
                job.update(message=line[:110])
                match = re.search(r"Epoch:\s*(\d+)", line)
                if match:
                    epoch = int(match.group(1))
                    total = int(job.total or 0)
                    job.update(
                        current=epoch,
                        percent=(epoch / total * 100) if total else None,
                    )
        code = process.wait(timeout=timeout)
    finally:
        if process.poll() is None:
            _kill_tree(process)
        shutil.rmtree(config_path.parent, ignore_errors=True)
    if code != 0:
        raise RvcError(
            "RVC 进程失败（退出码 %s）：%s" % (code, " / ".join(tail[-4:]))
        )
    if not result:
        result = {"type": "done"}
    return result


def training_dataset(project_root: str | Path, work_dir: str | Path) -> Path:
    """从项目的训练切片或参考音频里整理 RVC 数据集。"""
    project_root = Path(project_root)
    target = Path(work_dir) / "dataset"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    sources = [project_root / "train" / "audio32k", project_root / "reference"]
    picked = []
    for folder in sources:
        if not folder.is_dir():
            continue
        items = sorted(
            path for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in {".wav", ".flac", ".mp3"}
        )
        if items:
            picked = items
            break
    if not picked:
        raise RvcError("项目里没有可用的训练片段，请先导入素材并训练")
    total = 0.0
    for index, source in enumerate(picked, 1):
        destination = target / f"{index:04d}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        try:
            import wave
            with wave.open(str(destination), "rb") as handle:
                total += handle.getnframes() / max(1, handle.getframerate())
        except Exception:
            pass
    if len(picked) < 3 or total < 8:
        raise RvcError(
            "换声训练至少需要 3 段、总时长 8 秒以上的素材；当前 %s 段 / %.1f 秒"
            % (len(picked), total)
        )
    return target


def train(
    job,
    project_root: str | Path,
    *,
    root: str | Path | None = None,
    epochs: int = 40,
    batch_size: int = 4,
) -> dict:
    """训练 RVC v2 32k 换声模型，返回模型与索引路径。"""
    root_path = find_install(root)
    if not root_path:
        raise RvcError("没有找到 RVC 整合包，请先在工作台安装 RVC 运行时")
    project_root = Path(project_root)
    work = project_root / "train" / "rvc"
    work.mkdir(parents=True, exist_ok=True)
    training_dataset(project_root, work)
    exp = "wtv_%s_%s" % (
        project_root.name.replace("p_", ""),
        time.strftime("%m%d_%H%M%S"),
    )
    model_out = work / "voice.pth"
    index_out = work / "voice.index"
    job.update(total=epochs, current=0, message="准备换声训练数据")
    result = _run_bridge(
        job,
        root_path,
        "train_bridge.py",
        {
            "dataset": str((work / "dataset").resolve()),
            "exp": exp,
            "model_out": str(model_out.resolve()),
            "index_out": str(index_out.resolve()),
            "epochs": int(epochs),
            "batch_size": int(batch_size),
            "sample_rate": "32k",
        },
        timeout=6 * 3600,
    )
    if not model_out.is_file():
        raise RvcError("训练完成但没有生成换声模型")
    return {
        "model": str(model_out),
        "index": str(index_out) if index_out.is_file() else "",
        "exp": exp,
        "segments": int(result.get("segments") or 0),
        "seconds": float(result.get("seconds") or 0),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def convert(
    job,
    *,
    root: str | Path | None,
    model: str | Path,
    index: str | Path | None,
    input_dir: str | Path,
    output_dir: str | Path,
    pitch: int = 0,
    index_rate: float = 0.75,
    protect: float = 0.33,
    rms_mix_rate: float = 1.0,
    f0_method: str = "rmvpe",
    timeout: int = 6 * 3600,
) -> dict:
    root_path = find_install(root)
    if not root_path:
        raise RvcError("没有找到 RVC 整合包，请先在工作台安装 RVC 运行时")
    model_path = Path(model)
    if not model_path.is_file():
        raise RvcError("换声模型不存在，请先训练换声模型")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = _run_bridge(
        job,
        root_path,
        "infer_bridge.py",
        {
            "model": str(model_path.resolve()),
            "index": str(Path(index).resolve()) if index else "",
            "input": str(Path(input_dir).resolve()),
            "output": str(output.resolve()),
            "pitch": int(pitch),
            "index_rate": float(index_rate),
            "protect": float(protect),
            "rms_mix_rate": float(rms_mix_rate),
            "f0_method": f0_method,
        },
        timeout=timeout,
    )
    return {"output": str(output), "total": int(result.get("total") or 0)}
