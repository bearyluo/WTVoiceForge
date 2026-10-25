"""桌面启动器：双击 exe 后启动本地服务，并在内嵌 WebView2 窗口中打开工作台。

源码模式仍可直接 `python -m app`；打包后的绿色版使用这个入口。
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, server
from app.engines import gpt_sovits

HOST = "127.0.0.1"
PORT_RANGE = range(8765, 8796)
MUTEX_NAME = "Local\\WTVoiceForge.Launcher"
WINDOW_TITLE = "WT Voice Forge"
WEBVIEW2_PAGE_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
# 微软官方 Evergreen Bootstrapper（下载后仍会校验 Authenticode 签名）。
WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
_MUTEX_HANDLE = None


def _prepare_log() -> None:
    if not config.FROZEN:
        return
    try:
        log_dir = config.DEFAULT_DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / "launcher.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = handle
    sys.stderr = handle


def _pick_port(host: str) -> int:
    for port in PORT_RANGE:
        if not server.port_in_use(host, port):
            return port
    return 0


def _probe_health_info(
    host: str, port: int, timeout: float = 0.4
) -> tuple[str, dict]:
    """确认端口上跑的是 WT Voice Forge，并带回页面是否已经打开。"""
    url = f"http://{host}:{port}/"
    try:
        with urllib.request.urlopen(
            f"{url}api/health", timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return "", {}
    if payload.get("ok") and payload.get("app") == config.APP_NAME:
        return url, payload
    return "", {}


def _probe_health(host: str, port: int, timeout: float = 0.4) -> str:
    return _probe_health_info(host, port, timeout)[0]


def find_running_info(
    host: str = HOST, ports=PORT_RANGE
) -> tuple[str, dict]:
    """返回已经在运行的工作台地址和健康信息；没有则返回空字符串。

    端口探测并发执行。某些安全软件会让本机未监听端口的 connect 拖到超时，
    串行扫 31 个端口会把启动拖慢几十秒。
    """
    ordered = list(ports)

    def _probe(port: int) -> tuple[int, str, dict]:
        if not server.port_in_use(host, port):
            return port, "", {}
        url, payload = _probe_health_info(host, port)
        return port, url, payload

    found: list[tuple[int, str, dict]] = []
    if ordered:
        with ThreadPoolExecutor(max_workers=min(32, len(ordered))) as pool:
            found = list(pool.map(_probe, ordered))
    for _port, url, payload in sorted(found):
        if url:
            return url, payload
    return "", {}


def find_running(host: str = HOST, ports=PORT_RANGE) -> str:
    """返回已经在运行的工作台地址；没有则返回空字符串。"""
    return find_running_info(host, ports)[0]


def wait_running(timeout: float = 8.0, host: str = HOST) -> str:
    deadline = time.monotonic() + timeout
    while True:
        url = find_running(host)
        if url or time.monotonic() >= deadline:
            return url
        time.sleep(0.3)


def start_local_server() -> tuple[object, str]:
    """在当前进程里启动本地 HTTP 服务，返回 httpd 与工作台地址。"""
    port = _pick_port(HOST)
    if not port:
        raise RuntimeError("8765–8795 端口都被占用，找不到可用端口。")
    httpd = server.create_server(HOST, port)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://{HOST}:{port}/"


def stop_local_server(httpd) -> None:
    gpt_sovits.stop_server()
    if httpd is None:
        return
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass


def _native_gui_ready() -> tuple[bool, str]:
    """检查 pywebview 的 Windows 后端和系统 WebView2 运行时。"""
    try:
        from webview.platforms import winforms
    except Exception as exc:  # pythonnet/依赖缺失
        return False, f"内嵌窗口组件加载失败：{exc}"
    try:
        if winforms._is_chromium():
            return True, ""
    except Exception as exc:
        return False, f"WebView2 检测失败：{exc}"
    return False, "系统缺少 Microsoft Edge WebView2 Runtime"


def _apply_titlebar(hwnd: int) -> None:
    """把原生标题栏改成暖纸色，和 Field Manual 界面连成一体。"""
    if os.name != "nt" or not hwnd:
        return
    try:
        from ctypes import wintypes

        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        # 20 = 深色标题栏（此处关闭）；34/35/36 = 边框、标题栏底色、标题文字（Win11）。
        # DWM 颜色是 COLORREF：0x00BBGGRR。
        for attribute, value in (
            (20, 0),
            (34, 0x00AEC1C9),
            (35, 0x00D6E2E7),
            (36, 0x00252B25),
        ):
            current = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                attribute,
                ctypes.byref(current),
                ctypes.sizeof(current),
            )
    except Exception:
        pass


def _window_handle(window) -> int:
    """按标题找顶层窗口，避免从事件线程跨线程访问 WinForms 控件。"""
    if os.name != "nt":
        return 0
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = int(user32.FindWindowW(None, WINDOW_TITLE) or 0)
        if hwnd:
            return hwnd
    except Exception:
        pass
    try:
        return int(window.native.Handle)
    except Exception:
        return 0


def _apply_window_theme(window) -> int:
    hwnd = _window_handle(window)
    _apply_titlebar(hwnd)
    return hwnd


def _apply_window_theme_retries(window) -> None:
    """窗口显示后 DWM 属性可能被重置；每次重新按标题取句柄再设。"""
    deadline = 0.0
    for delay in (0.4, 1.2, 2.4):
        time.sleep(delay - deadline)
        deadline = delay
        _apply_titlebar(_window_handle(window))


def _apply_window_icon(window) -> None:
    """给 WebView2 窗口设置图标，窗口显示后再改标题栏配色。

    pywebview 的 start 回调在窗口创建前、另一个线程里执行，此时原生句柄
    还没就绪；DWM 属性必须等 shown 事件在 UI 线程触发后再设置。
    """
    try:
        from System.Drawing import Icon

        icon_path = config.WEB_DIR / "app.ico"
        if icon_path.is_file():
            window.native.Icon = Icon(str(icon_path))
    except Exception:
        pass

    def on_shown() -> None:
        _apply_window_theme(window)
        try:
            threading.Thread(
                target=_apply_window_theme_retries,
                args=(window,),
                daemon=True,
            ).start()
        except Exception:
            pass

    try:
        window.events.shown += on_shown
    except Exception:
        pass


def open_native_panel(url: str) -> tuple[bool, str]:
    """打开内嵌 WebView2 窗口；窗口关闭后才返回。"""
    ok, reason = _native_gui_ready()
    if not ok:
        return False, reason
    try:
        import webview

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "WTVoiceForge.LocalStudio"
            )
        except (AttributeError, OSError):
            pass

        # pywebview 默认取消 WebView2 下载；允许下载后，页面里的普通链接
        # 会弹出系统保存对话框，作为“导出安装包”之外的备用方式。
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass

        storage = config.ensure_data_dir(config.load_config()) / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=1400,
            height=900,
            min_size=(1000, 640),
            background_color="#e7e2d6",
            text_select=True,
        )
        webview.start(
            _apply_window_icon,
            (window,),
            private_mode=False,
            storage_path=str(storage),
        )
    except Exception as exc:
        return False, f"内嵌窗口启动失败：{exc}"
    return True, ""


def _show_error(title: str, message: str) -> None:
    print(f"{title}：{message}", flush=True)
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except (AttributeError, OSError):
        pass


def _signed_by_microsoft(path: Path) -> bool:
    if os.name != "nt":
        return False
    command = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:WTVF_SIGNATURE_FILE; "
        "if ($s.Status -eq 'Valid' -and "
        "$s.SignerCertificate.Subject -match 'Microsoft Corporation') { 'ok' } else { 'bad' }"
    )
    env = os.environ.copy()
    env["WTVF_SIGNATURE_FILE"] = str(path)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "ok" in (completed.stdout or "").lower()


def _install_webview2() -> tuple[bool, str]:
    """下载微软官方 Evergreen Bootstrapper，验签后静默安装。"""
    if os.name != "nt":
        return False, "当前系统不支持自动安装 WebView2"
    target = Path(tempfile.gettempdir()) / "WTVoiceForge-WebView2Setup.exe"
    try:
        request = urllib.request.Request(
            WEBVIEW2_BOOTSTRAPPER_URL,
            headers={"User-Agent": f"WTVoiceForge/{config.APP_VERSION}"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            with open(target, "wb") as handle:
                shutil.copyfileobj(response, handle, 1024 * 1024)
    except Exception as exc:
        target.unlink(missing_ok=True)
        return False, f"下载 WebView2 安装程序失败：{exc}"
    try:
        if target.stat().st_size < 1_000_000:
            return False, "下载的 WebView2 安装程序不完整"
        if not _signed_by_microsoft(target):
            return False, "下载文件的微软数字签名无效，已拒绝运行"
        completed = subprocess.run(
            [str(target), "/silent", "/install"],
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode not in (0, 3010):
            return False, f"WebView2 安装程序退出码 {completed.returncode}"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"启动 WebView2 安装程序失败：{exc}"
    finally:
        target.unlink(missing_ok=True)
    time.sleep(1.0)
    ok, reason = _native_gui_ready()
    return (True, "") if ok else (False, reason or "安装后仍未检测到 WebView2")


def _show_panel_error(reason: str) -> bool:
    """返回 True 表示刚刚装好 WebView2，可以重试打开窗口。"""
    print(f"无法打开本地窗口：{reason}", flush=True)
    if os.name != "nt":
        return False
    missing_webview = "WebView2" in reason
    if not missing_webview:
        try:
            ctypes.windll.user32.MessageBoxW(
                None, reason, "WT Voice Forge · 无法打开本地窗口", 0x10
            )
        except (AttributeError, OSError):
            pass
        return False

    message = (
        f"{reason}\n\n"
        "Windows 10/11 通常自带 WebView2；这台电脑可能精简了系统组件。\n\n"
        "点“是”自动下载微软官方 WebView2 安装程序并安装（约 2 MB，安装可能需要 1-2 分钟）。\n"
        "点“否”打开微软官方下载页，手动安装后再双击。"
    )
    try:
        result = ctypes.windll.user32.MessageBoxW(
            None, message, "WT Voice Forge · 缺少 WebView2", 0x24
        )
    except (AttributeError, OSError):
        return False
    if result != 6:  # IDNO / 关闭
        if result == 7:
            webbrowser.open(WEBVIEW2_PAGE_URL)
        return False

    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "正在下载并安装 WebView2。安装期间请不要重复双击；完成后会自动打开工作台。",
            "WT Voice Forge · 安装 WebView2",
            0x40,
        )
    except (AttributeError, OSError):
        pass
    ok, detail = _install_webview2()
    if not ok:
        _show_error("WT Voice Forge · WebView2 安装失败", detail)
        return False
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "WebView2 已安装，正在打开工作台。",
            "WT Voice Forge",
            0x40,
        )
    except (AttributeError, OSError):
        pass
    return True


def _window_exe(hwnd: int) -> str:
    """返回窗口所属进程的可执行文件名，用于优先聚焦内嵌窗口。"""
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ""
            return Path(buffer.value).name.lower()
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return ""


def _focus_workbench_window() -> bool:
    """把已经打开的工作台窗口提到前台，避免重复开窗口。"""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        app_exe = Path(sys.executable).name.lower()
        app_windows: list[int] = []
        other_windows: list[int] = []
        browser_windows: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            if "WT Voice Forge" not in title:
                return True
            exe = _window_exe(int(hwnd))
            if "Microsoft" in title or "和另外" in title:
                # 普通 Edge 窗口会带“和另外 N 个页面 / Microsoft Edge”。
                browser_windows.append(int(hwnd))
            elif exe == app_exe:
                app_windows.append(int(hwnd))
            else:
                other_windows.append(int(hwnd))
            return True

        user32.EnumWindows(_callback, 0)
        handles = app_windows or other_windows or browser_windows
        if not handles:
            return False
        hwnd = handles[0]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _page_is_active(url: str) -> bool:
    parsed = urlparse(url)
    _, payload = _probe_health_info(parsed.hostname or HOST, parsed.port or 80)
    return bool(payload.get("page_active"))


def _open_existing(url: str, active: bool | None = None) -> None:
    label = "已有工作台在运行"
    print(f"{label}：{url}", flush=True)
    if os.environ.get("WTVF_NO_BROWSER") == "1" or "--no-browser" in sys.argv:
        return
    if _focus_workbench_window():
        print("已切换到运行中的工作台窗口", flush=True)
        return
    if active is None:
        active = _page_is_active(url)
    if active:
        print("工作台页面已经打开，本次不重复开窗口", flush=True)
        return
    ok, reason = open_native_panel(url)
    if not ok and _show_panel_error(reason):
        ok, reason = open_native_panel(url)
        if not ok:
            _show_error("WT Voice Forge · 无法打开本地窗口", reason)


def _acquire_instance_mutex() -> bool:
    """Windows 命名互斥：双击多次时只让第一个进程创建窗口。

    端口扫描仍保留，用来接管旧版本或源码模式启动的服务。
    """
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _MUTEX_HANDLE = handle
        return True
    except (AttributeError, OSError):
        return True


def _release_instance_mutex() -> None:
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE is None or os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_MUTEX_HANDLE))
    except (AttributeError, OSError):
        pass
    _MUTEX_HANDLE = None


def main() -> None:
    _prepare_log()
    existing, info = find_running_info()
    if existing:
        _open_existing(existing, bool(info.get("page_active")))
        return
    if not _acquire_instance_mutex():
        existing = wait_running()
        if existing:
            _open_existing(existing)
        else:
            print("启动器已经在运行，但没有响应；请稍等或结束旧的 WT Voice Forge 进程。", flush=True)
        return
    httpd = None
    try:
        existing, info = find_running_info()
        if existing:
            _open_existing(existing, bool(info.get("page_active")))
            return
        httpd, url = start_local_server()
        print(f"workbench: {url}", flush=True)
        if os.environ.get("WTVF_NO_BROWSER") == "1" or "--no-browser" in sys.argv:
            while True:
                time.sleep(1)
        ok, reason = open_native_panel(url)
        if not ok and _show_panel_error(reason):
            ok, reason = open_native_panel(url)
            if not ok:
                _show_error("WT Voice Forge · 无法打开本地窗口", reason)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _show_error("WT Voice Forge · 启动失败", str(exc))
    finally:
        if httpd is not None:
            stop_local_server(httpd)
        _release_instance_mutex()


if __name__ == "__main__":
    main()
