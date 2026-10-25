"""参考素材与台词音频的用户操作：追加、删除、整理静音/切片、导入成品音频。"""
from __future__ import annotations

import shutil
import uuid
import wave
from datetime import datetime
from pathlib import Path

from app import refaudio, voice


def add_references(
    data_dir: str | Path,
    project_id: str,
    paths: list[str],
    *,
    prompt_lang: str = "zh",
) -> dict:
    """把单个/多个音频追加到现有参考素材，不覆盖已导入的文件。"""
    project = voice.load_project(data_dir, project_id)
    folder = voice.project_dir(data_dir, project_id) / "reference"
    folder.mkdir(parents=True, exist_ok=True)
    existing = list(project.get("reference", {}).get("files") or [])
    total = sum(
        (folder / name).stat().st_size
        for name in existing
        if (folder / name).is_file()
    )
    added: list[str] = []
    index = len(existing) + 1
    for raw in paths or []:
        root = Path(str(raw)).expanduser()
        if root.is_dir():
            sources = [
                item
                for item in sorted(root.rglob("*"))
                if item.is_file() and item.suffix.lower() in voice.AUDIO_EXTS
            ]
        elif root.is_file() and root.suffix.lower() in voice.AUDIO_EXTS:
            sources = [root]
        else:
            continue
        for source in sources:
            if len(existing) + len(added) >= voice.MAX_REFERENCE_FILES:
                break
            size = source.stat().st_size
            if total + size > voice.MAX_REFERENCE_BYTES:
                break
            name = voice._safe_filename(source.name)
            dest = folder / f"{index:03d}_{name}"
            while dest.exists():
                index += 1
                dest = folder / f"{index:03d}_{name}"
            shutil.copy2(source, dest)
            added.append(dest.name)
            total += size
            index += 1
    if not added:
        raise voice.VoiceError("没有可导入的音频（支持 wav/mp3/flac/m4a/ogg）")
    files = existing + added
    project["reference"]["files"] = files[: voice.MAX_REFERENCE_FILES]
    if not project["reference"].get("prompt_file"):
        project["reference"]["prompt_file"] = added[0]
    project["reference"]["prompt_lang"] = (
        prompt_lang or project["reference"].get("prompt_lang") or "zh"
    )
    project["model"]["state"] = "not_ready"
    project["model"]["detail"] = "参考音频已更新，等待重新训练或试听"
    voice.save_project(data_dir, project)
    return {"project": project, "added": added, "count": len(added)}


def remove_reference(data_dir: str | Path, project_id: str, name: str) -> dict:
    project = voice.load_project(data_dir, project_id)
    name = Path(str(name or "")).name
    files = list(project.get("reference", {}).get("files") or [])
    if name not in files:
        raise voice.VoiceError("参考素材里没有这个文件")
    folder = voice.project_dir(data_dir, project_id) / "reference"
    (folder / name).unlink(missing_ok=True)
    files.remove(name)
    texts = project["reference"].get("texts")
    if isinstance(texts, dict):
        texts.pop(name, None)
    project["reference"]["files"] = files
    if project["reference"].get("prompt_file") == name:
        project["reference"]["prompt_file"] = files[0] if files else ""
    project["model"]["state"] = "not_ready"
    project["model"]["detail"] = "参考音频已更新，等待重新训练或试听"
    voice.save_project(data_dir, project)
    return {"project": project, "removed": name}


