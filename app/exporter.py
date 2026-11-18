"""用 FMOD Studio CLI 构建语音包。

只读取官方工程；每次构建都复制元数据到项目目录，素材按目标 bank 复制/替换，
不修改游戏目录和官方工程。产出 `.wtvoice`（zip：manifest + banks + 安装说明）。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app import (
    catalog,
    cockpit,
    config,
    dynamic_bank,
    event_override,
    fmod_kit,
    kit_compat,
    localized,
    voice,
)

PLATFORM = "Desktop"
BANK_SUFFIXES = (".bank", ".assets.bank", ".strings.bank")


class ExportError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cli() -> dict | None:
    tools = fmod_kit.find_fmod_studio(config.load_config().fmod_studio_path)
    return tools[0] if tools else None


def _catalog(data_dir: str | Path) -> dict | None:
    data = catalog.store.snapshot()
    if data:
        return data
    data = catalog.load_full_catalog(data_dir)
    if data:
        return data
    fallback = config.load_config().data_dir
    if fallback and str(fallback) != str(data_dir):
        return catalog.load_full_catalog(fallback)
    return None


def readiness(project: dict) -> dict:
    banks: dict[str, int] = {}
    ready = 0
    unbound = 0
    for line in project.get("lines", []):
        if not line.get("audio"):
            continue
        ready += 1
        bank = str(line.get("source", {}).get("bank") or "").strip()
        if not bank:
            unbound += 1
            continue
        banks[bank] = banks.get(bank, 0) + 1
    cli = _cli()
    cfg = config.load_config()
    kit_ready = bool(
        cfg.kit_path and (Path(cfg.kit_path).expanduser() / "sounds.fspro").is_file()
    )
    return {
        "ready_lines": ready,
        "unbound_lines": unbound,
        "banks": [{"name": name, "lines": count} for name, count in sorted(banks.items())],
        "fmod": cli,
        "kit_ready": kit_ready,
        "ready": bool(cli and banks and kit_ready),
        "reason": (
            "还没有合成台词"
            if not ready
            else "这些台词没有绑定游戏事件，无法构建"
            if not banks
            else "没有检测到 FMOD Studio CLI"
            if not cli
            else "请先选择官方 FMOD 工程"
            if not kit_ready
            else ""
        ),
    }


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", name).strip(" ._")
    return cleaned[:48] or "voice_pack"


def _copy_tree(src: Path, dst: Path) -> None:
    os.environ.setdefault("MSYS_NO_PATHCONV", "1")
    command = [
        "robocopy",
        str(src),
        str(dst),
        "/E",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/R:1",
        "/W:1",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode >= 8:
        detail = (completed.stdout or b"").decode("utf-8", errors="replace")[-400:]
        raise ExportError(f"复制工程元数据失败：{detail}")


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        process.kill()


def _safe_rel(rel: str) -> str:
    """素材路径必须落在工程 Assets 目录内，拒绝绝对路径和 `..`。"""
    text = str(rel or "").replace("\\", "/").strip()
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or ":" in parts[0] or any(part == ".." for part in parts):
        raise ExportError(f"素材路径不合法：{rel}")
    return "/".join(parts)


def _tree_stamp(root: Path) -> dict:
    """目录内容签名：文件数 + 最新修改时间，用于发现官方工程增删/更新。"""
    count = 0
    latest = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                try:
                    stat = os.stat(os.path.join(dirpath, name), follow_symlinks=False)
                except OSError:
                    continue
                count += 1
                latest = max(latest, stat.st_mtime_ns)
    except OSError:
        pass
    return {"files": count, "mtime_ns": latest}


def _cache_stamp(kit: Path) -> dict:
    try:
        stat = (kit / "sounds.fspro").stat()
        return {
            "kit": str(kit.resolve()).lower(),
            "fspro": [stat.st_size, int(stat.st_mtime)],
            "metadata": _tree_stamp(kit / "Metadata"),
        }
    except OSError:
        return {}


def _cache_ok(base: Path, kit: Path) -> bool:
    if not (base / "Metadata").is_dir():
        return False
    try:
        saved = json.loads((base / ".ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return saved == _cache_stamp(kit)


def _bank_assets(data: dict, banks: set[str]) -> set[str]:
    assets = data.get("assets", {})
    paths: set[str] = set()
    for event in data.get("events", []):
        if event.get("bank") not in banks:
            continue
        for guid in event.get("asset_guids", []):
            asset = assets.get(guid)
            if asset and asset.get("path"):
                paths.add(_safe_rel(asset["path"]))
    return paths


def build(job, data_dir: str | Path, project_id: str) -> dict:
    from app import jobs

    project = voice.load_project(data_dir, project_id)
    cli = _cli()
    if not cli:
        raise ExportError("没有检测到 FMOD Studio CLI，请先安装 FMOD Studio")
    cfg = config.load_config()
    kit = Path(cfg.kit_path).expanduser().resolve() if cfg.kit_path else None
    if not kit or not (kit / "sounds.fspro").is_file():
        raise ExportError("请先选择官方 FMOD 工程")
    data = _catalog(data_dir)
    if not data:
        raise ExportError("事件目录尚未生成，请先在游戏素材页生成目录")
    # 构建会改事件引用/资产登记，用副本隔离，避免污染内存缓存与磁盘目录。
    data = copy.deepcopy(data)

    lines = [line for line in project.get("lines", []) if line.get("audio")]
    if not lines:
        raise ExportError("还没有已合成的台词")
    banks: set[str] = set()
    mapping: dict[str, dict] = {}
    skipped = 0
    for line in lines:
        bank = str(line.get("source", {}).get("bank") or "").strip()
        if not bank:
            skipped += 1
            continue
        banks.add(bank)
        audio = voice.line_audio_path(data_dir, project_id, line["id"]).resolve()
        if not audio.is_file():
            raise ExportError(f"合成音频不存在：{line.get('text', '')[:20]}")
        assets = [item.get("path") for item in line.get("source", {}).get("assets", []) if item.get("path")]
        if not assets and line.get("source", {}).get("path"):
            assets = [line["source"]["path"]]
        if not assets:
            raise ExportError(f"台词没有绑定素材：{line.get('text', '')[:20]}")
        plane_kill = event_override.is_target_source(line.get("source") or {})
        swapped = False
        for rel in assets:
            rel = _safe_rel(rel)
            origin = ""
            key = rel
            if plane_kill and rel == event_override.TARGET_ASSET:
                # 目标音与 obj_complete 共用素材：构建时换成隔离路径，
                # 同一包里的普通 obj_complete 仍走原路径，互不覆盖。
                origin = rel
                key = event_override.ISOLATED_ASSET
                swapped = True
            existing = mapping.get(key)
            if existing and existing["line"] != line["id"]:
                raise ExportError(
                    f"多个事件共用同一素材，无法同时替换：{key}（{existing['text'][:12]} / {line.get('text', '')[:12]}）"
                )
            entry = {"line": line["id"], "text": line.get("text", ""), "audio": audio}
            if origin:
                entry["origin"] = origin
                entry["source_event"] = event_override.TARGET_EVENT
            mapping[key] = entry
        if plane_kill and not swapped:
            raise ExportError(
                "空战击杀提示音的源素材不是 gui/obj_complete.wav，"
                "无法隔离共享音效；请重新生成事件目录"
            )
    if not banks:
        raise ExportError("没有可构建的台词：请从游戏素材按事件添加并合成")

    official_tables = localized.tables(kit)
    dynamic_banks = sorted(
        bank
        for bank in banks
        if bank not in official_tables and dynamic_bank.is_crew_bank(bank)
    )
    game_path = cfg.game_path or None
    if dynamic_banks and not game_path:
        raise ExportError(
            "这些语音表需要从游戏 bank 生成，请先在工作台选择《战争雷霆》目录"
        )

    folder = voice.project_dir(data_dir, project_id).resolve()
    build_root = (folder / "build").resolve()
    base = (build_root / "base").resolve()
    assets_dir = (build_root / "assets").resolve()
    export_dir = (folder / "exports").resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    for stale in (assets_dir, base / "Build", base / "Assets"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    if not _cache_ok(base, kit):
        job.update(
            stage="project",
            message="复制 FMOD 工程元数据（首次较慢，约 230 MB）",
            percent=3,
        )
        for stale in (base / "Metadata", base / "Mixing Desk", base / "Scripts"):
            if stale.exists():
                shutil.rmtree(stale, ignore_errors=True)
        marker = base / ".ready.json"
        try:
            marker.unlink()
        except OSError:
            pass
        base.mkdir(parents=True, exist_ok=True)
        shutil.copy2(kit / "sounds.fspro", base / "sounds.fspro")
        _copy_tree(kit / "Metadata", base / "Metadata")
        if (kit / "Mixing Desk").is_dir():
            _copy_tree(kit / "Mixing Desk", base / "Mixing Desk")
        if (kit / "Scripts").is_dir():
            _copy_tree(kit / "Scripts", base / "Scripts")
        stamp = _cache_stamp(kit)
        if not stamp:
            raise ExportError("官方 FMOD 工程缺少 sounds.fspro")
        marker.write_text(json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
    if dynamic_banks:
        job.update(stage="project", message="为游戏独有语音表补 AudioTable", percent=10)
        for bank in dynamic_banks:
            jobs.check_cancel(job)
            try:
                dynamic_bank.ensure_metadata(base, bank)
            except (OSError, dynamic_bank.DynamicBankError) as exc:
                raise ExportError(f"无法生成 {dynamic_bank.label(bank)} 的 AudioTable：{exc}") from exc
    compat = {"restored": [], "error": ""}
    isolation = {"applied": False, "original_guid": "", "cloned_guid": ""}
    if cockpit.BANK in banks:
        # 上一次构建可能改过事件长度；先恢复官方 XML，再按本次音频重写。
        cockpit.reset_event_metadata(base, kit, data)
        # 旧版官方工程没有 enemy_kill_assist；不补回会丢掉助攻提示音。
        try:
            compat = kit_compat.restore_aircraft_gui(base, kit, assets_dir, game_path)
        except OSError as exc:
            compat = {"restored": [], "error": f"助攻提示音补回失败：{exc}"}
        if compat.get("restored"):
            job.update(message="补回官方工程缺失的助攻提示音 ui_kill_assist")
        elif compat.get("error"):
            job.update(message=str(compat["error"])[:110])
        if event_override.ISOLATED_ASSET in mapping:
            # enemy_destr_by_plane 与 obj_complete 共用原音；只给目标事件克隆
            # 独立 AudioFile，obj_complete 仍指向原 GUID/原素材。
            try:
                isolation = event_override.apply(data, base, assets_dir, kit, mapping)
            except (OSError, RuntimeError) as exc:
                raise ExportError(
                    f"无法隔离空战击杀提示音的共享素材：{exc}"
                ) from exc
            job.update(message="已隔离空战击杀提示音素材，obj_complete 保持原音")
    jobs.check_cancel(job)

    job.update(stage="assets", message="收集目标 bank 的素材", percent=16)
    kit_assets = kit / "Assets"
    needed = _bank_assets(data, banks | {"masterbank"})
    table_banks = {
        name: source
        for name, source in official_tables.items()
        if name in banks
    }
    for source in table_banks.values():
        root = kit_assets / source
        if not root.is_dir():
            raise ExportError(f"本地化语音表的素材目录不存在：Assets/{source}")
        for item in root.rglob("*"):
            if item.is_file() and item.suffix.lower() in localized.AUDIO_SUFFIXES:
                needed.add(f"{source}/{item.relative_to(root).as_posix()}")
    if not needed and not dynamic_banks:
        raise ExportError("目录里没有这些 bank 的素材信息")
    missing = []
    for rel in sorted(needed):
        target = assets_dir / rel
        if target.is_file():
            # kit_compat / 动态语音表已经放好的原音，不要再按旧目录去工程里找。
            continue
        source = kit_assets / rel
        if not source.is_file():
            missing.append(rel)
            continue
        _link_or_copy(source, target)
    if missing:
        raise ExportError(
            f"官方工程缺少 {len(missing)} 个素材，无法构建（例如 {missing[0]}）；请先补全工程素材"
        )

    if table_banks:
        job.update(stage="assets", message="补齐游戏语音表新增的流…", percent=22)
        game_path = cfg.game_path or None
        for bank in table_banks:
            try:
                items = localized.streams(kit, bank, game_path)
            except localized.LocalizedError as exc:
                raise ExportError(str(exc)) from exc
            targets: dict[int, Path] = {}
            for stream in items:
                if not stream.get("game_only"):
                    continue
                for asset in stream.get("assets", []):
                    target = assets_dir / asset["path"]
                    if target.is_file():
                        continue
                    origin = str(asset.get("origin") or "").strip()
                    if origin:
                        source = kit_assets / origin
                        if not source.is_file():
                            raise ExportError(f"备用原音不存在：{origin}")
                        _link_or_copy(source, target)
                        continue
                    index = asset.get("game_index")
                    if index is None:
                        raise ExportError(
                            f"游戏语音表里有一条工程没有的原音：{stream.get('event')}；"
                            "请先在工作台选择战雷目录，应用会从游戏 bank 补齐"
                        )
                    targets[int(index)] = target
            if targets:
                game_bank = localized.game_bank_path(game_path, bank)
                if not game_bank:
                    raise ExportError(
                        f"构建 {bank} 需要从游戏 bank 补齐 {len(targets)} 条原音；"
                        "请先在工作台选择战雷目录"
                    )
                try:
                    localized.extract_indexes(game_bank, targets)
                except localized.LocalizedError as exc:
                    raise ExportError(str(exc)) from exc
    if dynamic_banks:
        job.update(stage="assets", message="解出游戏独有语音表的原音…", percent=24)
        for bank in dynamic_banks:
            jobs.check_cancel(job)
            try:
                dynamic_bank.extract_originals(
                    assets_dir, bank, game_path, job=job, data_dir=data_dir
                )
            except (OSError, localized.LocalizedError, dynamic_bank.DynamicBankError) as exc:
                raise ExportError(f"无法准备 {dynamic_bank.label(bank)} 的原音：{exc}") from exc
    jobs.check_cancel(job)

    replaced = 0
    for rel, item in mapping.items():
        target = assets_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        shutil.copy2(item["audio"], target)
        replaced += 1
    if cockpit.BANK in banks:
        # 官方事件长度是固定的；中文台词变长时 FMOD 会截断，必须同步改长度。
        adjusted = cockpit.adjust_event_lengths(base, data, mapping)
        adjusted += kit_compat.adjust_kill_assist_length(base, mapping)
        if adjusted == 0 and (
            event_override.ISOLATED_ASSET in mapping
            or any(str(rel).startswith("vws/") for rel in mapping)
        ):
            raise ExportError(
                "找不到这次替换的座舱告警事件，无法调整音频长度；请重新生成事件目录"
            )
        job.update(
            stage="assets",
            message=f"已调整 {adjusted} 个座舱告警事件长度",
            percent=27,
        )
    job.update(
        stage="assets",
        message=f"已替换 {replaced} 个素材，准备构建 {len(banks)} 个 bank",
        percent=28,
    )

    if table_banks:
        # Audio Table bank 不会读取 -shared-audio-source-dir，必须放在工程
        # 自己的 Assets/ 目录下；用硬链接把已替换好的文件镜像过去。
        for source in table_banks.values():
            root = assets_dir / source
            for item in root.rglob("*"):
                if item.is_file():
                    _link_or_copy(
                        item,
                        base / "Assets" / source / item.relative_to(root),
                    )
    if dynamic_banks:
        for bank in dynamic_banks:
            jobs.check_cancel(job)
            dynamic_bank.mirror_to_project(assets_dir, base, bank)

    job.update(stage="build", message="FMOD 构建中…", percent=32)
    command = [
        cli["path"],
        "-build",
        "-banks",
        ",".join(sorted(banks)),
        "-platforms",
        PLATFORM,
        "-shared-audio-source-dir",
        str(assets_dir),
        "-ignore-warnings",
        str(base / "sounds.fspro"),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(base),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: list[str] = []
    assert process.stdout is not None
    for raw in process.stdout:
        if job.cancel_event.is_set():
            _kill(process)
            raise jobs.JobCancelled
        line = raw.decode("utf-8", errors="replace").replace("\r", " ").strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-20:]
        job.update(message=line[:110], percent=min(88, (job.percent or 32) + 0.5))
    code = process.wait()
    if job.cancel_event.is_set():
        raise jobs.JobCancelled
    if code != 0:
        # FMOD 会顺带构建 masterbank；只用局部素材时它可能报错，
        # 只要目标 bank 文件生成就算成功，下面会逐个检查。
        job.update(message="FMOD 报告依赖警告，检查目标 bank…", percent=88)

    built_root = base / "Build" / "sound"
    built: list[Path] = []
    for bank in sorted(banks):
        found = [
            item
            for item in built_root.glob(bank + ".*")
            if item.is_file() and item.name.endswith(BANK_SUFFIXES)
        ]
        names = {item.name for item in found}
        required = {f"{bank}.bank", f"{bank}.assets.bank"}
        if not required.issubset(names):
            missing_names = "、".join(sorted(required - names))
            raise ExportError(
                f"FMOD 没有产出 {missing_names}，请查看构建输出：{' / '.join(tail[-4:])}"
            )
        built.extend(found)
    if not built:
        raise ExportError("FMOD 构建结束，但没有找到 bank 文件")

    job.update(stage="package", message="打包 .wtvoice", percent=92)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    package_name = f"{_safe_name(project.get('name', 'voice'))}_{stamp}.wtvoice"
    package_path = export_dir / package_name
    mapping_rows = []
    for line in lines:
        if line.get("source", {}).get("bank"):
            mapping_rows.append(
                {
                    "event_id": line.get("event_id", ""),
                    "event": line.get("event", ""),
                    "bank": line.get("source", {}).get("bank", ""),
                    "source_file": line.get("source", {}).get("file", ""),
                    "target_file": line.get("target_file", ""),
                    "text": line.get("text", ""),
                    "audio": line.get("audio", ""),
                }
            )
    manifest = {
        "schema": 1,
        "format": "wtvoice",
        "name": project.get("name", ""),
        "project": project.get("id", ""),
        "created_at": _now(),
        "game": "War Thunder",
        "platform": PLATFORM,
        "banks": [item.name for item in built],
        "dynamic_banks": dynamic_banks,
        "cockpit": cockpit.BANK in banks,
        "compat": compat,
        "isolation": isolation,
        "lines": len(mapping_rows),
        "skipped_unbound": skipped,
        "mapping": mapping_rows,
        "install": {
            "target": "War Thunder/sound/mod",
            "layout": "sound/mod",
            "enable_mod": "enable_mod:b=yes",
            "fmod_sound_enable": "fmod_sound_enable:b=yes",
            "client_side_only": True,
        },
        "config": {
            "enable_mod": True,
            "fmod_sound_enable": True,
        },
    }
    readme = (
        "WT Voice Forge 语音包\n"
        "================================\n\n"
        "安装（与官方社区步骤一致）：\n"
        "1. 关闭游戏。\n"
        "2. 把包里的 sound 文件夹合并到战雷根目录，最终文件位于：\n"
        "   <战雷目录>\\sound\\mod\\\n"
        "   也可以把本包直接拖进 WT Voice Forge 的“语音包”页自动安装。\n"
        "3. 确认 <战雷目录>\\config.blk 的 sound 段包含：\n"
        "   enable_mod:b=yes\n"
        "   fmod_sound_enable:b=yes\n"
        "4. 启动游戏；左上角显示“已启用自定义音效”即表示 mod 已加载。\n"
        "5. 如果这是乘员语音包：游戏设置 → 声音 → 成员组语言，\n"
        "   按语音包说明选择“游戏选项”或“成员组国籍”。\n"
        "6. 如果包含 aircraft_gui：这是座舱告警音效包，\n"
        "   安装后无需改成员组语言；未选中的告警、雷达/失速/界面等音效保留原音。\n\n"
        "还原：删除 sound\\mod 里对应 bank，或把 enable_mod 改回 no。\n"
        "这是客户端自定义音效，不修改游戏内存，不涉及反作弊。\n"
        "只替换包内列出的 bank；未列出的声音保持原样。\n"
        "发布前请确认音色授权和素材版权。\n"
    )
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("README.txt", readme)
        for item in built:
            archive.write(item, f"sound/mod/{item.name}")
    voice.set_export(
        data_dir,
        project_id,
        name=package_name,
        banks=sorted(banks),
        files=[item.name for item in built],
        lines=len(mapping_rows),
        bytes=package_path.stat().st_size,
    )
    job.update(stage="done", message="语音包已生成", percent=100)
    return {
        "export": str(package_path),
        "name": package_name,
        "banks": sorted(banks),
        "files": [item.name for item in built],
        "lines": len(mapping_rows),
        "skipped_unbound": skipped,
        "compat": compat,
        "isolation": isolation,
    }
