"""GPT-SoVITS 微调训练：切片参考音频 → 标注 → 特征 → s2/s1 训练 → 切换权重。

只调用整合包自带脚本，不修改包内容；训练数据放在声音项目的 train/ 下。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app import voice
from app.engines import gpt_sovits

MIN_FILES = 3
MIN_SECONDS = 8.0
# 对齐官方 v4 WebUI 默认：SoVITS 2 轮、GPT 15 轮、LoRA rank 32。
# 本机 144 秒素材 A/B 显示短句上 8/15 轮各有胜负，15 轮不是万能药；
# 先保证素材和参考文字质量，再考虑轮数。
S2_EPOCHS = 2
S1_EPOCHS = 15
LORA_RANK = 32
LANGUAGES = {"zh": "ZH", "yue": "YUE", "en": "EN", "ja": "JA", "ko": "KO"}


class TrainError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def readiness(project: dict) -> dict:
    files = project.get("reference", {}).get("files", [])
    texts = project.get("reference", {}).get("texts", {})
    missing = [name for name in files if not str(texts.get(name, "")).strip()]
    return {
        "files": len(files),
        "missing_texts": missing,
        "ready": len(files) >= MIN_FILES,
        "reason": "" if len(files) >= MIN_FILES else f"至少需要 {MIN_FILES} 段参考音频",
    }


def _convert(ffmpeg: Path, source: Path, target: Path, rate: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ar",
        str(rate),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    completed = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode != 0 or not target.is_file():
        detail = (completed.stderr or b"").decode("utf-8", errors="replace")[-300:]
        raise TrainError(f"音频转换失败：{source.name} {detail}")


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


def _clean(line: str) -> str:
    text = line.replace("\r", " ").replace("\x1b", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[-120:]


def _epoch_number(line: str) -> int:
    found = re.search(r"[Ee]poch[:\s]+(\d+)", line)
    return max(0, int(found.group(1))) if found else 0


def _run(
    job,
    command: list[str],
    *,
    root: Path,
    label: str,
    start: float,
    end: float,
    epochs: int = 0,
) -> None:
    from app import jobs

    job.update(stage=label, message=f"{label}…", percent=start)
    process = subprocess.Popen(
        [str(item) for item in command],
        cwd=str(root),
        env=gpt_sovits._env(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: list[str] = []
    progress = start
    assert process.stdout is not None
    for raw in process.stdout:
        if job.cancel_event.is_set():
            _kill(process)
            raise jobs.JobCancelled
        line = _clean(raw.decode("utf-8", errors="replace"))
        if not line:
            continue
        tail.append(line)
        tail = tail[-12:]
        epoch = _epoch_number(line) if epochs else 0
        if epoch:
            progress = max(
                progress,
                start + (end - start) * min(1.0, epoch / epochs),
            )
            job.update(
                message=f"{label}：第 {min(epoch, epochs)}/{epochs} 轮",
                percent=min(end, progress),
            )
        else:
            job.update(message=f"{label}：{line[:80]}", percent=min(end, progress))
    code = process.wait()
    if job.cancel_event.is_set():
        raise jobs.JobCancelled
    if code != 0:
        raise TrainError(f"{label} 失败（退出码 {code}）：{' / '.join(tail[-3:])}")
    job.update(message=f"{label} 完成", percent=end)


def _s1_config(root: Path, opt: Path, exp: str, weights_dir: Path) -> str:
    quote = lambda value: "'" + str(value).replace("\\", "/").replace("'", "''") + "'"
    return f"""train:
  seed: 1234
  epochs: {S1_EPOCHS}
  batch_size: 1
  save_every_n_epoch: 1
  precision: 16-mixed
  gradient_clip: 1.0
  if_save_every_weights: true
  if_save_latest: true
  if_dpo: false
  half_weights_save_dir: {quote(weights_dir)}
  exp_name: {quote(exp)}
optimizer:
  lr: 0.01
  lr_init: 0.00001
  lr_end: 0.0001
  warmup_steps: 2000
  decay_steps: 40000
data:
  max_eval_sample: 8
  max_sec: 54
  num_workers: 2
  pad_val: 1024