def process_references(
    data_dir: str | Path,
    project_id: str,
    *,
    trim: bool = True,
    split: bool = False,
    max_seconds: float = 30.0,
    threshold_db: float = -40.0,
) -> dict:
    """裁掉首尾静音、按需切片；原始文件移到 reference/_原始/ 保留。"""
    if not trim and not split:
        raise voice.VoiceError("没有选择要执行的整理项")
    project = voice.load_project(data_dir, project_id)
    folder = voice.project_dir(data_dir, project_id) / "reference"
    files = list(project.get("reference", {}).get("files") or [])
    texts = (
        project["reference"].get("texts")
        if isinstance(project["reference"].get("texts"), dict)
        else {}
    )
    originals = folder / "_原始"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_files: list[str] = []
    new_texts: dict[str, str] = {}
    outputs_by_name: dict[str, list[str]] = {}
    summary = {
        "trimmed": 0,
        "split": 0,
        "skipped": 0,
        "before_sec": 0.0,
        "after_sec": 0.0,
        "warnings": [],
    }
    for index, name in enumerate(files, 1):
        source = folder / Path(name).name
        if not source.is_file():
            continue
        if source.suffix.lower() != ".wav":
            new_files.append(source.name)
            if texts.get(name):
                new_texts[source.name] = texts[name]
            summary["skipped"] += 1
            summary["warnings"].append(f"{source.name}：不是 WAV，原样保留")
            continue
        stem = Path(name).stem[:40] or f"ref{index}"
        before = voice.wav_duration(source)
        summary["before_sec"] += before
        try:
            if split and before > max_seconds:
                parts = refaudio.split_at_silence(
                    source, folder, f"{index:03d}_{stem}", max_seconds=max_seconds
                )
                outputs: list[Path] = []
                for part in parts:
                    final = folder / f"{part.stem}_clean.wav"
                    info = refaudio.trim_silence(part, final, threshold_db=threshold_db)
                    part.unlink(missing_ok=True)
                    outputs.append(final)
                    summary["after_sec"] += info["duration_after"]
                summary["split"] += 1
            elif trim:
                final = folder / f"{index:03d}_{stem}_clean.wav"
                info = refaudio.trim_silence(source, final, threshold_db=threshold_db)
                outputs = [final]
                summary["trimmed"] += 1
                summary["after_sec"] += info["duration_after"]
                if texts.get(name):
                    new_texts[final.name] = texts[name]
            else:
                new_files.append(source.name)
                summary["after_sec"] += before
                if texts.get(name):
                    new_texts[source.name] = texts[name]
                continue
        except (OSError, wave.Error, refaudio.RefAudioError) as exc:
            new_files.append(source.name)
            summary["skipped"] += 1
            summary["warnings"].append(f"{source.name}：处理失败，保留原文件（{exc}）")
            if texts.get(name):
                new_texts[source.name] = texts[name]
            continue
        originals.mkdir(parents=True, exist_ok=True)
        target = originals / source.name
        if target.exists():
            target = originals / (
                f"{Path(source.name).stem}_{stamp}{Path(source.name).suffix}"
            )
        shutil.move(str(source), str(target))
        for output in outputs:
            new_files.append(output.name)
        outputs_by_name[name] = [output.name for output in outputs]

    if not new_files:
        raise voice.VoiceError("没有可用的参考素材")
    prompt_file = project["reference"].get("prompt_file") or ""
    prompt_outputs = outputs_by_name.get(prompt_file) or []
    split_prompt = bool(prompt_outputs and len(prompt_outputs) > 1)
    project["reference"]["files"] = new_files[: voice.MAX_REFERENCE_FILES]
    project["reference"]["texts"] = {
        name: text for name, text in new_texts.items() if name in new_files
    }
    project["reference"]["prompt_file"] = (
        prompt_outputs[0] if prompt_outputs else new_files[0]
    )
    if split_prompt:
        project["reference"]["prompt_text"] = ""
        summary["warnings"].append("参考素材被切片，请重新填写参考文字或自动识别")
    project["model"]["state"] = "not_ready"
    project["model"]["detail"] = "参考素材已整理，等待重新训练或试听"
    voice.save_project(data_dir, project)
    summary["before_sec"] = round(summary["before_sec"], 2)
    summary["after_sec"] = round(summary["after_sec"], 2)
    return {"project": project, "summary": summary}


def import_take(
    data_dir: str | Path,
    project_id: str,
    line_id: str,
    source: str | Path,
) -> dict:
    """把用户现成的 WAV 作为这条台词的一个版本导入。"""
    source_path = Path(str(source or "")).expanduser()
    if not source_path.is_file():
        raise voice.VoiceError("音频文件不存在")
    if source_path.suffix.lower() != ".wav":
        raise voice.VoiceError("导入成品音频目前只支持 WAV")
    project = voice.load_project(data_dir, project_id)
    line = next(
        (item for item in project.get("lines", []) if item.get("id") == line_id), None
    )
    if line is None:
        raise voice.VoiceError("台词不存在")
    takes = list(line.get("takes") or [])
    if len(takes) >= voice.MAX_TAKES:
        raise voice.VoiceError(
            f"这条台词已有 {voice.MAX_TAKES} 个版本，先删掉一个再导入"
        )
    take_id = "t_" + uuid.uuid4().hex[:12]
    dest = voice.take_audio_path(data_dir, project_id, line_id, take_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest)
    duration = voice.wav_duration(dest)
    if duration <= 0:
        dest.unlink(missing_ok=True)
        raise voice.VoiceError("无法读取这个 WAV 文件")
    takes.append(
        {
            "id": take_id,
            "duration_sec": duration,
            "seed": 0,
            "created_at": voice._now(),
            "imported": True,
        }
    )
    line["takes"] = takes
    result = voice._apply_take(data_dir, project_id, project, line, take_id)
    result["line"]["audio_source"] = "imported"
    voice.save_project(data_dir, result["project"])
    return result