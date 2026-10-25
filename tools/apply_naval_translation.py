"""把首轮海战中文译法套进翻译任务包。

输入：D:\\WTVoiceForge\\翻译任务\\海战_2026-10-24 下的空白任务 CSV
输出：同目录下的 *_中文.csv / 按语言_中文/*.csv

译法来自 tools/naval_translation_zh.py，按流名/术语整理，不是听写结果。
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

from app import translation  # noqa: E402
from tools import naval_translation_zh as mapping  # noqa: E402

PACKAGE = Path(r"D:\WTVoiceForge\翻译任务\海战_2026-10-24")


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
    key = group["group_key"]
    role, body = key.split(":", 1)
    if group.get("template"):
        kind = key.split(":", 1)[1]
        text = mapping.NUMERIC_TEXT.get(kind)
        if not text:
            raise KeyError(f"没有数字模板：{key}")
        return text, "high", "数字模板，已按流名套用；仍建议抽听"
    text = mapping.ROLE_ZH.get((role, body)) or mapping.BODY_ZH.get(body)
    if not text:
        raise KeyError(f"没有译法：{key}")
    confidence = "low" if body in mapping.LOW_CONFIDENCE else "medium"
    note = (
        "重点校对：流名语义不够明确，必须听原音"
        if confidence == "low"
        else "首轮译法，需听原音核对语气和省略成分"
    )
    return text, confidence, note


def stream_text(group: dict, event: str) -> str:
    text = group["_text"]
    if not group.get("template"):
        return text
    parsed = translation.parse_name(event)
    if parsed.get("number") is None:
        return text
    return text.format(n=parsed["number"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", default=str(PACKAGE))
    args = parser.parse_args(argv)
    package = Path(args.package)

    group_file = package / "海战翻译_分组任务.csv"
    group_fields, groups = read_csv(group_file)
    extra_group_fields = ["translation_source", "confidence", "review_note"]
    for group in groups:
        text, confidence, note = group_text(group)
        group["_text"] = text
        group["text"] = text
        group["translation_source"] = "identifier_first_pass"
        group["confidence"] = confidence
        group["review_note"] = note

    group_out = package / "海战翻译_分组任务_中文.csv"
    write_csv(group_out, group_fields + extra_group_fields, groups)
    by_id = {group["task_id"]: group for group in groups}

    # 流名索引
    name_file = package / "海战翻译_流名索引.csv"
    name_fields, names = read_csv(name_file)
    for row in names:
        group = by_id[row["task_id"]]
        row["text"] = stream_text(group, row["event"])
        row["translation_source"] = "identifier_first_pass"
        row["confidence"] = group["confidence"]
        row["review_note"] = group["review_note"]
    write_csv(
        package / "海战翻译_流名索引_中文.csv",
        name_fields + ["translation_source", "confidence", "review_note"],
        names,
    )

    # 逐语言任务
    stream_extra = ["translation_source", "confidence", "review_note"]
    stream_fields = translation.stream_fields()
    stream_total = 0
    for source in sorted((package / "按语言").glob("*.csv")):
        fields, rows = read_csv(source)
        for row in rows:
            group = by_id[row["task_id"]]
            row["text"] = stream_text(group, row["event"])
            row["status"] = "draft"
            row["translation_source"] = "identifier_first_pass"
            row["confidence"] = group["confidence"]
            row["review_note"] = group["review_note"]
        write_csv(
            package / "按语言_中文" / source.name,
            fields + stream_extra,
            rows,
        )
        stream_total += len(rows)

    # 机器可读映射，便于后续修正或重新套用
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "按流名整理的首轮译法，不是听写结果；合成前必须听原音校对。",
        "tasks": {
            group["task_id"]: {
                "group_key": group["group_key"],
                "category": group["category"],
                "role": group["group_key"].split(":", 1)[0],
                "template": group["template"],
                "text": group["text"],
                "confidence": group["confidence"],
                "note": group["review_note"],
            }
            for group in groups
        },
    }
    (package / "海战翻译_译文映射.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    counts = {"high": 0, "medium": 0, "low": 0}
    for group in groups:
        counts[group["confidence"]] += 1
    stats = {
        "generated_at": payload["generated_at"],
        "groups": len(groups),
        "streams": stream_total,
        "confidence": counts,
        "numeric_templates": sum(1 for group in groups if group["template"]),
    }
    (package / "海战翻译_中文_统计.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    note = f"""# 海战首轮中文译法

- 生成时间：{stats['generated_at']}
- 任务：{stats['groups']} 个（数字模板 {stats['numeric_templates']} 个）
- 逐语言流：{stats['streams']} 条
- 置信度：高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}

## 怎么用

优先看 `海战翻译_分组任务_中文.csv`。`text` 是首轮译文，`confidence` 标了把握程度：

- high：数字模板，直接套用；
- medium：常规命令/报告，听一遍原音确认语气即可；
- low：流名语义不够明确，必须重点听原音，必要时改写。

逐语言导入用 `按语言_中文/<bank>.csv`，`text` 已填好，`event_id` 与声音项目一致；
导入后逐条试听，发现不对就改 `text` 再重新导入。原空白任务仍保留在
`海战翻译_分组任务.csv` 和 `按语言/`，方便对照。

## 注意

这些译法按流名/术语整理，**不是听写结果**。海战没有官方中文语音表，数字读法
（尤其方位 080、深度 1040 英尺）、损管术语和语气都要在听原音后确认。低置信度
条目已在 `review_note` 标出。
"""
    (package / "首轮译文说明.md").write_text(note, encoding="utf-8")
    print(f"输出：{package}")
    print(f"分组 {len(groups)} 个，流 {stream_total} 条，置信度 {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
