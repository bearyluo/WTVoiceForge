"""声音项目：参考音频、台词表与合成结果。

每个项目保存在 <data>/voice_projects/<id>/：
  project.json          项目、参考音频、台词与状态
  reference/            导入的参考音频
  audio/<line_id>.wav   合成结果

这里只做本地文件与数据校验，不依赖具体的 GPT-SoVITS 安装。
"""
from __future__ import annotations

import array
import json
import math
import os
import re
import shutil
import sys
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac"}
MAX_REFERENCE_FILES = 500
MAX_REFERENCE_BYTES = 2 * 1024 * 1024 * 1024
SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")
TAKE_ID_RE = re.compile(r"t_[0-9a-f]{12}")
MAX_TAKES = 4

SPEED_MIN = 0.75
SPEED_MAX = 1.35
GAIN_MIN_DB = -12.0
GAIN_MAX_DB = 6.0
RAW_DIR = "raw"


class VoiceError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / "voice_projects"


def project_dir(data_dir: str | Path, project_id: str) -> Path:
    if not re.fullmatch(r"p_[0-9a-f]{12}", project_id or ""):
        raise VoiceError("无效的项目编号")
    return _root(data_dir) / project_id


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def save_project(data_dir: str | Path, project: dict) -> dict:
    project["updated_at"] = _now()
    path = project_dir(data_dir, project["id"]) / "project.json"
    _atomic_write(path, project)
    return project


def load_project(data_dir: str | Path, project_id: str) -> dict:
    path = project_dir(data_dir, project_id) / "project.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VoiceError("项目不存在") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceError("项目文件损坏") from exc
    if data.get("schema") != SCHEMA:
        raise VoiceError("项目版本不兼容")
    return data


def list_projects(data_dir: str | Path) -> list[dict]:
    root = _root(data_dir)
    if not root.is_dir():
        return []
    result = []
    for folder in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not folder.is_dir():
            continue
        try:
            project = load_project(data_dir, folder.name)
        except VoiceError:
            continue
        lines = project.get("lines", [])
        result.append(
            {
                "id": project["id"],
                "name": project.get("name", "未命名"),
                "created_at": project.get("created_at", ""),
                "updated_at": project.get("updated_at", ""),
                "reference_count": len(project.get("reference", {}).get("files", [])),
                "line_count": len(lines),
                "ready_count": sum(1 for line in lines if line.get("audio")),
                "model": project.get("model", {}),
                "rvc": project.get("rvc", {}),
                "quick": project.get("quick", {}),
                "export": project.get("export", {}),
            }
        )
    return result


def create_project(data_dir: str | Path, name: str) -> dict:
    title = (name or "").strip()[:60] or "未命名音色"
    project_id = "p_" + uuid.uuid4().hex[:12]
    project = {
        "schema": SCHEMA,
        "id": project_id,
        "name": title,
        "created_at": _now(),
        "updated_at": _now(),
        "reference": {
            "dir": "reference",
            "files": [],
            "texts": {},
            "prompt_file": "",
            "prompt_text": "",
            "prompt_lang": "zh",
        },
        "model": {"mode": "zero_shot", "state": "not_ready", "detail": ""},
        "lines": [],
    }
    folder = project_dir(data_dir, project_id)
    (folder / "reference").mkdir(parents=True, exist_ok=True)
    (folder / "audio").mkdir(parents=True, exist_ok=True)
    save_project(data_dir, project)
    return project


def _safe_filename(name: str) -> str:
    stem = Path(name).stem[:60] or "audio"
    suffix = Path(name).suffix.lower()
    cleaned = SAFE_NAME_RE.sub("_", stem).strip(" ._") or "audio"
    return f"{cleaned}{suffix}"


def import_reference(
    data_dir: str | Path,
    project_id: str,
    source: str | Path,
    prompt_text: str = "",
    prompt_lang: str = "zh",
) -> dict:
    project = load_project(data_dir, project_id)
    source_path = Path(source).expanduser()
    if not source_path.exists():
        raise VoiceError("参考音频路径不存在")
    folder = project_dir(data_dir, project_id)
    target = folder / "reference"
    target.mkdir(parents=True, exist_ok=True)

    candidates: list[Path] = []
    if source_path.is_file():
        if source_path.suffix.lower() not in AUDIO_EXTS:
            raise VoiceError("这个文件不是支持的音频格式")
        candidates = [source_path]
    else:
        for item in sorted(source_path.rglob("*")):
            if item.is_file() and item.suffix.lower() in AUDIO_EXTS:
                candidates.append(item)
            if len(candidates) >= MAX_REFERENCE_FILES:
                break

    if not candidates:
        raise VoiceError("这个文件夹里没有找到音频文件（wav/mp3/flac/m4a/ogg）")

    total = 0
    copied: list[str] = []
    for index, item in enumerate(candidates, 1):
        size = item.stat().st_size
        if total + size > MAX_REFERENCE_BYTES:
            break
        total += size
        name = _safe_filename(item.name)
        dest = target / f"{index:03d}_{name}"
        shutil.copy2(item, dest)
        copied.append(dest.name)

    if not copied:
        raise VoiceError("没有可导入的音频")

    project["reference"]["files"] = copied
    old_texts = project["reference"].get("texts")
    project["reference"]["texts"] = (
        {name: text for name, text in old_texts.items() if name in copied}
        if isinstance(old_texts, dict)
        else {}
    )
    project["reference"]["prompt_text"] = (prompt_text or project["reference"].get("prompt_text", "")).strip()
    project["reference"]["prompt_lang"] = (prompt_lang or "zh").strip() or "zh"
    if not project["reference"].get("prompt_file") or project["reference"]["prompt_file"] not in copied:
        project["reference"]["prompt_file"] = copied[0]
    project["model"]["state"] = "not_ready"
    project["model"]["detail"] = "参考音频已导入，等待克隆或训练"
    save_project(data_dir, project)
    return {
        "project": project,
        "copied": len(copied),
        "bytes": total,
        "prompt_file": project["reference"]["prompt_file"],
    }


