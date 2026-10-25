"""生成 RVC 跨语言 40 轮人耳 A/B 试听材料。

每轮：游戏原音 + 同一 RVC 换声模型输出。只生成材料，不代替人耳结论。
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import descriptions, jobs, localized  # noqa: E402
from app.engines import rvc  # noqa: E402

LANG_BANKS = {
    "en": (
        ("_crew_dialogs_common_en", 4),
        ("_crew_dialogs_ground_en", 3),
        ("_crew_dialogs_naval_en", 3),
    ),
    "ru": (
        ("_crew_dialogs_common_ru", 4),
        ("_crew_dialogs_ground_ru", 3),
        ("_crew_dialogs_naval_ru", 3),
    ),
    "de": (
        ("_crew_dialogs_common_de", 4),
        ("_crew_dialogs_ground_de", 3),
        ("_crew_dialogs_naval_de", 3),
    ),
    "zh": (
        ("_crew_dialogs_common_zh", 5),
        ("_crew_dialogs_ground_zh", 5),
    ),
}
LANG_LABELS = {"en": "英语", "ru": "俄语", "de": "德语", "zh": "中文"}


def safe(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "")).strip("_") or "clip"


def pick(bank: str, game: Path, count: int, seed: str) -> list[dict]:
    path = localized.game_bank_path(game, bank)
    if not path:
        raise SystemExit(f"游戏里找不到 {bank}")
    rows = localized.game_streams(path)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        name = str(row.get("name") or "")
        duration = float(row.get("duration_sec") or 0)
        if not name or name in grouped:
            continue
        if duration < 0.8 or duration > 8.0:
            continue
        if descriptions.describe_non_voice(name):
            continue
        grouped[name] = [row]
    names = sorted(grouped)
    if len(names) < count:
        raise SystemExit(f"{bank} 只有 {len(names)} 条可用语音，少于 {count}")
    picked = random.Random(f"{seed}:{bank}").sample(names, count)
    return [
        {
            "bank": bank,
            "event": name,
            "index": int(grouped[name][0]["index"]),
            "duration_sec": float(grouped[name][0]["duration_sec"] or 0),
        }
        for name in sorted(picked)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--index", default="")
    parser.add_argument("--root", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--per-language", type=int, default=10)
    args = parser.parse_args(argv)

    game = Path(args.game)
    out = Path(args.out)
    source_dir = out / "原音"
    converted_dir = out / "换声"
    source_dir.mkdir(parents=True, exist_ok=True)
    converted_dir.mkdir(parents=True, exist_ok=True)

    clips: list[dict] = []
    for lang, banks in LANG_BANKS.items():
        remaining = args.per_language
        for bank, wanted in banks:
            if remaining <= 0:
                break
            take = min(wanted, remaining)
            for item in pick(bank, game, take, lang):
                item["language"] = lang
                item["file"] = f"{lang}_{safe(bank)}__{safe(item['event'])}.wav"
                clips.append(item)
                remaining -= 1
    if len(clips) < args.per_language * len(LANG_BANKS):
        raise SystemExit(f"只选了 {len(clips)} 条，少于目标")

    for position, clip in enumerate(clips, 1):
        bank_path = localized.game_bank_path(game, clip["bank"])
        target = source_dir / clip["file"]
        localized.extract_stream(bank_path, clip["index"], target)
        print(f"[{position}/{len(clips)}] 原音 {clip['file']}", flush=True)

    job = jobs.Job("rvc_validation")
    result = rvc.convert(
        job,
        root=args.root or None,
        model=args.model,
        index=args.index or None,
        input_dir=source_dir,
        output_dir=converted_dir,
        pitch=0,
        index_rate=0.75,
        protect=0.33,
        rms_mix_rate=1.0,
        f0_method="rmvpe",
    )
    print(f"RVC：{result}", flush=True)

    rows = []
    for index, clip in enumerate(clips, 1):
        converted = converted_dir / clip["file"]
        if not converted.is_file():
            raise SystemExit(f"RVC 没有输出 {clip['file']}")
        clip["converted"] = f"换声/{clip['file']}"
        clip["source"] = f"原音/{clip['file']}"
        clip["converted_bytes"] = converted.stat().st_size
        rows.append(clip)

    cards = []
    for index, clip in enumerate(rows, 1):
        cards.append(
            f"""<article class="clip" data-index="{index}">
  <header><span class="no">{index:02d}</span>
    <div><strong>{html.escape(clip['event'])}</strong>
      <span class="meta">{LANG_LABELS[clip['language']]} · {html.escape(clip['bank'])} · {clip['duration_sec']:.1f} 秒</span>
    </div>
    <span class="lang">{clip['language'].upper()}</span>
  </header>
  <div class="players">
    <label><span>游戏原音</span><audio controls preload="none" src="{html.escape(clip['source'])}"></audio></label>
    <label><span>RVC 换声</span><audio controls preload="none" src="{html.escape(clip['converted'])}"></audio></label>
  </div>
  <div class="review">
    <label>相似度
      <select data-rate="{index}">
        <option value="">未评</option><option value="5">5 很像</option><option value="4">4</option>
        <option value="3">3 一般</option><option value="2">2</option><option value="1">1 不像</option>
      </select>
    </label>
    <label>自然度
      <select data-natural="{index}">
        <option value="">未评</option><option value="5">5 自然</option><option value="4">4</option>
        <option value="3">3 一般</option><option value="2">2</option><option value="1">1 机械</option>
      </select>
    </label>
    <input data-note="{index}" placeholder="口音/齿音/吞字/语调问题">
  </div>
