from __future__ import annotations

import array
import io
import math
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from app.engines import gpt_sovits


class DurationGuardTest(unittest.TestCase):
    def test_chinese_minimum_scales_with_characters(self) -> None:
        self.assertGreater(
            gpt_sovits._minimum_seconds("目标已摧毁，准备返航。", "zh"),
            gpt_sovits._minimum_seconds("收到。", "zh"),
        )
        self.assertGreaterEqual(gpt_sovits._minimum_seconds("好", "zh"), 0.4)

    def test_wav_seconds_reads_returned_audio(self) -> None:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 8000)
        self.assertAlmostEqual(gpt_sovits._wav_seconds(buffer.getvalue()), 0.5, places=2)
        self.assertEqual(gpt_sovits._wav_seconds(b"not a wav"), 0.0)


def _wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buffer.getvalue()


class SynthesizeGuardTest(unittest.TestCase):
    def test_retries_when_output_is_suspiciously_short(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "ref.wav"
            reference.write_bytes(_wav(4))
            out = root / "out.wav"
            seeds = []

            def fake_post(payload):
                seeds.append(payload["seed"])
                return _wav(0.5) if len(seeds) == 1 else _wav(3.0)

            with mock.patch.object(gpt_sovits, "_post_tts", side_effect=fake_post):
                gpt_sovits.synthesize(
                    text="目标已摧毁，准备返航。",
                    text_lang="zh",
                    ref_audio_path=reference,
                    prompt_text="参考文字",
                    prompt_lang="zh",
                    out_path=out,
                )
            self.assertEqual(len(seeds), 2)
            self.assertNotEqual(seeds[0], seeds[1])
            self.assertAlmostEqual(gpt_sovits._wav_seconds(out.read_bytes()), 3.0, places=2)

    def test_strict_minimum_rejects_truncated_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "ref.wav"
            reference.write_bytes(_wav(4))
            calls = []

            def fake_post(payload):
                calls.append(payload["seed"])
                return _wav(0.5)

            with mock.patch.object(gpt_sovits, "_post_tts", side_effect=fake_post):
                with self.assertRaises(gpt_sovits.GptSoVitsError):
                    gpt_sovits.synthesize(
                        text="目标已摧毁，准备返航。",
                        text_lang="zh",
                        ref_audio_path=reference,
                        prompt_text="参考文字",
                        prompt_lang="zh",
                        out_path=root / "out.wav",
                        min_seconds=gpt_sovits.strict_minimum_seconds(
                            "目标已摧毁，准备返航。", "zh"
                        ),
                        strict_min=True,
                    )
            self.assertEqual(len(calls), 3)
            self.assertFalse((root / "out.wav").exists())

    def test_sampling_parameters_reach_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root / "ref.wav"
            reference.write_bytes(_wav(4))
            captured = {}

            def fake_post(payload):
                captured.update(payload)
                return _wav(3.0)

            with mock.patch.object(gpt_sovits, "_post_tts", side_effect=fake_post):
                gpt_sovits.synthesize(
                    text="目标已摧毁，准备返航。",
                    text_lang="zh",
                    ref_audio_path=reference,
                    prompt_text="参考文字",
                    prompt_lang="zh",
                    out_path=root / "out.wav",
                    top_k=20,
                    top_p=0.9,
                    temperature=1.1,
                )
            self.assertEqual(captured["top_k"], 20)
            self.assertEqual(captured["top_p"], 0.9)
            self.assertEqual(captured["temperature"], 1.1)
            # 长句吞字/重复的社区修复：默认关闭并行推理。
            self.assertFalse(captured["parallel_infer"])


class ResampleTest(unittest.TestCase):
    def test_resamples_to_16k_mono(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.wav"
            target = root / "target.wav"
            rate = 22050
            samples = array.array(
                "h",
                (int(8000 * math.sin(index / 20.0)) for index in range(rate)),
            )
            with wave.open(str(source), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(samples.tobytes())

            gpt_sovits._to_16k_mono(source, target)

            with wave.open(str(target), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), 16000)
                self.assertAlmostEqual(handle.getnframes(), 16000, delta=2)

    def test_rejects_non_pcm16(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.wav"
            with wave.open(str(source), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(1)
                handle.setframerate(16000)
                handle.writeframes(b"\x80" * 1600)
            with self.assertRaises(gpt_sovits.GptSoVitsError):
                gpt_sovits._to_16k_mono(source, root / "target.wav")


if __name__ == "__main__":
    unittest.main()