def update_reference(
    data_dir: str | Path,
    project_id: str,
    *,
    prompt_text: str | None = None,
    prompt_lang: str | None = None,
    prompt_file: str | None = None,
) -> dict:
    project = load_project(data_dir, project_id)
    ref = project.setdefault("reference", {})
    if prompt_text is not None:
        ref["prompt_text"] = prompt_text.strip()
    if prompt_lang:
        ref["prompt_lang"] = prompt_lang.strip() or "zh"
    if prompt_file:
        name = Path(prompt_file).name
        if name not in ref.get("files", []):
            raise VoiceError("参考音频文件不存在")
        ref["prompt_file"] = name
    if not ref.get("prompt_file") and ref.get("files"):
        ref["prompt_file"] = ref["files"][0]
    save_project(data_dir, project)
    return {"project": project}


def set_reference_texts(data_dir: str | Path, project_id: str, texts: dict) -> dict:
    project = load_project(data_dir, project_id)
    files = project.get("reference", {}).get("files", [])
    cleaned: dict[str, str] = {}
    for name, text in (texts or {}).items():
        key = Path(str(name)).name
        if key not in files:
            continue
        value = str(text or "").strip()
        if value:
            cleaned[key] = value
    project.setdefault("reference", {})["texts"] = cleaned
    save_project(data_dir, project)
    return {"project": project}


def set_model(data_dir: str | Path, project_id: str, **fields) -> dict:
    project = load_project(data_dir, project_id)
    model = project.setdefault("model", {})
    for key, value in fields.items():
        if value is not None:
            model[key] = value
    save_project(data_dir, project)
    return {"project": project}


def set_rvc_model(data_dir: str | Path, project_id: str, **fields) -> dict:
    project = load_project(data_dir, project_id)
    record = project.setdefault("rvc", {})
    record.update({key: value for key, value in fields.items() if value is not None})
    record["updated_at"] = _now()
    save_project(data_dir, project)
    return {"project": project}


def training_dir(data_dir: str | Path, project_id: str) -> Path:
    return project_dir(data_dir, project_id) / "train"


