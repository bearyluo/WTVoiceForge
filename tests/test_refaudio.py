from __future__ import annotations

import math
import tempfile
import unittest
import wave
from pathlib import Path

from app import materials, refaudio, voice


def make_wav(path: Path, seconds: float, *, rate: int = 16000, silence_sec: float = 0.0, amp: float = 0.4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        total = int(rate * seconds)
        silent = int(rate * silence_sec)
        frames = bytearray()
        for index in range(total):
            quiet = index < silent or index >= total - silent
            value = 0 if quiet else int(32767 * amp * math.sin(2 * math.pi * 220 * index / rate))
            frames += int(value).to_bytes(2, "little", signed=True)
        handle.writeframes(bytes(frames))


class RefAudioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_trim_silence(self) -> None:
        source = self.root / "long.wav"
        make_wav(source, 3.0, silence_sec=0.8)
        target = self.root / "trimmed.wav"
        info = refaudio.trim_silence(source, target)
        self.assertLess(info["duration_after"], info["duration_before"])
        self.assertGreater(info["duration_after"], 1.2)
        self.assertLess(info["duration_after"], 1.7)

    def test_split_at_silence(self) -> None:
        source = self.root / "long.wav"
        make_wav(source, 8.0)
        parts = refaudio.split_at_silence(source, self.root / "parts", "clip", max_seconds=3.0)
        self.assertGreaterEqual(len(parts), 3)
        for part in parts:
            self.assertTrue(part.is_file())
            self.assertLessEqual(voice.wav_duration(part), 3.2)


class MaterialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = voice.create_project(self.root, "素材测试")
        self.refs = self.root / "refs"
        make_wav(self.refs / "a.wav", 0.5)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_add_and_remove_reference(self) -> None:
        result = materials.add_references(
            self.root, self.project["id"], [str(self.refs / "a.wav")]
        )
        self.assertEqual(result["count"], 1)
        name = result["added"][0]
        project = voice.load_project(self.root, self.project["id"])
        self.assertIn(name, project["reference"]["files"])
        self.assertEqual(project["reference"]["prompt_file"], name)
        removed = materials.remove_reference(self.root, self.project["id"], name)
        self.assertEqual(removed["project"]["reference"]["files"], [])
        self.assertEqual(removed["project"]["reference"]["prompt_file"], "")

    def test_process_trims_and_keeps_original(self) -> None:
        make_wav(self.refs / "b.wav", 2.0, silence_sec=0.6)
        materials.add_references(
            self.root, self.project["id"], [str(self.refs / "b.wav")]
        )
        result = materials.process_references(
            self.root, self.project["id"], trim=True, split=False
        )
        project = result["project"]
        files = project["reference"]["files"]
        self.assertEqual(len(files), 1)
        self.assertIn("_clean", files[0])
        summary = result["summary"]
        self.assertEqual(summary["trimmed"], 1)
        self.assertLess(summary["after_sec"], summary["before_sec"])
        reference = voice.project_dir(self.root, self.project["id"]) / "reference"
        self.assertTrue((reference / "_原始" / "001_b.wav").is_file())

    def test_import_take(self) -> None:
        added = voice.add_line(
            self.root, self.project["id"], "测试台词", event="manual"
        )
        line_id = added["line"]["id"]
        source = self.root / "take.wav"
        make_wav(source, 1.2)
        result = materials.import_take(
            self.root, self.project["id"], line_id, source
        )
        line = result["line"]
        self.assertEqual(line["status"], "ready")
        self.assertEqual(line["audio_source"], "imported")
        self.assertEqual(len(line["takes"]), 1)
        self.assertTrue(line["takes"][0].get("imported"))
        self.assertEqual(voice.wav_duration(
            voice.line_audio_path(self.root, self.project["id"], line_id)
        ), 1.2)
        with self.assertRaises(voice.VoiceError):
            materials.import_take(
                self.root, self.project["id"], line_id, self.root / "missing.wav"
            )


if __name__ == "__main__":
    unittest.main()