from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import jobs, original, voice
from tests.test_voice import make_wav


class ConvertProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kit = self.root / "kit"
        (self.kit / "Assets").mkdir(parents=True)
        self.game = self.root / "game"
        (self.game / "sound").mkdir(parents=True)
        self.bank = "_crew_dialogs_ground_fr"
        (self.game / "sound" / f"{self.bank}.assets.bank").write_bytes(b"bank")

        self.project = voice.create_project(self.root, "换声测试")
        for index, name in enumerate(("voice_a", "voice_b", "voice_tone")):
            source = {
                "event_id": f"at:{self.bank}:{name}",
                "event": name,
                "bank": self.bank,
                "localized": True,
                "game_only": True,
                "assets": [
                    {
                        "guid": "",
                        "path": f"dialogs_dynamic/{self.bank}/{index + 1}/{name}.wav",
                        "file": f"{name}.wav",
                        "game_index": index + 1,
                    }
                ],
            }
            added = voice.add_line(self.root, self.project["id"], "", source=source)
            if name == "voice_tone":
                continue
        project = voice.load_project(self.root, self.project["id"])
        for line in project["lines"]:
            line["text_source"] = "non_voice" if line["event"] == "voice_tone" else "original"
        project["quick"] = {"mode": "original"}
        voice.save_project(self.root, project)
        model = voice.project_dir(self.root, self.project["id"]) / "train" / "rvc" / "voice.pth"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"fake-model")
        voice.set_rvc_model(
            self.root, self.project["id"], model=str(model), index="", segments=3
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_converts_primary_original_and_skips_non_voice(self) -> None:
        def fake_extract(_bank, targets, temp_root=None):
            for target in targets.values():
                make_wav(target, 0.3)
            return len(targets)

        def fake_convert(job, **kwargs):
            output = Path(kwargs["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            for item in sorted(Path(kwargs["input_dir"]).glob("*.wav")):
                make_wav(output / item.name, 0.25)
            job.update(total=2, current=2, percent=98, message="换声完成")
            return {"output": str(output), "total": 2}

        job = jobs.Job("voice_convert")
        with mock.patch.object(
            original.localized,
            "game_bank_path",
            return_value=self.game / "sound" / f"{self.bank}.assets.bank",
        ), mock.patch.object(
            original.localized, "extract_indexes", side_effect=fake_extract
        ), mock.patch.object(
            original.rvc, "convert", side_effect=fake_convert
        ):
            result = original.convert_project(
                job,
                self.root,
                self.project["id"],
                self.kit,
                self.game,
            )

        project = voice.load_project(self.root, self.project["id"])
        by_name = {line["event"]: line for line in project["lines"]}
        self.assertEqual(result["converted"], 2)
        self.assertEqual(result["total"], 2)
        self.assertEqual(by_name["voice_a"]["status"], "ready")
        self.assertTrue(
            (voice.project_dir(self.root, self.project["id"]) / by_name["voice_a"]["audio"]).is_file()
        )
        self.assertEqual(by_name["voice_tone"]["status"], "draft")
        self.assertEqual(project["quick"]["converted_count"], 2)

    def test_rejects_non_original_project(self) -> None:
        project = voice.load_project(self.root, self.project["id"])
        project["quick"]["mode"] = "zh"
        voice.save_project(self.root, project)
        with self.assertRaises(original.OriginalError):
            original.convert_project(
                jobs.Job("voice_convert"),
                self.root,
                self.project["id"],
                self.kit,
                self.game,
            )

    def test_rejects_missing_model(self) -> None:
        project = voice.load_project(self.root, self.project["id"])
        project["rvc"] = {}
        voice.save_project(self.root, project)
        with self.assertRaises(original.OriginalError):
            original.convert_project(
                jobs.Job("voice_convert"),
                self.root,
                self.project["id"],
                self.kit,
                self.game,
            )


if __name__ == "__main__":
    unittest.main()
