"""RVC 训练桥。

由主程序用 RVC 整合包自带 Python 执行：
1. 切片/重采样
2. 提取 F0 和 HuBERT 特征
3. 训练 v2 32k 模型
4. 构建检索索引
5. 把模型和索引复制到声音项目目录
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def run(cmd, cwd, stage):
    emit({"type": "stage", "stage": stage})
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    tail = []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-30:]
        print(line, flush=True)
    code = process.wait()
    if code != 0:
        raise RuntimeError("%s 失败（退出码 %s）：%s" % (stage, code, " / ".join(tail[-5:])))
    return tail


def build_index(exp_dir):
    import faiss
    import numpy as np

    feature_dir = exp_dir / "3_feature768"
    files = sorted(feature_dir.glob("*.npy"))
    if not files:
        raise RuntimeError("没有 HuBERT 特征，无法构建索引")
    chunks = [np.load(str(path)) for path in files]
    big = np.concatenate(chunks, 0)
    np.save(str(exp_dir / "total_fea.npy"), big)
    n_ivf = min(int(16 * np.sqrt(big.shape[0])), max(1, big.shape[0] // 39))
    index = faiss.index_factory(768, "IVF%s,Flat" % n_ivf)
    index_ivf = faiss.extract_index_ivf(index)
    index_ivf.nprobe = 1
    index.train(big)
    index.add(big)
    name = "added_IVF%s_Flat_nprobe_1_%s_v2.index" % (n_ivf, exp_dir.name)
    target = exp_dir / name
    faiss.write_index(index, str(target))
    return target


def main():
    if len(sys.argv) < 2:
        raise SystemExit("missing config")
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(cfg["root"]).resolve()
    os.chdir(root)
    dataset = Path(cfg["dataset"]).resolve()
    exp_name = str(cfg["exp"]).strip()
    exp_dir = root / "logs" / exp_name
    model_out = Path(cfg["model_out"]).resolve()
    index_out = Path(cfg["index_out"]).resolve()
    epochs = int(cfg.get("epochs", 40))
    batch_size = int(cfg.get("batch_size", 4))
    sample_rate = str(cfg.get("sample_rate", "32k"))
    save_every = int(cfg.get("save_every", 10))
    python = sys.executable
    started = time.time()

    if not dataset.is_dir():
        raise RuntimeError("训练素材目录不存在：%s" % dataset)
    if not any(dataset.glob("*.wav")):
        raise RuntimeError("训练素材目录里没有 WAV")
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / "preprocess.log").write_text("", encoding="utf-8")
    (exp_dir / "extract_f0_feature.log").write_text("", encoding="utf-8")

    emit({"type": "stage", "stage": "preprocess"})
    run(
        [
            python,
            "infer/modules/train/preprocess.py",
            str(dataset),
            "32000",
            "4",
            str(exp_dir),
            "False",
            "3.7",
        ],
        root,
        "数据切分",
    )
    emit({"type": "stage", "stage": "f0"})
    run(
        [
            python,
            "infer/modules/train/extract/extract_f0_rmvpe.py",
            "1",
            "0",
            "0",
            str(exp_dir),
            "True",
        ],
        root,
        "F0 提取",
    )
    emit({"type": "stage", "stage": "feature"})
    run(
        [
            python,
            "infer/modules/train/extract_feature_print.py",
            "cuda",
            "1",
            "0",
            "0",
            str(exp_dir),
            "v2",
            "True",
        ],
        root,
        "HuBERT 特征提取",
    )

    def names(folder):
        return {path.name.split(".")[0] for path in (exp_dir / folder).glob("*")}

    common = (
        names("0_gt_wavs")
        & names("3_feature768")
        & names("2a_f0")
        & names("2b-f0nsf")
    )
    if not common:
        raise RuntimeError("数据切分和特征提取没有生成可训练的片段")
    lines = []
    for name in sorted(common):
        lines.append(
            "%s/0_gt_wavs/%s.wav|%s/3_feature768/%s.npy|%s/2a_f0/%s.wav.npy|%s/2b-f0nsf/%s.wav.npy|0"
            % (exp_dir, name, exp_dir, name, exp_dir, name, exp_dir, name)
        )
    for _ in range(2):
        lines.append(
            "%s/logs/mute/0_gt_wavs/mute32k.wav|%s/logs/mute/3_feature768/mute.npy|%s/logs/mute/2a_f0/mute.wav.npy|%s/logs/mute/2b-f0nsf/mute.wav.npy|0"
            % (root, root, root, root)
        )
    (exp_dir / "filelist.txt").write_text("\n".join(lines), encoding="utf-8")
    config_src = root / "configs" / "inuse" / "v2" / "32k.json"
    if not config_src.is_file():
        source = root / "configs" / "v2" / "32k.json"
        config_src.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, config_src)
    (exp_dir / "config.json").write_text(
        config_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    emit({"type": "stage", "stage": "train", "segments": len(common), "epochs": epochs})

    run(
        [
            python,
            "infer/modules/train/train.py",
            "-e",
            exp_name,
            "-sr",
            sample_rate,
            "-f0",
            "1",
            "-bs",
            str(batch_size),
            "-g",
            "0",
            "-te",
            str(epochs),
            "-se",
            str(save_every),
            "-pg",
            "assets/pretrained_v2/f0G32k.pth",
            "-pd",
            "assets/pretrained_v2/f0D32k.pth",
            "-l",
            "1",
            "-c",
            "0",
            "-sw",
            "1",
            "-v",
            "v2",
        ],
        root,
        "模型训练",
    )

    weight = root / "assets" / "weights" / (exp_name + ".pth")
    if not weight.is_file():
        candidates = sorted(
            (root / "assets" / "weights").glob(exp_name + "*.pth"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        weight = candidates[0] if candidates else None
    if not weight or not weight.is_file():
        raise RuntimeError("训练结束，但没有找到模型文件")
    model_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(weight, model_out)

    emit({"type": "stage", "stage": "index"})
    index = build_index(exp_dir)
    index_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(index, index_out)
    result = {
        "type": "done",
        "model": str(model_out),
        "index": str(index_out),
        "exp": exp_name,
        "segments": len(common),
        "seconds": round(time.time() - started, 1),
    }
    (model_out.parent / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    emit(result)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        emit({"type": "error", "error": traceback.format_exc()})
        raise