model:
  vocab_size: 1025
  phoneme_vocab_size: 732
  embedding_dim: 512
  hidden_dim: 512
  head: 16
  linear_units: 2048
  n_layer: 24
  dropout: 0
  EOS: 1024
  random_bert: 0
inference:
  top_k: 15
pretrained_s1: {quote(root / 'GPT_SoVITS/pretrained_models/s1v3.ckpt')}
train_semantic_path: {quote(opt / '6-name2semantic.tsv')}
train_phoneme_path: {quote(opt / '2-name2text.txt')}
output_dir: {quote(opt / 'logs_s1_v4')}
"""


def train(job, data_dir: str | Path, project_id: str) -> dict:
    from app import jobs

    project = voice.load_project(data_dir, project_id)
    files = project.get("reference", {}).get("files", [])
    if len(files) < MIN_FILES:
        raise TrainError(f"至少需要 {MIN_FILES} 段参考音频，当前 {len(files)} 段")
    root = gpt_sovits.find_install()
    if not root:
        raise TrainError("没有找到 GPT-SoVITS 整合包，请先安装")
    ffmpeg = root / "ffmpeg.exe"
    if not ffmpeg.is_file():
        raise TrainError("整合包里没有 ffmpeg.exe，无法转换参考音频")
    folder = voice.project_dir(data_dir, project_id)
    work = voice.training_dir(data_dir, project_id)
    work.mkdir(parents=True, exist_ok=True)
    for child in work.iterdir():
        if child.name == "model":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    audio32 = work / "audio32k"
    audio16 = work / "asr16"
    audio32.mkdir(parents=True, exist_ok=True)
    audio16.mkdir(parents=True, exist_ok=True)

    language = str(project.get("reference", {}).get("prompt_lang", "zh") or "zh").lower()
    if language not in LANGUAGES:
        raise TrainError(f"训练暂不支持语言：{language}")

    texts = dict(project.get("reference", {}).get("texts", {}) or {})
    names: list[tuple[str, str, Path]] = []
    job.update(stage="convert", message="转换参考音频", percent=2)
    for index, name in enumerate(files, 1):
        jobs.check_cancel(job)
        source = folder / "reference" / name
        if not source.is_file():
            raise TrainError(f"参考音频不存在：{name}")
        stem = f"{index:04d}"
        target32 = audio32 / f"{stem}.wav"
        target16 = audio16 / f"{stem}.wav"
        _convert(ffmpeg, source, target32, 32000)
        _convert(ffmpeg, source, target16, 16000)
        names.append((name, stem, target32))
        job.update(
            message=f"转换 {index}/{len(files)}：{name}",
            percent=2 + 10 * index / len(files),
        )

    total_seconds = sum(voice.wav_duration(item[2]) for item in names)
    if total_seconds < MIN_SECONDS:
        raise TrainError(f"参考音频总时长只有 {total_seconds:.1f} 秒，至少需要 {MIN_SECONDS:.0f} 秒")

    missing = [name for name, _stem, _path in names if not texts.get(name, "").strip()]
    if missing and language in ("zh", "yue"):
        job.update(stage="asr", message=f"自动识别 {len(missing)} 段参考文字", percent=12)
        recognized = gpt_sovits.transcribe_folder(audio16, language)
        stem_to_name = {stem: name for name, stem, _path in names}
        for stem, text in recognized.items():
            original = stem_to_name.get(Path(stem).stem)
            if original and text:
                texts[original] = text
        voice.set_reference_texts(data_dir, project_id, texts)
        missing = [name for name, _stem, _path in names if not texts.get(name, "").strip()]
    if missing:
        raise TrainError(
            "这些参考音频还没有文字，请先在“参考音频文字”里逐条填写："
            + "、".join(missing[:8])
        )

    reference_info = project.get("reference", {})
    if not str(reference_info.get("prompt_text", "")).strip():
        first_name = names[0][0]
        voice.update_reference(
            data_dir,
            project_id,
            prompt_text=texts[first_name],
            prompt_file=first_name,
        )
        project = voice.load_project(data_dir, project_id)

    list_path = work / "list.txt"
    lines = [
        f"{stem}.wav|speaker0|{LANGUAGES[language]}|{texts[name].strip()}"
        for name, stem, _path in names
    ]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    exp = f"wt_{project_id}_{time.strftime('%H%M%S')}"
    opt = root / "logs" / exp
    opt.mkdir(parents=True, exist_ok=True)

    base_env = gpt_sovits._env(root)
    base_env.update(
        {
            "inp_text": str(list_path),
            "inp_wav_dir": str(audio32),
            "exp_name": exp,
            "opt_dir": str(opt),
            "bert_pretrained_dir": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
            "version": "v4",
            "is_half": "True",
            "i_part": "0",
            "all_parts": "1",
            "_CUDA_VISIBLE_DEVICES": "0",
        }
    )

    gpt_sovits.stop_server()
    env3 = dict(
        base_env,
        pretrained_s2G=str(root / "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"),
        s2config_path="GPT_SoVITS/configs/s2.json",
    )

    def prep_step(label: str, script: str, start: float, end: float, env: dict) -> None:
        from app import jobs as _jobs

        _jobs.check_cancel(job)
        job.update(stage=label, message=f"{label}…", percent=start)
        process = subprocess.Popen(
            gpt_sovits.script_command(script),
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        tail: list[str] = []
        assert process.stdout is not None
        for raw in process.stdout:
            if job.cancel_event.is_set():
                _kill(process)
                raise _jobs.JobCancelled
            line = _clean(raw.decode("utf-8", errors="replace"))
            if line:
                tail.append(line)
                tail = tail[-10:]
                job.update(message=f"{label}：{line[:80]}")
        code = process.wait()
        if job.cancel_event.is_set():
            raise _jobs.JobCancelled
        if code != 0:
            raise TrainError(f"{label} 失败（退出码 {code}）：{' / '.join(tail[-3:])}")
        job.update(message=f"{label} 完成", percent=end)

    prep_step("文本与 BERT 特征", "GPT_SoVITS/prepare_datasets/1-get-text.py", 15, 20, base_env)
    prep_step(
        "HuBERT 特征",
        "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py",
        20,
        25,
        dict(base_env, cnhubert_base_dir="GPT_SoVITS/pretrained_models/chinese-hubert-base"),
    )
    prep_step("语义 Token", "GPT_SoVITS/prepare_datasets/3-get-semantic.py", 25, 30, env3)

    text_parts = sorted(opt.glob("2-name2text-*.txt"))
    if text_parts:
        merged = []
        for part in text_parts:
            merged.extend(part.read_text(encoding="utf-8").strip("\n").split("\n"))
            part.unlink()
        (opt / "2-name2text.txt").write_text("\n".join(merged) + "\n", encoding="utf-8")
    if not (opt / "2-name2text.txt").is_file():
        raise TrainError("文本特征没有生成，训练中止")

    semantic_parts = sorted(opt.glob("6-name2semantic-*.tsv"))
    if semantic_parts:
        merged = ["item_name\tsemantic_audio"]
        for part in semantic_parts:
            content = part.read_text(encoding="utf-8").strip("\n")
            if content:
                merged.extend(content.split("\n"))
            part.unlink()
        (opt / "6-name2semantic.tsv").write_text("\n".join(merged) + "\n", encoding="utf-8")
    if not (opt / "6-name2semantic.tsv").is_file():
        raise TrainError("语义 Token 没有生成，训练中止")

    weights_sovits = root / "SoVITS_weights_v4"
    weights_gpt = root / "GPT_weights_v4"
    weights_sovits.mkdir(exist_ok=True)
    weights_gpt.mkdir(exist_ok=True)

    s2 = json.loads((root / "GPT_SoVITS/configs/s2.json").read_text(encoding="utf-8"))
    s2["train"].update(
        {
            "batch_size": 1,
            "epochs": S2_EPOCHS,
            "save_every_epoch": 1,
            "if_save_latest": True,
            "if_save_every_weights": True,
            "text_low_lr_rate": 0.4,
            "pretrained_s2G": str(root / "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"),
            "pretrained_s2D": str(root / "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Dv4.pth"),
            "gpu_numbers": "0",
            "grad_ckpt": True,
            "lora_rank": LORA_RANK,
        }
    )
    s2["model"]["version"] = "v4"
    s2["data"]["exp_dir"] = s2["s2_ckpt_dir"] = str(opt)
    s2["save_weight_dir"] = str(weights_sovits)
    s2["name"] = exp
    s2["version"] = "v4"
    s2_path = work / "s2.json"
    s2_path.write_text(json.dumps(s2, ensure_ascii=False), encoding="utf-8")
    _run(
        job,
        gpt_sovits.script_command("GPT_SoVITS/s2_train_v3_lora.py", ["--config", str(s2_path)]),
        root=root,
        label="SoVITS 训练",
        start=30,
        end=65,
        epochs=S2_EPOCHS,
    )

    s1_path = work / "s1.yaml"
    s1_path.write_text(_s1_config(root, opt, exp, weights_gpt), encoding="utf-8")
    s1_env = dict(base_env, hz="25hz")
    _run_env(
        job,
        gpt_sovits.script_command("GPT_SoVITS/s1_train.py", ["--config_file", str(s1_path)]),
        root=root,
        env=s1_env,
        label="GPT 训练",
        start=65,
        end=92,
        epochs=S1_EPOCHS,
    )

    sovits_files = sorted(weights_sovits.glob(f"{exp}*.pth"), key=lambda item: item.stat().st_mtime)
    gpt_files = sorted(weights_gpt.glob(f"{exp}*.ckpt"), key=lambda item: item.stat().st_mtime)
    if not sovits_files or not gpt_files:
        raise TrainError("训练结束但没有找到权重文件，请查看 data/logs/gpt-sovits-api.log")
    sovits = sovits_files[-1]
    gpt = gpt_files[-1]
    model_dir = work / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    for old in model_dir.glob("*"):
        old.unlink(missing_ok=True)
    project_sovits = model_dir / sovits.name
    project_gpt = model_dir / gpt.name
    shutil.copy2(sovits, project_sovits)
    shutil.copy2(gpt, project_gpt)
    for item in sovits_files:
        item.unlink(missing_ok=True)
    for item in gpt_files:
        item.unlink(missing_ok=True)
    shutil.rmtree(opt / "logs_s2_v4_lora_32", ignore_errors=True)
    shutil.rmtree(opt / "logs_s1_v4", ignore_errors=True)
    model = {
        "mode": "fine_tuned",
        "state": "ready",
        "detail": f"微调完成：{len(files)} 段 / {total_seconds:.1f} 秒",
        "exp": exp,
        "sovits_path": str(project_sovits),
        "gpt_path": str(project_gpt),
        "trained_at": _now(),
        "seconds": round(total_seconds, 1),
    }
    job.update(stage="activate", message="切换训练好的模型", percent=95)
    voice.set_model(data_dir, project_id, **model)
    for line in project.get("lines", []):
        voice.mark_line(
            data_dir,
            project_id,
            line["id"],
            status="draft",
            audio="",
            duration_sec=0.0,
        )
    voice.clear_all_takes(data_dir, project_id)
    gpt_sovits.activate(model)
    job.update(stage="done", message="训练完成，已切换新音色", percent=100)
    return {"model": model, "sovits": str(sovits), "gpt": str(gpt), "seconds": round(total_seconds, 1)}


def _run_env(
    job,
    command: list[str],
    *,
    root: Path,
    env: dict,
    label: str,
    start: float,
    end: float,
    epochs: int = 0,
) -> None:
    from app import jobs

    job.update(stage=label, message=f"{label}…", percent=start)
    process = subprocess.Popen(
        [str(item) for item in command],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: list[str] = []
    progress = start
    assert process.stdout is not None
    for raw in process.stdout:
        if job.cancel_event.is_set():
            _kill(process)
            raise jobs.JobCancelled
        line = _clean(raw.decode("utf-8", errors="replace"))
        if not line:
            continue
        tail.append(line)
        tail = tail[-12:]
        epoch = _epoch_number(line) if epochs else 0
        if epoch:
            progress = max(
                progress,
                start + (end - start) * min(1.0, epoch / epochs),
            )
            job.update(
                message=f"{label}：第 {min(epoch, epochs)}/{epochs} 轮",
                percent=min(end, progress),
            )
        else:
            job.update(message=f"{label}：{line[:80]}", percent=min(end, progress))
    code = process.wait()
    if job.cancel_event.is_set():
        raise jobs.JobCancelled
    if code != 0:
        raise TrainError(f"{label} 失败（退出码 {code}）：{' / '.join(tail[-3:])}")
    job.update(message=f"{label} 完成", percent=end)
