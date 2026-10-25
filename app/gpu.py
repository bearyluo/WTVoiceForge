"""显卡识别与后端建议。

M0 只做识别，不加载 torch：
- NVIDIA：优先 nvidia-smi 读准确显存；
- Windows 其他显卡：PowerShell CIM 读名称/驱动/AdapterRAM；
- 结果缓存 60 秒，避免首页每次刷新都起进程。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CACHE_TTL = 60.0
_cache: tuple[float, list["GpuInfo"]] | None = None


@dataclass
class GpuInfo:
    name: str
    vendor: str
    vram_mb: int | None
    driver: str
    backend: str
    backend_label: str
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


def _run(command: list[str], timeout: float = 8.0) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


VIRTUAL_GPU_MARKERS = (
    "virtual display",
    "remote display",
    "basic display",
    "basic render",
    "parsec",
    "gameviewer",
    "sunshine",
    "idd",
)


def _is_virtual_gpu(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in VIRTUAL_GPU_MARKERS)


def _vendor_of(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered:
        return "NVIDIA"
    if "amd" in lowered or "radeon" in lowered or "advanced micro devices" in lowered:
        return "AMD"
    if "intel" in lowered:
        return "Intel"
    return "Other"


def _backend_of(vendor: str) -> tuple[str, str]:
    if vendor == "NVIDIA":
        return "cuda", "CUDA"
    if vendor in ("AMD", "Intel"):
        return "dml", "DirectML"
    return "cpu", "CPU"


def _windows_gpus() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json -Compress"
    )
    output = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    if not output:
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _nvidia_smi() -> list[dict[str, Any]]:
    candidates = [
        shutil.which("nvidia-smi"),
        str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
        str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "nvidia-smi.exe"),
    ]
    exe = next((item for item in candidates if item and Path(item).is_file()), None)
    if not exe:
        return []

    output = _run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    result: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            vram_mb = int(float(parts[1]))
        except ValueError:
            vram_mb = None
        result.append({"name": parts[0], "vram_mb": vram_mb, "driver": parts[2]})
    return result


def detect_gpus(force: bool = False) -> list[GpuInfo]:
    global _cache
    now = time.monotonic()
    if not force and _cache and now - _cache[0] < _CACHE_TTL:
        return list(_cache[1])

    raw = _windows_gpus()
    smi = _nvidia_smi()
    smi_by_name = {str(item.get("name", "")).lower(): item for item in smi}

    gpus: list[GpuInfo] = []
    for item in raw:
        name = str(item.get("Name") or "Unknown GPU").strip()
        if _is_virtual_gpu(name):
            continue
        vendor = _vendor_of(name)
        backend, backend_label = _backend_of(vendor)
        driver = str(item.get("DriverVersion") or "").strip()
        vram_mb: int | None = None
        adapter_ram = item.get("AdapterRAM")
        if isinstance(adapter_ram, (int, float)) and adapter_ram > 0:
            vram_mb = int(adapter_ram / 1024 / 1024)

        # nvidia-smi 的显存更可靠（AdapterRAM 在 4GB 以上会溢出）
        smi_item = smi_by_name.get(name.lower())
        if not smi_item and vendor == "NVIDIA" and smi:
            smi_item = smi[0]
        if smi_item:
            vram_mb = smi_item.get("vram_mb") or vram_mb
            driver = str(smi_item.get("driver") or driver)

        gpus.append(
            GpuInfo(
                name=name,
                vendor=vendor,
                vram_mb=vram_mb,
                driver=driver,
                backend=backend,
                backend_label=backend_label,
                source="nvidia-smi+CIM" if smi_item else "CIM",
            )
        )

    if not gpus:
        for item in smi:
            name = str(item.get("name") or "NVIDIA GPU").strip()
            if _is_virtual_gpu(name):
                continue
            gpus.append(
                GpuInfo(
                    name=name,
                    vendor="NVIDIA",
                    vram_mb=item.get("vram_mb"),
                    driver=str(item.get("driver") or ""),
                    backend="cuda",
                    backend_label="CUDA",
                    source="nvidia-smi",
                )
            )

    _cache = (now, gpus)
    return list(gpus)


def recommend_backend() -> str:
    return detect_summary().get("recommended_backend", "cpu")


def detect_summary(force: bool = False) -> dict:
    gpus = detect_gpus(force=force)
    has_nvidia = any(gpu.vendor == "NVIDIA" for gpu in gpus)
    has_amd = any(gpu.vendor == "AMD" for gpu in gpus)
    has_intel = any(gpu.vendor == "Intel" for gpu in gpus)
    amd_trainable = any(
        gpu.vendor == "AMD" and (gpu.vram_mb is None or gpu.vram_mb >= 4096) for gpu in gpus
    )

    if has_nvidia:
        backend, label = "cuda", "NVIDIA CUDA"
    elif amd_trainable:
        backend, label = "dml", "AMD DirectML"
    elif has_intel:
        backend, label = "dml", "Intel DirectML（实验性）"
    else:
        backend, label = "cpu", "CPU"

    notes: list[str] = []
    if has_amd and amd_trainable:
        notes.append("A 卡换声走 DirectML；训练为 Beta，6GB 起，失败会自动降级 CPU。")
    elif has_amd:
        notes.append("检测到 AMD 核显/低显存设备：可试听，训练请用独显或 CPU（很慢）。")
    if has_nvidia:
        notes.append("N 卡走 CUDA；4GB 可轻量训练，2GB 可换声。")
    if not gpus:
        notes.append("未检测到独显：装包/试听可用，训练不建议。")
    if has_intel and not has_nvidia and not has_amd:
        notes.append("Intel GPU 仅标实验性，不作为首版训练承诺。")

    return {
        "gpus": [gpu.to_dict() for gpu in gpus],
        "has_nvidia": has_nvidia,
        "has_amd": has_amd,
        "has_intel": has_intel,
        "amd_trainable": amd_trainable,
        "cpu_only": not gpus,
        "recommended_backend": backend,
        "recommended_label": label,
        "notes": notes,
    }
