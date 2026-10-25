"""游戏 bank 解包、试听清单与 best-effort 匹配。

只读取游戏的 sound 目录；输出写到数据目录下的 unpacked/<bank>。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

from app import banks, config, vgmstream
from app.jobs import JobCancelled

MANIFEST_SCHEMA = 1
MANIFEST_NAME = "manifest.json"
SUMMARY_NAME = "summary.json"
STREAMS_DIR = "streams"
BANK_RE = re.compile(r"^[A-Za-z0-9._-]+$")
POLL_SECONDS = 0.6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir() -> Path:
    cfg = config.load_config()
    return Path(cfg.data_dir).expanduser() if cfg.data_dir else config.DEFAULT_DATA_DIR


def output_root(base: str | Path | None = None) -> Path:
    root = Path(base).expanduser() if base else data_dir()
    return root / "unpacked"


def bank_dir(bank: str, base: str | Path | None = None) -> Path:
    if not bank or not BANK_RE.fullmatch(bank):
        raise ValueError("非法 bank 名称")
    root = output_root(base).resolve()
    target = (root / bank).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("bank 路径越界") from exc
    return target


def resolve_bank(game_path: str | Path, bank_name: str):
    items = banks.scan_banks(Path(game_path).expanduser() / "sound")
    for item in items:
        if item.name == bank_name:
            return item
    raise ValueError(f"游戏目录中没有找到 bank：{bank_name}")


def _count_wavs(root: Path) -> int:
    return sum(1 for path in root.rglob("*.wav") if path.is_file())


def _tail(path: Path, limit: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def _parse_output_name(filename: str) -> tuple[int | None, str]:
    stem = Path(filename).stem
    match = re.match(r"^(\d+)_(.*)$", stem)
    if match:
        return int(match.group(1)), match.group(2)
    return None, stem


def sanitize_component(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = "stream"
    return value[:80].rstrip(" .") or "stream"


def normalize_name(value: str) -> str:
    stem = Path(value.replace("\\", "/")).stem.lower()
    stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", stem).strip("_")
    previous = None
    while previous != stem:
        previous = stem
        stem = re.sub(r"[-_]0*\d+$", "", stem)
    return stem.strip("_")


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_manifest(bank: str, base: str | Path | None = None) -> dict | None:
    return _load_json(bank_dir(bank, base) / MANIFEST_NAME)


def load_summary(bank: str, base: str | Path | None = None) -> dict | None:
    return _load_json(bank_dir(bank, base) / SUMMARY_NAME)


def _write_json(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _catalog_assets(catalog_data: dict | None, bank: str) -> tuple[dict, dict, dict]:
    exact: dict[str, dict] = {}
    base: dict[str, list[dict]] = {}
    normalized: dict[str, list[dict]] = {}
    if not catalog_data:
        return exact, base, normalized
    assets = catalog_data.get("assets", {})
    for event in catalog_data.get("events", []):
        if event.get("bank") != bank:
            continue
        for guid in event.get("asset_guids", []):
            asset = assets.get(guid)
            if not asset or not asset.get("path"):
                continue
            path = asset["path"]
            key = str(Path(path).with_suffix("")).replace("\\", "/").lower()
            entry = {
                "path": path,
                "event": event.get("event", ""),
                "exists": bool(asset.get("exists")),
            }
            exact.setdefault(key, entry)
            base.setdefault(Path(path).stem.lower(), []).append(entry)
            normalized.setdefault(normalize_name(path), []).append(entry)
    return exact, base, normalized


def match_streams(streams: list[dict], catalog_data: dict | None, bank: str) -> dict:
    exact, base, normalized = _catalog_assets(catalog_data, bank)
    matched_paths: set[str] = set()
    for stream in streams:
        name = stream.get("name", "")
        name_path = name.replace("\\", "/")
        key = str(Path(name_path).with_suffix("")).replace("\\", "/").lower()
        entry = None
        kind = ""
        if key in exact:
            entry = exact[key]
            kind = "exact"
        else:
            candidates = base.get(Path(name_path).stem.lower(), [])
            unique_base = {item["path"].lower(): item for item in candidates}
            if len(unique_base) == 1:
                entry = next(iter(unique_base.values()))
                kind = "basename"
            else:
                candidates = normalized.get(normalize_name(name_path), [])
                unique = {item["path"].lower(): item for item in candidates}
                if len(unique) == 1:
                    entry = next(iter(unique.values()))
                    kind = "normalized"
        if entry:
            stream["match"] = kind
            stream["asset"] = entry["path"]
            stream["events"] = [entry["event"]] if entry["event"] else []
            matched_paths.add(entry["path"].lower())
        else:
            stream["match"] = ""
            stream["asset"] = ""
            stream["events"] = []

    catalog_paths = set()
    if catalog_data:
        for event in catalog_data.get("events", []):
            if event.get("bank") != bank:
                continue
            for guid in event.get("asset_guids", []):
                asset = catalog_data.get("assets", {}).get(guid)
                if asset and asset.get("path"):
                    catalog_paths.add(asset["path"].lower())
    return {
        "matched_streams": sum(1 for stream in streams if stream.get("match")),
        "unmatched_streams": sum(1 for stream in streams if not stream.get("match")),
        "catalog_assets": len(catalog_paths),
        "matched_assets": len(matched_paths),
    }


def _process_output(
    temp: Path,
    staging: Path,
    bank: str,
    catalog_data: dict | None,
    source: dict,
    vgmstream_version: str,
) -> dict:
    streams_dir = staging / STREAMS_DIR
    streams_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[Path, Path]] = []
    for path in temp.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".wav":
            continue
        try:
            relative = path.relative_to(temp)
        except ValueError:
            continue
        if any(part == ".." for part in relative.parts):
            continue
        candidates.append((path, relative))

    streams: list[dict] = []
    used_ids: set[str] = set()
    used_files: set[str] = set()
    total_size = 0
    total_duration = 0.0
    for position, (path, relative) in enumerate(sorted(candidates, key=lambda item: item[1].as_posix().lower()), 1):
        index, name = _parse_output_name(relative.name)
        if index is None:
            index = position
        stream_id = str(index)
        if stream_id in used_ids:
            stream_id = f"{index}-{position}"
        used_ids.add(stream_id)

        safe = sanitize_component(name.replace("/", "_").replace("\\", "_"))
        filename = f"{index:05d}_{safe}.wav"
        if filename.lower() in used_files:
            filename = f"{index:05d}_{safe}_{position}.wav"
        used_files.add(filename.lower())
        destination = streams_dir / filename
        shutil.move(str(path), str(destination))
        size = destination.stat().st_size
        total_size += size
        duration = None
        try:
            with wave.open(str(destination), "rb") as handle:
                rate = handle.getframerate() or 1
                duration = round(handle.getnframes() / rate, 3)
                total_duration += duration
        except (OSError, wave.Error):
            pass
        streams.append(
            {
                "id": stream_id,
                "index": index,
                "name": name,
                "original": relative.as_posix(),
                "file": f"{STREAMS_DIR}/{filename}",
                "size": size,
                "duration_sec": duration,
                "match": "",
                "asset": "",
                "events": [],
            }
        )

    counts = match_streams(streams, catalog_data, bank)
    counts.update(
        {
            "streams": len(streams),
            "size_bytes": total_size,
            "duration_sec": round(total_duration, 3),
        }
    )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "bank": bank,
        "generated_at": _now(),
        "vgmstream": vgmstream_version,
        "source": source,
        "counts": counts,
        "streams": streams,
    }
    summary = {
        "schema": MANIFEST_SCHEMA,
        "bank": bank,
        "generated_at": manifest["generated_at"],
        "vgmstream": vgmstream_version,
        "source": source,
        "counts": counts,
    }
    _write_json(staging / MANIFEST_NAME, manifest)
    _write_json(staging / SUMMARY_NAME, summary)
    return summary


def _swap_staging(staging: Path, target: Path) -> None:
    root = target.parent
    backup = root / f".old-{uuid.uuid4().hex[:8]}"
    had_target = target.exists()
    if had_target:
        try:
            target.replace(backup)
        except OSError as exc:
            raise ValueError("解包目录被占用，请先关闭正在播放的音频") from exc
    try:
        staging.replace(target)
    except OSError:
        if had_target and backup.exists():
            backup.replace(target)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def unpack_bank(
    job,
    game_path: str | Path,
    bank_name: str,
    *,
    catalog_data: dict | None = None,
    base: str | Path | None = None,
    force: bool = False,
) -> dict:
    target = bank_dir(bank_name, base)
    if not force:
        summary = load_summary(bank_name, base)
        if summary:
            job.update(stage="done", message="已有解包结果", current=1, total=1)
            return {"cached": True, **summary}

    bank = resolve_bank(game_path, bank_name)
    job.update(stage="runtime", message="检查 vgmstream", current=0, total=None)
    exe = vgmstream.ensure(job)
    root = output_root(base)
    root.mkdir(parents=True, exist_ok=True)
    estimate = max(512 * 1024 * 1024, int(bank.size_bytes * 12))
    free = shutil.disk_usage(root).free
    if free < estimate:
        raise ValueError(
            f"磁盘空间不足：解包 {bank.name} 建议预留约 {estimate / 1024 ** 3:.1f} GB，"
            f"当前可用 {free / 1024 ** 3:.1f} GB"
        )

    temp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    staging = root / f".staging-{uuid.uuid4().hex[:8]}"
    log_path = temp / "vgmstream.log"
    process: subprocess.Popen | None = None
    try:
        command = vgmstream.command(exe, bank.assets_path, temp)
        job.update(stage="extract", message="解包 bank", current=0, total=None)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(Path(exe).parent),
                creationflags=creationflags,
            )
            while process.poll() is None:
                if job.cancel_event.is_set():
                    _terminate(process)
                    raise JobCancelled
                count = _count_wavs(temp)
                job.update(
                    stage="extract",
                    message=f"解出 {count} 个流",
                    current=count,
                    total=None,
                )
                time.sleep(POLL_SECONDS)

        if job.cancel_event.is_set():
            raise JobCancelled
        count = _count_wavs(temp)
        if process.returncode != 0:
            tail = _tail(log_path)
            raise ValueError(
                f"vgmstream 退出码 {process.returncode}，已解出 {count} 个流。"
                + (f" 日志：{tail}" if tail else "")
            )
        if count == 0:
            tail = _tail(log_path)
            raise ValueError(
                "没有解出任何音频，可能是 bank 格式或 vgmstream 版本不兼容。"
                + (f" 日志：{tail}" if tail else "")
            )

        job.update(stage="process", message="整理文件与匹配", current=0, total=None)
        source = {
            "assets_bank": bank.assets_path.rsplit(os.sep, 1)[-1],
            "size_bytes": bank.size_bytes,
            "modified": bank.modified,
        }
        summary = _process_output(
            temp,
            staging,
            bank.name,
            catalog_data,
            source,
            vgmstream.version(exe) or vgmstream.VERSION,
        )
        _swap_staging(staging, target)
        job.update(
            stage="done",
            message="解包完成",
            current=summary["counts"]["streams"],
            total=summary["counts"]["streams"],
        )
        return {"cached": False, **summary}
    finally:
        shutil.rmtree(temp, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        if process is not None and process.poll() is None:
            _terminate(process)


def summary_for_banks(game_path: str | Path | None, base: str | Path | None = None) -> dict:
    path = Path(game_path).expanduser() if game_path else None
    entries = []
    if path:
        for bank in banks.scan_banks(path / "sound"):
            summary = load_summary(bank.name, base)
            entries.append(
                {
                    "name": bank.name,
                    "label": bank.label,
                    "category": bank.category,
                    "category_label": bank.category_label,
                    "scope": bank.scope,
                    "language": bank.language,
                    "language_label": bank.language_label,
                    "size_bytes": bank.size_bytes,
                    "modified": bank.modified,
                    "output": summary,
                }
            )
    return {
        "ok": True,
        "vgmstream": vgmstream.status(),
        "game_path": str(path) if path else "",
        "game_available": bool(path and (path / "sound").is_dir()),
        "output_root": str(output_root(base)),
        "banks": entries,
    }


def stream_file(bank: str, stream_id: str, base: str | Path | None = None) -> tuple[Path, dict]:
    manifest = load_manifest(bank, base)
    if not manifest:
        raise ValueError("这个 bank 还没有解包结果")
    for stream in manifest.get("streams", []):
        if str(stream.get("id")) == str(stream_id):
            target = (bank_dir(bank, base) / stream["file"]).resolve()
            root = bank_dir(bank, base).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("解包文件路径越界") from exc
            if not target.is_file():
                raise ValueError("解包文件不存在，请重新解包")
            return target, stream
    raise ValueError("没有找到这个流")
