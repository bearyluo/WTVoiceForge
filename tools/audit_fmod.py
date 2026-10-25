"""FMOD 工具链与 bank 头兼容性审计。

只读：比较本地 FMOD Studio CLI、官方工程标注版本、工程序列化版本，以及
游戏 bank 与应用构建 bank 的 FEV 头字段。游戏内兼容性仍需实机试听。
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def file_version(path: Path) -> dict:
    if os.name != "nt" or not path.is_file():
        return {}
    escaped = str(path).replace("'", "''")
    script = (
        f"$v=(Get-Item -LiteralPath '{escaped}').VersionInfo;"
        "[pscustomobject]@{FileVersion=$v.FileVersion;ProductVersion=$v.ProductVersion}"
        " | ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return json.loads(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return {}


def bank_header(path: Path) -> dict:
    try:
        head = path.read_bytes()[:28]
    except OSError:
        return {}
    if head[:4] != b"RIFF" or head[8:12] != b"FEV ":
        return {"ok": False, "head": head.hex()}
    return {
        "ok": True,
        "head": head.hex(),
        "fev_version": int.from_bytes(head[20:24], "little"),
        "fev_build": int.from_bytes(head[24:28], "little"),
    }


def kit_version(kit: Path) -> dict:
    result: dict = {}
    readme = kit / "Readme.md"
    if readme.is_file():
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
            match = re.search(
                r"(?:Actual Fmod Studio version|Актуальная версия Fmod Studio)\s*[-–:]?\s*([0-9.]+)",
                text,
                re.IGNORECASE,
            )
            if match:
                result["readme_version"] = match.group(1)
        except OSError:
            pass
    fspro = kit / "sounds.fspro"
    if fspro.is_file():
        try:
            head = fspro.read_text(encoding="utf-8", errors="replace")[:1000]
            match = re.search(r'serializationModel="Studio\.([0-9.]+)"', head)
            if match:
                result["serialization_model"] = match.group(1)
        except OSError:
            pass
    return result


def latest_built_bank(data: Path) -> dict:
    patterns = [
        str(data / "voice_projects" / "*" / "exports" / "*.wtvoice"),
        str(data / "语音包导出" / "*.wtvoice"),
    ]
    files = [item for pattern in patterns for item in glob.glob(pattern)]
    if not files:
        return {}
    latest = max(files, key=os.path.getmtime)
    try:
        with zipfile.ZipFile(latest) as archive:
            for name in archive.namelist():
                if not name.endswith(".bank"):
                    continue
                with archive.open(name) as stream:
                    head = stream.read(28)
                if head[:4] != b"RIFF" or head[8:12] != b"FEV ":
                    continue
                return {
                    "package": latest,
                    "bank": name,
                    "fev_version": int.from_bytes(head[20:24], "little"),
                    "fev_build": int.from_bytes(head[24:28], "little"),
                    "head": head.hex(),
                }
    except (OSError, zipfile.BadZipFile):
        return {}
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", required=True)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--data", default="")
    parser.add_argument("--cli", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    game = Path(args.game)
    kit = Path(args.kit)
    data = Path(args.data) if args.data else ROOT / "data"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cli = Path(args.cli) if args.cli else next(
        (
            path
            for path in (
                Path(r"C:\Program Files\FMOD SoundSystem\FMOD Studio 2.02.28\fmodstudiocl.exe"),
                Path(r"C:\Program Files\FMOD SoundSystem\FMOD Studio 2.02.28\fmodstudio.exe"),
            )
            if path.is_file()
        ),
        None,
    )
    game_headers: list[dict] = []
    versions: Counter = Counter()
    for path in sorted((game / "sound").glob("*.bank")):
        header = bank_header(path)
        if not header.get("ok"):
            continue
        game_headers.append({"bank": path.name, **header})
        versions[(header["fev_version"], header["fev_build"])] += 1
    built = latest_built_bank(data) if data else {}
    kit_info = kit_version(kit)
    cli_info = file_version(cli) if cli else {}
    cli_version = (
        cli_info.get("ProductVersion")
        or cli_info.get("FileVersion")
        or ""
    )
    if not cli_version and cli:
        folder = re.search(r"FMOD Studio\s+([0-9]+\.[0-9]+\.[0-9]+)", str(cli.parent))
        if folder:
            cli_version = folder.group(1)
            cli_info["version_source"] = "安装目录名（exe 无版本资源）"
    cli_major_minor = ".".join(str(cli_version).split(".")[:2]) if cli_version else ""
    kit_doc = kit_info.get("readme_version", "")
    kit_major_minor = ".".join(str(kit_doc).split(".")[:2]) if kit_doc else ""
    match_kit = bool(
        cli_major_minor and kit_major_minor and cli_major_minor == kit_major_minor
    )
    match_headers = bool(
        built
        and all(
            header["fev_version"] == built["fev_version"]
            and header["fev_build"] == built["fev_build"]
            for header in game_headers
        )
    )

    payload = {
        "cli": {
            "path": str(cli) if cli else "",
            "version": cli_version,
            "major_minor": cli_major_minor,
            **cli_info,
        },
        "kit": {"path": str(kit), **kit_info},
        "kit_doc_version": kit_doc,
        "version_match": match_kit,
        "game_bank_headers": {
            "count": len(game_headers),
            "formats": [
                {"fev_version": version, "fev_build": build, "banks": count}
                for (version, build), count in versions.most_common()
            ],
            "samples": game_headers[:5],
        },
        "built_bank": built,
        "header_match_all_game_banks": match_headers,
        "in_game_verified": False,
        "verdict": (
            "本地 CLI 与工程标注同一 2.02 系列，且应用构建 bank 的 FEV 头与游戏 bank 一致；"
            "但未在游戏内实机加载验证。"
            if match_kit and match_headers
            else "版本或 bank 头存在差异，需要先实机验证再发布。"
        ),
    }
    (out / "FMOD兼容审计.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# FMOD 兼容性审计",
        "",
        f"- 本地 CLI：`{cli or '未找到'}`",
        f"- CLI 版本：{cli_version or '未知'}（主次版本 {cli_major_minor or '未知'}）",
        f"- 工程标注：{kit_doc or 'Readme 未找到'}；序列化模型：{kit_info.get('serialization_model', '未知')}",
        f"- 与工程主次版本一致：{'是' if match_kit else '否'}",
        f"- 游戏 bank 样本：{len(game_headers)} 个；FEV 格式分布："
        + "、".join(
            f"0x{version:02X}/0x{build:02X} × {count}"
            for (version, build), count in versions.most_common()
        ),
        "",
    ]
    if built:
        lines += [
            f"- 最近构建包：`{built.get('package')}`",
            f"- 构建 bank：`{built.get('bank')}`",
            f"- 构建 FEV：0x{built.get('fev_version', 0):02X}/0x{built.get('fev_build', 0):02X}",
            f"- 与全部游戏 bank 头一致：{'是' if match_headers else '否'}",
        ]
    lines += [
        "",
        "## 结论",
        "",
        payload["verdict"],
        "",
        "## 仍需实机确认",
        "",
        "1. 关闭游戏后安装包含乘员表/座舱表的测试包。",
        "2. 进入一局空战/陆战/海战，确认语音触发、音量、无爆音和 no mod 提示。",
        "3. 记录游戏版本号；游戏更新后重新跑本审计。",
        "",
        "工具链同属 2.02 并不等于游戏一定接受；只有游戏内加载和实听能作为发布依据。",
    ]
    (out / "FMOD兼容审计.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload["verdict"], ensure_ascii=False))
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
