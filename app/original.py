"""原语言换声：保留游戏原台词，只换音色。

流程：
1. 从游戏 bank / 官方工程取出每句的原音（只取主变体）；
2. 调 RVC 批量换声；
3. 把结果写回台词的 audio 字段，后续仍走普通 FMOD 构建。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app import cockpit, dynamic_bank, jobs, localized, voice
from app.engines import rvc


class OriginalError(RuntimeError):
    pass


def _primary_asset(line: dict) -> dict | None:
    assets = (line.get("source") or {}).get("assets") or []
    for asset in assets:
        if isinstance(asset, dict) and asset.get("path"):
            return asset
    source = line.get("source") or {}
    if source.get("path"):
        return {"path": source["path"], "file": source.get("file", "")}
    return None


def _line_name(line: dict) -> str:
    source = line.get("source") or {}
    return str(source.get("event") or line.get("event") or "").strip()


def _collect_inputs(
    job,
    project: dict,
    kit: Path,
    game_path: Path | None,
    input_dir: Path,
) -> dict[str, Path]:
    lines = [
        line for line in project.get("lines", [])
        if line.get("text_source") != "non_voice" and _primary_asset(line)
    ]
    if not lines:
        raise OriginalError("没有需要换声的台词")

    input_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    by_bank: dict[str, list[tuple[dict, dict]]] = {}
    for line in lines:
        asset = _primary_asset(line)
        bank = str((line.get("source") or {}).get("bank") or line.get("bank") or "")
        if cockpit.is_cockpit_bank(bank):
            assets = (line.get("source") or {}).get("assets") or []
            if assets:
                asset = assets[cockpit.primary_asset_index(assets)]
                if str(asset.get("path") or "").startswith("vws/") and (
                    asset.get("game_index") is None or not asset.get("voice")
                ):
                    fresh = cockpit.source_index(
                        kit, game_path, _line_name(line), asset.get("path")
                    )
                    asset = {**asset, "game_index": fresh, "voice": "primary"}
        result[line["id"]] = input_dir / f"{line['id']}.wav"
        if asset.get("game_index") is not None and bank:
            by_bank.setdefault(bank, []).append((line, asset))
            continue
        source = kit / "Assets" / str(asset.get("path") or "")
        if not source.is_file() and asset.get("origin"):
            source = kit / "Assets" / str(asset["origin"])
        if not source.is_file():
            raise OriginalError(
                "找不到原音：%s（%s）" % (_line_name(line), asset.get("path"))
            )
        shutil.copy2(source, result[line["id"]])

    total = len(lines)
    done = sum(1 for path in result.values() if path.is_file())
    for bank, items in by_bank.items():
        jobs.check_cancel(job)
        game_bank = localized.game_bank_path(game_path, bank)
        if not game_bank:
            raise OriginalError(f"游戏里找不到语音表：{bank}")
        targets = {
            int(asset["game_index"]): result[line["id"]]
            for line, asset in items
        }
        job.update(
            message=f"解出原音：{dynamic_bank.label(bank)}（{len(targets)} 条）",
            percent=min(60, (done + len(targets)) / max(1, total) * 60),
        )
        try:
            localized.extract_indexes(game_bank, targets, temp_root=input_dir)
        except localized.LocalizedError as exc:
            raise OriginalError(str(exc)) from exc
        done += len(targets)
    return {line_id: path for line_id, path in result.items() if path.is_file()}


def convert_project(
    job,
    data_dir: str | Path,
    project_id: str,
    kit: str | Path,
    game_path: str | Path | None,
    *,
    root: str | Path | None = None,
) -> dict:
    data_path = Path(data_dir)
    project = voice.load_project(data_path, project_id)
    if project.get("quick", {}).get("mode") != "original":
        raise OriginalError("这个项目不是原语言换声项目")
    rvc_info = project.get("rvc") or {}
    model = str(rvc_info.get("model") or "")
    index = str(rvc_info.get("index") or "")
    if not model or not Path(model).is_file():
        raise OriginalError("还没有训练换声模型")
    project_root = voice.project_dir(data_path, project_id)
    work = project_root / "build" / "rvc"
    shutil.rmtree(work, ignore_errors=True)
    input_dir = work / "input"
    output_dir = work / "output"
    job.update(total=100, current=0, message="收集原音")
    inputs = _collect_inputs(
        job,
        project,
        Path(kit),
        Path(game_path) if game_path else None,
        input_dir,
    )
    try:
        result = rvc.convert(
            job,
            root=root,
            model=model,
            index=index,
            input_dir=input_dir,
            output_dir=output_dir,
            pitch=0,
            index_rate=0.75,
            protect=0.33,
            rms_mix_rate=1.0,
            f0_method="rmvpe",
        )
    except rvc.RvcError as exc:
        raise OriginalError(str(exc)) from exc

    errors: list[str] = []
    converted = 0
    audio_dir = project_root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    total = len(inputs)
    for position, (line_id, source) in enumerate(inputs.items(), 1):
        jobs.check_cancel(job)
        output = output_dir / f"{line_id}.wav"
        if not output.is_file():
            errors.append(f"{line_id}：换声没有输出")
            continue
        target = audio_dir / f"{line_id}.wav"
        if target.exists():
            target.unlink()
        voice.raw_audio_path(data_path, project_id, line_id).unlink(missing_ok=True)
        shutil.copy2(output, target)
        line = next((item for item in project.get("lines", []) if item.get("id") == line_id), None)
        if line is None:
            continue
        line["audio"] = f"audio/{line_id}.wav"
        line["status"] = "ready"
        line["error"] = ""
        try:
            line["duration_sec"] = voice.wav_duration(target)
        except OSError:
            line["duration_sec"] = 0.0
        line.pop("takes", None)
        line.pop("selected_take", None)
        if abs(float(voice.line_tuning(line)["gain_db"])) > 0.001:
            voice.apply_line_gain(data_path, project_id, project, line)
        converted += 1
        job.update(
            total=total,
            current=position,
            message=f"写回换声结果 {position}/{total}",
            percent=95 + position / max(1, total) * 5,
        )
    project["quick"]["converted_count"] = converted
    project["quick"]["convert_errors"] = errors[:100]
    voice.save_project(data_path, project)
    return {
        "project": project,
        "converted": converted,
        "total": total,
        "errors": errors[:100],
        "rvc": result,
    }
