"""把海战翻译任务包里的首轮中文译法整理成应用内置数据。

输入是 tools/apply_naval_translation.py 生成的 *_中文.csv。输出
app/data/naval_translations.json，按 bank 和流名索引，供快速制作直接套用。

这些译法按流名/术语整理，不是听写结果；导入后仍需听原音校对。
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path(r"D:\WTVoiceForge\翻译任务\海战_2026-10-24\按语言_中文")
DEFAULT_TARGET = ROOT / "app" / "data" / "naval_translations.json"


def read_rows(folder: Path) -> tuple[dict, Counter]:
    banks: dict[str, dict] = {}
    counts: Counter = Counter()
    for path in sorted(folder.glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                bank = str(row.get("bank") or "").strip()
                event = str(row.get("event") or row.get("event_id") or "").strip()
                if event.startswith("at:"):
                    event = event.rsplit(":", 1)[-1]
                text = str(row.get("text") or "").strip()
                if not bank or not event or not text:
                    continue
                confidence = str(row.get("confidence") or "medium").strip()
                entry = {"text": text, "confidence": confidence}
                existing = banks.setdefault(bank, {}).get(event)
                if existing and existing.get("text") != text:
                    counts["conflicts"] += 1
                    continue
                if not existing:
                    counts[confidence] += 1
                banks[bank][event] = entry
    return banks, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    args = parser.parse_args(argv)

    source = Path(args.source)
    target = Path(args.target)
    if not source.is_dir():
        raise SystemExit(f"找不到海战译法目录：{source}")
    banks, counts = read_rows(source)
    if not banks:
        raise SystemExit("海战译法目录里没有可用的 text")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "note": (
            "按流名整理的首轮中文译法，不是听写结果；"
            "数字读法、损管术语和语气必须听原音后确认。"
        ),
        "streams": sum(len(rows) for rows in banks.values()),
        "banks": banks,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"输出：{target}")
    print(
        f"语音表 {len(banks)} 张 / 流 {payload['streams']} 条 / "
        f"高 {counts['high']} 中 {counts['medium']} 低 {counts['low']} / "
        f"冲突 {counts['conflicts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
