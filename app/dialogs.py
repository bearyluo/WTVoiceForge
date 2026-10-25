"""本机原生文件夹选择框。

优先用独立的 PowerShell 进程调 Windows 文件夹选择器，避免在 HTTP 线程里
直接操作 Tk；打包成 exe 后主线程已经有一个 Tk 窗口，跨线程 Tk 不安全。
"""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from app import config


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pick_windows(title: str, initial: str) -> str | None:
    if os.name != "nt":
        return None
    start = Path(initial).expanduser() if initial else Path.home()
    if not start.is_dir():
        start = Path.home()
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.ShowNewFolderButton = $true;"
        f"$d.Description = {_ps_quote(title)};"
        f"$d.SelectedPath = {_ps_quote(str(start))};"
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        "$p = $d.SelectedPath;"
        "[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($p)))"
        "}"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    payload = (result.stdout or "").strip()
    if not payload:
        return ""
    try:
        return base64.b64decode(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _pick_tk(title: str, initial: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError("当前运行环境没有图形界面，请手动输入路径。") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    start = Path(initial).expanduser() if initial else Path.home()
    if not start.is_dir():
        start = Path.home()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title=title,
            initialdir=str(start),
            mustexist=True,
        )
    finally:
        root.destroy()
    return selected or ""


def pick_folder(title: str = "选择文件夹", initial: str = "") -> str:
    selected = _pick_windows(title, initial)
    if selected is not None:
        return selected
    if config.FROZEN:
        raise RuntimeError("系统文件夹选择器没有启动，请手动输入路径。")
    return _pick_tk(title, initial)


def _file_filter(suffixes: list[str] | None) -> str:
    if not suffixes:
        return "音频文件|*.wav;*.mp3;*.flac;*.m4a;*.ogg;*.opus;*.aac|所有文件|*.*"
    pattern = ";".join(f"*{suffix}" for suffix in suffixes)
    return f"模型文件|{pattern}|所有文件|*.*"


def _pick_windows_files(
    title: str, initial: str, suffixes: list[str] | None = None
) -> list[str] | None:
    if os.name != "nt":
        return None
    start = Path(initial).expanduser() if initial else Path.home()
    if not start.is_dir():
        start = Path.home()
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Multiselect = $true;"
        "$d.CheckFileExists = $true;"
        f"$d.Filter = {_ps_quote(_file_filter(suffixes))};"
        f"$d.Title = {_ps_quote(title)};"
        f"$d.InitialDirectory = {_ps_quote(str(start))};"
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {"
        "$p = [string]::Join([Environment]::NewLine, $d.FileNames);"
        "[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($p)))"
        "}"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    payload = (result.stdout or "").strip()
    if not payload:
        return []
    try:
        text = base64.b64decode(payload).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return [line for line in text.splitlines() if line.strip()]


def _pick_tk_files(
    title: str, initial: str, suffixes: list[str] | None = None
) -> list[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError("当前运行环境没有图形界面，请手动输入路径。") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    start = Path(initial).expanduser() if initial else Path.home()
    if not start.is_dir():
        start = Path.home()
    if suffixes:
        filetypes = [
            ("模型文件", " ".join(f"*{suffix}" for suffix in suffixes)),
            ("所有文件", "*.*"),
        ]
    else:
        filetypes = [
            ("音频文件", "*.wav *.mp3 *.flac *.m4a *.ogg *.opus *.aac"),
            ("所有文件", "*.*"),
        ]
    try:
        selected = filedialog.askopenfilenames(
            parent=root,
            title=title,
            initialdir=str(start),
            filetypes=filetypes,
        )
    finally:
        root.destroy()
    return list(selected or [])


def pick_files(
    title: str = "选择音频文件",
    initial: str = "",
    suffixes: list[str] | None = None,
) -> list[str]:
    selected = _pick_windows_files(title, initial, suffixes)
    if selected is not None:
        return selected
    if config.FROZEN:
        raise RuntimeError("系统文件选择器没有启动，请手动输入路径。")
    return _pick_tk_files(title, initial, suffixes)
