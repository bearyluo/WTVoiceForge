"""逐表审计战雷乘员语音表：工程流、游戏流、中文文本覆盖和抽样解包。

只读游戏 sound 目录和官方 FMOD 工程；输出 CSV/JSON/Markdown 报告。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import dynamic_bank, localized, quickpack, text_catalog  # noqa: E402


def unique_names(rows) -> set[str]:
    return {str(item.get("name") or "") for item in rows if item.get("name")}


def sample_extract(path: Path, index: int | None) -> dict:
    if index is None:
        return {"ok": False, "detail": "没有流"}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.wav"
            localized.extract_stream(path, index, target)
            size = target.stat().st_size
            head = target.read_bytes()[:12]
            ok = head[:4] == b"RIFF" and head[8:12] == b"WAVE" and size > 44
            return {
                "ok": ok,
                "detail": f"{size} 字节" if ok else f"WAV 头异常：{head!r}",
            }
    except Exception as exc:  # noqa: BLE001 - 审计报告要保留原因
        return {"ok": False, "detail": str(exc)[-200:]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--data", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    game = Path(args.game)
    kit = Path(args.kit)
    data = Path(args.data) if args.data else ROOT / "data"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tables = localized.tables(kit)
    catalog = (text_catalog.load(data).get("scopes") or {})
    catalog_ready = {
        scope: bool(catalog.get(scope)) for scope in ("common", "ground")
    }
    gaps = (quickpack._gaps().get("banks") or {})
    naval_texts = quickpack._naval_texts()

    zh_names: dict[str, set[str]] = {}
    zh_banks: dict[str, list[str]] = {}
    for scope in ("common", "ground", "naval"):
        names: set[str] = set()
        banks: list[str] = []
        for source in localized.official_sources(
            f"_crew_dialogs_{scope}_en", game
        ):
            banks.append(source["bank"])
            names.update(unique_names(localized.game_streams(source["path"])))
        zh_names[scope] = names
        zh_banks[scope] = banks

    rows: list[dict] = []
    started = time.time()
    files = sorted((game / "sound").glob("_crew_dialogs_*.assets.bank"))
    if args.limit:
        files = files[: args.limit]
    print(f"语音表 {len(files)} 张，开始读取…", flush=True)
    for position, path in enumerate(files, 1):
        bank = path.name[: -len(".assets.bank")]
        parsed = dynamic_bank.parse_bank(bank)
        if not parsed:
            continue
        scope, language, supplement = parsed
        official = bank in tables
        kit_names = set(localized._kit_files(kit, bank)) if official else set()
        game_rows = localized.game_streams(path)
        game_names = unique_names(game_rows)
        if scope == "naval":
            translated = set(naval_texts.get(bank, {}))
            native = False
            text_status = "bundled_first_pass"
        elif language == "zh":
            translated = game_names
            native = True
            text_status = "native"
        elif catalog_ready.get(scope):
            translated = set((catalog.get(scope) or {}))
            translated.update(gaps.get(bank, {}))
            native = False
            text_status = "asr_plus_gap"
        else:
            translated = set(gaps.get(bank, {}))
            native = False
            text_status = "catalog_missing"
        covered = game_names & translated if not native else set(game_names)
        missing = sorted(game_names - translated) if not native else []
        known_text = text_status != "catalog_missing"
        sample = sample_extract(path, game_rows[0]["index"] if game_rows else None)
        rows.append(
            {
                "bank": bank,
                "scope": scope,
                "language": language,
                "supplemental": supplement,
                "official": official,
                "kit_events": len(kit_names),
                "game_events": len(game_names),
                "game_streams": len(game_rows),
                "game_only": len(game_names - kit_names) if official else len(game_names),
                "kit_only": len(kit_names - game_names) if official else 0,
                "zh_banks": ",".join(zh_banks.get(scope, [])),
                "text_covered": len(covered),
                "text_missing": len(missing) if known_text else None,
                "gap_covered": sum(1 for name in game_names if name in gaps.get(bank, {})),
                "text_status": text_status,
                "text_native": native,
                "sample_ok": sample["ok"],
                "sample_detail": sample["detail"],
                "missing_sample": missing[:20],
            }
        )
        print(
            f"[{position}/{len(files)}] {bank}: 工程 {len(kit_names)} / 游戏 {len(game_names)} / "
            f"文本 {text_status} / 缺 {'' if not known_text else len(missing)} / "
            f"抽样 {'OK' if sample['ok'] else '失败'}",
            flush=True,
        )

    counts = {
        "banks": len(rows),
        "official": sum(1 for row in rows if row["official"]),
        "dynamic": sum(1 for row in rows if not row["official"]),
        "common": sum(1 for row in rows if row["scope"] == "common"),
        "ground": sum(1 for row in rows if row["scope"] == "ground"),
        "naval": sum(1 for row in rows if row["scope"] == "naval"),
        "game_events": sum(row["game_events"] for row in rows),
        "game_streams": sum(row["game_streams"] for row in rows),
        "text_missing": sum(
            row["text_missing"] for row in rows if row["text_missing"] is not None
        ),
        "text_unknown_banks": sum(
            1 for row in rows if row["text_missing"] is None
        ),
        "sample_ok": sum(1 for row in rows if row["sample_ok"]),
        "sample_failed": sum(1 for row in rows if not row["sample_ok"]),
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": str(game),
        "kit": str(kit),
        "kit_tables": len(tables),
        "catalog_ready": catalog_ready,
        "elapsed_sec": round(time.time() - started, 1),
        "counts": counts,
        "banks": rows,
    }
    (out / "语音表审计.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = list(rows[0].keys()) if rows else []
    with (out / "语音表审计.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})

    lines = [
        "# 乘员语音表审计",
        "",
        f"- 时间：{payload['generated_at']}",
        f"- 游戏：`{game}`",
        f"- 工程：`{kit}`",
        f"- 工程 AudioTable：{len(tables)} 张",
        f"- 游戏乘员表：{counts['banks']} 张（通用 {counts['common']} / 陆战 {counts['ground']} / 海战 {counts['naval']}）",
        f"- 官方工程表：{counts['official']} 张；游戏独有：{counts['dynamic']} 张",
        f"- 游戏事件：{counts['game_events']} 个；流：{counts['game_streams']} 条",
        f"- 中文文本缺口：{counts['text_missing']} 个事件；"
        f"另有 {counts['text_unknown_banks']} 张表尚未生成听写缓存（状态标为待听写）",
        f"- 抽样解包：成功 {counts['sample_ok']} / 失败 {counts['sample_failed']}",
        "",
        "## 每张表",
        "",
        "| bank | 类型 | 工程事件 | 游戏事件 | 流 | 文本状态 | 缺文本 | 抽样 |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for row in rows:
        kind = "官方" if row["official"] else "游戏独有"
        missing = "待听写" if row["text_missing"] is None else row["text_missing"]
        lines.append(
            f"| `{row['bank']}` | {kind} | {row['kit_events']} | {row['game_events']} | "
            f"{row['game_streams']} | {row['text_status']} | {missing} | "
            f"{'OK' if row['sample_ok'] else '失败'} |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- 中文文本覆盖按当前缓存里的官方中文原音听写（common/ground）和缺口译文统计；"
        "没有听写缓存时标为 `catalog_missing`，不代表缺译文，首次制作会自动生成。"
        "海战使用内置首轮译法，仍需听原音校对。",
        "- 抽样只验证 vgmstream 能解出第一段流的合法 WAV，不代表游戏内播放和音量正确。",
        "- 工程事件数是官方 FMOD 工程里该 bank 的流名数；游戏独有表没有工程元数据。",
    ]
    (out / "语音表审计.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
