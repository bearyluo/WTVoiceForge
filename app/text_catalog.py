"""本机生成“官方中文原音”的听写初稿。

快速制作需要给上千条目标语言台词填中文文本，但官方中文语音表只有音频。
这里用 vgmstream 解出官方中文原音，再调用本机 GPT-SoVITS 整合包里的
FunASR 做批量听写。结果只缓存在用户数据目录，不随应用分发。

首次生成通常需要几分钟；之后直接复用缓存。所有处理都在本机完成。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import jobs, localized, vgmstream
from app.engines import gpt_sovits

SCOPES = ("common", "ground")
CACHE_REL = Path("cache") / "quick" / "chinese_text_catalog.json"


class TextCatalogError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cache_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / CACHE_REL


def load(data_dir: str | Path) -> dict:
    path = cache_path(data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("scopes"), dict):
        return {}
    return data


def status(data_dir: str | Path) -> dict:
    catalog = load(data_dir)
    scopes = {
        scope: len(values or {})
        for scope, values in (catalog.get("scopes") or {}).items()
        if isinstance(values, dict)
    }
    return {
        "ready": bool(scopes.get("common") or scopes.get("ground")),
        "generated_at": str(catalog.get("generated_at") or ""),
        "engine": str(catalog.get("engine") or ""),
        "scopes": scopes,
    }


def _update(job, **kwargs) -> None:
    if job is not None:
        job.update(**kwargs)


def _step(job, message: str = "") -> None:
    if job is not None:
        jobs.check_cancel(job)
        job.step(message or None)


def _unique_streams(
    bank_path: Path,
    out_dir: Path,
    temp_root: Path,
    *,
    prefix: str = "",
    skip_names: set[str] | None = None,
) -> dict[str, str]:
    """每个流名只解出第一条，避免同一条台词解四遍。"""
    rows = localized.game_streams(bank_path)
    if not rows:
        raise TextCatalogError(f"读不到官方中文语音表：{bank_path.name}")
    skip = skip_names or set()
    targets: dict[int, Path] = {}
    names: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        index = row.get("index")
        if not name or index is None or name in skip or name in seen:
            continue
        seen.add(name)
        filename = f"{prefix}{int(index):04d}.wav"
        targets[int(index)] = out_dir / filename
        names[filename] = name
    if not targets:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    localized.extract_indexes(bank_path, targets, temp_root=temp_root)
    return names


def _convert(raw_dir: Path, asr_dir: Path, job=None) -> None:
    files = sorted(raw_dir.glob("*.wav"))
    if not files:
        raise TextCatalogError(f"没有解出音频：{raw_dir.name}")
    root = gpt_sovits.find_install()
    if not root:
        raise TextCatalogError("没有找到 GPT-SoVITS 整合包，无法听写官方中文原音")
    ffmpeg = Path(root) / "ffmpeg.exe"
    asr_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(files, 1):
        if job is not None:
            jobs.check_cancel(job)
        target = asr_dir / source.name
        if target.is_file():
            continue
        if ffmpeg.is_file():
            completed = subprocess.run(
                [
                    str(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(target),
                ],
                capture_output=True,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0 or not target.is_file():
                detail = (completed.stderr or b"").decode("utf-8", errors="replace")[-200:]
                raise TextCatalogError(f"转码失败：{source.name} {detail}")
        else:
            try:
                gpt_sovits._to_16k_mono(source, target)
            except Exception as exc:  # noqa: BLE001 - 统一成可读错误
                raise TextCatalogError(f"转码失败：{source.name} {exc}") from exc
        if index % 50 == 0:
            _update(job, message=f"转换官方中文原音 {index}/{len(files)}")
    _update(job, message=f"已转换 {len(files)} 条官方中文原音")


def _transcribe(folder: Path, names: dict[str, str], job=None) -> dict[str, str]:
    if not names:
        return {}
    _update(job, message="正在听写官方中文原音（首次约 3-10 分钟，请勿关闭）")
    try:
        recognized = gpt_sovits.transcribe_folder(folder, "zh", timeout=7200)
    except gpt_sovits.GptSoVitsError as exc:
        raise TextCatalogError(f"官方中文原音听写失败：{exc}") from exc
    result: dict[str, str] = {}
    for filename, name in names.items():
        text = str(recognized.get(filename) or "").strip()
        if text and name not in result:
            result[name] = text
    return result


def build(
    job,
    data_dir: str | Path,
    game_path: str | Path | None,
    *,
    force: bool = False,
) -> dict:
    """生成并缓存听写初稿；已有可用缓存时直接返回。"""
    data_path = Path(data_dir)
    cached = load(data_path)
    scopes_cached = cached.get("scopes") or {}
    if not force and scopes_cached.get("common") and scopes_cached.get("ground"):
        _update(
            job,
            message="中文原音听写缓存已存在，直接复用",
            total=1,
            current=1,
        )
        return cached
    if not game_path:
        raise TextCatalogError(
            "首次制作需要先在工作台选择《战争雷霆》游戏目录，用来听写官方中文原音"
        )
    game = Path(game_path)
    if not (game / "sound").is_dir():
        raise TextCatalogError("游戏目录里没有 sound 文件夹，请重新选择游戏目录")
    if not vgmstream.find():
        raise TextCatalogError("没有检测到 vgmstream，无法解出官方中文原音")
    if not gpt_sovits.find_install():
        raise TextCatalogError("没有找到 GPT-SoVITS 整合包，无法听写官方中文原音")

    common_bank = localized.game_bank_path(game, "_crew_dialogs_common_zh")
    ground_bank = localized.game_bank_path(game, "_crew_dialogs_ground_zh")
    if not common_bank or not ground_bank:
        raise TextCatalogError(
            "游戏里缺少官方中文语音表（common_zh / ground_zh），无法生成文本初稿"
        )
    supplement = localized.game_bank_path(game, "_crew_dialogs_ground_sm_zh")

    target = cache_path(data_path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp = Path(
        tempfile.mkdtemp(
            prefix=f"catalog-build-{uuid.uuid4().hex[:6]}-",
            dir=str(parent),
        )
    )
    raw = temp / "raw"
    asr = temp / "asr"
    _update(job, message="准备听写官方中文原音", total=6, current=0)
    try:
        _update(job, message="解出通用/空勤中文原音")
        common_names = _unique_streams(
            common_bank, raw / "common", temp, prefix="c_"
        )
        _step(job)

        _update(job, message="解出陆战中文原音")
        ground_main = _unique_streams(
            ground_bank, raw / "ground", temp, prefix="g_"
        )
        ground_names = dict(ground_main)
        if supplement:
            main_names = set(ground_main.values())
            ground_names.update(
                _unique_streams(
                    supplement,
                    raw / "ground",
                    temp,
                    prefix="sm_",
                    skip_names=main_names,
                )
            )
        _step(job)

        _update(job, message="转换官方中文原音为 16 kHz")
        _convert(raw / "common", asr / "common", job)
        _convert(raw / "ground", asr / "ground", job)
        _step(job)

        _update(job, message="听写通用/空勤台词")
        common = _transcribe(asr / "common", common_names, job)
        _step(job)

        _update(job, message="听写陆战台词")
        ground = _transcribe(asr / "ground", ground_names, job)
        _step(job)

        if not common and not ground:
            raise TextCatalogError("官方中文原音没有听写出任何文本，请检查游戏目录和 FunASR")
        catalog = {
            "generated_at": _now(),
            "engine": "FunASR paraformer-zh（GPT-SoVITS 整合包）",
            "note": "官方中文原音的自动听写初稿，可能有错字；重要句请在快速制作里试听原音后修改。",
            "scopes": {"common": common, "ground": ground},
        }
        _update(job, message="写入中文文本缓存")
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex[:6]}.tmp")
        temporary.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        _step(job, "中文文本缓存已生成")
        return catalog
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def ensure(job, data_dir: str | Path, game_path: str | Path | None) -> dict:
    """确保缓存可用；旧缓存只有一半时重新生成。"""
    cached = load(data_dir)
    scopes = cached.get("scopes") or {}
    if scopes.get("common") and scopes.get("ground"):
        return cached
    return build(job, data_dir, game_path)
