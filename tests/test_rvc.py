from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import components, jobs
from app.engines import rvc
from tests.test_voice import make_wav


class RecommendedPackageTest(unittest.TestCase):
    def test_picks_by_gpu(self) -> None:
        self.assertEqual(
            components.recommended_rvc(
                {"has_nvidia": True, "gpus": [{"name": "NVIDIA GeForce RTX 3060 Laptop GPU"}]}
            ),
            "rvc-nvidia",
        )
        self.assertEqual(
            components.recommended_rvc(
                {"has_nvidia": True, "gpus": [{"name": "NVIDIA GeForce RTX 5090"}]}
            ),
            "rvc-nvidia50",
        )
        self.assertEqual(
            components.recommended_rvc(
                {"has_nvidia": False, "has_amd": True, "gpus": [{"name": "AMD Radeon RX 7800"}]}
            ),
            "rvc-amd",
        )
        self.assertEqual(components.recommended_rvc({"has_nvidia": False, "gpus": []}), "rvc-nvidia")


class TrainingDatasetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        (self.project / "reference").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_copies_reference_clips(self) -> None:
        for index in range(3):
            make_wav(self.project / "reference" / f"{index}.wav", 3.0)
        dataset = rvc.training_dataset(self.project, self.root / "work")
        self.assertEqual(len(list(dataset.glob("*.wav"))), 3)

    def test_rejects_short_dataset(self) -> None:
        for index in range(2):
            make_wav(self.project / "reference" / f"{index}.wav", 1.0)
        with self.assertRaises(rvc.RvcError):
            rvc.training_dataset(self.project, self.root / "work")

    def test_missing_dataset_raises(self) -> None:
        (self.project / "reference").rmdir()
        with self.assertRaises(rvc.RvcError):
            rvc.training_dataset(self.project, self.root / "work")


class FakeProcess:
    def __init__(self, lines: list[str], code: int = 0) -> None:
        self.stdout = iter([line + "\n" for line in lines])
        self._code = code

    def wait(self, timeout=None) -> int:
        return self._code

    def poll(self) -> int:
        return self._code


class BridgeParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.job = jobs.Job("rvc_train")

    def run_bridge(self, lines: list[str], code: int = 0):
        process = FakeProcess(lines, code)
        with mock.patch.object(rvc, "runtime_python", return_value=Path("python")), \
             mock.patch.object(rvc, "bridge_path", return_value=Path("bridge.py")), \
             mock.patch.object(rvc.subprocess, "Popen", return_value=process):
            return rvc._run_bridge(
                self.job, Path("root"), "train_bridge.py", {}, timeout=30
            )

    def test_parses_json_progress_and_epoch(self) -> None:
        result = self.run_bridge(
            [
                json.dumps({"type": "stage", "stage": "train", "total": 10}),
                "INFO Epoch: 5",
                json.dumps({"type": "progress", "done": 2, "total": 3, "file": "a.wav"}),
                json.dumps({"type": "done", "model": "x.pth", "segments": 4}),
            ]
        )
        self.assertEqual(result["model"], "x.pth")
        self.assertEqual(result["segments"], 4)
        self.assertEqual(self.job.total, 3)
        self.assertEqual(self.job.current, 2)
        self.assertIn("换声 2/3", self.job.message)

    def test_named_stage_updates_message(self) -> None:
        self.run_bridge(
            [
                json.dumps({"type": "stage", "stage": "f0", "total": 4}),
                json.dumps({"type": "stage", "stage": "train", "total": 4}),
            ]
        )
        self.assertEqual(self.job.message, "训练换声模型")

    def test_error_event_raises(self) -> None:
        with self.assertRaises(rvc.RvcError):
            self.run_bridge([json.dumps({"type": "error", "error": "显存不足"})])

    def test_nonzero_exit_raises_with_tail(self) -> None:
        with self.assertRaises(rvc.RvcError) as caught:
            self.run_bridge(["boom"], code=1)
        self.assertIn("boom", str(caught.exception))


class GuardTest(unittest.TestCase):
    def test_train_requires_runtime(self) -> None:
        with mock.patch.object(rvc, "find_install", return_value=None):
            with self.assertRaises(rvc.RvcError):
                rvc.train(jobs.Job("rvc_train"), Path("project"))

    def test_convert_requires_model(self) -> None:
        with mock.patch.object(rvc, "find_install", return_value=Path("rvc")):
            with self.assertRaises(rvc.RvcError):
                rvc.convert(
                    jobs.Job("voice_convert"),
                    root=Path("rvc"),
                    model=Path("missing.pth"),
                    index="",
                    input_dir=Path("in"),
                    output_dir=Path("out"),
                )


if __name__ == "__main__":
    unittest.main()
