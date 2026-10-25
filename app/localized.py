"""FMOD AudioTable 本地化语音表（乘员语音）。

官方工程里 `_crew_dialogs_ground_en_us` 这类 bank 不是普通事件 bank，而是
“Audio Table” bank：Bank XML 的 sourceDirectory 指向一整棵素材目录，FMOD
会把目录里的 WAV 全部打进 `.assets.bank`。游戏的事件通过 ProgrammerSound
按流名取音频，所以按文件/流名精确替换即可汉化。

这里的函数只读官方工程和游戏 bank；解包临时文件写到应用缓存目录。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from app import vgmstream, voice

SCOPE_LABELS = {
    "common": "通用乘员",
    "ground": "陆战乘员",
    "naval": "海战乘员",
}
LANG_LABELS = {
    "ar": "阿拉伯语",
    "cz": "捷克语",
    "de": "德军德语",
    "el": "希腊语",
    "en": "英军英语",
    "en_au": "澳军英语",
    "en_us": "美军英语",
    "en_za": "南非英语",
    "fi": "芬兰语",
    "fr": "法军法语",
    "gl": "加利西亚语",
    "he": "希伯来语",
    "hi": "印地语",
    "hu": "匈牙利语",
    "id": "印度尼西亚语",
    "it": "意军意大利语",
    "jp": "日军日语",
    "ko": "韩语",
    "lt": "立陶宛语",
    "nl": "荷兰语",
    "nw": "挪威语",
    "pl": "波兰语",
    "pt": "葡萄牙语",
    "ru": "苏军俄语",
    "sp": "西班牙语",
    "sr": "塞尔维亚语",
    "sv": "瑞典语",
    "th": "泰语",
    "tr": "土耳其语",
    "uk": "乌克兰语",
    "us": "美军英语",
    "vi": "越南语",
    "zh": "中文",
}
AUDIO_SUFFIXES = {".wav"}


class LocalizedError(RuntimeError):
    pass


def clear_cache() -> None:
    for func in (_tables, _kit_file_map, _game_streams, _all_kit_files, _streams):
        func.cache_clear()


def _norm_source(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    if text.startswith("Assets/"):
        text = text[len("Assets/") :]
    if not text or ".." in text.split("/") or ":" in text.split("/")[0]:
        raise LocalizedError(f"非法语音表目录：{value}")
    return text


def _bank_meta_path(kit: Path) -> Path:
    return kit / "Metadata" / "Bank"


def _dir_signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return (stat.st_mtime_ns,)
    except OSError:
        return (0,)


@lru_cache(maxsize=16)
def _tables(kit_str: str, signature: int) -> dict[str, str]:
    kit = Path(kit_str)
    root = _bank_meta_path(kit)
    result: dict[str, str] = {}
    if not root.is_dir():
        return result
    for item in root.glob("*.xml"):
        try:
            text = item.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        source = re.search(
            r'<property name="sourceDirectory">\s*<value>([^<]*)</value>', text
        )
        if not source:
            continue
        name = re.search(
            r'<property name="name">\s*<value>([^<]*)</value>', text
        )
        if not name:
            continue
        try:
            result[name.group(1).strip()] = _norm_source(source.group(1))
        except LocalizedError:
            continue
    return result


def tables(kit: str | Path) -> dict[str, str]:
    kit_path = Path(kit).expanduser().resolve()
    return dict(_tables(str(kit_path), _dir_signature(_bank_meta_path(kit_path))[0]))


def _split_bank(bank: str) -> tuple[str, str]:
    match = re.fullmatch(r"_crew_dialogs_(common|ground|naval)_([a-z_]+)", bank)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _label(bank: str) -> str:
    scope, language = _split_bank(bank)
    scope_label = SCOPE_LABELS.get(scope, scope or "语音表")
    language_label = LANG_LABELS.get(language, language or "未知语言")
    return f"{language_label} · {scope_label}"


def banks(kit: str | Path) -> list[dict]:
    kit_path = Path(kit).expanduser().resolve()
    result = []
    for bank, source in sorted(tables(kit_path).items()):
        root = kit_path / "Assets" / source
        if not root.is_dir():
            continue
        files = [
            item
            for item in root.rglob("*")
            if item.is_file() and item.suffix.lower() in AUDIO_SUFFIXES
        ]
        if not files:
            continue
        scope, language = _split_bank(bank)
        result.append(
            {
                "id": bank,
                "label": _label(bank),
                "scope": scope,
                "language": language,
                "source": source,
                "file_count": len(files),
                "stream_count": len({item.stem for item in files}),
            }
        )
    return result


@lru_cache(maxsize=16)
def _kit_file_map(kit_str: str, bank: str, signature: int) -> dict[str, list[str]]:
    kit = Path(kit_str)
    source = tables(kit).get(bank)
    if not source:
        return {}
    root = kit / "Assets" / source
    if not root.is_dir():
        return {}
    result: dict[str, list[str]] = {}
    for item in root.rglob("*"):
        if not item.is_file() or item.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        rel = item.relative_to(kit / "Assets").as_posix()
        result.setdefault(item.stem, []).append(rel)
    for paths in result.values():
        paths.sort(key=lambda rel: (0 if "/med/" in rel else 1, rel))
    return result


def _kit_files(kit: Path, bank: str) -> dict[str, list[str]]:
    source = tables(kit).get(bank)
    if not source:
        return {}
    root = kit / "Assets" / source
    return dict(
        _kit_file_map(str(kit), bank, _dir_signature(root)[0])
    )


def game_bank_path(game_path: str | Path | None, bank: str) -> Path | None:
    if not game_path:
        return None
    path = Path(game_path).expanduser() / "sound" / f"{bank}.assets.bank"
    return path if path.is_file() else None


@lru_cache(maxsize=16)
def _game_streams(bank_path_str: str, signature: tuple) -> tuple[dict, ...]:
    path = Path(bank_path_str)
    exe = vgmstream.find()
    if not exe:
        raise LocalizedError("没有检测到 vgmstream，无法读取游戏语音表")
    completed = subprocess.run(
        [str(exe), "-m", "-S", "0", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return ()
    streams = []
    for block in (completed.stdout or "").split("metadata for ")[1:]:
        index = re.search(r"stream index:\s*(\d+)", block)
        name = re.search(r"stream name:\s*(.+)", block)
        if not index or not name:
            continue
        duration = 0.0
        match = re.search(r"play duration:.*?\((\d+):(\d+(?:\.\d+)?)", block)
        if match:
            duration = int(match.group(1)) * 60 + float(match.group(2))
        streams.append(
            {
                "index": int(index.group(1)),
                "name": name.group(1).strip(),
                "duration_sec": round(duration, 3),
            }
        )
    return tuple(streams)


def game_streams(bank_path: Path) -> list[dict]:
    try:
        stat = bank_path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        return []
    return [dict(item) for item in _game_streams(str(bank_path), signature)]


@lru_cache(maxsize=16)
def _all_kit_files(kit_str: str, signature: int) -> dict[str, list[tuple[str, str]]]:
    kit = Path(kit_str)
    result: dict[str, list[tuple[str, str]]] = {}
    for bank, source in tables(kit).items():
        scope, language = _split_bank(bank)
        root = kit / "Assets" / source
        if not root.is_dir():
            continue
        for item in root.rglob("*"):
            if not item.is_file() or item.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            rel = item.relative_to(kit / "Assets").as_posix()
            result.setdefault(item.stem, []).append((language, rel))
    return result


def _fallback(kit: Path, bank: str, name: str) -> list[str]:
    _, language = _split_bank(bank)
    family = (language or "").split("_")[0]
    if not family:
        return []
    data = _all_kit_files(str(kit), _dir_signature(_bank_meta_path(kit))[0])
    hits = [
        rel
        for lang, rel in data.get(name, [])
        if (lang or "").split("_")[0] == family
    ]
    hits.sort(key=lambda rel: (0 if "/med/" in rel else 1, rel))
    return hits


def _duration(kit: Path, rel: str) -> float:
    try:
        return round(voice.wav_duration(kit / "Assets" / rel), 3)
    except OSError:
        return 0.0


@lru_cache(maxsize=64)
def _streams(
    kit_str: str,
    bank: str,
    game_bank_str: str,
    kit_signature: int,
    game_signature: tuple,
) -> tuple[dict, ...]:
    kit = Path(kit_str)
    source = tables(kit).get(bank)
    if not source:
        raise LocalizedError(f"不是本地化语音表 bank：{bank}")
    kit_files = _kit_files(kit, bank)
    game_path = Path(game_bank_str) if game_bank_str else None
    game_items = game_streams(game_path) if game_path and game_path.is_file() else []
    game_by_name: dict[str, list[dict]] = {}
    for item in game_items:
        game_by_name.setdefault(item["name"], []).append(item)

    names = sorted(game_by_name or kit_files)
    result = []
    for name in names:
        paths = list(kit_files.get(name, []))
        items = game_by_name.get(name, [])
        fallback = "" if paths else (_fallback(kit, bank, name) or [""])[0]
        if not paths:
            if items:
                paths = [
                    f"{source}/_game/{item['index']}/{name}.wav" for item in items
                ]
            elif fallback:
                paths = [f"{source}/_game/fallback/{name}.wav"]
        elif len(paths) < len(items):
            # 工程只带了一份，但游戏 bank 有两个变体；给多出来的流补一个槽位，
            # 构建时用同一句中文音频填满，避免留下英文变体。
            for item in items[len(paths) :]:
                paths.append(f"{source}/_game/{item['index']}/{name}.wav")
        if not paths:
            continue
        kit_duration = _duration(kit, paths[0]) if paths[0] in kit_files.get(name, []) else 0.0
        if not kit_duration and items:
            kit_duration = max(item["duration_sec"] for item in items)
        assets = []
        for index, rel in enumerate(paths):
            asset = {
                "guid": "",
                "path": rel,
                "file": f"{name}.wav",
                "duration_sec": kit_duration,
                "origin": fallback if rel not in kit_files.get(name, []) else "",
            }
            if rel not in kit_files.get(name, []) and index < len(items):
                asset["game_index"] = items[index]["index"]
            assets.append(asset)
        scope, language = _split_bank(bank)
        result.append(
            {
                "event_id": f"at:{bank}:{name}",
                "event": name,
                "bank": bank,
                "label": _label(bank),
                "scope": scope,
                "language": language,
                "localized": True,
                "game_only": not kit_files.get(name),
                "source_file": f"{name}.wav",
                "target_file": f"{name}.wav",
                "duration_sec": kit_duration,
                "assets": assets,
            }
        )
    return tuple(result)


def streams(kit: str | Path, bank: str, game_path: str | Path | None = None) -> list[dict]:
    kit_path = Path(kit).expanduser().resolve()
    source = tables(kit_path).get(bank)
    if not source:
        raise LocalizedError(f"不是本地化语音表 bank：{bank}")
    game_bank = game_bank_path(game_path, bank)
    game_sig = (0, 0)
    if game_bank:
        stat = game_bank.stat()
        game_sig = (stat.st_size, stat.st_mtime_ns)
    data = _streams(
        str(kit_path),
        bank,
        str(game_bank) if game_bank else "",
        _dir_signature(kit_path / "Assets" / source)[0],
        game_sig,
    )
    return [dict(item, assets=[dict(asset) for asset in item["assets"]]) for item in data]


def extract_stream(game_bank: Path, index: int, destination: Path) -> Path:
    exe = vgmstream.find()
    if not exe:
        raise LocalizedError("没有检测到 vgmstream，无法从游戏 bank 补语音")
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(exe), "-s", str(index), "-o", str(destination), str(game_bank)],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=120,
    )
    if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        detail = (completed.stderr or completed.stdout or b"").decode(
            "utf-8", errors="replace"
        )[-300:]
        raise LocalizedError(f"无法从游戏 bank 提取流 {index}：{detail}")
    return destination


def official_bank(bank: str) -> str:
    scope, _language = _split_bank(bank)
    if not scope:
        raise LocalizedError(f"无法识别语音表 bank：{bank}")
    return f"_crew_dialogs_{scope}_zh"


# 游戏里除了 ground_zh，还有一张补充表 ground_sm_zh，包含一部分
# ground_zh 没有的流（例如美军灭火/散热提示）。按优先级排在后面。
ZH_SUPPLEMENT_BANKS = {
    "ground": ("_crew_dialogs_ground_sm_zh",),
}


def official_sources(bank: str, game_path: str | Path | None) -> list[dict]:
    """返回可复用的游戏中文语音表，按优先级排列。

    主表是 `_crew_dialogs_<scope>_zh`；陆战再追加补充表 `ground_sm_zh`。
    海战当前没有官方中文表，返回空列表。
    """
    scope, _language = _split_bank(bank)
    if not scope:
        raise LocalizedError(f"无法识别语音表 bank：{bank}")
    names = [f"_crew_dialogs_{scope}_zh", *ZH_SUPPLEMENT_BANKS.get(scope, ())]
    result: list[dict] = []
    for index, name in enumerate(names):
        path = game_bank_path(game_path, name)
        if path:
            result.append({"bank": name, "path": path, "supplemental": index > 0})
    return result


def official_cn_names(game_path: str | Path | None, scope: str) -> set[str]:
    """返回某个 scope 下游戏官方中文表已有的流名（陆战含 ground_sm_zh）。"""
    names: set[str] = set()
    for source in official_sources(f"_crew_dialogs_{scope}_en", game_path):
        names.update(item["name"] for item in game_streams(source["path"]))
    return names


def coverage(kit: str | Path, game_path: str | Path | None = None) -> dict:
    """统计每张语音表在游戏里的流名数量，以及官方中文原音的覆盖情况。"""
    kit_path = Path(kit).expanduser().resolve()
    entries = banks(kit_path)

    zh_names: dict[str, set[str]] = {}
    zh_banks: dict[str, list[str]] = {}
    for scope in ("common", "ground", "naval"):
        names: list[str] = []
        collected: set[str] = set()
        for source in official_sources(f"_crew_dialogs_{scope}_en", game_path):
            names.append(source["bank"])
            collected.update(item["name"] for item in game_streams(source["path"]))
        zh_names[scope] = collected
        zh_banks[scope] = names

    rows: list[dict] = []
    for entry in entries:
        bank = entry["id"]
        scope, language = _split_bank(bank)
        path = game_bank_path(game_path, bank)
        game_names = {item["name"] for item in game_streams(path)} if path else set()
        kit_names = set(_kit_files(kit_path, bank))
        covered = game_names & zh_names.get(scope, set())
        missing = sorted(game_names - zh_names.get(scope, set()))
        rows.append(
            {
                "bank": bank,
                "label": entry["label"],
                "scope": scope,
                "language": language,
                "kit": len(kit_names),
                "game": len(game_names) if path else None,
                "game_only": len(game_names - kit_names) if path else 0,
                "kit_only": len(kit_names - game_names) if path else 0,
                "zh_banks": list(zh_banks.get(scope, [])),
                "zh_cover": len(covered),
                "zh_missing": len(missing) if path else 0,
                "missing": missing,
                "has_game_bank": bool(path),
            }
        )

    extras: list[dict] = []
    if game_path:
        known = {entry["id"] for entry in entries}
        sound = Path(game_path).expanduser() / "sound"
        try:
            for item in sorted(sound.glob("_crew_dialogs_*.assets.bank")):
                name = item.name[: -len(".assets.bank")]
                if name in known or name.endswith("_zh"):
                    continue
                scope, language = _split_bank(name)
                if not scope:
                    continue
                extras.append(
                    {
                        "bank": name,
                        "scope": scope,
                        "language": language,
                        "size": item.stat().st_size,
                    }
                )
        except OSError:
            extras = []

    return {
        "banks": rows,
        "zh_banks": zh_banks,
        "extra_game_banks": extras,
        "has_game": any(row["has_game_bank"] for row in rows),
    }


def _line_stream_name(line: dict) -> str:
    source = line.get("source") or {}
    for value in (
        line.get("target_file"),
        source.get("file"),
        line.get("event"),
    ):
        text = str(value or "").strip()
        if not text:
            continue
        text = Path(text.replace("\\", "/")).name
        for suffix in AUDIO_SUFFIXES:
            if text.lower().endswith(suffix):
                text = text[: -len(suffix)]
                break
        if text:
            return text
    return ""


def copy_official_audio(
    data_dir: str | Path,
    project_id: str,
    bank: str,
    game_path: str | Path | None,
) -> dict:
    """把游戏自带中文语音表里的同名流直接复制到台词，优先复用官方中文原音。

    陆战会同时查 `ground_zh` 和补充表 `ground_sm_zh`，主表优先；
    海战没有官方中文表时会明确报错，需要用户自己翻译并合成。
    """
    project = voice.load_project(data_dir, project_id)
    sources = official_sources(bank, game_path)
    if not sources:
        scope, _language = _split_bank(bank)
        if scope == "naval":
            raise LocalizedError("海战没有官方中文语音表，需要逐句翻译后用 GPT-SoVITS 合成")
        raise LocalizedError(f"游戏目录里没有中文语音表 {official_bank(bank)}")

    by_name: dict[str, tuple[dict, dict]] = {}
    source_totals: dict[str, int] = {}
    for source in sources:
        rows = game_streams(source["path"])
        source_totals[source["bank"]] = len({item["name"] for item in rows})
        for item in rows:
            by_name.setdefault(item["name"], (source, item))

    targets: dict[str, dict[int, Path]] = {}
    selected: list[tuple[dict, dict, dict, Path]] = []
    for line in project.get("lines", []):
        if line.get("audio"):
            continue
        name = _line_stream_name(line)
        hit = by_name.get(name)
        if not hit:
            continue
        source, item = hit
        cached = (
            Path(data_dir)
            / "cache"
            / "localized"
            / source["bank"]
            / f"{int(item['index']):04d}_{name}.wav"
        )
        if not cached.is_file():
            targets.setdefault(source["bank"], {})[int(item["index"])] = cached
        selected.append((line, source, item, cached))
    source_paths = {source["bank"]: source["path"] for source in sources}
    for source_bank, wanted in targets.items():
        extract_indexes(source_paths[source_bank], wanted)

    copied = 0
    supplemented = 0
    for line, source, item, cached in selected:
        if not cached.is_file():
            continue
        destination = voice.line_audio_path(data_dir, project_id, line["id"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, destination)
        line["audio"] = f"audio/{line['id']}.wav"
        line["status"] = "ready"
        line["duration_sec"] = float(
            item.get("duration_sec") or voice.wav_duration(destination) or 0.0
        )
        line["error"] = ""
        line["audio_source"] = (
            "official_cn_extra" if source["supplemental"] else "official_cn"
        )
        copied += 1
        if source["supplemental"]:
            supplemented += 1
    if copied:
        voice.save_project(data_dir, project)
    missing = [
        str(line.get("event") or line.get("target_file") or line["id"])
        for line in project.get("lines", [])
        if not line.get("audio")
    ]
    return {
        "project": project,
        "source_bank": sources[0]["bank"],
        "source_banks": [source["bank"] for source in sources],
        "source_totals": source_totals,
        "source_total": len(by_name),
        "supplemented": supplemented,
        "copied": copied,
        "missing": missing,
    }


def extract_indexes(
    game_bank: Path,
    targets: dict[int, Path],
    temp_root: str | Path | None = None,
) -> int:
    """一次解出整张 bank，再按流序号取出需要的 WAV（比逐个 -s 快得多）。"""
    if not targets:
        return 0
    exe = vgmstream.find()
    if not exe:
        raise LocalizedError("没有检测到 vgmstream，无法从游戏 bank 补语音")
    if temp_root:
        Path(temp_root).mkdir(parents=True, exist_ok=True)
    temp = Path(
        tempfile.mkdtemp(
            prefix="wtv_localized_",
            dir=str(temp_root) if temp_root else None,
        )
    )
    try:
        completed = subprocess.run(
            [str(exe), "-S", "0", "-o", str(temp / "?s_?n.wav"), str(game_bank)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=300,
        )
        if completed.returncode != 0:
            raise LocalizedError("无法解出游戏语音表 bank")
        done = 0
        for item in temp.glob("*.wav"):
            match = re.match(r"(\d+)_", item.name)
            if not match:
                continue
            index = int(match.group(1))
            target = targets.get(index)
            if not target:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()
            shutil.move(str(item), str(target))
            done += 1
        missing = [index for index in targets if not targets[index].is_file()]
        if missing:
            raise LocalizedError(
                f"游戏 bank 缺少 {len(missing)} 条流（例如 index {missing[0]}）"
            )
        return done
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def source_audio(
    kit: str | Path,
    bank: str,
    name: str,
    *,
    game_path: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> Path | None:
    """返回一条本地化语音的原音；工程没有时从游戏 bank 提取到缓存。"""
    kit_path = Path(kit).expanduser().resolve()
    items = streams(kit_path, bank, game_path)
    item = next((row for row in items if row["event"] == name), None)
    if not item:
        return None
    for asset in item["assets"]:
        direct = kit_path / "Assets" / asset["path"]
        if direct.is_file():
            return direct
        origin = asset.get("origin")
        if origin:
            fallback = kit_path / "Assets" / origin
            if fallback.is_file():
                return fallback
        index = asset.get("game_index")
        game_bank = game_bank_path(game_path, bank)
        if index is not None and game_bank and data_dir:
            cache = Path(data_dir) / "cache" / "localized" / bank
            target = cache / f"{int(index):04d}_{name}.wav"
            if not target.is_file():
                extract_stream(game_bank, int(index), target)
            return target
    return None
