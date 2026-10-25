"""GPT-SoVITS 本地运行时：发现、启动 API、文本合成。

官方 Windows 整合包（MIT）:
  https://huggingface.co/lj1995/GPT-SoVITS-windows-package

整合包自带 Python 运行时和预训练模型。我们只调用包内的 api_v2.py，
默认用参考音频做零样本克隆；训练后的权重可以通过 /set_gpt_weights 和
/set_sovits_weights 切换。
"""
from __future__ import annotations

import array
import atexit
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import zlib
from pathlib import Path

from app import config

PACKAGE_NAME = "GPT-SoVITS-v4-20250529"
DEFAULT_API_PORT = 9880
SERVER_START_TIMEOUT = 240
LANGUAGES = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文", "yue": "粤语"}
_PROCESS: subprocess.Popen | None = None
_PORT = 0
_LOG_PATH: Path | None = None
_ACTIVE_WEIGHTS: tuple[str, str] | None = None

DEFAULT_SOVITS = "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"
DEFAULT_GPT = "GPT_SoVITS/pretrained_models/s1v3.ckpt"


class GptSoVitsError(RuntimeError):
    pass


def candidate_roots() -> list[Path]:
    cfg = config.load_config()
    data_dir = Path(cfg.data_dir).expanduser() if cfg.data_dir else config.DEFAULT_DATA_DIR
    roots: list[Path] = []
    if cfg.gpt_sovits_path:
        roots.append(Path(cfg.gpt_sovits_path).expanduser())
    roots.extend(
        [
            data_dir / "components" / "gpt-sovits" / PACKAGE_NAME,
            data_dir / "components" / "gpt-sovits",
            config.APP_ROOT / "vendor" / "gpt-sovits",
            Path(r"D:\GPT-SoVITS"),
            Path(r"D:\GPT-SoVITS-v4"),
        ]
    )
    seen: set[str] = set()
    result = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        result.append(root)
    return result


def _looks_like_install(root: Path) -> bool:
    if not root.is_dir():
        return False
    return (
        (root / "api_v2.py").is_file()
        and (root / "GPT_SoVITS").is_dir()
        and (
            (root / "runtime" / "python.exe").is_file()
            or (root / "python.exe").is_file()
        )
    )


def locate_install(root: Path) -> Path | None:
    root = Path(root)
    if _looks_like_install(root):
        return root
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and _looks_like_install(child):
                return child
    return None


def find_install() -> Path | None:
    for root in candidate_roots():
        found = locate_install(root)
        if found:
            return found
    return None


