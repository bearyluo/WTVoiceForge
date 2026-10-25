"""把陆战/空战缺口的第一轮中文译法套进任务包。

用法：
    py -3.13 tools/apply_gap_translation.py --package "D:\\WTVoiceForge\\翻译任务\\陆战缺口_2026-10-24" --prefix 陆战翻译 --title 陆战缺口
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import ground_air_translation_zh as mapping  # noqa: E402


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def group_text(group: dict) -> tuple[str, str, str]:
    body = group["group_key"].split(":", 1)[1]
    text = mapping.BODY_ZH.get(body)
    if not text:
        raise KeyError(f"没有译法：{group['group_key']}")
    confidence = "low" if body in mapping.LOW_CONFIDENCE else "medium"
    note = (
        "重点校对：流名语义不够明确，必须听原音"
        if confidence == "low"
        else "首轮译法，需听原音核对语气和省略成分"
    )
    return text, confidence, note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--prefix", required=True, help="例如 陆战翻译")
    parser.add_argument("--title", required=True, help="例如 陆战缺口")
    args = parser.parse_args(argv)
    package = Path(args.package)
    prefix = args.prefix

    group_fields, groups = read_csv(package / f"{prefix}_分组任务.csv")
    extra = ["translation_source", "confidence", "review_note"]
    for group in groups:
        text, confidence, note = group_text(group)
        group["_text"] = text
        group["text"] = text
        group["translation_source"] = "identifier_first_pass"
        group["confidence"] = confidence
        group["review_note"] = note
    write_csv(package / f"{prefix}_分组任务_中文.csv", group_fields + extra, groups)
    by_id = {group["task_id"]: group for group in groups}

    name_fields, names = read_csv(package / f"{prefix}_流名索引.csv")
    for row in names:
        group = by_id[row["task_id"]]
        row["text"] = group["_text"]
        row["translation_source"] = "identifier_first_pass"
        row["confidence"] = group["confidence"]
        row["review_note"] = group["review_note"]
    write_csv(
        package / f"{prefix}_流名索引_中文.csv",
        name_fields + extra,
        names,
    )

    stream_total = 0
    for source in sorted((package / "按语言").glob("*.csv")):
        fields, rows = read_csv(source)
        for row in rows:
            group = by_id[row["task_id"]]
            row["text"] = group["_text"]
            row["status"] = "draft"
            row["translation_source"] = "identifier_first_pass"
            row["confidence"] = group["confidence"]
            row["review_note"] = group["review_note"]
        write_csv(package / "按语言_中文" / source.name, fields + extra, rows)
        stream_total += len(rows)

    counts = {"high": 0, "medium": 0, "low": 0}
    for group in groups:
        counts[group["confidence"]] += 1
    now = datetime.now().isoformat(timespec="seconds")
    payload = {
        "generated_at": now,
        "scope": args.title,
        "note": "按流名整理的首轮译法，不是听写结果；合成前必须听原音校对。",
        "stats": {
            "groups": len(groups),
            "streams": stream_total,
            "confidence": counts,
        },
        "tasks": {
            group["task_id"]: {
                "group_key": group["group_key"],
                "text": group["text"],
                "confidence": group["confidence"],
                "note": group["review_note"],
            }
            for group in groups
        },
    }
    (package / f"{prefix}_译文映射.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (package / f"{args.title}_首轮译文.md").write_text(
        f"""# {args.title}首轮中文译法

- 生成时间：{now}
- 任务：{len(groups)} 个
- 逐语言流：{stream_total} 条
- 置信度：中 {counts['medium']} / 低 {counts['low']}

`{prefix}_分组任务_中文.csv` 的 `text` 是首轮译文；逐语言导入用
`按语言_中文/<bank>.csv`。低置信度条目已在 `review_note` 标明，必须听原音重点校对。
这些译法按流名/术语整理，不是听写结果。
""",
        encoding="utf-8",
    )
    print(f"输出：{package}")
    print(f"分组 {len(groups)} 个，流 {stream_total} 条，置信度 {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
