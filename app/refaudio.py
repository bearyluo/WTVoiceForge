"""参考素材的轻量整理：裁掉首尾静音，长素材按静音切片。

只处理 WAV，保证无损、可预期；其它格式原样保留给训练流程。
处理结果写回项目 reference 目录，原始文件移到 reference/_原始/ 方便恢复。
"""
from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path


class RefAudioError(RuntimeError):
    pass


def _rms(chunk: bytes, width: int) -> float:
    if not chunk:
        return 0.0
    if width == 2:
        data = array("h")
        data.frombytes(chunk[: len(chunk) - len(chunk) % 2])
        values = [value / 32768.0 for value in data]
    elif width == 4:
        data = array("i")
        data.frombytes(chunk[: len(chunk) - len(chunk) % 4])
        values = [value / 2147483648.0 for value in data]
    else:
        values = [abs(byte - 128) / 128.0 for byte in chunk]
    return math.sqrt(sum(value * value for value in values) / max(1, len(values)))


def _db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


def inspect(path: str | Path) -> dict:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.getnframes()
        data = handle.readframes(frames)
    window_frames = max(1, int(rate * 0.02))
    window_bytes = window_frames * channels * width
    levels = [
        _db(_rms(data[offset : offset + window_bytes], width))
        for offset in range(0, len(data), window_bytes)
    ]
    return {
        "duration_sec": round(frames / rate, 3) if rate else 0.0,
        "sample_rate": rate,
        "channels": channels,
        "levels": levels,
        "window_sec": 0.02,
    }


def _slice_points(
    levels: list[float], threshold_db: float, pad_windows: int
) -> tuple[int, int]:
    loud = [index for index, level in enumerate(levels) if level > threshold_db]
    if not loud:
        return 0, len(levels) - 1
    return max(0, loud[0] - pad_windows), min(len(levels) - 1, loud[-1] + pad_windows)


def trim_silence(
    source: str | Path,
    target: str | Path,
    *,
    threshold_db: float = -40.0,
    pad_sec: float = 0.08,
) -> dict:
    info = inspect(source)
    levels = info["levels"] or [0.0]
    pad_windows = max(1, int(pad_sec / info["window_sec"]))
    start, end = _slice_points(levels, threshold_db, pad_windows)
    with wave.open(str(source), "rb") as handle:
        params = handle.getparams()
        frames = handle.getnframes()
    window_frames = max(1, int(params.framerate * info["window_sec"]))
    first = min(start * window_frames, frames)
    last = min((end + 1) * window_frames, frames)
    with wave.open(str(source), "rb") as handle:
        handle.setpos(first)
        data = handle.readframes(max(0, last - first))
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target_path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(data)
    after = round(len(data) / max(1, params.nchannels * params.sampwidth * params.framerate), 3)
    return {
        "duration_before": info["duration_sec"],
        "duration_after": after,
        "trimmed_sec": round(max(0.0, info["duration_sec"] - after), 3),
    }


def split_at_silence(
    source: str | Path,
    out_dir: str | Path,
    prefix: str,
    *,
    max_seconds: float = 30.0,
    threshold_db: float = -40.0,
    min_seconds: float = 3.0,
) -> list[Path]:
    """把长素材在最安静的窗口处切开，每段尽量不超过 max_seconds。"""
    info = inspect(source)
    levels = info["levels"]
    with wave.open(str(source), "rb") as handle:
        params = handle.getparams()
        frames = handle.getnframes()
        handle.rewind()
        data = handle.readframes(frames)
    window_frames = max(1, int(params.framerate * info["window_sec"]))
    window_bytes = window_frames * params.nchannels * params.sampwidth
    if info["duration_sec"] <= max_seconds or len(levels) < 4:
        target = Path(out_dir) / f"{prefix}_part1.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source).read_bytes())
        return [target]

    max_windows = max(1, int(max_seconds / info["window_sec"]))
    min_windows = max(1, int(min_seconds / info["window_sec"]))
    cuts = [0]
    start = 0
    while len(levels) - start > max_windows * 1.25:
        limit = start + max_windows
        floor = start + int(max_windows * 0.6)
        candidates = range(floor, limit)
        cut = min(candidates, key=lambda index: levels[index])
        if cut - start < min_windows:
            cut = limit
        cuts.append(cut)
        start = cut
    cuts.append(len(levels))

    outputs: list[Path] = []
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    for index, (begin, end) in enumerate(zip(cuts, cuts[1:]), 1):
        first = begin * window_frames
        last = min(end * window_frames, frames)
        if last - first <= 0:
            continue
        target = out_root / f"{prefix}_part{index}.wav"
        with wave.open(str(target), "wb") as handle:
            handle.setparams(params)
            handle.writeframes(data[first * params.nchannels * params.sampwidth : last * params.nchannels * params.sampwidth])
        outputs.append(target)
    return outputs or [Path(source)]