def python_exe(root: Path) -> Path:
    candidates = [root / "runtime" / "python.exe", root / "python.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise GptSoVitsError("整合包里没有找到 Python 运行时")


def status() -> dict:
    root = find_install()
    if not root:
        return {
            "available": False,
            "running": False,
            "path": "",
            "detail": "未安装 GPT-SoVITS 整合包",
        }
    running = bool(_PROCESS and _PROCESS.poll() is None)
    return {
        "available": True,
        "running": running,
        "path": str(root),
        "python": str(python_exe(root)),
        "port": _PORT if running else 0,
        "detail": "已安装" if not running else "API 已启动",
    }


def script_command(script: str | Path, args: list[str] | None = None) -> list[str]:
    """用整合包运行时执行脚本；绕过内置 Python 的 ._pth 路径隔离。"""
    root = find_install()
    if not root:
        raise GptSoVitsError("没有找到 GPT-SoVITS 整合包，请先在工作台安装")
    script_path = Path(script)
    if not script_path.is_absolute():
        script_path = root / script_path
    if not script_path.is_file():
        raise GptSoVitsError(f"整合包缺少脚本：{script_path}")
    paths = [
        root,
        root / "GPT_SoVITS",
        root / "tools",
        root / "tools" / "asr",
        root / "GPT_SoVITS" / "BigVGAN",
    ]
    bootstrap = (
        "import runpy,sys; "
        f"sys.path[:0]={[str(path) for path in paths]!r}; "
        "sys.argv=sys.argv[1:]; "
        "runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    return [str(python_exe(root)), "-c", bootstrap, str(script_path), *(args or [])]


def _env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    paths = [
        root,
        root / "GPT_SoVITS",
        root / "tools",
        root / "tools" / "asr",
        root / "GPT_SoVITS" / "BigVGAN",
    ]
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in paths)
    env["PYTHONUNBUFFERED"] = "1"
    env["no_proxy"] = "localhost, 127.0.0.1, ::1"
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _log_tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def _api_ready(port: int) -> bool:
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/docs", method="GET")
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def start_server() -> dict:
    global _PROCESS, _PORT, _LOG_PATH
    if _PROCESS and _PROCESS.poll() is None and _PORT and _api_ready(_PORT):
        return status()

    root = find_install()
    if not root:
        raise GptSoVitsError("没有找到 GPT-SoVITS 整合包，请先在工作台安装")
    python = python_exe(root)
    port = _free_port()
    log_dir = Path(config.load_config().data_dir or config.DEFAULT_DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "gpt-sovits-api.log"
    log_file = log_path.open("ab")
    command = [
        str(python),
        str(root / "api_v2.py"),
        "-a",
        "127.0.0.1",
        "-p",
        str(port),
        "-c",
        str(root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _PROCESS = subprocess.Popen(
        command,
        cwd=str(root),
        env=_env(root),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    _PORT = port
    _LOG_PATH = log_path
    deadline = time.time() + SERVER_START_TIMEOUT
    while time.time() < deadline:
        if _PROCESS.poll() is not None:
            tail = _log_tail(log_path)
            raise GptSoVitsError(f"GPT-SoVITS 启动失败：{tail or '进程已退出'}")
        if _api_ready(port):
            return status()
        time.sleep(1.0)
    raise GptSoVitsError("GPT-SoVITS 启动超时，请查看 data/logs/gpt-sovits-api.log")


def stop_server() -> None:
    global _PROCESS, _PORT
    if _PROCESS and _PROCESS.poll() is None:
        _PROCESS.terminate()
        try:
            _PROCESS.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _PROCESS.kill()
    _PROCESS = None
    _PORT = 0
    _ACTIVE_WEIGHTS = None


_ASR_SCRIPT = """\
import json
import sys

import torch
from funasr import AutoModel

model = AutoModel(
    model="tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    model_revision="v2.0.4",
    disable_update=True,
    device="cuda" if torch.cuda.is_available() else "cpu",
)
result = model.generate(input=sys.argv[1])
text = result[0].get("text", "") if result else ""
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump({"text": text}, handle, ensure_ascii=False)
"""


def _to_16k_mono(source: Path, target: Path) -> None:
    """FunASR 只吃 16 kHz 单声道，用纯标准库做一次线性重采样。"""
    with wave.open(str(source), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        if width != 2:
            raise GptSoVitsError("自动识别只支持 16 位 PCM 的 wav，请换一段或手动填写文字")
        frames = handle.readframes(handle.getnframes())
    samples = array.array("h")
    samples.frombytes(frames)
    if channels > 1:
        samples = array.array(
            "h",
            (
                int(sum(samples[index : index + channels]) / channels)
                for index in range(0, len(samples) - channels + 1, channels)
            ),
        )
    if rate != 16000 and samples:
        ratio = 16000.0 / rate
        out_len = max(1, int(len(samples) * ratio))
        resampled = array.array("h", bytes(2 * out_len))
        last = len(samples) - 1
        for index in range(out_len):
            pos = index / ratio
            left = int(pos)
            frac = pos - left
            right = min(last, left + 1)
            resampled[index] = int(samples[left] * (1.0 - frac) + samples[right] * frac)
        samples = resampled
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(samples.tobytes())


def transcribe(
    ref_audio_path: Path,
    language: str = "zh",
    timeout: int = 600,
) -> str:
    """用整合包自带的 FunASR 识别参考音频，返回严格对应的文字。"""
    if language not in ("zh", "yue"):
        raise GptSoVitsError("自动识别目前支持中文/粤语；其它语言请手动填写参考文字")
    root = find_install()
    if not root:
        raise GptSoVitsError("没有找到 GPT-SoVITS 整合包，请先在工作台安装")
    if not ref_audio_path.is_file():
        raise GptSoVitsError("参考音频不存在")
    with tempfile.TemporaryDirectory(prefix="wt-asr-") as temp:
        temp_dir = Path(temp)
        audio16 = temp_dir / "ref16k.wav"
        _to_16k_mono(ref_audio_path, audio16)
        script = temp_dir / "asr_once.py"
        script.write_text(_ASR_SCRIPT, encoding="utf-8")
        result_path = temp_dir / "result.json"
        command = [
            str(python_exe(root)),
            str(script),
            str(audio16),
            str(result_path),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            command,
            cwd=str(root),
            env=_env(root),
            capture_output=True,
            timeout=timeout,
            creationflags=creationflags,
        )
        if not result_path.is_file():
            tail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace")
            raise GptSoVitsError(f"参考音频识别失败：{tail[-300:] or '没有输出'}")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        text = str(data.get("text", "")).strip()
        if not text:
            raise GptSoVitsError("没有识别出文字，请换一段更清晰的参考音频")
        return text


_ASR_MANY_SCRIPT = """\
import json
import sys
from pathlib import Path

import torch
from funasr import AutoModel

folder = Path(sys.argv[1])
out = Path(sys.argv[2])
model = AutoModel(
    model="tools/asr/models/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    model_revision="v2.0.4",
    disable_update=True,
    device="cuda" if torch.cuda.is_available() else "cpu",
)
result = {}
for path in sorted(folder.glob("*.wav")):
    try:
        items = model.generate(input=str(path))
        result[path.name] = items[0].get("text", "") if items else ""
    except Exception as exc:
        result[path.name] = ""
        print("ASR failed", path.name, exc)
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
"""


def transcribe_folder(folder: Path, language: str = "zh", timeout: int = 1800) -> dict[str, str]:
    """一次加载 FunASR，批量识别文件夹里的 16 kHz 单声道 WAV。"""
    if language not in ("zh", "yue"):
        raise GptSoVitsError("批量识别目前支持中文/粤语；其它语言请手动填写参考文字")
    root = find_install()
    if not root:
        raise GptSoVitsError("没有找到 GPT-SoVITS 整合包，请先在工作台安装")
    folder = Path(folder)
    if not folder.is_dir():
        raise GptSoVitsError("参考音频目录不存在")
    with tempfile.TemporaryDirectory(prefix="wt-asr-many-") as temp:
        temp_dir = Path(temp)
        script = temp_dir / "asr_many.py"
        script.write_text(_ASR_MANY_SCRIPT, encoding="utf-8")
        result_path = temp_dir / "result.json"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [str(python_exe(root)), str(script), str(folder), str(result_path)],
            cwd=str(root),
            env=_env(root),
            capture_output=True,
            timeout=timeout,
            creationflags=creationflags,
        )
        if not result_path.is_file():
            tail = (completed.stderr or completed.stdout or b"").decode("utf-8", errors="replace")
            raise GptSoVitsError(f"批量识别失败：{tail[-300:] or '没有输出'}")
        data = json.loads(result_path.read_text(encoding="utf-8"))
        return {str(name): str(text or "").strip() for name, text in data.items()}


def _set_weight(endpoint: str, path: Path) -> None:
    port = _PORT
    if not port or not _api_ready(port):
        start_server()
        port = _PORT
    query = urllib.parse.urlencode({"weights_path": str(path)})
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/{endpoint}?{query}", timeout=300
        ) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise GptSoVitsError(f"切换模型失败：{raw[:300]}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GptSoVitsError(f"无法连接 GPT-SoVITS：{exc}") from exc


def set_weights(sovits_path: Path, gpt_path: Path) -> None:
    global _ACTIVE_WEIGHTS
    sovits_path = Path(sovits_path)
    gpt_path = Path(gpt_path)
    if not sovits_path.is_file() or not gpt_path.is_file():
        raise GptSoVitsError("训练权重文件不存在")
    set_weights_key = (str(sovits_path), str(gpt_path))
    if _ACTIVE_WEIGHTS == set_weights_key:
        return
    _set_weight("set_sovits_weights", sovits_path)
    _set_weight("set_gpt_weights", gpt_path)
    _ACTIVE_WEIGHTS = set_weights_key


def activate(model: dict | None) -> None:
    """合成前切换到项目对应的模型；零样本项目回到 v4 底模。"""
    root = find_install()
    if not root:
        raise GptSoVitsError("没有找到 GPT-SoVITS 整合包，请先在工作台安装")
    model = model or {}
    if model.get("mode") == "fine_tuned":
        sovits = Path(model.get("sovits_path", ""))
        gpt = Path(model.get("gpt_path", ""))
        if not sovits.is_file() or not gpt.is_file():
            raise GptSoVitsError("训练好的模型文件不存在，请重新训练或换回零样本")
        set_weights(sovits, gpt)
        return
    set_weights(root / DEFAULT_SOVITS, root / DEFAULT_GPT)


def _post_tts(payload: dict) -> bytes:
    port = _PORT
    if not port or not _api_ready(port):
        start_server()
        port = _PORT
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/tts",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            message = payload.get("message") or raw
            if payload.get("Exception"):
                message = f"{message}（{payload['Exception']}）"
        except json.JSONDecodeError:
            message = raw
        raise GptSoVitsError(f"GPT-SoVITS 合成失败：{message[:500]}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GptSoVitsError(f"无法连接 GPT-SoVITS：{exc}") from exc
    if "json" in content_type.lower():
        try:
            message = json.loads(body.decode("utf-8", errors="replace")).get("message")
        except (json.JSONDecodeError, AttributeError):
            message = body.decode("utf-8", errors="replace")[:300]
        raise GptSoVitsError(f"GPT-SoVITS 返回错误：{message}")
    return body


def _wav_seconds(body: bytes) -> float:
    try:
        with wave.open(io.BytesIO(body), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (wave.Error, OSError, ZeroDivisionError):
        return 0.0


def _minimum_seconds(text: str, text_lang: str) -> float:
    """随机种子偶尔会让模型提前结束；按字符数给一个保守下限。"""
    spoken = sum(1 for char in text if char.isalnum())
    per_char = 0.11 if text_lang in ("zh", "ja", "ko", "yue") else 0.025
    return max(0.4, spoken * per_char)


def strict_minimum_seconds(text: str, text_lang: str) -> float:
    """多版本候选的截断保护：单版本仍保持宽松下限，避免误伤快语速。"""
    return _minimum_seconds(text, text_lang) * 1.45


def synthesize(
    *,
    text: str,
    text_lang: str,
    ref_audio_path: Path,
    prompt_text: str,
    prompt_lang: str,
    out_path: Path,
    speed_factor: float = 1.0,
    seed: int | None = None,
    top_k: int = 5,
    top_p: float = 1.0,
    temperature: float = 1.0,
    min_seconds: float | None = None,
    strict_min: bool = False,
) -> Path:
    if not text.strip():
        raise GptSoVitsError("台词不能为空")
    if not ref_audio_path.is_file():
        raise GptSoVitsError("参考音频不存在")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = text.strip()
    language = text_lang or "zh"
    base_seed = (
        int(seed)
        if seed is not None
        else zlib.crc32((content + prompt_text).encode("utf-8")) & 0x7FFFFFFF
    )
    minimum = max(
        _minimum_seconds(content, language),
        float(min_seconds or 0.0),
    ) / max(0.5, float(speed_factor or 1.0))
    top_k = max(1, min(100, int(top_k or 5)))
    top_p = max(0.05, min(1.0, float(top_p or 1.0)))
    temperature = max(0.05, min(2.0, float(temperature or 1.0)))
    audio = b""
    for attempt in range(3):
        payload = {
            "text": content,
            "text_lang": language,
            "ref_audio_path": str(ref_audio_path),
            "prompt_text": prompt_text.strip(),
            "prompt_lang": prompt_lang or "zh",
            "text_split_method": "cut5",
            "batch_size": 1,
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": float(speed_factor or 1.0),
            # 官方 API 默认开启并行推理，但长句在部分环境下会吞字/重复
            # （社区 issue #2658：关掉后恢复正常）。这里固定关掉换稳定。
            "parallel_infer": False,
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "seed": (base_seed + attempt) & 0x7FFFFFFF,
        }
        audio = _post_tts(payload)
        if _wav_seconds(audio) >= minimum:
            break
    if strict_min and _wav_seconds(audio) < minimum:
        raise GptSoVitsError("合成音频过短，可能被截断；请重试或换一个参考音频")
    out_path.write_bytes(audio)
    return out_path


atexit.register(stop_server)
