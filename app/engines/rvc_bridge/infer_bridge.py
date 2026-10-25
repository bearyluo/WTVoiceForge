"""RVC 批量换声桥。

由主程序的 RVC 运行时执行，工作目录必须是 RVC 整合包根目录。
配置通过 JSON 传入，输出逐条 JSON 进度，主程序据此更新任务栏。
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

AUDIO_EXT = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac"}


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("missing config")
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(cfg["root"]).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    # Config 会解析命令行；把参数清掉，避免把 --config 吃掉。
    sys.argv = [sys.argv[0]]

    model = Path(cfg["model"]).resolve()
    if not model.is_file():
        emit({"type": "error", "error": "换声模型不存在：%s" % model})
        raise SystemExit(2)
    input_dir = Path(cfg["input"]).resolve()
    output_dir = Path(cfg["output"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not input_dir.is_dir():
        emit({"type": "error", "error": "待换声目录不存在：%s" % input_dir})
        raise SystemExit(2)

    os.environ["weight_root"] = str(model.parent)
    os.environ["index_root"] = str(root / "logs")
    os.environ["outside_index_root"] = str(root / "assets" / "indices")
    os.environ["rmvpe_root"] = str(root / "assets" / "rmvpe")

    import torch
    from configs.config import Config
    from infer.modules.vc.modules import VC

    config = Config()
    if cfg.get("device") == "cpu" or not torch.cuda.is_available():
        config.device = "cpu"
        config.is_half = False
    else:
        config.device = "cuda:0"
        config.is_half = bool(cfg.get("is_half", True))

    index = str(cfg.get("index") or "").strip()
    index_rate = float(cfg.get("index_rate", 0.75))
    if not index or not Path(index).is_file():
        index = ""
        index_rate = 0.0

    vc = VC(config)
    vc.get_vc(model.name)
    files = sorted(
        path for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_EXT
    )
    total = len(files)
    if not total:
        emit({"type": "error", "error": "待换声目录里没有音频"})
        raise SystemExit(2)

    emit({"type": "stage", "stage": "infer", "total": total})
    import soundfile as sf

    for position, path in enumerate(files, 1):
        info, result = vc.vc_single(
            0,
            str(path),
            int(cfg.get("pitch", 0)),
            None,
            str(cfg.get("f0_method", "rmvpe")),
            index,
            "",
            index_rate,
            int(cfg.get("filter_radius", 3)),
            int(cfg.get("resample_sr", 0)),
            float(cfg.get("rms_mix_rate", 1.0)),
            float(cfg.get("protect", 0.33)),
        )
        if not result or result[0] is None or result[1] is None:
            emit({"type": "error", "file": path.name, "error": str(info)})
            raise SystemExit(2)
        sample_rate, audio = result
        sf.write(str(output_dir / (path.stem + ".wav")), audio, int(sample_rate))
        emit(
            {
                "type": "progress",
                "done": position,
                "total": total,
                "file": path.name,
            }
        )
    emit({"type": "done", "total": total})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        emit({"type": "error", "error": traceback.format_exc()})
        raise
