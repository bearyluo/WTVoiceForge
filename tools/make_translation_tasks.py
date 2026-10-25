"""从游戏本地化语音表生成翻译任务包。

用法（项目根目录）：
    py -3.13 tools/make_translation_tasks.py --scope naval
    py -3.13 tools/make_translation_tasks.py --scope ground --languages en_us

输出默认写到 D:\\WTVoiceForge\\翻译任务\\<scope>_<日期>。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, localized, translation  # noqa: E402

SCOPE_LABELS = {"common": "通用", "ground": "陆战", "naval": "海战"}
SCOPE_PREFIX = {"common": "CM", "ground": "GD", "naval": "NV"}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(translation.csv_text(rows, fields), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成本地化语音表翻译任务")
    parser.add_argument("--scope", default="naval", choices=sorted(SCOPE_LABELS))
    parser.add_argument("--language", action="append", default=None, help="可重复，例如 --language en_us")
    parser.add_argument("--game", default="", help="战雷目录；默认取应用配置")
    parser.add_argument("--kit", default="", help="官方 FMOD 工程；默认取应用配置")
    parser.add_argument("--output", default="", help="输出目录")
    parser.add_argument("--missing", action="store_true", help="只导出没有官方中文原音的缺口")
    args = parser.parse_args(argv)

    cfg = config.load_config()
    game = Path(args.game or cfg.game_path).expanduser()
    kit = Path(args.kit or cfg.kit_path).expanduser()
    if not game.is_dir():
        print(f"战雷目录不存在：{game}")
        return 2
    if not kit.is_dir():
        print(f"官方 FMOD 工程不存在：{kit}")
        return 2

    scope_label = SCOPE_LABELS[args.scope]
    wanted = set(args.language or [])
    banks = [
        item
        for item in localized.banks(kit)
        if item["scope"] == args.scope and (not wanted or item["language"] in wanted)
    ]
    if not banks:
        print("没有找到匹配的本地化语音表")
        return 2

    official_cn = (
        localized.official_cn_names(game, args.scope) if args.missing else set()
    )
    sources: dict[str, dict] = {}
    for item in banks:
        bank = item["id"]
        path = localized.game_bank_path(game, bank)
        if not path:
            print(f"跳过 {bank}：游戏目录里没有对应 .assets.bank")
            continue
        print(f"读取 {bank} ...", flush=True)
        streams = localized.game_streams(path)
        if args.missing:
            streams = [row for row in streams if row.get("name") not in official_cn]
        if not streams:
            print(f"跳过 {bank}：没有缺口")
            continue
        sources[item["language"]] = {"bank": bank, "streams": streams}
    if not sources:
        print("没有可读取的语音表")
        return 2

    result = translation.build(
        sources,
        prefix=SCOPE_PREFIX.get(args.scope, "T"),
        scope_label=f"{scope_label}缺口" if args.missing else scope_label,
    )
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    result["game_path"] = str(game)
    result["kit_path"] = str(kit)

    stamp = datetime.now().strftime("%Y-%m-%d")
    folder = f"{scope_label}缺口_{stamp}" if args.missing else f"{scope_label}_{stamp}"
    out = Path(args.output).expanduser() if args.output else Path(r"D:\WTVoiceForge\翻译任务") / folder
    out.mkdir(parents=True, exist_ok=True)

    languages = sorted(sources)
    write_csv(out / f"{scope_label}翻译_分组任务.csv", result["groups"], translation.group_fields())
    write_csv(
        out / f"{scope_label}翻译_流名索引.csv",
        result["names"],
        translation.name_fields(languages),
    )
    write_csv(
        out / f"{scope_label}翻译_术语表.csv",
        translation.glossary_rows(),
        ["term", "chinese", "example", "note"],
    )
    lang_dir = out / "按语言"
    for language in languages:
        rows = [row for row in result["rows"] if row["language"] == language]
        write_csv(lang_dir / f"{sources[language]['bank']}.csv", rows, translation.stream_fields())

    (out / "README.md").write_text(
        translation.readme_text(
            result,
            title=f"{scope_label}缺口翻译" if args.missing else f"{scope_label}翻译",
        ),
        encoding="utf-8",
    )
    (out / f"{scope_label}翻译_统计.json").write_text(
        json.dumps(
            {
                "scope": scope_label,
                "generated_at": result["generated_at"],
                "game_path": result["game_path"],
                "languages": result["languages"],
                "stats": result["stats"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    stats = result["stats"]
    print("")
    print(f"输出：{out}")
    print(
        f"流名 {stats['streams']} 条 / 唯一名 {stats['unique_names']} 个 / "
        f"任务 {stats['groups']} 个（数字模板 {stats['numeric_groups']}，短句 {stats['phrase_groups']}）"
    )
    for language, info in result["languages"].items():
        print(f"  {language:6s} {info['streams']:4d} 条  {info['duration_sec'] / 60:5.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