def _copy_weight(source: str | Path, target_dir: Path, suffixes: set[str]) -> Path:
    path = Path(str(source or "")).expanduser()
    if not path.is_file():
        raise VoiceError(f"模型文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix not in suffixes:
        raise VoiceError(f"不支持的模型格式：{suffix or path.name}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise VoiceError(f"读不到模型文件：{path}") from exc
    if size <= 0:
        raise VoiceError(f"模型文件是空的：{path.name}")
    if size > 8 * 1024 * 1024 * 1024:
        raise VoiceError(f"模型文件超过 8 GB，已拒绝：{path.name}")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _safe_filename(path.name)
    if target.resolve() == path.resolve():
        return target
    if target.exists():
        target.unlink()
    shutil.copy2(path, target)
    return target


def reset_audio_lines(
    data_dir: str | Path,
    project_id: str,
    project: dict | None = None,
) -> dict:
    """切换模型后清掉旧音色生成的音频，避免新旧音色混在一起。"""
    project = project or load_project(data_dir, project_id)
    for line in project.get("lines", []):
        _drop_takes(data_dir, project_id, line)
        line_audio_path(data_dir, project_id, line["id"]).unlink(missing_ok=True)
        line["audio"] = ""
        line["audio_source"] = ""
        line["status"] = "draft"
        line["duration_sec"] = 0.0
        line["error"] = ""
    project.pop("export", None)
    save_project(data_dir, project)
    return project


def import_model(
    data_dir: str | Path,
    project_id: str,
    sovits_path: str | Path,
    gpt_path: str | Path,
    *,
    reset_audio: bool = True,
) -> dict:
    """导入外部训练好的 GPT-SoVITS 权重对（.pth + .ckpt）。"""
    project = load_project(data_dir, project_id)
    model_dir = training_dir(data_dir, project_id) / "model"
    sovits = _copy_weight(sovits_path, model_dir, {".pth"})
    gpt = _copy_weight(gpt_path, model_dir, {".ckpt", ".pth"})
    now = _now()
    project["model"] = {
        "mode": "fine_tuned",
        "state": "ready",
        "detail": "外部导入的 GPT-SoVITS 权重",
        "source": "external",
        "sovits_path": str(sovits),
        "gpt_path": str(gpt),
        "imported_at": now,
        "trained_at": now,
    }
    if reset_audio:
        reset_audio_lines(data_dir, project_id, project)
    else:
        save_project(data_dir, project)
    return {
        "project": project,
        "sovits_path": str(sovits),
        "gpt_path": str(gpt),
        "reset": bool(reset_audio),
    }


def import_rvc_model(
    data_dir: str | Path,
    project_id: str,
    model_path: str | Path,
    index_path: str | Path | None = None,
    *,
    reset_audio: bool = True,
) -> dict:
    """导入外部训练好的 RVC 换声模型（.pth，可选 .index）。"""
    project = load_project(data_dir, project_id)
    target_dir = training_dir(data_dir, project_id) / "rvc"
    model = _copy_weight(model_path, target_dir, {".pth"})
    index = ""
    if str(index_path or "").strip():
        index = str(_copy_weight(index_path, target_dir, {".index"}))
    now = _now()
    project["rvc"] = {
        **{
            key: value
            for key, value in (project.get("rvc") or {}).items()
            if key not in {"model", "index"}
        },
        "model": str(model),
        "index": index,
        "source": "external",
        "imported_at": now,
        "updated_at": now,
    }
    if reset_audio:
        reset_audio_lines(data_dir, project_id, project)
    else:
        save_project(data_dir, project)
    return {
        "project": project,
        "model": str(model),
        "index": index,
        "reset": bool(reset_audio),
    }


def set_export(data_dir: str | Path, project_id: str, **fields) -> dict:
    project = load_project(data_dir, project_id)
    record = {"created_at": _now()}
    record.update({key: value for key, value in fields.items() if value is not None})
    project["export"] = record
    history = project.setdefault("export_history", [])
    history.insert(0, record)
    del history[10:]
    save_project(data_dir, project)
    return {"project": project}


def add_line(
    data_dir: str | Path,
    project_id: str,
    text: str,
    event: str = "",
    bank: str = "",
    event_id: str = "",
    source: dict | None = None,
) -> dict:
    project = load_project(data_dir, project_id)
    content = (text or "").strip()
    if not content and not source:
        raise VoiceError("台词不能为空")
    source = source or {}
    source_id = str(source.get("event_id") or event_id or "").strip()
    if source_id:
        for existing in project.get("lines", []):
            if existing.get("source", {}).get("event_id") == source_id:
                raise VoiceError(f"事件已经在台词表里：{existing.get('event') or source_id}")
    assets = source.get("assets") if isinstance(source.get("assets"), list) else []
    primary = assets[0] if assets else {}
    path = str(primary.get("path") or source.get("path") or "").strip()
    line = {
        "id": "l_" + uuid.uuid4().hex[:12],
        "event": (event or source.get("event") or "").strip(),
        "event_id": source_id,
        "bank": (bank or source.get("bank") or "").strip(),
        "text": content,
        "status": "draft",
        "audio": "",
        "duration_sec": 0.0,
        "error": "",
        "source": {
            "event_id": source_id,
            "event": (event or source.get("event") or "").strip(),
            "bank": (bank or source.get("bank") or "").strip(),
            "path": path,
            "file": Path(path).name if path else "",
            "assets": assets,
        },
        "target_file": Path(path).name if path else "",
        "updated_at": _now(),
    }
    project.setdefault("lines", []).append(line)
    save_project(data_dir, project)
    return {"project": project, "line": line}


def add_lines_bulk(data_dir: str | Path, project_id: str, items: list[dict]) -> dict:
    project = load_project(data_dir, project_id)
    lines = project.setdefault("lines", [])
    added = 0
    for item in items[:500]:
        content = str(item.get("text", "")).strip()
        if not content:
            continue
        lines.append(
            {
                "id": "l_" + uuid.uuid4().hex[:12],
                "event": str(item.get("event", "")).strip(),
                "event_id": str(item.get("event_id", "")).strip(),
                "bank": str(item.get("bank", "")).strip(),
                "text": content,
                "status": "draft",
                "audio": "",
                "duration_sec": 0.0,
                "error": "",
                "updated_at": _now(),
            }
        )
        added += 1
    save_project(data_dir, project)
    return {"project": project, "added": added}


def add_localized_lines(
    data_dir: str | Path,
    project_id: str,
    bank: str,
    streams: list[dict],
    source_project: str = "",
) -> dict:
    """把本地化语音表的流批量加入台词表，按流名精确绑定源文件。"""
    project = load_project(data_dir, project_id)
    lines = project.setdefault("lines", [])
    existing = {
        str(line.get("source", {}).get("event_id") or "")
        for line in lines
    }
    added = 0
    skipped = 0
    for stream in streams[:2000]:
        event_id = str(stream.get("event_id") or "").strip()
        if not event_id or event_id in existing:
            skipped += 1
            continue
        assets = stream.get("assets") if isinstance(stream.get("assets"), list) else []
        primary = assets[0] if assets else {}
        path = str(primary.get("path") or "").strip()
        if not path:
            skipped += 1
            continue
        name = str(stream.get("event") or Path(path).stem).strip()
        lines.append(
            {
                "id": "l_" + uuid.uuid4().hex[:12],
                "event": name,
                "event_id": event_id,
                "bank": bank,
                "text": "",
                "status": "draft",
                "audio": "",
                "duration_sec": 0.0,
                "error": "",
                "source": {
                    "event_id": event_id,
                    "event": name,
                    "bank": bank,
                    "path": path,
                    "file": str(primary.get("file") or Path(path).name),
                    "assets": assets,
                    "language": stream.get("language", ""),
                    "scope": stream.get("scope", ""),
                    "label": stream.get("label", ""),
                    "group": stream.get("group", ""),
                    "kind": stream.get("kind", ""),
                    "note": stream.get("note", ""),
                    "event_names": stream.get("event_names") or [name],
                    "default_selected": stream.get("default_selected", True),
                    "localized": True,
                    "game_only": bool(stream.get("game_only")),
                },
                "target_file": str(stream.get("target_file") or Path(path).name),
                "updated_at": _now(),
            }
        )
        existing.add(event_id)
        added += 1
    save_project(data_dir, project)
    result = {"project": project, "added": added, "skipped": skipped}
    if source_project and source_project != project_id:
        copied = copy_matching_lines(data_dir, project_id, source_project)
        result.update(
            {
                "project": copied["project"],
                "matched": copied["matched"],
                "text_copied": copied["text_copied"],
                "audio_copied": copied["audio_copied"],
                "missing": copied["missing"],
            }
        )
    return result


def _line_key(line: dict) -> str:
    source = line.get("source") or {}
    for value in (
        line.get("target_file"),
        source.get("file"),
        line.get("event"),
    ):
        text = str(value or "").strip().lower()
        if not text:
            continue
        text = Path(text.replace("\\", "/")).name
        for suffix in AUDIO_EXTS:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
        if text:
            return text
    return ""


def copy_matching_lines(
    data_dir: str | Path,
    project_id: str,
    source_project_id: str,
) -> dict:
    """按同名流/目标文件名，把另一个中文项目的文本和已合成音频复制过来。"""
    target = load_project(data_dir, project_id)
    source = load_project(data_dir, source_project_id)
    source_map: dict[str, tuple[int, dict]] = {}
    for line in source.get("lines", []):
        key = _line_key(line)
        if not key:
            continue
        audio = line_audio_path(data_dir, source_project_id, line["id"]) if line.get("audio") else None
        rank = (1 if audio and audio.is_file() else 0, 1 if line.get("text") else 0)
        best = source_map.get(key)
        if best is None or rank > best[0]:
            source_map[key] = (rank, line)

    text_copied = 0
    audio_copied = 0
    matched = 0
    missing: list[str] = []
    changed = False
    for line in target.get("lines", []):
        key = _line_key(line)
        item = source_map.get(key) if key else None
        if not item:
            if not line.get("text"):
                missing.append(
                    str(line.get("event") or line.get("target_file") or line["id"])
                )
            continue
        source_line = item[1]
        matched += 1
        if not line.get("text") and source_line.get("text"):
            line["text"] = source_line["text"]
            line["status"] = "draft"
            line["updated_at"] = _now()
            text_copied += 1
            changed = True
        if not line.get("audio") and source_line.get("audio"):
            src_audio = line_audio_path(
                data_dir, source_project_id, source_line["id"]
            )
            dst_audio = line_audio_path(data_dir, project_id, line["id"])
            if src_audio.is_file():
                dst_audio.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_audio, dst_audio)
                source_tuning = source_line.get("tuning")
                if isinstance(source_tuning, dict) and source_tuning:
                    copied_tuning = dict(source_tuning)
                    src_raw = raw_audio_path(
                        data_dir, source_project_id, source_line["id"]
                    )
                    if src_raw.is_file():
                        dst_raw = raw_audio_path(data_dir, project_id, line["id"])
                        dst_raw.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_raw, dst_raw)
                    elif float(copied_tuning.get("gain_db") or 0):
                        # 没有原始备份时主音频已经带过音量，不能再次叠加。
                        copied_tuning.pop("gain_db", None)
                    line["tuning"] = copied_tuning
                line["audio"] = f"audio/{line['id']}.wav"
                line["status"] = "ready"
                line["audio_source"] = "project_copy"
                line["duration_sec"] = float(
                    source_line.get("duration_sec")
                    or wav_duration(dst_audio)
                    or 0.0
                )
                line["error"] = ""
                line["updated_at"] = _now()
                audio_copied += 1
                changed = True
        if not line.get("text"):
            missing.append(str(line.get("event") or line.get("target_file") or line["id"]))

    if changed:
        save_project(data_dir, target)
    return {
        "project": target,
        "matched": matched,
        "text_copied": text_copied,
        "audio_copied": audio_copied,
        "missing": missing,
    }


