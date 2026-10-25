"""生成“快速制作”使用的官方中文听写缓存。

官方中文语音表只有音频，没有字幕。这里用 vgmstream 解出官方中文原音，
再用整合包自带的 FunASR 批量听写。结果只写到用户数据目录的缓存里，
不随应用分发；普通用户第一次点“准备中文台词”时也会自动生成。

运行（需要 D 盘的 GPT-SoVITS 整合包和游戏目录）：
  py -3.13 tools/build_chinese_text_catalog.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, localized, quickpack, text_catalog  # noqa: E402

TARGET_BANKS = [
    "_crew_dialogs_common_de",
    "_crew_dialogs_common_en",
    "_crew_dialogs_common_ru",
    "_crew_dialogs_ground_de",
    "_crew_dialogs_ground_en",
    "_crew_dialogs_ground_en_us",
    "_crew_dialogs_ground_ru",
]


def _missing(kit: str, game_path: str, catalog: dict) -> dict[str, list[str]]:
    gaps = quickpack._gaps().get("banks") or {}
    overrides = quickpack._overrides()
    prefixes = tuple(overrides.get("non_voice_prefixes") or ())
    scope_translations = overrides.get("scope_translations") or {}
    result: dict[str, list[str]] = {}
    for bank in TARGET_BANKS:
        scope, _language = localized._split_bank(bank)
        rows = localized.streams(kit, bank, game_path)
        missing = []
        for row in rows:
            name = str(row.get("event") or "").strip()
            if not name:
                continue
            if quickpack._is_non_voice(name, prefixes):
                continue
            if (gaps.get(bank) or {}).get(name):
                continue
            if (catalog.get("scopes", {}).get(scope, {}) or {}).get(name):
                continue
            if (scope_translations.get(scope) or {}).get(name):
                continue
            missing.append(name)
        if missing:
            result[bank] = missing
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", default="")
    parser.add_argument("--kit", default="")
    parser.add_argument("--data-dir", default="", help="应用数据目录（缓存写到它下面）")
    args = parser.parse_args()
    cfg = config.load_config()
    game_path = args.game or cfg.game_path
    kit = args.kit or cfg.kit_path
    data_dir = Path(args.data_dir or cfg.data_dir or config.DEFAULT_DATA_DIR)
    if not game_path or not kit:
        raise SystemExit("请先在工作台选择游戏目录和官方 FMOD 工程")

    catalog = text_catalog.build(None, data_dir, game_path, force=True)
    print(f"已写入 {text_catalog.cache_path(data_dir)}")
    for scope, values in (catalog.get("scopes") or {}).items():
        print(f"  {scope}: {len(values)} 条听写文本")

    missing = _missing(kit, game_path, catalog)
    print("覆盖检查（已排除提示音、缺口译文和占位音译文）：")
    total_missing = 0
    for bank in TARGET_BANKS:
        names = missing.get(bank, [])
        total_missing += len(names)
        print(f"  {bank}: 缺 {len(names)} 条")
        for name in names[:10]:
            print(f"    - {name}")
    print(f"合计缺少 {total_missing} 条")


if __name__ == "__main__":
    main()
