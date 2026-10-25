"""构建前准备 vgmstream：下载官方 r2117、校验 SHA-256、解压到 vendor。

用法（项目根目录）：
    py -3.13 packaging/fetch_vgmstream.py
    py -3.13 packaging/fetch_vgmstream.py --dest vendor/vgmstream --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import vgmstream  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="下载并校验 vgmstream 运行时")
    parser.add_argument("--dest", default=str(ROOT / "vendor" / "vgmstream"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dest = Path(args.dest)
    exe = dest / vgmstream.EXE_NAME
    if exe.is_file() and not args.force:
        print(f"已存在：{exe}")
        return 0

    last = {"bucket": -1}

    def progress(stage: str, current: int, total: int | None, message: str) -> None:
        if stage == "download" and total:
            bucket = int(current * 100 / total) // 5
            if bucket != last["bucket"]:
                last["bucket"] = bucket
                print(f"  {message}：{current * 100 / total:.0f}%")
        elif stage in ("verify", "extract"):
            print(f"  {message}")

    print(f"下载 {vgmstream.URL}")
    result = vgmstream.install_to(dest, progress=progress, force=args.force)
    version = vgmstream.version(result)
    if not version:
        print("错误：解压后无法运行 vgmstream-cli.exe", file=sys.stderr)
        return 1
    print(f"OK: {result}")
    print(f"版本: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