def update_line(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    **changes,
) -> dict:
    project = load_project(data_dir, project_id)
    for line in project.get("lines", []):
        if line.get("id") != line_id:
            continue
        for key in ("text", "event", "bank"):
            if isinstance(changes.get(key), str):
                line[key] = changes[key].strip()
        if isinstance(changes.get("text"), str):
            line["status"] = "draft"
            line["error"] = ""
            line["audio"] = ""
            line["duration_sec"] = 0.0
            line.pop("audio_source", None)
            _drop_takes(data_dir, project_id, line)
        line["updated_at"] = _now()
        save_project(data_dir, project)
        return {"project": project, "line": line}
    raise VoiceError("台词不存在")


def delete_line(data_dir: str | Path, project_id: str, line_id: str) -> dict:
    project = load_project(data_dir, project_id)
    lines = project.get("lines", [])
    kept = [line for line in lines if line.get("id") != line_id]
    if len(kept) == len(lines):
        raise VoiceError("台词不存在")
    project["lines"] = kept
    save_project(data_dir, project)
    audio = project_dir(data_dir, project_id) / "audio" / f"{line_id}.wav"
    try:
        audio.unlink()
    except OSError:
        pass
    raw_audio_path(data_dir, project_id, line_id).unlink(missing_ok=True)
    shutil.rmtree(takes_dir(data_dir, project_id, line_id), ignore_errors=True)
    return {"project": project}


