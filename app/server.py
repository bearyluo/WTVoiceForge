"""本地 HTTP 服务。

只监听 127.0.0.1，不做账号/上传/遥测。
M0 API：
  GET  /api/health
  GET  /api/state
  GET  /api/game/detect
  GET  /api/game/scan?path=
  GET  /api/kit/inspect?path=
  GET  /api/catalog?path=&limit=
  GET  /api/gpu
  GET  /api/rvc/detect?path=
  GET  /api/components
  GET  /api/components/job
  GET  /api/storage
  POST /api/storage/refresh
  POST /api/storage/clean
  POST /api/config
  POST /api/game/select
  POST /api/kit/select
  POST /api/rvc/select
  POST /api/components/preflight
  POST /api/components/download
  POST /api/components/cancel
  POST /api/components/uninstall
  POST /api/dialog/folder
  POST /api/packs/upload
  GET  /api/packs/installed
  POST /api/packs/install
  POST /api/packs/uninstall
  POST /api/packs/restore-all
  POST /api/packs/enable-mod
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

# 允许 `python app/server.py` 直接运行
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import (
    banks,
    catalog,
    cockpit,
    components,
    config,
    dialogs,
    dynamic_bank,
    fmod_kit,
    game,
    gpu,
    jobs,
    localized,
    materials,
    original,
    packs,
    quickpack,
    storage,
    translation,
    unpack,
    vgmstream,
    voice,
)
from app import exporter
from app.engines import gpt_sovits, rvc, trainer

# 多版本只换随机种子，参考音频和采样都保持官方默认。
# 之前的“换参考 + 放宽 top_k/温度”实测会让音色漂移、发音变差，已回退。
TAKE_PROFILE = {"top_k": 5, "top_p": 1.0, "temperature": 1.0}
PAGE_ACTIVE_SECONDS = 90.0
_PAGE_SEEN_AT = 0.0
_PAGE_SEEN_LOCK = threading.Lock()


def note_page_activity(now: float | None = None) -> None:
    """记录最近一次页面/接口请求，用来判断浏览器里是否已经有工作台。

    /api/health 只给启动器探测用，不计入；不然每次双击都会把自己当成已打开。
    """
    global _PAGE_SEEN_AT
    with _PAGE_SEEN_LOCK:
        _PAGE_SEEN_AT = time.monotonic() if now is None else float(now)


def page_active(now: float | None = None) -> bool:
    with _PAGE_SEEN_LOCK:
        seen = _PAGE_SEEN_AT
    if not seen:
        return False
    current = time.monotonic() if now is None else float(now)
    return current - seen <= PAGE_ACTIVE_SECONDS


def reset_page_activity() -> None:
    global _PAGE_SEEN_AT
    with _PAGE_SEEN_LOCK:
        _PAGE_SEEN_AT = 0.0


def _json_safe(value):
    return value


def _data_dir(cfg: config.AppConfig | None = None) -> Path:
    cfg = cfg or config.load_config()
    return Path(cfg.data_dir).expanduser() if cfg.data_dir else config.DEFAULT_DATA_DIR


def _kit_path(cfg: config.AppConfig | None = None) -> str:
    cfg = cfg or config.load_config()
    return cfg.kit_path.strip()


def _bool_value(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _open_folder(path: Path) -> None:
    """在系统文件管理器里打开一个已存在的目录。"""
    if not path.is_dir():
        raise FileNotFoundError(f"目录不存在：{path}")
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _known_folder(cfg: config.AppConfig, target: str) -> Path:
    if target == "data":
        return config.ensure_data_dir(cfg)
    if target == "exports":
        folder = config.ensure_data_dir(cfg) / "语音包导出"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    if target == "game":
        return Path(cfg.game_path).expanduser()
    if target == "kit":
        return Path(cfg.kit_path).expanduser()
    if target == "rvc":
        return Path(cfg.rvc_path).expanduser()
    raise ValueError("未知的目录类型")


def _voice_package_source(
    cfg: config.AppConfig, project_id: str, name: str
) -> Path:
    """校验并返回制作页构建产物路径。"""
    if not name or Path(name).name != name or not name.endswith(".wtvoice"):
        raise ValueError("无效的语音包文件名")
    try:
        path = voice.project_dir(_data_dir(cfg), project_id) / "exports" / name
    except voice.VoiceError as exc:
        raise ValueError(str(exc)) from exc
    if not path.is_file():
        raise FileNotFoundError("语音包不存在，请重新构建")
    return path


def _catalog_status(cfg: config.AppConfig | None = None, recheck: bool = False) -> dict:
    cfg = cfg or config.load_config()
    kit = _kit_path(cfg)
    if not kit or not Path(kit).is_dir():
        return {
            "available": False,
            "cached": False,
            "stale": False,
            "reason": "未选择官方 FMOD 工程",
            "cache_file": "",
            "generated_at": "",
            "stats": None,
        }
    return catalog.store.status(kit, _data_dir(cfg), recheck=recheck)


def build_state() -> dict:
    cfg = config.load_config()
    installs = game.detect_installs()

    selected_game = None
    bank_items: list[banks.BankInfo] = []
    if cfg.game_path and game.is_warthunder_dir(cfg.game_path):
        selected_game = game.scan_game(cfg.game_path)
        bank_items = banks.scan_banks(Path(cfg.game_path) / "sound")

    kit_info = None
    if cfg.kit_path:
        info = fmod_kit.inspect_kit(cfg.kit_path)
        if info.valid:
            kit_info = info.to_dict()

    kit_candidates = [item.to_dict() for item in fmod_kit.find_kits([cfg.kit_path] if cfg.kit_path else None)]
    if kit_info is None and kit_candidates:
        kit_info = kit_candidates[0]

    return {
        "ok": True,
        "app": {
            "name": config.APP_NAME,
            "version": config.APP_VERSION,
            "milestone": config.MILESTONE,
        },
        "config": cfg.to_dict(),
        "installs": [item.to_dict() for item in installs],
        "selected_game": selected_game.to_dict() if selected_game else None,
        "banks": banks.summarize_banks(bank_items),
        "bank_list": [item.to_dict() for item in bank_items],
        "kit": kit_info,
        "kit_candidates": kit_candidates,
        "fmod_studio": fmod_kit.find_fmod_studio(),
        "vgmstream": vgmstream.status(),
        "gpu": gpu.detect_summary(),
        "gpt_sovits": gpt_sovits.status(),
        "rvc": rvc.detect_rvc(cfg.rvc_path).to_dict(),
        "next_milestone": "M2：GPT-SoVITS 音色克隆与文本合成",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"WTVoiceForge/{config.APP_VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---------- responses ----------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400, **extra) -> None:
        payload = {"ok": False, "error": message}
        payload.update(extra)
        self._send_json(payload, status=status)

    def _send_static(self) -> None:
        rel = "index.html" if self.path in ("/", "") else unquote(urlparse(self.path).path).lstrip("/")
        root = config.WEB_DIR.resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._send_error_json("非法路径", status=404)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send_error_json("文件不存在", status=404)
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target.suffix == ".svg":
            content_type = "image/svg+xml"

        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- audio preview ----------

    @staticmethod
    def _sniff_audio(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                head = handle.read(12)
        except OSError:
            head = b""
        if head.startswith(b"OggS"):
            return "audio/ogg"
        if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return "audio/wav"
        return mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    def _send_audio(self, path: Path, head_only: bool = False) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._send_error_json("音频文件不存在", status=404)
            return

        content_type = self._sniff_audio(path)
        start = 0
        end = max(0, size - 1)
        status = 200
        content_range = ""
        range_header = (self.headers.get("Range") or "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or (not match.group(1) and not match.group(2)):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                suffix = int(match.group(2))
                start = max(0, size - suffix)
                end = size - 1
            if size == 0 or start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
            content_range = f"bytes {start}-{end}/{size}"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        self.send_header("Accept-Ranges", "bytes")
        if content_range:
            self.send_header("Content-Range", content_range)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if head_only or size == 0:
            return

        remaining = end - start + 1
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                while remaining > 0:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_audio_route(self, api_path: str, parsed, head_only: bool = False) -> bool:
        cfg = config.load_config()
        data_dir = _data_dir(cfg)
        if api_path == "/api/audio/catalog":
            guid = self._query_value(parsed, "guid")
            kit = _kit_path(cfg)
            if not guid or not kit:
                self._send_error_json("缺少 guid 或官方工程", status=400)
                return True
            status = _catalog_status(cfg)
            data = catalog.store.snapshot()
            if not status.get("available") or not data:
                self._send_error_json("事件目录尚未生成", status=409)
                return True
            asset = data.get("assets", {}).get(guid)
            if not asset or not asset.get("exists") or not asset.get("path"):
                self._send_error_json("素材不存在或缺失", status=404)
                return True
            root = (Path(kit) / "Assets").resolve()
            target = (root / asset["path"]).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                self._send_error_json("非法素材路径", status=404)
                return True
            if not target.is_file():
                self._send_error_json("素材文件不存在", status=404)
                return True
            self._send_audio(target, head_only=head_only)
            return True

        if api_path == "/api/audio/source":
            project_id = self._query_value(parsed, "project")
            line_id = self._query_value(parsed, "line")
            kit = _kit_path(cfg)
            if not project_id or not line_id or not kit:
                self._send_error_json("缺少项目、台词或官方工程", status=400)
                return True
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return True
            line = next(
                (item for item in project.get("lines", []) if item.get("id") == line_id),
                None,
            )
            if not line:
                self._send_error_json("台词不存在", status=404)
                return True
            root = (Path(kit).resolve() / "Assets").resolve()
            source = line.get("source") or {}
            asset_arg = self._query_value(parsed, "asset")
            try:
                asset_index = max(0, int(asset_arg or 0))
            except ValueError:
                asset_index = 0
            # aircraft_gui 的工程素材是扩展名 .wav 的 Ogg；浏览器按 .wav
            # MIME 播放可能失败，座舱告警改为从游戏 bank 解出 PCM 再试听。
            is_cockpit = str(source.get("bank") or "") == cockpit.BANK
            if is_cockpit:
                assets = source.get("assets") or []
                if not asset_arg and assets:
                    asset_index = cockpit.primary_asset_index(assets)
                chosen = (
                    assets[asset_index]
                    if asset_index < len(assets)
                    else (assets[0] if assets else None)
                )
                trusted = isinstance(chosen, dict) and (
                    chosen.get("voice")
                    or not str(chosen.get("path") or "").startswith("vws/")
                )
                game_index = chosen.get("game_index") if trusted else None
                if game_index is None and isinstance(chosen, dict) and chosen.get("path"):
                    # 旧项目只记了一条原始流；按素材路径实时补算语种序号。
                    try:
                        game_index = cockpit.source_index(
                            kit,
                            cfg.game_path or None,
                            str(source.get("event") or ""),
                            str(chosen["path"]),
                        )
                    except (OSError, localized.LocalizedError):
                        game_index = None
                if game_index is not None:
                    try:
                        target = cockpit.source_audio(
                            cfg.game_path or None,
                            str(source.get("event") or ""),
                            int(game_index),
                            data_dir,
                        )
                    except localized.LocalizedError:
                        target = None
                    if target:
                        self._send_audio(target, head_only=head_only)
                        return True
            if not is_cockpit:
                for asset in source.get("assets") or []:
                    rel = str(asset.get("path") or "").strip()
                    if not rel:
                        continue
                    target = (root / rel).resolve()
                    try:
                        target.relative_to(root)
                    except ValueError:
                        continue
                    if target.is_file():
                        self._send_audio(target, head_only=head_only)
                        return True
                    origin = str(asset.get("origin") or "").strip()
                    if origin:
                        fallback = (root / origin).resolve()
                        try:
                            fallback.relative_to(root)
                        except ValueError:
                            fallback = None
                        if fallback and fallback.is_file():
                            self._send_audio(fallback, head_only=head_only)
                            return True
            if source.get("localized") and source.get("bank") and source.get("event"):
                target = None
                try:
                    target = localized.source_audio(
                        kit,
                        source["bank"],
                        source["event"],
                        game_path=cfg.game_path or None,
                        data_dir=data_dir,
                    )
                except localized.LocalizedError:
                    target = None
                if not target:
                    try:
                        target = dynamic_bank.source_audio(
                            source["bank"],
                            source["event"],
                            cfg.game_path or None,
                            data_dir,
                        )
                    except (localized.LocalizedError, dynamic_bank.DynamicBankError):
                        target = None
                if target:
                    self._send_audio(target, head_only=head_only)
                    return True
            self._send_error_json("这条台词没有可播放的原音", status=404)
            return True

        if api_path == "/api/audio/unpacked":
            bank = self._query_value(parsed, "bank")
            stream_id = self._query_value(parsed, "stream")
            if not bank or not stream_id:
                self._send_error_json("缺少 bank 或 stream", status=400)
                return True
            try:
                target, _stream = unpack.stream_file(bank, stream_id, data_dir)
            except ValueError as exc:
                self._send_error_json(str(exc), status=404)
                return True
            self._send_audio(target, head_only=head_only)
            return True
        return False

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        api_path = unquote(parsed.path)
        if api_path == "/api/voice/audio":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            line_id = self._query_value(parsed, "line")
            take_id = self._query_value(parsed, "take")
            try:
                if take_id:
                    path = voice.find_take(
                        _data_dir(cfg), project_id, line_id, take_id
                    )
                else:
                    path = voice.line_audio_path(_data_dir(cfg), project_id, line_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            if not path.is_file():
                self._send_error_json("这条台词还没有合成音频", status=404)
                return
            self._send_audio(path, head_only=True)
            return
        if api_path == "/api/voice/reference/audio":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            name = Path(unquote(self._query_value(parsed, "file"))).name
            try:
                project = voice.load_project(_data_dir(cfg), project_id)
                files = project.get("reference", {}).get("files") or []
                if name not in files:
                    raise voice.VoiceError("参考素材里没有这个文件")
                path = voice.project_dir(_data_dir(cfg), project_id) / "reference" / name
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            if not path.is_file():
                self._send_error_json("参考素材不存在", status=404)
                return
            self._send_audio(path, head_only=True)
            return
        if self._handle_audio_route(api_path, parsed, head_only=True):
            return
        self._send_error_json("不支持的方法", status=405)

    # ---------- request helpers ----------

    @staticmethod
    def _query(parsed) -> dict[str, list[str]]:
        return parse_qs(parsed.query, keep_blank_values=True)

    @staticmethod
    def _query_value(parsed, name: str) -> str:
        values = parse_qs(parsed.query, keep_blank_values=True).get(name)
        return values[0].strip() if values else ""

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            raise ValueError("Content-Length 无效")
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 无效") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def _handle_pack_upload(self, parsed) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._send_error_json("Content-Length 无效", status=400)
            return
        if length <= 0:
            self._send_error_json("没有收到语音包文件", status=400)
            return
        if length > packs.MAX_UPLOAD_BYTES:
            self.close_connection = True
            self._send_error_json("语音包超过 1 GB，已拒绝", status=413)
            return
        filename = self._query_value(parsed, "filename")
        cfg = config.load_config()
        data_dir = _data_dir(cfg)
        try:
            result = packs.receive_upload(data_dir, filename, self.rfile, length)
            info = packs.load_pack(data_dir, result["token"])
            self._send_json(
                {
                    "ok": True,
                    "pack": result["pack"],
                    "game": packs.inspect_game(cfg, data_dir, info),
                }
            )
        except packs.PackError as exc:
            self._send_error_json(str(exc), status=400)
        except OSError as exc:
            self._send_error_json(f"读取上传文件失败：{exc}", status=500)

    # ---------- GET ----------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        api_path = unquote(parsed.path)
        if api_path != "/api/health":
            note_page_activity()
        if not api_path.startswith("/api/"):
            self._send_static()
            return

        if api_path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "app": config.APP_NAME,
                    "version": config.APP_VERSION,
                    "milestone": config.MILESTONE,
                    "page_active": page_active(),
                }
            )
            return

        if api_path == "/api/state":
            self._send_json(build_state())
            return

        if api_path == "/api/storage":
            cfg = config.load_config()
            self._send_json(storage.usage(_data_dir(cfg)))
            return

        if api_path == "/api/quick/options":
            cfg = config.load_config()
            self._send_json(
                {
                    "ok": True,
                    **quickpack.options(
                        _data_dir(cfg), cfg.kit_path or None, cfg.game_path or None
                    ),
                    "rvc": rvc.detect_rvc(cfg.rvc_path or None).to_dict(),
                    "rvc_component": components.recommended_rvc(),
                }
            )
            return

        if api_path == "/api/voice/preview/audio":
            cfg = config.load_config()
            name = self._query_value(parsed, "name")
            if not re.fullmatch(r"pv_[0-9a-f]{12}\.wav", name):
                self._send_error_json("试听文件无效", status=404)
                return
            path = _data_dir(cfg) / "cache" / "preview" / name
            if not path.is_file():
                self._send_error_json("试听文件不存在", status=404)
                return
            self._send_audio(path, head_only=False)
            return

        if api_path == "/api/game/detect":
            self._send_json({"ok": True, "installs": [item.to_dict() for item in game.detect_installs()]})
            return

        if api_path == "/api/game/scan":
            path = self._query_value(parsed, "path")
            if not path:
                path = config.load_config().game_path
            if not path:
                self._send_error_json("缺少 path 参数")
                return
            if not game.is_warthunder_dir(path):
                self._send_error_json("不是有效的战雷目录（需要 aces.exe、sound、config.blk）", status=400, path=path)
                return
            info = game.scan_game(path)
            items = banks.scan_banks(Path(path) / "sound")
            self._send_json(
                {
                    "ok": True,
                    "game": info.to_dict(),
                    "banks": banks.summarize_banks(items),
                    "bank_list": [item.to_dict() for item in items],
                }
            )
            return

        if api_path == "/api/kit/inspect":
            path = self._query_value(parsed, "path")
            if not path:
                path = config.load_config().kit_path
            if not path:
                self._send_error_json("缺少 path 参数")
                return
            info = fmod_kit.inspect_kit(path, compute_size=self._query_value(parsed, "size") in ("1", "true"))
            self._send_json({"ok": info.valid, "kit": info.to_dict()})
            return

        if api_path == "/api/catalog/status":
            cfg = config.load_config()
            status = _catalog_status(cfg, recheck=_bool_value(self._query_value(parsed, "recheck")))
            self._send_json({"ok": True, **status, "job": jobs.manager.snapshot()})
            return

        if api_path == "/api/catalog/query":
            cfg = config.load_config()
            kit = _kit_path(cfg)
            if not kit or not Path(kit).is_dir():
                self._send_error_json("请先选择官方 FMOD 工程", status=400)
                return
            status = _catalog_status(cfg)
            data = catalog.store.snapshot()
            if not status.get("available") or not data:
                self._send_error_json("事件目录尚未生成", status=409, catalog=status)
                return
            try:
                page = int(self._query_value(parsed, "page") or "1")
            except ValueError:
                page = 1
            try:
                page_size = int(self._query_value(parsed, "page_size") or "50")
            except ValueError:
                page_size = 50
            result = catalog.query_catalog(
                data,
                bank=self._query_value(parsed, "bank"),
                category=self._query_value(parsed, "category"),
                language=self._query_value(parsed, "language"),
                q=self._query_value(parsed, "q"),
                has_assets=self._query_value(parsed, "has_assets"),
                include_unassigned=_bool_value(self._query_value(parsed, "include_unassigned")),
                page=page,
                page_size=page_size,
                sort=self._query_value(parsed, "sort") or "bank",
                order=self._query_value(parsed, "order") or "asc",
            )
            self._send_json({"ok": True, "catalog": result, "cache": status})
            return

        if api_path == "/api/jobs/current":
            self._send_json({"ok": True, "job": jobs.manager.snapshot()})
            return

        if api_path == "/api/unpack":
            cfg = config.load_config()
            game_path = cfg.game_path if cfg.game_path and game.is_warthunder_dir(cfg.game_path) else None
            self._send_json(unpack.summary_for_banks(game_path, _data_dir(cfg)))
            return

        if api_path == "/api/unpack/manifest":
            bank = self._query_value(parsed, "bank")
            if not bank:
                self._send_error_json("缺少 bank 参数")
                return
            try:
                manifest = unpack.load_manifest(bank, _data_dir(config.load_config()))
            except ValueError as exc:
                self._send_error_json(str(exc), status=400)
                return
            if not manifest:
                self._send_error_json("这个 bank 还没有解包结果", status=404)
                return
            self._send_json({"ok": True, "manifest": manifest})
            return

        if api_path == "/api/localized/banks":
            cfg = config.load_config()
            kit = _kit_path(cfg)
            if not kit:
                self._send_error_json("请先在“工作台”选择官方 FMOD 工程", status=400)
                return
            try:
                items = localized.banks(kit)
            except localized.LocalizedError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, "banks": items})
            return

        if api_path == "/api/localized/streams":
            cfg = config.load_config()
            kit = _kit_path(cfg)
            bank = self._query_value(parsed, "bank")
            if not kit or not bank:
                self._send_error_json("缺少 bank 或官方工程", status=400)
                return
            try:
                items = localized.streams(kit, bank, cfg.game_path or None)
            except localized.LocalizedError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "bank": bank,
                    "streams": items,
                    "game_only": sum(1 for item in items if item.get("game_only")),
                }
            )
            return

        if api_path == "/api/localized/coverage":
            cfg = config.load_config()
            kit = _kit_path(cfg)
            if not kit:
                self._send_error_json("请先在“工作台”选择官方 FMOD 工程", status=400)
                return
            try:
                result = localized.coverage(kit, cfg.game_path or None)
            except localized.LocalizedError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/localized/translation-task":
            cfg = config.load_config()
            kit = _kit_path(cfg)
            bank = self._query_value(parsed, "bank")
            if not kit or not bank:
                self._send_error_json("缺少 bank 或官方工程", status=400)
                return
            scope, language = localized._split_bank(bank)
            if not scope:
                self._send_error_json("不是本地化语音表 bank", status=400)
                return
            missing_only = self._query_value(parsed, "missing").lower() in (
                "1",
                "true",
                "yes",
            )
            if missing_only and not cfg.game_path:
                self._send_error_json("只导出缺口需要先选择战雷目录", status=400)
                return
            try:
                game_bank = localized.game_bank_path(cfg.game_path or None, bank)
                if game_bank:
                    streams = localized.game_streams(game_bank)
                else:
                    streams = [
                        {
                            "name": item.get("event", ""),
                            "index": (item.get("assets") or [{}])[0].get("game_index") or 0,
                            "duration_sec": item.get("duration_sec", 0.0),
                        }
                        for item in localized.streams(kit, bank, cfg.game_path or None)
                    ]
                if missing_only:
                    covered = localized.official_cn_names(cfg.game_path or None, scope)
                    streams = [
                        item for item in streams if item.get("name") not in covered
                    ]
                    if not streams:
                        self._send_error_json("这张表没有中文原音缺口", status=404)
                        return
                result = translation.build(
                    {language: {"bank": bank, "streams": streams}},
                    prefix="T",
                    scope_label=(
                        f"{localized._label(bank)}缺口"
                        if missing_only
                        else localized._label(bank)
                    ),
                )
            except localized.LocalizedError as exc:
                self._send_error_json(str(exc), status=400)
                return
            title = f"{bank}_缺口翻译" if missing_only else f"{bank}_翻译"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    f"{title}_分组任务.csv",
                    translation.csv_text(result["groups"], translation.group_fields()),
                )
                archive.writestr(
                    f"{title}_逐条任务.csv",
                    translation.csv_text(result["rows"], translation.stream_fields()),
                )
                archive.writestr(
                    f"{title}_术语表.csv",
                    translation.csv_text(
                        translation.glossary_rows(),
                        ["term", "chinese", "example", "note"],
                    ),
                )
                archive.writestr(
                    "README.txt",
                    translation.readme_text(result, title=title),
                )
            body = buffer.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{bank}_translation_task.zip"',
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if api_path == "/api/packs/installed":
            cfg = config.load_config()
            self._send_json({"ok": True, **packs.list_installed(cfg, _data_dir(cfg))})
            return

        if api_path == "/api/packs/inspect":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            token = self._query_value(parsed, "token")
            try:
                info = packs.load_pack(data_dir, token)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=404)
                return
            self._send_json(
                {
                    "ok": True,
                    "pack": packs._public(info),
                    "game": packs.inspect_game(cfg, data_dir, info),
                }
            )
            return

        if api_path == "/api/packs/contents":
            cfg = config.load_config()
            token = self._query_value(parsed, "token")
            try:
                result = packs.contents(_data_dir(cfg), token)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=404)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/packs/conflicts":
            cfg = config.load_config()
            token = self._query_value(parsed, "token")
            try:
                result = packs.conflicts(_data_dir(cfg), token)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=404)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/engine":
            self._send_json({"ok": True, "engine": gpt_sovits.status()})
            return

        if api_path == "/api/voice/projects":
            cfg = config.load_config()
            self._send_json({"ok": True, "projects": voice.list_projects(_data_dir(cfg))})
            return

        if api_path == "/api/voice/project":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            data_dir = _data_dir(cfg)
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            quick = project.get("quick")
            if (
                isinstance(quick, dict)
                and quick.get("banks")
                and "line_scopes" not in quick
            ):
                inferred = quickpack.infer_line_scopes(project)
                if inferred:
                    quick["line_scopes"] = inferred
                    voice.save_project(data_dir, project)
            self._send_json(
                {
                    "ok": True,
                    "project": project,
                    "training": trainer.readiness(project),
                    "build": exporter.readiness(project),
                }
            )
            return

        if api_path == "/api/voice/export":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            try:
                project = voice.load_project(_data_dir(cfg), project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            buffer = io.StringIO()
            fields = ["event_id", "event", "bank", "source_file", "source_path", "text", "status"]
            writer = csv.DictWriter(buffer, fieldnames=fields)
            writer.writeheader()
            writer.writerows(voice.export_rows(project))
            body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="voice-lines.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if api_path == "/api/voice/package":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            name = self._query_value(parsed, "name")
            as_zip = self._query_value(parsed, "as").lower() == "zip"
            try:
                path = _voice_package_source(cfg, project_id, name)
            except ValueError as exc:
                self._send_error_json(str(exc), status=400)
                return
            except FileNotFoundError as exc:
                self._send_error_json(str(exc), status=404)
                return
            try:
                body = path.read_bytes()
            except OSError as exc:
                self._send_error_json(str(exc), status=500)
                return
            download_name = (
                f"{Path(name).stem}.zip" if as_zip else Path(name).name
            )
            ascii_name = download_name.encode("ascii", "ignore").decode() or (
                "voice_pack.zip" if as_zip else "voice_pack.wtvoice"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{ascii_name}\"; "
                f"filename*=UTF-8''{quote(download_name)}",
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if api_path == "/api/voice/audio":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            line_id = self._query_value(parsed, "line")
            take_id = self._query_value(parsed, "take")
            try:
                if take_id:
                    path = voice.find_take(
                        _data_dir(cfg), project_id, line_id, take_id
                    )
                else:
                    path = voice.line_audio_path(_data_dir(cfg), project_id, line_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            if not path.is_file():
                self._send_error_json("这条台词还没有合成音频", status=404)
                return
            self._send_audio(path, head_only=False)
            return

        if api_path == "/api/voice/reference/audio":
            cfg = config.load_config()
            project_id = self._query_value(parsed, "project")
            name = Path(unquote(self._query_value(parsed, "file"))).name
            try:
                project = voice.load_project(_data_dir(cfg), project_id)
                files = project.get("reference", {}).get("files") or []
                if name not in files:
                    raise voice.VoiceError("参考素材里没有这个文件")
                path = voice.project_dir(_data_dir(cfg), project_id) / "reference" / name
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            if not path.is_file():
                self._send_error_json("参考素材不存在", status=404)
                return
            self._send_audio(path, head_only=False)
            return

        if api_path.startswith("/api/audio/"):
            if self._handle_audio_route(api_path, parsed, head_only=False):
                return

        if api_path == "/api/catalog":
            path = self._query_value(parsed, "path")
            if not path:
                path = config.load_config().kit_path
            if not path:
                self._send_error_json("缺少 path 参数")
                return
            try:
                limit = int(self._query_value(parsed, "limit") or "200")
            except ValueError:
                limit = 200
            limit = max(1, min(limit, 2000))
            result = catalog.build_catalog(path, limit=limit)
            if result.errors and result.event_count == 0:
                self._send_json({"ok": False, "catalog": result.to_dict()}, status=400)
                return
            self._send_json({"ok": True, "catalog": result.to_dict()})
            return

        if api_path == "/api/gpu":
            self._send_json({"ok": True, "gpu": gpu.detect_summary(force=True)})
            return

        if api_path == "/api/rvc/detect":
            path = self._query_value(parsed, "path") or config.load_config().rvc_path
            self._send_json({"ok": True, "rvc": rvc.detect_rvc(path or None).to_dict()})
            return

        if api_path == "/api/components":
            self._send_json(components.describe())
            return

        if api_path == "/api/components/job":
            self._send_json({"ok": True, "job": components.manager.snapshot()})
            return

        if api_path == "/api/config":
            self._send_json({"ok": True, "config": config.load_config().to_dict()})
            return

        self._send_error_json("未知接口", status=404)

    # ---------- POST ----------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        api_path = unquote(parsed.path)
        if api_path == "/api/packs/upload":
            self._handle_pack_upload(parsed)
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_error_json(str(exc), status=400)
            return

        if api_path == "/api/storage/refresh":
            cfg = config.load_config()
            self._send_json(storage.usage(_data_dir(cfg), force=True))
            return

        if api_path == "/api/storage/clean":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            raw = payload.get("categories")
            categories = (
                [str(item) for item in raw] if isinstance(raw, list) and raw else None
            )
            snapshot = components.manager.snapshot()
            allow_archives = not bool(snapshot and snapshot.get("active"))
            try:
                result = storage.cleanup(
                    data_dir,
                    categories,
                    allow_archives=allow_archives,
                )
            except ValueError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json(
                {
                    **result,
                    "storage": storage.usage(data_dir, force=True),
                }
            )
            return

        if api_path == "/api/packs/install":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            token = str(payload.get("token", "")).strip()
            enable_mod = bool(payload.get("enable_mod"))
            config_flags = payload.get("config") if isinstance(payload.get("config"), dict) else None
            allow_masterbank = bool(payload.get("allow_masterbank"))
            try:
                entry = packs.install(
                    cfg,
                    data_dir,
                    token,
                    enable_mod=enable_mod,
                    config=config_flags,
                    allow_masterbank=allow_masterbank,
                )
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "pack": entry,
                    "installed": packs.list_installed(cfg, data_dir),
                }
            )
            return

        if api_path == "/api/packs/uninstall":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            pack_id = str(payload.get("pack_id", "")).strip()
            try:
                result = packs.uninstall(cfg, data_dir, pack_id)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json(
                {"ok": True, **result, "installed": packs.list_installed(cfg, data_dir)}
            )
            return

        if api_path == "/api/packs/restore-all":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            try:
                result = packs.restore_all(cfg, data_dir)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json(
                {"ok": True, **result, "installed": packs.list_installed(cfg, data_dir)}
            )
            return

        if api_path == "/api/packs/enable-mod":
            cfg = config.load_config()
            enabled = bool(payload.get("enabled", True))
            try:
                result = packs.set_enable_mod(cfg, _data_dir(cfg), enabled)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/packs/discard":
            cfg = config.load_config()
            token = str(payload.get("token", "")).strip()
            try:
                result = packs.discard_upload(_data_dir(cfg), token)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/config":
            allowed = (
                "game_path",
                "kit_path",
                "gpt_sovits_path",
                "rvc_path",
                "components_dir",
                "fmod_studio_path",
                "data_dir",
            )
            changes = {key: payload[key] for key in allowed if isinstance(payload.get(key), str)}
            cfg = config.update_config(**changes)
            self._send_json({"ok": True, "config": cfg.to_dict()})
            return

        if api_path == "/api/game/select":
            path = str(payload.get("path", "")).strip()
            if not game.is_warthunder_dir(path):
                self._send_error_json("不是有效的战雷目录", status=400, path=path)
                return
            cfg = config.update_config(game_path=path)
            info = game.scan_game(path)
            items = banks.scan_banks(Path(path) / "sound")
            self._send_json(
                {
                    "ok": True,
                    "config": cfg.to_dict(),
                    "game": info.to_dict(),
                    "banks": banks.summarize_banks(items),
                    "bank_list": [item.to_dict() for item in items],
                }
            )
            return

        if api_path == "/api/kit/select":
            path = str(payload.get("path", "")).strip()
            info = fmod_kit.inspect_kit(path)
            if not info.valid:
                self._send_error_json("官方 FMOD 工程目录无效", status=400, kit=info.to_dict())
                return
            cfg = config.update_config(kit_path=path)
            self._send_json({"ok": True, "config": cfg.to_dict(), "kit": info.to_dict()})
            return

        if api_path == "/api/rvc/select":
            path = str(payload.get("path", "")).strip()
            status = rvc.detect_rvc(path)
            if not status.available:
                self._send_error_json("没有识别到 RVC 主目录", status=400, rvc=status.to_dict())
                return
            cfg = config.update_config(rvc_path=status.path)
            self._send_json({"ok": True, "config": cfg.to_dict(), "rvc": status.to_dict()})
            return

        if api_path == "/api/rvc/train":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            epochs = int(payload.get("epochs") or 40)
            project = voice.load_project(data_dir, project_id)
            root = rvc.find_install(cfg.rvc_path or None)
            if not root:
                self._send_error_json("没有找到 RVC 整合包，请先在工作台安装 RVC 运行时", status=400)
                return
            project_root = voice.project_dir(data_dir, project_id)

            def runner(job: jobs.Job) -> dict:
                result = rvc.train(job, project_root, root=root, epochs=epochs)
                voice.set_rvc_model(data_dir, project_id, **result)
                return result

            try:
                job = jobs.manager.start(
                    "rvc_train", runner, subject=f"训练换声模型 · {project.get('name', '音色')}"
                )
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409, job=jobs.manager.snapshot())
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/voice/convert":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            kit = _kit_path(cfg)
            project_id = str(payload.get("project", "")).strip()
            project = voice.load_project(data_dir, project_id)
            if project.get("quick", {}).get("mode") != "original":
                self._send_error_json("这个项目不是原语言换声项目", status=400)
                return
            if not kit:
                self._send_error_json("请先选择官方 FMOD 工程", status=400)
                return
            root = rvc.find_install(cfg.rvc_path or None)

            def runner(job: jobs.Job) -> dict:
                return original.convert_project(
                    job,
                    data_dir,
                    project_id,
                    kit,
                    cfg.game_path or None,
                    root=root,
                )

            try:
                job = jobs.manager.start(
                    "voice_convert", runner, subject=f"原语言换声 · {project.get('name', '语音包')}"
                )
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409, job=jobs.manager.snapshot())
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/catalog/build":
            cfg = config.load_config()
            kit = _kit_path(cfg)
            if not kit or not Path(kit).is_dir():
                self._send_error_json("请先选择官方 FMOD 工程", status=400)
                return
            force = _bool_value(str(payload.get("force", "")))
            if not force:
                status = _catalog_status(cfg)
                if status.get("available") and not status.get("stale"):
                    self._send_json({"ok": True, "cached": True, "job": None, "catalog": status})
                    return
            data_dir = config.ensure_data_dir(cfg)

            def run_build(job):
                data = catalog.build_full_catalog(
                    kit,
                    progress=lambda stage, current, total, message: job.update(
                        stage=stage,
                        current=current,
                        total=total,
                        message=message,
                    ),
                    cancel_event=job.cancel_event,
                )
                catalog.store.set(data, data_dir)
                return {
                    "stats": data["stats"],
                    "generated_at": data["generated_at"],
                    "cache_file": str(catalog.cache_path(data_dir)),
                }

            try:
                job = jobs.manager.start("catalog", run_build, subject=Path(kit).name)
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409, job=jobs.manager.snapshot())
                return
            self._send_json({"ok": True, "cached": False, "job": job})
            return

        if api_path == "/api/jobs/current/cancel" or api_path == "/api/unpack/cancel":
            self._send_json({"ok": True, "job": jobs.manager.cancel()})
            return

        if api_path == "/api/unpack/start":
            cfg = config.load_config()
            if not cfg.game_path or not game.is_warthunder_dir(cfg.game_path):
                self._send_error_json("请先选择有效的战雷目录", status=400)
                return
            bank = str(payload.get("bank", "")).strip()
            if not bank:
                self._send_error_json("缺少 bank 参数", status=400)
                return
            try:
                unpack.resolve_bank(cfg.game_path, bank)
            except ValueError as exc:
                self._send_error_json(str(exc), status=400)
                return
            data_dir = config.ensure_data_dir(cfg)
            _catalog_status(cfg)
            catalog_data = catalog.store.snapshot()
            force = _bool_value(str(payload.get("force", "")))

            def run_unpack(job):
                return unpack.unpack_bank(
                    job,
                    cfg.game_path,
                    bank,
                    catalog_data=catalog_data,
                    base=data_dir,
                    force=force,
                )

            try:
                job = jobs.manager.start("unpack", run_unpack, subject=bank)
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409, job=jobs.manager.snapshot())
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/components/preflight":
            try:
                result = components.preflight(
                    str(payload.get("id", "")).strip(),
                    target=str(payload.get("target", "")).strip() or None,
                    slim=bool(payload.get("slim", False)),
                )
            except components.ComponentError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/components/download":
            try:
                job = components.manager.start(
                    str(payload.get("id", "")).strip(),
                    target=str(payload.get("target", "")).strip() or None,
                    keep_archive=bool(payload.get("keep_archive", False)),
                    source=str(payload.get("source", "auto")).strip() or "auto",
                    slim=bool(payload.get("slim", False)),
                )
            except components.ComponentBusy as exc:
                self._send_error_json(str(exc), status=409, job=components.manager.snapshot())
                return
            except components.ComponentError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/components/cancel":
            snapshot = components.manager.cancel()
            self._send_json({"ok": True, "job": snapshot})
            return

        if api_path == "/api/components/uninstall":
            snapshot = components.manager.snapshot()
            if snapshot and snapshot.get("active"):
                self._send_error_json("组件任务正在运行，请先取消或等待完成", status=409)
                return
            if jobs.manager.busy():
                self._send_error_json(
                    "有训练或合成任务正在使用运行时，请先等待任务结束", status=409
                )
                return
            try:
                result = components.uninstall(str(payload.get("id", "")).strip())
            except components.ComponentBusy as exc:
                self._send_error_json(str(exc), status=409)
                return
            except components.ComponentError as exc:
                self._send_error_json(str(exc), status=400)
                return
            cfg = config.load_config()
            storage.invalidate()
            self._send_json(
                {
                    "ok": True,
                    **result,
                    "components": components.describe(),
                    "storage": storage.usage(_data_dir(cfg), force=True),
                }
            )
            return

        if api_path == "/api/dialog/folder":
            title = str(payload.get("title", "")).strip() or "选择文件夹"
            initial = str(payload.get("initial", "")).strip()
            try:
                selected = dialogs.pick_folder(title=title, initial=initial)
            except Exception as exc:
                self._send_error_json(str(exc), status=500)
                return
            self._send_json({"ok": True, "path": selected, "cancelled": not selected})
            return

        if api_path == "/api/dialog/files":
            title = str(payload.get("title", "")).strip() or "选择音频文件"
            initial = str(payload.get("initial", "")).strip()
            kind = str(payload.get("kind", "")).strip()
            suffixes = (
                [".pth", ".ckpt", ".index"] if kind == "model" else None
            )
            try:
                selected = dialogs.pick_files(
                    title=title, initial=initial, suffixes=suffixes
                )
            except Exception as exc:
                self._send_error_json(str(exc), status=500)
                return
            self._send_json({"ok": True, "paths": selected, "cancelled": not selected})
            return

        if api_path == "/api/voice/package/export":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            name = str(payload.get("name", "")).strip()
            try:
                source = _voice_package_source(cfg, project_id, name)
            except ValueError as exc:
                self._send_error_json(str(exc), status=400)
                return
            except FileNotFoundError as exc:
                self._send_error_json(str(exc), status=404)
                return
            folder = _known_folder(cfg, "exports")
            target = folder / source.name
            temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copy2(source, temp)
                os.replace(temp, target)
            except OSError as exc:
                try:
                    temp.unlink()
                except OSError:
                    pass
                self._send_error_json(f"导出失败：{exc}", status=500)
                return
            self._send_json(
                {
                    "ok": True,
                    "name": target.name,
                    "path": str(target),
                    "folder": str(folder),
                    "bytes": target.stat().st_size,
                }
            )
            return

        if api_path == "/api/voice/package/install":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            name = str(payload.get("name", "")).strip()
            try:
                source = _voice_package_source(cfg, project_id, name)
            except ValueError as exc:
                self._send_error_json(str(exc), status=400)
                return
            except FileNotFoundError as exc:
                self._send_error_json(str(exc), status=404)
                return
            try:
                registered = packs.register_archive(data_dir, source, source.name)
            except packs.PackError as exc:
                self._send_error_json(str(exc), status=400)
                return
            token = registered["token"]
            try:
                entry = packs.install(
                    cfg,
                    data_dir,
                    token,
                    enable_mod=True,
                    config={"enable_mod": True, "fmod_sound_enable": True},
                )
            except packs.PackError as exc:
                try:
                    packs.discard_upload(data_dir, token)
                except packs.PackError:
                    pass
                self._send_error_json(str(exc), status=400)
                return
            self._send_json(
                {
                    "ok": True,
                    "pack": entry,
                    "installed": packs.list_installed(cfg, data_dir),
                }
            )
            return

        if api_path == "/api/voice/projects":
            cfg = config.load_config()
            try:
                project = voice.create_project(_data_dir(cfg), str(payload.get("name", "")))
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, "project": project})
            return

        if api_path == "/api/voice/reference":
            cfg = config.load_config()
            try:
                result = voice.import_reference(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    str(payload.get("path", "")).strip(),
                    prompt_text=str(payload.get("prompt_text", "")),
                    prompt_lang=str(payload.get("prompt_lang", "zh")),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/reference/add":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            paths = payload.get("paths")
            if not isinstance(paths, list):
                self._send_error_json("缺少 paths", status=400)
                return
            try:
                result = materials.add_references(
                    _data_dir(cfg), project_id, [str(item) for item in paths]
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/reference/delete":
            cfg = config.load_config()
            try:
                result = materials.remove_reference(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    str(payload.get("name", "")).strip(),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/reference/process":
            cfg = config.load_config()
            try:
                max_seconds = float(payload.get("max_seconds") or 30)
            except (TypeError, ValueError):
                max_seconds = 30.0
            try:
                result = materials.process_references(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    trim=bool(payload.get("trim", True)),
                    split=bool(payload.get("split", False)),
                    max_seconds=max(5.0, min(120.0, max_seconds)),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            except OSError as exc:
                self._send_error_json(f"素材处理失败：{exc}", status=500)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/take/import":
            cfg = config.load_config()
            try:
                result = materials.import_take(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    str(payload.get("line", "")).strip(),
                    str(payload.get("path", "")).strip(),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/prompt":
            cfg = config.load_config()
            try:
                result = voice.update_reference(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    prompt_text=str(payload["prompt_text"]) if "prompt_text" in payload else None,
                    prompt_lang=str(payload["prompt_lang"]) if "prompt_lang" in payload else None,
                    prompt_file=str(payload["prompt_file"]) if "prompt_file" in payload else None,
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/reference-texts":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            texts = payload.get("texts")
            if not isinstance(texts, dict):
                self._send_error_json("缺少 texts", status=400)
                return
            try:
                result = voice.set_reference_texts(_data_dir(cfg), project_id, texts)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/model":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            mode = str(payload.get("mode", "")).strip()
            if mode not in ("zero_shot", "fine_tuned"):
                self._send_error_json("mode 只能是 zero_shot 或 fine_tuned", status=400)
                return
            try:
                project = voice.load_project(_data_dir(cfg), project_id)
                model = project.get("model", {})
                if mode == "fine_tuned" and not (
                    model.get("sovits_path") and model.get("gpt_path")
                ):
                    raise voice.VoiceError("还没有训练好的模型")
                result = voice.set_model(
                    _data_dir(cfg),
                    project_id,
                    mode=mode,
                    state="ready" if mode == "fine_tuned" else "not_ready",
                    detail="使用微调模型" if mode == "fine_tuned" else "已切换回零样本",
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/model/import":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            sovits = str(payload.get("sovits", "")).strip()
            gpt = str(payload.get("gpt", "")).strip()
            if not sovits or not gpt:
                self._send_error_json("需要同时选择 SoVITS 的 .pth 和 GPT 的 .ckpt", status=400)
                return
            try:
                result = voice.import_model(
                    _data_dir(cfg),
                    project_id,
                    sovits,
                    gpt,
                    reset_audio=bool(payload.get("reset_audio", True)),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/rvc/model/import":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            model = str(payload.get("model", "")).strip()
            index = str(payload.get("index", "")).strip()
            if not model:
                self._send_error_json("先选择 RVC 的 .pth 换声模型", status=400)
                return
            try:
                result = voice.import_rvc_model(
                    _data_dir(cfg),
                    project_id,
                    model,
                    index,
                    reset_audio=bool(payload.get("reset_audio", True)),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/build":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            ready = exporter.readiness(project)
            if not ready["ready"]:
                self._send_error_json(ready["reason"] or "还不能构建语音包", status=400)
                return

            def runner(job: jobs.Job) -> dict:
                return exporter.build(job, data_dir, project_id)

            try:
                snapshot = jobs.manager.start("build", runner, subject=project.get("name", ""))
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409)
                return
            self._send_json({"ok": True, "job": snapshot})
            return

        if api_path == "/api/voice/train":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            if not gpt_sovits.find_install():
                self._send_error_json("还没有安装 GPT-SoVITS 运行时", status=409)
                return
            ready = trainer.readiness(project)
            if not ready["ready"]:
                self._send_error_json(ready["reason"], status=400)
                return

            def runner(job: jobs.Job) -> dict:
                return trainer.train(job, data_dir, project_id)

            try:
                snapshot = jobs.manager.start("train", runner, subject=project.get("name", ""))
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409)
                return
            self._send_json({"ok": True, "job": snapshot})
            return

        if api_path == "/api/voice/transcribe":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            root = voice.project_dir(data_dir, project_id)
            wanted = str(payload.get("file", "")).strip()
            if wanted:
                if Path(wanted).name != wanted or wanted not in project.get("reference", {}).get("files", []):
                    self._send_error_json("参考音频文件不存在", status=400)
                    return
                reference = root / "reference" / wanted
            else:
                reference = voice.reference_path(project, root)
            if not reference:
                self._send_error_json("请先导入参考音频", status=400)
                return
            language = str(payload.get("lang", "") or project.get("reference", {}).get("prompt_lang") or "zh")
            try:
                text = gpt_sovits.transcribe(reference, language=language)
                result = voice.update_reference(data_dir, project_id, prompt_text=text)
            except (voice.VoiceError, gpt_sovits.GptSoVitsError) as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, "text": text, **result})
            return

        if api_path == "/api/voice/import-events":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
            catalog_data = catalog.store.snapshot()
            if not catalog_data:
                self._send_error_json("事件目录尚未生成，请先在游戏素材页生成目录", status=409)
                return
            by_id = {event.get("id"): event for event in catalog_data.get("events", [])}
            assets = catalog_data.get("assets", {})
            added = 0
            skipped = 0
            for event_id in ids:
                event = by_id.get(str(event_id))
                if not event:
                    skipped += 1
                    continue
                event_assets = []
                for guid in event.get("asset_guids", []):
                    asset = assets.get(guid)
                    if asset and asset.get("path"):
                        event_assets.append({"guid": guid, "path": asset["path"]})
                source = {
                    "event_id": event.get("id", ""),
                    "event": event.get("event", ""),
                    "bank": event.get("bank", ""),
                    "assets": event_assets,
                }
                try:
                    voice.add_line(
                        _data_dir(cfg),
                        project_id,
                        "",
                        event=event.get("event", ""),
                        bank=event.get("bank", ""),
                        event_id=event.get("id", ""),
                        source=source,
                    )
                    added += 1
                except voice.VoiceError:
                    skipped += 1
            try:
                project = voice.load_project(_data_dir(cfg), project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            self._send_json({"ok": True, "project": project, "added": added, "skipped": skipped})
            return

        if api_path == "/api/voice/import-localized":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            bank = str(payload.get("bank", "")).strip()
            source_project = str(payload.get("source_project", "")).strip()
            kit = _kit_path(cfg)
            if not project_id or not bank or not kit:
                self._send_error_json("缺少项目、语音表或官方工程", status=400)
                return
            try:
                voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            try:
                items = localized.streams(kit, bank, cfg.game_path or None)
            except localized.LocalizedError as exc:
                self._send_error_json(str(exc), status=400)
                return
            names = payload.get("names") if isinstance(payload.get("names"), list) else []
            if names:
                wanted = {str(name) for name in names}
                items = [
                    item
                    for item in items
                    if item.get("event") in wanted or item.get("event_id") in wanted
                ]
            try:
                result = voice.add_localized_lines(
                    data_dir, project_id, bank, items, source_project=source_project
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/copy-official":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            bank = str(payload.get("bank", "")).strip()
            if not project_id or not bank:
                self._send_error_json("缺少项目或语音表", status=400)
                return
            try:
                voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            try:
                result = localized.copy_official_audio(
                    data_dir, project_id, bank, cfg.game_path or None
                )
            except localized.LocalizedError as exc:
                self._send_error_json(str(exc), status=400)
                return
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/lines":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            try:
                if isinstance(payload.get("items"), list):
                    result = voice.add_lines_bulk(_data_dir(cfg), project_id, payload["items"])
                else:
                    result = voice.add_line(
                        _data_dir(cfg),
                        project_id,
                        str(payload.get("text", "")),
                        event=str(payload.get("event", "")),
                        bank=str(payload.get("bank", "")),
                        event_id=str(payload.get("event_id", "")),
                    )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/line/update":
            cfg = config.load_config()
            try:
                result = voice.update_line(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    str(payload.get("line", "")).strip(),
                    text=str(payload.get("text", "")) if "text" in payload else None,
                    event=str(payload.get("event", "")) if "event" in payload else None,
                    bank=str(payload.get("bank", "")) if "bank" in payload else None,
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/line/tuning":
            cfg = config.load_config()
            raw_ids = payload.get("lines")
            line_ids = (
                [str(item) for item in raw_ids]
                if isinstance(raw_ids, list)
                else [str(payload.get("line", "")).strip()]
            )
            line_ids = [item for item in line_ids if item]
            if not line_ids:
                self._send_error_json("先选择要调整的台词", status=400)
                return
            reference = None
            if "reference" in payload:
                reference = str(payload.get("reference") or "")
            values = {}
            for key, label in (("speed", "语速"), ("gain_db", "音量")):
                if key not in payload:
                    continue
                raw = payload.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    values[key] = float(raw)
                except (TypeError, ValueError):
                    self._send_error_json(f"{label}必须是数字", status=400)
                    return
            try:
                result = voice.set_line_tuning(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    line_ids,
                    reference=reference,
                    speed=values.get("speed"),
                    gain_db=values.get("gain_db"),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/take/select":
            cfg = config.load_config()
            try:
                result = voice.select_take(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    str(payload.get("line", "")).strip(),
                    str(payload.get("take", "")).strip(),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/voice/import-texts":
            cfg = config.load_config()
            project_id = str(payload.get("project", "")).strip()
            rows = payload.get("rows")
            if not isinstance(rows, list):
                raw = str(payload.get("csv", "")).lstrip("\ufeff")
                try:
                    rows = list(csv.DictReader(io.StringIO(raw)))
                except csv.Error as exc:
                    self._send_error_json(f"CSV 解析失败：{exc}", status=400)
                    return
            try:
                result = voice.import_texts(_data_dir(cfg), project_id, rows)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/quick/prepare":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            kit = _kit_path(cfg)
            source_project = str(payload.get("source_project", "")).strip()
            nations = payload.get("nations") if isinstance(payload.get("nations"), list) else []
            scopes = payload.get("scopes") if isinstance(payload.get("scopes"), list) else []
            line_scopes = (
                payload.get("line_scopes")
                if isinstance(payload.get("line_scopes"), list)
                else None
            )
            bank_ids = payload.get("banks") if isinstance(payload.get("banks"), list) else []
            cockpit_events_raw = payload.get("cockpit_events")
            cockpit_events = (
                [str(item) for item in cockpit_events_raw if str(item)]
                if isinstance(cockpit_events_raw, list)
                else None
            )
            mode = str(payload.get("mode", "zh")).strip() or "zh"
            name = str(payload.get("name", "")).strip()
            if not kit:
                self._send_error_json("请先在工作台选择官方 FMOD 工程", status=400)
                return
            if not source_project:
                self._send_error_json("先选择一个训练好的音色项目", status=400)
                return
            try:
                voice.load_project(data_dir, source_project)
                selected_scopes = [str(item) for item in scopes]
                selected_line_scopes = (
                    [str(item) for item in line_scopes]
                    if line_scopes is not None
                    else None
                )
                bank_scopes = (
                    quickpack.bank_scopes_for_lines(selected_line_scopes)
                    if selected_line_scopes is not None
                    else selected_scopes
                )
                quickpack.selected_banks(
                    [str(item) for item in nations],
                    bank_scopes,
                    [str(item) for item in bank_ids],
                    kit,
                    cfg.game_path or None,
                )
                if mode not in {item["id"] for item in quickpack.MODES}:
                    raise voice.VoiceError(f"未知的成品语言：{mode}")
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return

            def runner(job: jobs.Job) -> dict:
                job.update(message="准备语音包")
                result = quickpack.prepare(
                    job,
                    data_dir,
                    kit,
                    cfg.game_path or None,
                    source_project,
                    [str(item) for item in nations],
                    selected_scopes,
                    name,
                    mode,
                    [str(item) for item in bank_ids],
                    selected_line_scopes,
                    cockpit_events,
                )
                return result

            try:
                job = jobs.manager.start(
                    "quick_prepare", runner, subject=name or "快速语音包"
                )
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409)
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/voice/preview":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            text = str(payload.get("text", "")).strip()
            if not text:
                self._send_error_json("先输入要试听的文本", status=400)
                return
            if len(text) > 200:
                self._send_error_json("试听文本最多 200 字", status=400)
                return
            if not gpt_sovits.find_install():
                self._send_error_json("还没有安装 GPT-SoVITS 运行时", status=409)
                return
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            if not (project.get("reference", {}).get("prompt_text") or "").strip():
                self._send_error_json("请先填写参考音频对应的文字", status=400)
                return
            preview_reference = ""
            if "reference" in payload:
                preview_reference = Path(str(payload.get("reference") or "")).name
                if preview_reference and preview_reference not in (
                    project.get("reference", {}).get("files") or []
                ):
                    self._send_error_json("参考语气不在这个项目的参考素材里", status=400)
                    return
            preview_speed = 1.0
            preview_gain = 0.0
            try:
                if payload.get("speed") not in (None, ""):
                    preview_speed = float(payload["speed"])
                if payload.get("gain_db") not in (None, ""):
                    preview_gain = float(payload["gain_db"])
            except (TypeError, ValueError):
                self._send_error_json("语速或音量必须是数字", status=400)
                return
            preview_speed = max(voice.SPEED_MIN, min(voice.SPEED_MAX, preview_speed))
            preview_gain = max(voice.GAIN_MIN_DB, min(voice.GAIN_MAX_DB, preview_gain))

            def runner(job: jobs.Job) -> dict:
                current = voice.load_project(data_dir, project_id)
                root = voice.project_dir(data_dir, project_id)
                choices = voice.reference_choices(current, root, 1)
                if not choices:
                    raise voice.VoiceError("参考音频文件不存在，请重新导入")
                choice = choices[0]
                if preview_reference:
                    texts = (
                        current.get("reference", {}).get("texts")
                        if isinstance(current.get("reference", {}).get("texts"), dict)
                        else {}
                    )
                    choice = {
                        "name": preview_reference,
                        "path": root / "reference" / preview_reference,
                        "prompt_text": str(
                            texts.get(preview_reference)
                            or current.get("reference", {}).get("prompt_text", "")
                        ).strip(),
                    }
                prompt_text = str(
                    choice.get("prompt_text")
                    or current.get("reference", {}).get("prompt_text", "")
                ).strip()
                prompt_lang = current.get("reference", {}).get("prompt_lang", "zh")
                out_dir = data_dir / "cache" / "preview"
                out_dir.mkdir(parents=True, exist_ok=True)
                for old in sorted(out_dir.glob("pv_*.wav"), key=lambda p: p.stat().st_mtime)[:-50]:
                    old.unlink(missing_ok=True)
                name_ = f"pv_{uuid.uuid4().hex[:12]}.wav"
                out = out_dir / name_
                job.update(total=1, message="启动 GPT-SoVITS，准备试听")
                gpt_sovits.start_server()
                gpt_sovits.activate(current.get("model"))
                job.update(message=f"正在试听：{text[:30]}")
                seed = zlib.crc32((text + prompt_text).encode("utf-8")) & 0x7FFFFFFF
                gpt_sovits.synthesize(
                    text=text,
                    text_lang="zh",
                    ref_audio_path=choice["path"],
                    prompt_text=prompt_text,
                    prompt_lang=prompt_lang,
                    out_path=out,
                    speed_factor=preview_speed,
                    seed=seed,
                    min_seconds=gpt_sovits.strict_minimum_seconds(text, "zh"),
                    strict_min=True,
                )
                if abs(preview_gain) > 0.001:
                    voice.apply_gain_to_file(out, preview_gain)
                job.step()
                return {
                    "name": name_,
                    "duration_sec": voice.wav_duration(out),
                    "url": f"/api/voice/preview/audio?name={name_}",
                    "speed": preview_speed,
                    "gain_db": preview_gain,
                }

            try:
                job = jobs.manager.start("preview", runner, subject=project.get("name", ""))
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409)
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/voice/synthesize":
            cfg = config.load_config()
            data_dir = _data_dir(cfg)
            project_id = str(payload.get("project", "")).strip()
            requested = payload.get("lines") if isinstance(payload.get("lines"), list) else []
            force = bool(payload.get("force", False))
            dedupe_requested = bool(payload.get("dedupe", False))
            text_lang = str(payload.get("text_lang", "zh") or "zh").strip()
            try:
                versions = int(payload.get("versions", 1) or 1)
            except (TypeError, ValueError):
                versions = 1
            versions = max(1, min(int(voice.MAX_TAKES), versions))
            try:
                speed = float(payload.get("speed", 1.0) or 1.0)
            except (TypeError, ValueError):
                speed = 1.0
            speed = max(0.5, min(2.0, speed))
            try:
                project = voice.load_project(data_dir, project_id)
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=404)
                return
            if not gpt_sovits.find_install():
                self._send_error_json("还没有安装 GPT-SoVITS 运行时", status=409)
                return
            if not (project.get("reference", {}).get("prompt_text") or "").strip():
                self._send_error_json("请先填写参考音频对应的台词（prompt 文本）", status=400)
                return

            def runner(job: jobs.Job) -> dict:
                current = voice.load_project(data_dir, project_id)
                root = voice.project_dir(data_dir, project_id)
                references = voice.reference_choices(current, root, 1)
                if not references:
                    raise voice.VoiceError("参考音频文件不存在，请重新导入")
                default_choice = references[0]
                prompt_text = current.get("reference", {}).get("prompt_text", "")
                prompt_lang = current.get("reference", {}).get("prompt_lang", "zh")
                wanted = set(str(item) for item in requested)
                lines = [
                    line
                    for line in current.get("lines", [])
                    if line.get("text")
                    and (not wanted or line.get("id") in wanted)
                    and (force or not line.get("audio") or line.get("status") != "ready")
                ]
                if not lines:
                    raise voice.VoiceError("没有需要合成的台词")
                dedupe = dedupe_requested and versions == 1

                def group_of(line: dict) -> dict:
                    tuning = voice.line_tuning(line)
                    reference = voice.tuning_reference(
                        current, root, line, default_choice
                    ) or default_choice
                    return {
                        "reference": reference,
                        "tuning": tuning,
                        "speed": (
                            tuning["speed"]
                            if isinstance(line.get("tuning"), dict)
                            else speed
                        ),
                    }

                if dedupe:
                    grouped: dict[tuple, dict] = {}
                    for line in lines:
                        info = group_of(line)
                        key = (
                            str(line.get("text") or "").strip(),
                            str(info["reference"].get("name") or ""),
                            round(float(info["speed"]), 2),
                            round(float(info["tuning"]["gain_db"]), 1),
                        )
                        entry = grouped.get(key)
                        if entry is None:
                            entry = {**info, "lines": []}
                            grouped[key] = entry
                        entry["lines"].append(line)
                    groups = list(grouped.values())
                    job.update(
                        total=len(groups),
                        message=(
                            f"去重后合成 {len(groups)} 组（文本+语气），覆盖 {len(lines)} 句"
                        ),
                    )
                else:
                    groups = [{**group_of(line), "lines": [line]} for line in lines]
                    job.update(
                        total=len(lines) * versions,
                        message=f"启动 GPT-SoVITS（每句 {versions} 版）",
                    )
                job.update(
                    total=None,
                    message="正在启动 GPT-SoVITS 引擎（首次约 1–2 分钟）…",
                )
                gpt_sovits.start_server()
                job.update(message="引擎已启动，载入音色模型…")
                gpt_sovits.activate(current.get("model"))
                job.update(
                    total=len(groups) * versions,
                    current=0,
                    message="引擎就绪，开始合成",
                )
                synced = 0
                take_total = 0
                errors: list[str] = []
                partial_errors: list[str] = []
                for index, group in enumerate(groups, 1):
                    group_lines = group["lines"]
                    line = group_lines[0]
                    reference = group["reference"]
                    line_speed = float(group["speed"])
                    take_prompt = reference["prompt_text"] or prompt_text
                    jobs.check_cancel(job)
                    steps = 0
                    strict_min = (
                        gpt_sovits.strict_minimum_seconds(line["text"], text_lang)
                        if versions > 1
                        else None
                    )
                    try:
                        takes = []
                        variant_errors: list[str] = []
                        for variant in range(versions):
                            jobs.check_cancel(job)
                            base_seed = zlib.crc32(
                                (line["text"] + take_prompt).encode("utf-8")
                            ) & 0x7FFFFFFF
                            coverage = (
                                f"（覆盖 {len(group_lines)} 句）"
                                if dedupe and len(group_lines) > 1
                                else ""
                            )
                            job.update(
                                message=(
                                    f"合成 {index}/{len(groups)}：{line['text'][:24]}"
                                    f" · 第 {variant + 1}/{versions} 版{coverage}"
                                )
                            )
                            try:
                                take_id = "t_" + uuid.uuid4().hex[:12]
                                out = voice.take_audio_path(
                                    data_dir, project_id, line["id"], take_id
                                )
                                seed = (base_seed + variant * 104729) & 0x7FFFFFFF
                                gpt_sovits.synthesize(
                                    text=line["text"],
                                    text_lang=text_lang,
                                    ref_audio_path=reference["path"],
                                    prompt_text=take_prompt,
                                    prompt_lang=prompt_lang,
                                    out_path=out,
                                    speed_factor=line_speed,
                                    seed=seed,
                                    top_k=TAKE_PROFILE["top_k"],
                                    top_p=TAKE_PROFILE["top_p"],
                                    temperature=TAKE_PROFILE["temperature"],
                                    min_seconds=strict_min,
                                    strict_min=versions > 1,
                                )
                                duration = voice.wav_duration(out)
                                if duration <= 0.05:
                                    raise voice.VoiceError("合成音频为空")
                                takes.append(
                                    {
                                        "id": take_id,
                                        "duration_sec": duration,
                                        "seed": seed,
                                        "ref_file": reference["name"],
                                        "created_at": voice._now(),
                                    }
                                )
                            except jobs.JobCancelled:
                                raise
                            except Exception as exc:  # noqa: BLE001
                                variant_errors.append(f"第 {variant + 1} 版：{exc}")
                            finally:
                                steps += 1
                                job.step()
                        if not takes:
                            raise voice.VoiceError(
                                "；".join(variant_errors) or "没有可用的合成版本"
                            )
                        voice.save_takes(
                            data_dir,
                            project_id,
                            line["id"],
                            takes,
                            selected_id=takes[0]["id"],
                        )
                        take_total += len(takes)
                        synced += 1
                        if variant_errors:
                            partial_errors.append(
                                f"{line.get('text', '')[:24]}："
                                f"{len(variant_errors)} 个候选失败"
                            )
                        if dedupe and len(group_lines) > 1:
                            source_take = voice.take_audio_path(
                                data_dir, project_id, line["id"], takes[0]["id"]
                            )
                            for other in group_lines[1:]:
                                jobs.check_cancel(job)
                                try:
                                    other_take_id = "t_" + uuid.uuid4().hex[:12]
                                    target = voice.take_audio_path(
                                        data_dir, project_id, other["id"], other_take_id
                                    )
                                    target.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(source_take, target)
                                    voice.save_takes(
                                        data_dir,
                                        project_id,
                                        other["id"],
                                        [
                                            {
                                                **takes[0],
                                                "id": other_take_id,
                                                "created_at": voice._now(),
                                            }
                                        ],
                                        selected_id=other_take_id,
                                    )
                                    take_total += 1
                                    synced += 1
                                except jobs.JobCancelled:
                                    raise
                                except Exception as exc:  # noqa: BLE001
                                    message = str(exc)
                                    errors.append(
                                        f"{other.get('text', '')[:24]}：复用音频失败：{message}"
                                    )
                                    voice.set_line_error(
                                        data_dir, project_id, other["id"], message
                                    )
                    except jobs.JobCancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        message = str(exc)
                        affected = len(group_lines) if dedupe else 1
                        suffix = f"（影响 {affected} 句）" if affected > 1 else ""
                        errors.append(f"{line.get('text', '')[:24]}：{message}{suffix}")
                        for target in group_lines:
                            voice.set_line_error(
                                data_dir, project_id, target["id"], message
                            )
                summary = (
                    f"已合成 {synced} 句 · 每句 {versions} 个候选（同参考，只换种子），点“选用”挑一版"
                    if versions > 1
                    else f"已合成 {synced} 句"
                )
                if dedupe:
                    summary = f"去重合成 {len(groups)} 组（文本+语气），覆盖 {synced} 句"
                if partial_errors:
                    summary += f"；{len(partial_errors)} 句有候选失败"
                if errors:
                    summary += f"；{len(errors)} 个错误"
                job.update(message=summary)
                return {
                    "project": project_id,
                    "synced": synced,
                    "versions": versions,
                    "takes": take_total,
                    "dedupe": dedupe,
                    "unique_texts": len(groups),
                    "errors": errors[:10],
                    "partial_errors": partial_errors[:10],
                }

            try:
                job = jobs.manager.start("synthesize", runner, subject=project.get("name", ""))
            except jobs.JobBusy as exc:
                self._send_error_json(str(exc), status=409)
                return
            self._send_json({"ok": True, "job": job})
            return

        if api_path == "/api/voice/line/delete":
            cfg = config.load_config()
            try:
                result = voice.delete_line(
                    _data_dir(cfg),
                    str(payload.get("project", "")).strip(),
                    str(payload.get("line", "")).strip(),
                )
            except voice.VoiceError as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, **result})
            return

        if api_path == "/api/system/open-folder":
            target = str(payload.get("target", "")).strip()
            try:
                folder = _known_folder(config.load_config(), target)
                _open_folder(folder)
            except (ValueError, FileNotFoundError, OSError) as exc:
                self._send_error_json(str(exc), status=400)
                return
            self._send_json({"ok": True, "target": target, "path": str(folder)})
            return

        if api_path == "/api/system/uninstall":
            if not config.FROZEN:
                self._send_error_json("当前是源码模式，没有卸载程序；请使用安装版", status=400)
                return
            app_dir = Path(sys.executable).resolve().parent
            uninstaller = app_dir / "unins000.exe"
            if not uninstaller.is_file():
                self._send_error_json("找不到卸载程序 unins000.exe，请用 Windows 设置卸载", status=400)
                return
            keep_data = bool(payload.get("keep_data"))
            args = [str(uninstaller), "/KEEPDATA" if keep_data else "/DELETEAPPDATA"]
            try:
                subprocess.Popen(
                    args,
                    cwd=str(app_dir),
                    close_fds=True,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except OSError as exc:
                self._send_error_json(f"启动卸载程序失败：{exc}", status=500)
                return
            self._send_json({"ok": True, "keep_data": keep_data})
            threading.Timer(1.2, lambda: os._exit(0)).start()
            return

        self._send_error_json("未知接口", status=404)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(host: str = "127.0.0.1", port: int = 8765) -> Server:
    return Server((host, port), Handler)


def port_in_use(host: str, port: int) -> bool:
    """避免 Windows 上 SO_REUSEADDR 让两个实例同时监听同一端口。"""
    if port == 0:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="WT Voice Forge 本地服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)

    if port_in_use(args.host, args.port):
        print(f"端口 {args.port} 已被占用，可能已经有一个工作台在运行：")
        print(f"http://{args.host}:{args.port}/")
        print("请先关闭已有实例，或使用 --port 换一个端口。")
        return

    httpd = create_server(args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"{config.APP_NAME} v{config.APP_VERSION} ({config.MILESTONE})")
    print(f"工作台：{url}")
    print("按 Ctrl+C 停止。")

    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
