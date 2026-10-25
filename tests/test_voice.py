from __future__ import annotations

import math
import tempfile
import unittest
import wave
from pathlib import Path

from app import voice


def make_wav(path: Path, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))


def make_tone(path: Path, seconds: float = 0.2, peak: int = 10000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 16000
    frames = bytearray()
    for index in range(int(rate * seconds)):
        value = int(peak * math.sin(2 * math.pi * 220 * index / rate))
        frames.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def wav_peak(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    return max(
        (abs(int.from_bytes(frames[index : index + 2], "little", signed=True))
         for index in range(0, len(frames), 2)),
        default=0,
    )


class VoiceProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = voice.create_project(self.root, "测试音色")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reference_import_and_prompt(self) -> None:
        source = self.root / "refs"
        make_wav(source / "a.wav")
        make_wav(source / "b.wav", 0.3)
        result = voice.import_reference(
            self.root,
            self.project["id"],
            source,
            prompt_text="你好，这是参考音频。",
            prompt_lang="zh",
        )
        self.assertEqual(result["copied"], 2)
        project = voice.load_project(self.root, self.project["id"])
        self.assertEqual(len(project["reference"]["files"]), 2)
        self.assertEqual(project["reference"]["prompt_text"], "你好，这是参考音频。")
        self.assertTrue(project["reference"]["prompt_file"].endswith(".wav"))

    def test_line_crud_and_audio_reset(self) -> None:
        added = voice.add_line(
            self.root,
            self.project["id"],
            "准备起飞",
            event="takeoff",
            bank="aircraft_gui",
        )
        line_id = added["line"]["id"]
        out = voice.line_audio_path(self.root, self.project["id"], line_id)
        make_wav(out, 0.5)
        voice.mark_line(
            self.root,
            self.project["id"],
            line_id,
            status="ready",
            audio=f"audio/{line_id}.wav",
            duration_sec=0.5,
        )
        project = voice.load_project(self.root, self.project["id"])
        self.assertEqual(project["lines"][0]["status"], "ready")
        voice.update_line(self.root, self.project["id"], line_id, text="准备降落")
        project = voice.load_project(self.root, self.project["id"])
        self.assertEqual(project["lines"][0]["text"], "准备降落")
        self.assertEqual(project["lines"][0]["status"], "draft")
        self.assertEqual(project["lines"][0]["audio"], "")
        voice.delete_line(self.root, self.project["id"], line_id)
        self.assertEqual(voice.load_project(self.root, self.project["id"])["lines"], [])

    def test_source_mapping_and_duplicate_event(self) -> None:
        source = {
            "event_id": "evt-1",
            "event": "enemy_destroyed",
            "bank": "aircraft_gui",
            "assets": [{"guid": "g1", "path": "gui/enemy_destroyed.wav"}],
        }
        result = voice.add_line(
            self.root,
            self.project["id"],
            "",
            source=source,
        )
        line = result["line"]
        self.assertEqual(line["source"]["file"], "enemy_destroyed.wav")
        self.assertEqual(line["target_file"], "enemy_destroyed.wav")
        self.assertEqual(line["text"], "")
        # 同一个事件不能重复绑定，避免翻译文本串行。
        with self.assertRaises(voice.VoiceError):
            voice.add_line(self.root, self.project["id"], "", source=source)

        result = voice.import_texts(
            self.root,
            self.project["id"],
            [
                {
                    "event_id": "evt-1",
                    "source_file": "enemy_destroyed.wav",
                    "text": "击毁目标",
                },
                {
                    "event_id": "evt-1",
                    "source_file": "wrong.wav",
                    "text": "错误的文件名",
                },
                {
                    "event_id": "missing",
                    "text": "找不到事件",
                },
            ],
        )
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["skipped"], 2)
        project = voice.load_project(self.root, self.project["id"])
        self.assertEqual(project["lines"][0]["text"], "击毁目标")

    def test_bulk_lines_and_listing(self) -> None:
        result = voice.add_lines_bulk(
            self.root,
            self.project["id"],
            [
                {"text": "第一句", "event": "one", "bank": "aircraft_gui"},
                {"text": "", "event": "empty"},
                {"text": "第二句", "event": "two"},
            ],
        )
        self.assertEqual(result["added"], 2)
        projects = voice.list_projects(self.root)
        self.assertEqual(projects[0]["line_count"], 2)
        self.assertEqual(projects[0]["name"], "测试音色")

    def test_reference_texts_and_model_state(self) -> None:
        source = self.root / "refs"
        make_wav(source / "a.wav")
        make_wav(source / "b.wav", 0.3)
        imported = voice.import_reference(self.root, self.project["id"], source)
        names = imported["project"]["reference"]["files"]

        result = voice.set_reference_texts(
            self.root,
            self.project["id"],
            {names[0]: "第一段", "不存在.wav": "忽略", names[1]: "  "},
        )
        texts = result["project"]["reference"]["texts"]
        self.assertEqual(texts, {names[0]: "第一段"})

        voice.set_model(
            self.root,
            self.project["id"],
            mode="fine_tuned",
            state="ready",
            sovits_path="a.pth",
        )
        model = voice.load_project(self.root, self.project["id"])["model"]
        self.assertEqual(model["mode"], "fine_tuned")
        self.assertEqual(model["sovits_path"], "a.pth")

    def test_training_readiness(self) -> None:
        from app.engines import trainer

        self.assertFalse(trainer.readiness(self.project)["ready"])
        source = self.root / "refs"
        for index in range(3):
            make_wav(source / f"{index}.wav")
        imported = voice.import_reference(self.root, self.project["id"], source)
        ready = trainer.readiness(imported["project"])
        self.assertTrue(ready["ready"])
        self.assertEqual(len(ready["missing_texts"]), 3)

    def test_takes_save_select_and_cleanup(self) -> None:
        added = voice.add_line(self.root, self.project["id"], "多版本测试")
        line_id = added["line"]["id"]
        folder = voice.takes_dir(self.root, self.project["id"], line_id)
        first = "t_111111111111"
        second = "t_222222222222"
        make_wav(folder / f"{first}.wav", 0.3)
        make_wav(folder / f"{second}.wav", 0.5)

        result = voice.save_takes(
            self.root,
            self.project["id"],
            line_id,
            [
                {"id": first, "duration_sec": 0.3, "seed": 1, "ref_file": "a.wav"},
                {"id": second, "duration_sec": 0.5, "seed": 2, "ref_file": "b.wav"},
            ],
            selected_id=first,
        )
        line = result["line"]
        self.assertEqual([item["id"] for item in line["takes"]], [first, second])
        self.assertEqual(line["takes"][0]["ref_file"], "a.wav")
        self.assertEqual(line["takes"][1]["ref_file"], "b.wav")
        self.assertEqual(line["selected_take"], first)
        self.assertAlmostEqual(line["duration_sec"], 0.3, places=3)
        self.assertTrue(voice.line_audio_path(self.root, self.project["id"], line_id).is_file())

        selected = voice.select_take(
            self.root, self.project["id"], line_id, second
        )["line"]
        self.assertEqual(selected["selected_take"], second)
        self.assertAlmostEqual(selected["duration_sec"], 0.5, places=3)
        main = voice.line_audio_path(self.root, self.project["id"], line_id)
        self.assertAlmostEqual(voice.wav_duration(main), 0.5, places=3)

        source = voice.find_take(
            self.root, self.project["id"], line_id, second
        )
        self.assertEqual(source, folder / f"{second}.wav")
        with self.assertRaises(voice.VoiceError):
            voice.select_take(self.root, self.project["id"], line_id, "t_333333333333")

        voice.update_line(self.root, self.project["id"], line_id, text="改词后清空")
        project = voice.load_project(self.root, self.project["id"])
        line = next(item for item in project["lines"] if item["id"] == line_id)
        self.assertNotIn("takes", line)
        self.assertNotIn("selected_take", line)
        self.assertEqual(line["audio"], "")
        self.assertFalse(folder.exists())

    def test_line_tuning_reference_and_defaults(self) -> None:
        source = self.root / "refs"
        make_wav(source / "calm.wav")
        make_wav(source / "angry.wav")
        voice.import_reference(self.root, self.project["id"], source)
        project = voice.load_project(self.root, self.project["id"])
        files = project["reference"]["files"]
        voice.set_reference_texts(
            self.root,
            self.project["id"],
            {files[0]: "平静地说", files[1]: "生气地说"},
        )
        project = voice.load_project(self.root, self.project["id"])
        line = voice.add_line(self.root, self.project["id"], "收到")["line"]
        self.assertEqual(voice.line_tuning(line), {"reference": "", "speed": 1.0, "gain_db": 0.0})

        result = voice.set_line_tuning(
            self.root,
            self.project["id"],
            [line["id"]],
            reference=files[1],
            speed=1.2,
            gain_db=3,
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["needs_synthesis"], 0)
        saved = next(
            item
            for item in voice.load_project(self.root, self.project["id"])["lines"]
            if item["id"] == line["id"]
        )
        self.assertEqual(
            voice.line_tuning(saved),
            {"reference": files[1], "speed": 1.2, "gain_db": 3.0},
        )
        choice = voice.tuning_reference(
            voice.load_project(self.root, self.project["id"]),
            voice.project_dir(self.root, self.project["id"]),
            saved,
            None,
        )
        self.assertEqual(choice["name"], files[1])
        self.assertEqual(choice["prompt_text"], "生气地说")

        voice.set_line_tuning(
            self.root, self.project["id"], [line["id"]], reference="", speed=1.0, gain_db=0
        )
        saved = next(
            item
            for item in voice.load_project(self.root, self.project["id"])["lines"]
            if item["id"] == line["id"]
        )
        self.assertEqual(saved.get("tuning"), {})
        self.assertEqual(voice.line_tuning(saved)["reference"], "")

    def test_line_tuning_gain_rerenders_from_take(self) -> None:
        line = voice.add_line(self.root, self.project["id"], "音量测试")["line"]
        take_id = "t_abcdef012345"
        take = voice.take_audio_path(self.root, self.project["id"], line["id"], take_id)
        make_tone(take, peak=10000)
        voice.save_takes(
            self.root,
            self.project["id"],
            line["id"],
            [{"id": take_id, "duration_sec": 0.2}],
        )
        main = voice.line_audio_path(self.root, self.project["id"], line["id"])
        self.assertAlmostEqual(wav_peak(main), 10000, delta=2)

        voice.set_line_tuning(
            self.root, self.project["id"], [line["id"]], gain_db=6
        )
        self.assertAlmostEqual(wav_peak(main), 19952, delta=80)
        voice.set_line_tuning(
            self.root, self.project["id"], [line["id"]], gain_db=-6
        )
        self.assertAlmostEqual(wav_peak(main), 5011, delta=40)
        voice.set_line_tuning(
            self.root, self.project["id"], [line["id"]], gain_db=0
        )
        self.assertAlmostEqual(wav_peak(main), 10000, delta=2)
        self.assertFalse(
            voice.raw_audio_path(self.root, self.project["id"], line["id"]).exists()
        )

    def test_line_tuning_gain_caps_and_preserves_take(self) -> None:
        line = voice.add_line(self.root, self.project["id"], "防削波")["line"]
        take_id = "t_abcdef012346"
        take = voice.take_audio_path(self.root, self.project["id"], line["id"], take_id)
        make_tone(take, peak=30000)
        voice.save_takes(
            self.root,
            self.project["id"],
            line["id"],
            [{"id": take_id, "duration_sec": 0.2}],
        )
        result = voice.set_line_tuning(
            self.root, self.project["id"], [line["id"]], gain_db=6
        )
        self.assertEqual(result["capped"], 1)
        main = voice.line_audio_path(self.root, self.project["id"], line["id"])
        self.assertEqual(wav_peak(main), 32767)
        saved = voice.load_project(self.root, self.project["id"])["lines"][0]
        self.assertLess(saved["gain_actual_db"], 6)
        self.assertEqual(wav_peak(take), 30000)

    def test_tone_change_invalidates_audio(self) -> None:
        source = self.root / "refs"
        make_wav(source / "a.wav")
        make_wav(source / "b.wav")
        voice.import_reference(self.root, self.project["id"], source)
        self.project = voice.load_project(self.root, self.project["id"])
        files = self.project["reference"]["files"]
        line = voice.add_line(self.root, self.project["id"], "换语气")["line"]
        take_id = "t_abcdef012347"
        take = voice.take_audio_path(self.root, self.project["id"], line["id"], take_id)
        make_tone(take)
        voice.save_takes(
            self.root,
            self.project["id"],
            line["id"],
            [{"id": take_id, "duration_sec": 0.2}],
        )
        result = voice.set_line_tuning(
            self.root,
            self.project["id"],
            [line["id"]],
            reference=files[1],
            speed=1.1,
        )
        self.assertEqual(result["needs_synthesis"], 1)
        saved = voice.load_project(self.root, self.project["id"])["lines"][0]
        self.assertEqual(saved["audio"], "")
        self.assertEqual(saved["status"], "draft")
        self.assertNotIn("takes", saved)
        self.assertFalse(
            voice.line_audio_path(self.root, self.project["id"], line["id"]).exists()
        )
        with self.assertRaises(voice.VoiceError):
            voice.set_line_tuning(
                self.root,
                self.project["id"],
                [line["id"]],
                reference="not-in-project.wav",
            )
        with self.assertRaises(voice.VoiceError):
            voice.set_line_tuning(
                self.root, self.project["id"], ["l_ffffffffffff"], gain_db=1
            )

    def test_render_gain_supports_24_bit(self) -> None:
        path = self.root / "pcm24.wav"
        frames = bytearray()
        for index in range(1600):
            value = int(500000 * math.sin(2 * math.pi * 220 * index / 16000))
            frames.extend(value.to_bytes(3, "little", signed=True))
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(3)
            handle.setframerate(16000)
            handle.writeframes(bytes(frames))
        voice.render_gain(path, path, 3)
        with wave.open(str(path), "rb") as handle:
            data = handle.readframes(handle.getnframes())
        peak = max(
            abs(int.from_bytes(data[index : index + 3], "little", signed=True))
            for index in range(0, len(data), 3)
        )
        self.assertAlmostEqual(peak, 707106, delta=4000)

    def test_reference_choices_spread_across_files(self) -> None:
        source = self.root / "refs"
        for index in range(6):
            make_wav(source / f"{index:03d}.wav")
        voice.import_reference(self.root, self.project["id"], source)
        project = voice.load_project(self.root, self.project["id"])
        files = project["reference"]["files"]
        voice.set_reference_texts(
            self.root,
            self.project["id"],
            {name: f"第 {index + 1} 段参考" for index, name in enumerate(files)},
        )

        project = voice.load_project(self.root, self.project["id"])
        choices = voice.reference_choices(
            project, voice.project_dir(self.root, self.project["id"]), 3
        )
        self.assertEqual([item["name"] for item in choices], [files[0], files[2], files[4]])
        self.assertEqual(choices[1]["prompt_text"], "第 3 段参考")
        self.assertTrue(all(item["path"].is_file() for item in choices))

    def test_clear_all_takes(self) -> None:
        added = voice.add_line(self.root, self.project["id"], "清空版本")
        line_id = added["line"]["id"]
        folder = voice.takes_dir(self.root, self.project["id"], line_id)
        take_id = "t_abcdef012345"
        make_wav(folder / f"{take_id}.wav", 0.2)
        voice.save_takes(
            self.root,
            self.project["id"],
            line_id,
            [{"id": take_id, "duration_sec": 0.2}],
        )
        voice.clear_all_takes(self.root, self.project["id"])
        project = voice.load_project(self.root, self.project["id"])
        line = next(item for item in project["lines"] if item["id"] == line_id)
        self.assertNotIn("takes", line)
        self.assertFalse(folder.exists())

    def test_import_external_weights_resets_audio(self) -> None:
        line = voice.add_line(
            self.root, self.project["id"], "准备起飞", event="takeoff", bank="aircraft_gui"
        )["line"]
        audio = voice.line_audio_path(self.root, self.project["id"], line["id"])
        make_wav(audio)
        voice.mark_line(
            self.root,
            self.project["id"],
            line["id"],
            audio=f"audio/{line['id']}.wav",
            status="ready",
        )
        voice.set_export(self.root, self.project["id"], name="旧包")
        source = self.root / "weights"
        sovits = source / "voice_e10.pth"
        gpt = source / "voice-e15.ckpt"
        source.mkdir()
        sovits.write_bytes(b"sovits")
        gpt.write_bytes(b"gpt")

        result = voice.import_model(
            self.root, self.project["id"], sovits, gpt
        )
        project = result["project"]
        self.assertEqual(project["model"]["mode"], "fine_tuned")
        self.assertEqual(project["model"]["source"], "external")
        self.assertTrue(Path(project["model"]["sovits_path"]).is_file())
        self.assertTrue(Path(project["model"]["gpt_path"]).is_file())
        self.assertIn("train", Path(project["model"]["sovits_path"]).parts)
        self.assertNotIn("export", project)
        self.assertFalse(audio.is_file())
        line = next(item for item in project["lines"] if item["id"] == line["id"])
        self.assertEqual(line["audio"], "")
        self.assertEqual(line["status"], "draft")

        index = source / "voice.index"
        index.write_bytes(b"index")
        rvc_model = source / "rvc.pth"
        rvc_model.write_bytes(b"rvc")
        rvc = voice.import_rvc_model(
            self.root,
            self.project["id"],
            rvc_model,
            index,
        )
        self.assertTrue(Path(rvc["model"]).is_file())
        self.assertTrue(Path(rvc["index"]).is_file())
        self.assertEqual(rvc["project"]["rvc"]["source"], "external")

        bad = source / "notes.txt"
        bad.write_text("x", encoding="utf-8")
        with self.assertRaises(voice.VoiceError):
            voice.import_model(self.root, self.project["id"], bad, gpt)

    def test_invalid_project_and_source(self) -> None:
        with self.assertRaises(voice.VoiceError):
            voice.load_project(self.root, "../bad")
        source = self.root / "empty"
        source.mkdir()
        with self.assertRaises(voice.VoiceError):
            voice.import_reference(self.root, self.project["id"], source)


if __name__ == "__main__":
    unittest.main()