def line_audio_path(data_dir: str | Path, project_id: str, line_id: str) -> Path:
    if not re.fullmatch(r"l_[0-9a-f]{12}", line_id or ""):
        raise VoiceError("无效的台词编号")
    path = project_dir(data_dir, project_id) / "audio" / f"{line_id}.wav"
    return path


def takes_dir(data_dir: str | Path, project_id: str, line_id: str) -> Path:
    if not re.fullmatch(r"l_[0-9a-f]{12}", line_id or ""):
        raise VoiceError("无效的台词编号")
    return project_dir(data_dir, project_id) / "audio" / "takes" / line_id


def take_audio_path(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    take_id: str,
) -> Path:
    if not TAKE_ID_RE.fullmatch(take_id or ""):
        raise VoiceError("无效的版本编号")
    return takes_dir(data_dir, project_id, line_id) / f"{take_id}.wav"


def raw_audio_path(data_dir: str | Path, project_id: str, line_id: str) -> Path:
    """没有候选版本时（换声/旧项目）保留一份未加音量的原始音频。"""
    if not re.fullmatch(r"l_[0-9a-f]{12}", line_id or ""):
        raise VoiceError("无效的台词编号")
    return project_dir(data_dir, project_id) / "audio" / RAW_DIR / f"{line_id}.wav"