</article>"""
        )
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RVC 跨语言 40 轮人耳试听</title>
<style>
:root{{--bg:#f6f2e8;--panel:#fffdf8;--line:#ded5c4;--text:#2e332a;--muted:#7a7466;--accent:#6f7652}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 "Segoe UI",system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:24px;margin:0 0 6px}} .sub{{color:var(--muted);margin:0 0 18px}}
.bar{{position:sticky;top:0;background:rgba(246,242,232,.94);backdrop-filter:blur(6px);padding:10px 0;border-bottom:1px solid var(--line);z-index:2;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
button{{border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:6px;padding:7px 12px;cursor:pointer}}
button.ghost{{background:transparent;color:var(--accent)}}
.clip{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}}
.clip header{{display:flex;gap:10px;align-items:flex-start}}
.no{{font:600 12px/1 ui-monospace,Consolas,monospace;color:var(--accent);border:1px solid var(--line);border-radius:5px;padding:6px}}
.clip strong{{display:block;font-size:13px;word-break:break-all}}
.meta{{display:block;color:var(--muted);font-size:12px;margin-top:2px}}
.lang{{margin-left:auto;font-size:11px;border:1px solid var(--line);border-radius:999px;padding:3px 8px;color:var(--muted)}}
.players{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}}
.players label{{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}}
audio{{width:100%;height:34px}}
.review{{display:grid;grid-template-columns:130px 130px 1fr;gap:8px}}
.review label{{font-size:12px;color:var(--muted)}} select,input{{width:100%;border:1px solid var(--line);border-radius:5px;background:#fff;padding:5px;font:inherit}}
@media(max-width:700px){{.players,.review{{grid-template-columns:1fr}}}}
</style></head><body><main>
<h1>RVC 跨语言 40 轮人耳试听</h1>
<p class="sub">同一 RVC 模型、同一参数（pitch 0 / index 0.75 / protect 0.33 / rmvpe），每轮对比游戏原音与换声。共 {len(rows)} 轮。评价只保存在本机浏览器，点“导出评价”下载 JSON。</p>
<div class="bar"><button id="export">导出评价 JSON</button><button class="ghost" id="clear">清空评价</button><span id="count" class="sub" style="margin:0"></span></div>
{''.join(cards)}
</main>
<script>
const KEY='wtvf.rvc40.review';
function load(){{try{{return JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch{{return {{}}}}}}
let data=load();
function save(){{localStorage.setItem(KEY,JSON.stringify(data));updateCount();}}
function updateCount(){{const n=Object.values(data).filter(x=>x.rate||x.natural||x.note).length;document.getElementById('count').textContent=`已评价 ${{n}} / {len(rows)}`;}}
document.querySelectorAll('[data-rate],[data-natural],[data-note]').forEach(node=>{{
  const key=node.dataset.rate?'rate':node.dataset.natural?'natural':'note';
  const index=node.dataset.rate||node.dataset.natural||node.dataset.note;
  const saved=data[index]&&data[index][key]||''; if(saved) node.value=saved;
  node.addEventListener('input',()=>{{data[index]=data[index]||{{}};data[index][key]=node.value;save();}});
}});
document.getElementById('export').addEventListener('click',()=>{{
  const payload={{created_at:new Date().toISOString(),clips:{json.dumps([{i+1:c['event']} for i,c in enumerate(rows)],ensure_ascii=False)},reviews:data}};
  const blob=new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='RVC40_评价.json';a.click();URL.revokeObjectURL(a.href);
}});
document.getElementById('clear').addEventListener('click',()=>{{if(confirm('清空本机评价？')){{data={{}};localStorage.removeItem(KEY);location.reload();}}}});
updateCount();
</script></body></html>"""
    (out / "试听对比.html").write_text(page, encoding="utf-8")
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": str(Path(args.model).resolve()),
        "index": str(Path(args.index).resolve()) if args.index else "",
        "parameters": {
            "pitch": 0,
            "index_rate": 0.75,
            "protect": 0.33,
            "rms_mix_rate": 1.0,
            "f0_method": "rmvpe",
        },
        "clips": rows,
        "human_verdict": "",
        "note": (
            "材料由真实游戏台词生成；相似度、自然度、口音和齿音必须由人耳填写。"
            "ASR 只能发现吞字，不能代替听感。"
        ),
    }
    (out / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "说明.md").write_text(
        f"""# RVC 跨语言 40 轮试听

- 生成时间：{payload['generated_at']}
- 模型：`{payload['model']}`
- 轮数：{len(rows)}（英/俄/德/中各 {args.per_language} 条）
- 参数：pitch 0 / index 0.75 / protect 0.33 / rmvpe
- 原音：`原音/`；换声：`换声/`

打开 `试听对比.html` 逐轮听，评价会保存在浏览器，点“导出评价 JSON”保存。
`report.json` 里的 `human_verdict` 留空，等待人耳结论；不要用 ASR 结果代替。

这些是游戏真实 AudioTable 流名；口音、齿音、吞字和语气需要你亲自判断。
""",
        encoding="utf-8",
    )
    print(f"输出：{out} · {len(rows)} 轮", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
