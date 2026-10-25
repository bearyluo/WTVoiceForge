"""从游戏自带的 dialogs_chat bank 补全官方 FMOD 工程缺少的无线电语音。

官方工程只随包提供了英语/俄语无线电源文件（Assets/radio_chat/eng、ru），
其余语种在 Metadata/AudioFile 里有路径和时长，但 assets 被 Gaijin 省略。
游戏 sound/dialogs_chat_<lang>.assets.bank 里的流名与工程 assetPath 文件名
逐条一致，因此可以按名字精确还原，而不是靠猜。

用法（源码目录）：
    py -3.13 tools/fill_radio_chat.py \
        --kit "D:/path/to/fmod_studio_warthunder_for_modders" \
        --game "D:/SteamLibrary/steamapps/common/War Thunder"

默认只补目录里被事件引用的缺失文件；--dry-run 只统计不落盘。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import catalog, vgmstream  # noqa: E402

# 工程前缀 → 游戏 bank 后缀。官方工程用 ge/chn/hchn 这类旧代码，游戏用 de/zh。
LANGUAGE_BANKS = {
    "chn": "zh",
    "cze": "cz",
    "fr": "fr",
    "ge": "de",
    "he": "he",
    "hun": "hu",
    "it": "it",
    "jp": "jp",
    "kor": "ko",
    "pol": "pl",
    "por": "pt",
    "sp": "sp",
    "srb": "sr",
    "sw": "sv",
    "tur": "tr",
    "vie": "vi",
}

NAME_RE = re.compile(r"^[a-z0-9_]+$")
INDEX_PREFIX_RE = re.compile(r"^\d+_")


def duration_of(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (wave.Error, OSError):
        return None


def load_catalog(kit: Path, cache: Path | None) -> dict:
    if cache:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("schema") != catalog.CATALOG_SCHEMA:
            raise SystemExit(f"缓存结构版本不匹配：{cache}")
        return data
    print("没有指定缓存，先扫描官方工程……")
    return catalog.build_full_catalog(kit)


def missing_by_prefix(data: dict) -> dict[str, dict[str, list[tuple[str, float | None]]]]:
    """prefix → 文件名 → [(相对路径, 元数据时长), ...]（同名可出现在多个 voice 目录）。"""
    out: dict[str, dict[str, list[tuple[str, float | None]]]] = {}
    for asset in data.get("assets", {}).values():
        if asset.get("exists"):
            continue
        path = str(asset.get("path") or "")
        parts = path.split("/")
        if len(parts) != 4 or parts[0] != "radio_chat" or not path.endswith(".wav"):
            continue
        prefix, name = parts[1], parts[-1][: -len(".wav")]
        rel = "/".join(parts[2:])
        out.setdefault(prefix, {}).setdefault(name, []).append((rel, asset.get("length")))
    return out


def find_bank(sound: Path, lang: str) -> Path:
    candidates = [
        sound / f"dialogs_chat_{lang}.assets.bank",
        sound / f"dialogs_chat_{lang}.bank",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"游戏目录没有 dialogs_chat_{lang}.assets.bank")


def extract_bank(exe: Path, bank: Path, temp: Path) -> list[Path]:
    command = [
        str(exe),
        "-S",
        "0",
        "-o",
        str(temp / "?s_?n.wav"),
        str(bank),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        check=False,
    )
    if completed.returncode != 0:
        tail = completed.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise RuntimeError(f"vgmstream 解包失败（{completed.returncode}）：{' | '.join(tail)}")
    return sorted(temp.glob("*.wav"))


def fill_language(
    *,
    kit: Path,
    sound: Path,
    exe: Path,
    prefix: str,
    lang: str,
    missing: dict[str, list[tuple[str, float | None]]],
    dry_run: bool,
    strict_duration: bool,
) -> dict:
    stats = {
        "prefix": prefix,
        "bank": f"dialogs_chat_{lang}",
        "decoded": 0,
        "copied": 0,
        "skipped": 0,
        "extra": 0,
        "bad_name": 0,
        "duration_mismatch": 0,
        "errors": [],
    }
    total_missing = sum(len(items) for items in missing.values())
    if not total_missing:
        return stats
    bank = find_bank(sound, lang)
    print(f"[{prefix}] {bank.name}：{total_missing} 个缺失文件")
    temp = Path(tempfile.mkdtemp(prefix=f"wtfill-{prefix}-", dir=kit))
    try:
        files = extract_bank(exe, bank, temp)
        stats["decoded"] = len(files)
        streams: dict[str, list[tuple[Path, float | None]]] = {}
        for file in files:
            name = INDEX_PREFIX_RE.sub("", file.stem)
            if not NAME_RE.fullmatch(name):
                stats["bad_name"] += 1
                continue
            streams.setdefault(name, []).append((file, duration_of(file)))
        stats["extra"] = sum(len(items) for name, items in streams.items() if name not in missing)

        for name, candidates in missing.items():
            available = streams.get(name)
            for rel, expected in candidates:
                dest = kit / "Assets" / "radio_chat" / prefix / rel
                if dest.exists():
                    stats["skipped"] += 1
                    continue
                if not available:
                    stats["errors"].append(f"{rel}: bank 里没有同名流")
                    continue
                source, actual = min(
                    available,
                    key=lambda item: abs((item[1] or 0) - (expected or 0)),
                )
                if strict_duration and expected and actual is not None and abs(expected - actual) > 0.05:
                    stats["duration_mismatch"] += 1
                    stats["errors"].append(
                        f"{rel}: 时长 {actual:.3f}s ≠ {expected:.3f}s"
                    )
                    continue
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                stats["copied"] += 1
        if stats["decoded"] == 0:
            stats["errors"].append("bank 没有解出任何流")
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return stats


def clone_hchn(kit: Path, missing: dict[str, list[tuple[str, float | None]]], dry_run: bool) -> int:
    """hchn 与 chn 的元数据时长逐条一致，游戏里没有独立 hchn bank。"""
    copied = 0
    source_root = kit / "Assets" / "radio_chat" / "chn"
    target_root = kit / "Assets" / "radio_chat" / "hchn"
    for name, candidates in missing.items():
        suffix = name[len("hchn_") :] if name.startswith("hchn_") else None
        if not suffix:
            continue
        for rel, expected in candidates:
            dest = target_root / rel
            if dest.exists():
                continue
            source = source_root / rel
            if not source.is_file():
                matches = [
                    path
                    for path in source_root.rglob(f"*_{suffix}.wav")
                    if path.stem.startswith("chn_")
                ]
                if len(matches) != 1:
                    continue
                source = matches[0]
            actual = duration_of(source)
            if expected and actual is not None and abs(expected - actual) > 0.05:
                continue
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
            copied += 1
    return copied


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="从游戏 bank 补全官方工程缺失的无线电语音")
    parser.add_argument("--kit", required=True, help="官方 FMOD 工程根目录")
    parser.add_argument("--game", required=True, help="War Thunder 根目录")
    parser.add_argument("--cache", help="catalog.json；省略则现场扫描工程")
    parser.add_argument("--vgmstream", help="vgmstream-cli.exe；省略则自动发现")
    parser.add_argument("--lang", action="append", help="只处理指定工程前缀，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    parser.add_argument("--clone-hchn", action="store_true", help="用 chn 音频补 hchn（时长逐条一致时）")
    parser.add_argument("--loose-duration", action="store_true", help="不做时长校验")
    args = parser.parse_args(argv)

    kit = Path(args.kit).expanduser().resolve()
    sound = Path(args.game).expanduser().resolve() / "sound"
    if not (kit / "Metadata").is_dir() or not (kit / "Assets").is_dir():
        parser.error(f"不是有效的 FMOD 工程：{kit}")
    if not sound.is_dir():
        parser.error(f"找不到游戏 sound 目录：{sound}")

    exe = Path(args.vgmstream).resolve() if args.vgmstream else vgmstream.find()
    if not exe or not exe.is_file():
        parser.error("没有找到 vgmstream-cli.exe，请用 --vgmstream 指定")

    cache = Path(args.cache).expanduser().resolve() if args.cache else None
    data = load_catalog(kit, cache)
    missing = missing_by_prefix(data)

    prefixes = [p for p in (args.lang or LANGUAGE_BANKS) if p in LANGUAGE_BANKS]
    total = {
        "decoded": 0,
        "copied": 0,
        "skipped": 0,
        "extra": 0,
        "bad_name": 0,
        "duration_mismatch": 0,
    }
    failures = 0
    for prefix in prefixes:
        lang = LANGUAGE_BANKS[prefix]
        try:
            stats = fill_language(
                kit=kit,
                sound=sound,
                exe=exe,
                prefix=prefix,
                lang=lang,
                missing=missing.get(prefix, {}),
                dry_run=args.dry_run,
                strict_duration=not args.loose_duration,
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[{prefix}] 失败：{exc}")
            continue
        for key in total:
            total[key] += stats.get(key, 0)
        note = "（预演）" if args.dry_run else ""
        print(
            f"[{prefix}] 解出 {stats['decoded']}，补入 {stats['copied']}{note}，"
            f"已存在 {stats['skipped']}，多余流 {stats['extra']}，"
            f"时长不符 {stats['duration_mismatch']}"
        )
        for error in stats["errors"][:3]:
            print(f"  ! {error}")

    hchn_copied = 0
    if args.clone_hchn and "hchn" in missing:
        hchn_copied = clone_hchn(kit, missing["hchn"], dry_run=args.dry_run)

    print(
        f"合计：解出 {total['decoded']}，补入 {total['copied']}，已存在 {total['skipped']}，"
        f"多余流 {total['extra']}，坏名 {total['bad_name']}，"
        f"时长不符 {total['duration_mismatch']}，失败语种 {failures}"
    )
    if args.clone_hchn:
        print(f"hchn 复制：{hchn_copied}{'（预演）' if args.dry_run else ''}")
    print("记得重新生成目录缓存，让工作台看到补全后的素材。")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