def _clamp(value, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return max(low, min(high, number))


def line_tuning(line: dict) -> dict:
    raw = line.get("tuning") if isinstance(line.get("tuning"), dict) else {}
    reference = Path(str(raw.get("reference") or "")).name
    return {
        "reference": reference,
        "speed": round(_clamp(raw.get("speed", 1.0), SPEED_MIN, SPEED_MAX, 1.0), 2),
        "gain_db": round(_clamp(raw.get("gain_db", 0.0), GAIN_MIN_DB, GAIN_MAX_DB, 0.0), 1),
    }


def _tuning_storage(tuning: dict) -> dict:
    stored: dict = {}
    if tuning.get("reference"):
        stored["reference"] = tuning["reference"]
    if abs(float(tuning.get("speed", 1.0)) - 1.0) > 0.001:
        stored["speed"] = round(float(tuning["speed"]), 2)
    if abs(float(tuning.get("gain_db", 0.0))) > 0.001:
        stored["gain_db"] = round(float(tuning["gain_db"]), 1)
    return stored


def tuning_reference(
    project: dict,
    root: Path,
    line: dict,
    fallback: dict | None,
) -> dict | None:
    """优先用这条台词指定的参考语气，找不到时回到项目默认参考。"""
    tuning = line_tuning(line)
    name = tuning["reference"]
    ref = project.get("reference") or {}
    files = ref.get("files") or []
    if name and name in files:
        path = Path(root) / "reference" / name
        if path.is_file():
            texts = ref.get("texts") if isinstance(ref.get("texts"), dict) else {}
            prompt = str(texts.get(name) or ref.get("prompt_text") or "").strip()
            return {"name": name, "path": path, "prompt_text": prompt}
    return fallback


def _pcm_scale(frames: bytes, width: int, gain_db: float) -> tuple[bytes, float]:
    """按整数 PCM 缩放音量；正增益遇到峰值时自动收窄，避免削波。"""
    if not frames or abs(gain_db) < 0.001:
        return frames, 0.0
    factor = 10 ** (gain_db / 20.0)
    if width == 1:
        values = [sample - 128 for sample in frames]
        peak, full = (max(abs(value) for value in values), 127) if values else (0, 127)
    elif width == 2:
        samples = array.array("h")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        values = list(samples)
        peak, full = (max((abs(value) for value in values), default=0), 32767)
    elif width == 3:
        values = [
            int.from_bytes(frames[index : index + 3], "little", signed=True)
            for index in range(0, len(frames) - 2, 3)
        ]
        peak, full = (max((abs(value) for value in values), default=0), 8388607)
    elif width == 4:
        samples = array.array("i")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        values = list(samples)
        peak, full = (max((abs(value) for value in values), default=0), 2147483647)
    else:
        raise VoiceError("音量调整只支持 8/16/24/32 位整数 PCM WAV")
    actual = gain_db
    if factor > 1.0 and peak:
        limit = full / peak
        if factor > limit:
            factor = limit
            actual = 20 * math.log10(max(1e-6, factor))
    if width == 1:
        adjusted = bytes(max(0, min(255, int(value * factor) + 128)) for value in values)
    elif width == 2:
        output = array.array("h", (max(-32768, min(32767, int(value * factor))) for value in values))
        if sys.byteorder != "little":
            output.byteswap()
        adjusted = output.tobytes()
    elif width == 3:
        buffer = bytearray()
        for value in values:
            scaled = max(-8388608, min(8388607, int(value * factor)))
            buffer.extend(int(scaled).to_bytes(3, "little", signed=True))
        adjusted = bytes(buffer)
    else:
        output = array.array("i", (max(-2147483648, min(2147483647, int(value * factor))) for value in values))
        if sys.byteorder != "little":
            output.byteswap()
        adjusted = output.tobytes()
    return adjusted, round(actual, 1)


def render_gain(source: Path, target: Path, gain_db: float) -> float:
    """把 source 按 dB 缩放后写到 target，返回实际增益（可能因防削波变小）。"""
    source = Path(source)
    target = Path(target)
    with wave.open(str(source), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())
    if params.comptype != "NONE":
        raise VoiceError("音量调整只支持未压缩的 PCM WAV")
    adjusted, actual = _pcm_scale(frames, params.sampwidth, float(gain_db or 0.0))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with wave.open(str(temporary), "wb") as handle:
            handle.setparams(params)
            handle.writeframes(adjusted)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return actual


def apply_gain_to_file(source: Path, gain_db: float) -> float:
    """就地调整一个 WAV 的音量（试听缓存等临时文件用）。"""
    source = Path(source)
    return render_gain(source, source, gain_db)


def _selected_take_path(
    data_dir: str | Path,
    project_id: str,
    line: dict,
) -> Path | None:
    take_id = str(line.get("selected_take") or "")
    if not take_id:
        return None
    try:
        path = take_audio_path(data_dir, project_id, line["id"], take_id)
    except VoiceError:
        return None
    return path if path.is_file() else None


def apply_line_gain(
    data_dir: str | Path,
    project_id: str,
    project: dict,
    line: dict,
) -> float:
    """把台词的音量设置渲染到主音频；有候选版本时从候选重新渲染。"""
    tuning = line_tuning(line)
    gain = float(tuning["gain_db"])
    main = line_audio_path(data_dir, project_id, line["id"])
    raw = raw_audio_path(data_dir, project_id, line["id"])
    take = _selected_take_path(data_dir, project_id, line)
    source: Path | None = None
    if take:
        raw.unlink(missing_ok=True)
        source = take
    elif main.is_file():
        if not raw.is_file():
            raw.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(main, raw)
        source = raw
    if not source:
        return 0.0
    if abs(gain) > 0.001:
        actual = render_gain(source, main, gain)
    else:
        actual = 0.0
        # 音量回到 0 时从原始候选/备份恢复，避免在已加音量的文件上反复处理。
        temporary = main.with_name(f".{main.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, main)
        finally:
            temporary.unlink(missing_ok=True)
    line["duration_sec"] = round(wav_duration(main), 3)
    line["gain_actual_db"] = actual
    return actual


def set_line_tuning(
    data_dir: str | Path,
    project_id: str,
    line_ids: list[str],
    *,
    reference: str | None = None,
    speed: float | None = None,
    gain_db: float | None = None,
) -> dict:
    """批量保存台词的语气/语速/音量。

    只改音量时立刻从原始音频重渲染；改参考或语速会让旧音频失效，
    需要重新生成才能保持一致。
    """
    project = load_project(data_dir, project_id)
    wanted = [str(item) for item in line_ids if str(item)]
    if not wanted:
        raise VoiceError("先选择要调整的台词")
    files = project.get("reference", {}).get("files") or []
    reference_name: str | None = None
    if reference is not None:
        reference_name = Path(str(reference)).name
        if reference_name and reference_name not in files:
            raise VoiceError("参考语气不在这个项目的参考素材里")
    updated = 0
    needs_synthesis = 0
    capped = 0
    for line in project.get("lines", []):
        if line.get("id") not in wanted:
            continue
        if line.get("text_source") == "non_voice":
            continue
        current = line_tuning(line)
        target = dict(current)
        if reference is not None:
            target["reference"] = reference_name or ""
        if speed is not None:
            target["speed"] = round(_clamp(speed, SPEED_MIN, SPEED_MAX, current["speed"]), 2)
        if gain_db is not None:
            target["gain_db"] = round(
                _clamp(gain_db, GAIN_MIN_DB, GAIN_MAX_DB, current["gain_db"]), 1
            )
        tone_changed = (
            target["reference"] != current["reference"]
            or abs(target["speed"] - current["speed"]) > 0.001
        )
        gain_changed = abs(target["gain_db"] - current["gain_db"]) > 0.001
        had_audio = bool(line.get("audio") or line.get("takes"))
        line["tuning"] = _tuning_storage(target)
        line["updated_at"] = _now()
        updated += 1
        if tone_changed:
            _drop_takes(data_dir, project_id, line)
            audio = line_audio_path(data_dir, project_id, line["id"])
            audio.unlink(missing_ok=True)
            raw_audio_path(data_dir, project_id, line["id"]).unlink(missing_ok=True)
            line["audio"] = ""
            line["status"] = "draft"
            line["duration_sec"] = 0.0
            line["error"] = ""
            line.pop("audio_source", None)
            if had_audio:
                needs_synthesis += 1
        elif gain_changed and line.get("audio"):
            actual = apply_line_gain(data_dir, project_id, project, line)
            if abs(actual - target["gain_db"]) > 0.15:
                capped += 1
    if not updated:
        raise VoiceError("台词不存在")
    if needs_synthesis or updated:
        # 台词音频或音量已经变化，旧安装包不再代表当前项目。
        project.pop("export", None)
    save_project(data_dir, project)
    return {
        "project": project,
        "updated": updated,
        "needs_synthesis": needs_synthesis,
        "capped": capped,
    }


def _find_take(line: dict, take_id: str) -> dict:
    for take in line.get("takes", []) or []:
        if take.get("id") == take_id:
            return take
    raise VoiceError("这条台词没有这个版本")


def find_take(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    take_id: str,
) -> Path:
    """按版本编号定位音频；同时确认该版本确实属于这条台词。"""
    project = load_project(data_dir, project_id)
    line = next((item for item in project.get("lines", []) if item.get("id") == line_id), None)
    if line is None:
        raise VoiceError("台词不存在")
    _find_take(line, take_id)
    path = take_audio_path(data_dir, project_id, line_id, take_id)
    if not path.is_file():
        raise VoiceError("这个版本的音频不存在")
    return path


def _drop_takes(data_dir: str | Path, project_id: str, line: dict) -> None:
    line.pop("takes", None)
    line.pop("selected_take", None)
    shutil.rmtree(takes_dir(data_dir, project_id, line["id"]), ignore_errors=True)
    raw_audio_path(data_dir, project_id, line["id"]).unlink(missing_ok=True)


def clear_all_takes(
    data_dir: str | Path,
    project_id: str,
    line_ids: list[str] | None = None,
) -> dict:
    """换模型或改词后旧版本不再可信，统一清掉。"""
    project = load_project(data_dir, project_id)
    wanted = set(line_ids or [])
    changed = False
    for line in project.get("lines", []):
        if wanted and line.get("id") not in wanted:
            continue
        if line.get("takes") or line.get("selected_take"):
            changed = True
        _drop_takes(data_dir, project_id, line)
    if changed:
        save_project(data_dir, project)
    return project


def _apply_take(
    data_dir: str | Path,
    project_id: str,
    project: dict,
    line: dict,
    take_id: str,
    *,
    persist: bool = True,
) -> dict:
    take = _find_take(line, take_id)
    source = take_audio_path(data_dir, project_id, line["id"], take_id)
    if not source.is_file():
        raise VoiceError("这个版本的音频不存在")
    destination = line_audio_path(data_dir, project_id, line["id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    gain = float(line_tuning(line)["gain_db"])
    actual = render_gain(source, destination, gain) if abs(gain) > 0.001 else 0.0
    if abs(gain) <= 0.001:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    raw_audio_path(data_dir, project_id, line["id"]).unlink(missing_ok=True)
    line["audio"] = f"audio/{line['id']}.wav"
    line["duration_sec"] = round(float(wav_duration(destination) or take.get("duration_sec") or 0.0), 3)
    line["status"] = "ready"
    line["error"] = ""
    line["audio_source"] = "synthesized"
    line["selected_take"] = take_id
    line["gain_actual_db"] = actual
    line["updated_at"] = _now()
    if persist:
        save_project(data_dir, project)
    return {"project": project, "line": line, "take": take}


def save_takes(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    takes: list[dict],
    selected_id: str = "",
) -> dict:
    """保存一次合成的全部候选，并把默认版本复制成主音频。"""
    project = load_project(data_dir, project_id)
    line = next((item for item in project.get("lines", []) if item.get("id") == line_id), None)
    if line is None:
        raise VoiceError("台词不存在")
    folder = takes_dir(data_dir, project_id, line_id)
    clean: list[dict] = []
    for item in takes or []:
        take_id = str(item.get("id") or "")
        if not TAKE_ID_RE.fullmatch(take_id):
            continue
        path = folder / f"{take_id}.wav"
        if not path.is_file():
            continue
        record = {
            "id": take_id,
            "duration_sec": round(
                float(item.get("duration_sec") or wav_duration(path) or 0.0), 3
            ),
            "seed": int(item.get("seed") or 0),
            "created_at": str(item.get("created_at") or _now()),
        }
        ref_file = Path(str(item.get("ref_file") or "")).name
        if ref_file:
            record["ref_file"] = ref_file
        clean.append(record)
    if not clean:
        raise VoiceError("没有可用的合成版本")
    clean = clean[:MAX_TAKES]
    keep = {item["id"] for item in clean}
    if folder.is_dir():
        for old in folder.glob("*.wav"):
            if old.stem not in keep:
                old.unlink(missing_ok=True)
    line["takes"] = clean
    chosen = selected_id if selected_id in keep else clean[0]["id"]
    return _apply_take(data_dir, project_id, project, line, chosen)


def select_take(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    take_id: str,
) -> dict:
    project = load_project(data_dir, project_id)
    line = next((item for item in project.get("lines", []) if item.get("id") == line_id), None)
    if line is None:
        raise VoiceError("台词不存在")
    if str(take_id or "") not in {item.get("id") for item in line.get("takes", []) or []}:
        raise VoiceError("这条台词没有这个版本")
    return _apply_take(data_dir, project_id, project, line, str(take_id))


def set_line_error(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    error: str,
) -> dict:
    """合成失败时保留旧音频和候选，只记录错误。"""
    project = load_project(data_dir, project_id)
    for line in project.get("lines", []):
        if line.get("id") == line_id:
            line["status"] = "error"
            line["error"] = str(error or "")[:500]
            line["updated_at"] = _now()
            save_project(data_dir, project)
            return line
    raise VoiceError("台词不存在")


def mark_line(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    *,
    status: str,
    audio: str = "",
    duration_sec: float = 0.0,
    error: str = "",
) -> dict:
    project = load_project(data_dir, project_id)
    for line in project.get("lines", []):
        if line.get("id") == line_id:
            line["status"] = status
            line["audio"] = audio
            line["duration_sec"] = round(float(duration_sec or 0.0), 3)
            line["error"] = error
            if status == "ready" and audio:
                line["audio_source"] = "synthesized"
            elif not audio:
                line.pop("audio_source", None)
            line["updated_at"] = _now()
            save_project(data_dir, project)
            return line
    raise VoiceError("台词不存在")


def export_rows(project: dict) -> list[dict]:
    rows = []
    for line in project.get("lines", []):
        source = line.get("source", {})
        rows.append(
            {
                "event_id": line.get("event_id", ""),
                "event": line.get("event", ""),
                "bank": line.get("bank", ""),
                "source_file": source.get("file", ""),
                "source_path": source.get("path", ""),
                "text": line.get("text", ""),
                "status": line.get("status", ""),
            }
        )
    return rows


def import_texts(data_dir: str | Path, project_id: str, rows: list[dict]) -> dict:
    """按 event_id 精确更新台词；缺少 event_id 时只接受唯一的 source_file。"""
    project = load_project(data_dir, project_id)
    lines = project.get("lines", [])
    by_event = {line.get("event_id"): line for line in lines if line.get("event_id")}
    by_file: dict[str, list[dict]] = {}
    for line in lines:
        name = line.get("source", {}).get("file", "")
        if name:
            by_file.setdefault(name, []).append(line)

    matched = 0
    skipped = 0
    errors: list[str] = []
    for index, row in enumerate(rows, 2):
        text = str(row.get("text", "")).strip()
        event_id = str(row.get("event_id", "")).strip()
        source_file = str(row.get("source_file", "")).strip()
        target = by_event.get(event_id) if event_id else None
        if target is None and source_file:
            candidates = by_file.get(source_file, [])
            if len(candidates) == 1:
                target = candidates[0]
            elif len(candidates) > 1:
                errors.append(f"第 {index} 行：文件名 {source_file} 对应多个事件，请填 event_id")
                skipped += 1
                continue
        if target is None:
            errors.append(f"第 {index} 行：找不到对应事件")
            skipped += 1
            continue
        expected_file = target.get("source", {}).get("file", "")
        if source_file and expected_file and source_file != expected_file:
            errors.append(f"第 {index} 行：文件名不匹配（应为 {expected_file}）")
            skipped += 1
            continue
        if not text:
            skipped += 1
            continue
        target["text"] = text
        target["status"] = "draft"
        target["error"] = ""
        target["audio"] = ""
        target["duration_sec"] = 0.0
        target.pop("audio_source", None)
        _drop_takes(data_dir, project_id, target)
        target["updated_at"] = _now()
        matched += 1
    save_project(data_dir, project)
    return {"project": project, "matched": matched, "skipped": skipped, "errors": errors[:20]}


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            return round(handle.getnframes() / float(handle.getframerate()), 3)
    except (wave.Error, OSError):
        return 0.0


def reference_path(project: dict, root: Path) -> Path | None:
    name = project.get("reference", {}).get("prompt_file", "")
    if not name:
        files = project.get("reference", {}).get("files", [])
        name = files[0] if files else ""
    if not name:
        return None
    path = root / "reference" / name
    return path if path.is_file() else None


def reference_choices(project: dict, root: Path, count: int) -> list[dict]:
    """为多版本合成挑选参考音频。

    第一版沿用项目当前的参考音频，其余版本在“有文字”的参考里均匀取样，
    让候选在语气/停顿上出现可听的差别；只有一段参考时重复使用同一段。
    """
    ref = project.get("reference") or {}
    files: list[str] = []
    for item in ref.get("files", []) or []:
        name = Path(str(item)).name
        if name and name not in files and (root / "reference" / name).is_file():
            files.append(name)
    if not files:
        return []
    texts = ref.get("texts") if isinstance(ref.get("texts"), dict) else {}
    fallback = str(ref.get("prompt_text") or "").strip()
    base = Path(str(ref.get("prompt_file") or "")).name
    if base not in files:
        base = files[0]
    with_text = [name for name in files if str(texts.get(name) or "").strip()]
    pool = with_text or files
    start = pool.index(base) if base in pool else 0
    wanted = max(1, min(MAX_TAKES, int(count or 1)))
    step = max(1, int(round(len(pool) / wanted)))
    choices: list[dict] = []
    for index in range(wanted):
        name = base if index == 0 else pool[(start + index * step) % len(pool)]
        choices.append(
            {
                "name": name,
                "path": root / "reference" / name,
                "prompt_text": str(texts.get(name) or fallback).strip(),
            }
        )
    return choices
