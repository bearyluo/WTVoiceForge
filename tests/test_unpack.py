from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from app import jobs, unpack


def write_wav(path: Path, seconds: float = 0.25, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


class UnpackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "War Thunder"
        (self.game / "sound").mkdir(parents=True)
        (self.game / "sound" / "aircraft_gui.assets.bank").write_bytes(b"RIFF")
        (self.game / "sound" / "aircraft_gui.bank").write_bytes(b"bank")
        self.data_dir = self.root / "data"
        self.catalog = {
            "assets": {
                "g1": {"path": "gui/enemy_destroyed.wav", "exists": True},
                "g2": {"path": "gui/foo-001.wav", "exists": True},
                "g3": {"path": "other/bar.wav", "exists": True},
            },
            "events": [
                {"event": "enemy_destroyed", "bank": "aircraft_gui", "asset_guids": ["g1"]},
                {"event": "foo", "bank": "aircraft_gui", "asset_guids": ["g2"]},
                {"event": "bar", "bank": "aircraft_gui", "asset_guids": ["g3"]},
            ],
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bank_dir_rejects_traversal(self) -> None:
        with self.assertRaises(ValueError):
            unpack.bank_dir("../evil", self.data_dir)
        with self.assertRaises(ValueError):
            unpack.bank_dir("a/b", self.data_dir)
        self.assertEqual(unpack.bank_dir("aircraft_gui", self.data_dir).name, "aircraft_gui")

    def test_sanitize_and_normalize(self) -> None:
        self.assertEqual(unpack.sanitize_component('bad<>:"/\\|?* name'), "bad_________ name")
        self.assertEqual(unpack.normalize_name("gui/Foo-001"), "foo")
        self.assertEqual(unpack.normalize_name("vws_missile_launch"), "vws_missile_launch")

    def test_match_streams_levels(self) -> None:
        streams = [
            {"name": "enemy_destroyed"},
            {"name": "foo"},
            {"name": "bar"},
            {"name": "unknown"},
        ]
        counts = unpack.match_streams(streams, self.catalog, "aircraft_gui")
        self.assertEqual(streams[0]["match"], "basename")
        self.assertEqual(streams[0]["asset"], "gui/enemy_destroyed.wav")
        self.assertEqual(streams[1]["match"], "normalized")
        self.assertEqual(streams[1]["asset"], "gui/foo-001.wav")
        self.assertEqual(streams[2]["match"], "basename")
        self.assertEqual(streams[3]["match"], "")
        self.assertEqual(counts["matched_streams"], 3)
        self.assertEqual(counts["unmatched_streams"], 1)
        self.assertEqual(counts["catalog_assets"], 3)
        self.assertEqual(counts["matched_assets"], 3)

    def test_process_output_writes_manifest(self) -> None:
        temp = self.root / "temp"
        staging = self.root / "staging"
        write_wav(temp / "1_enemy_destroyed.wav")
        write_wav(temp / "2_foo-001.wav")
        write_wav(temp / "3_bad name.wav")
        summary = unpack._process_output(
            temp,
            staging,
            "aircraft_gui",
            self.catalog,
            {"assets_bank": "aircraft_gui.assets.bank"},
            "r2117",
        )
        self.assertEqual(summary["counts"]["streams"], 3)
        self.assertEqual(summary["counts"]["matched_streams"], 2)
        manifest = json.loads((staging / unpack.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["bank"], "aircraft_gui")
        self.assertEqual(len(manifest["streams"]), 3)
        self.assertTrue(all((staging / item["file"]).is_file() for item in manifest["streams"]))
        self.assertTrue(all(item["file"].startswith("streams/") for item in manifest["streams"]))
        self.assertEqual(
            {item["name"] for item in manifest["streams"]},
            {"enemy_destroyed", "foo-001", "bad name"},
        )

    def test_unpack_bank_with_fake_process_and_cache(self) -> None:
        class FakeProcess:
            def __init__(self, command, **kwargs):
                out_dir = Path(command[4]).parent
                write_wav(out_dir / "1_enemy_destroyed.wav")
                write_wav(out_dir / "2_foo-001.wav")
                self.returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        job = jobs.Job("unpack", "aircraft_gui")
        with mock.patch.object(unpack.vgmstream, "ensure", return_value=Path("fake.exe")), mock.patch.object(
            unpack.vgmstream, "version", return_value="r2117"
        ), mock.patch.object(unpack.subprocess, "Popen", FakeProcess), mock.patch.object(
            unpack.shutil, "disk_usage"
        ) as usage:
            usage.return_value = type("Usage", (), {"free": 10 * 1024 ** 3})()
            result = unpack.unpack_bank(
                job,
                self.game,
                "aircraft_gui",
                catalog_data=self.catalog,
                base=self.data_dir,
            )
            self.assertFalse(result["cached"])
            self.assertEqual(result["counts"]["streams"], 2)
            target = unpack.bank_dir("aircraft_gui", self.data_dir)
            self.assertTrue((target / unpack.MANIFEST_NAME).is_file())
            self.assertTrue((target / "streams" / "00001_enemy_destroyed.wav").is_file())

            with mock.patch.object(unpack.subprocess, "Popen") as popen:
                cached = unpack.unpack_bank(
                    job,
                    self.game,
                    "aircraft_gui",
                    catalog_data=self.catalog,
                    base=self.data_dir,
                )
                popen.assert_not_called()
                self.assertTrue(cached["cached"])

    def test_unpack_cancel_cleans_temp(self) -> None:
        class HangingProcess:
            def __init__(self, command, **kwargs):
                out_dir = Path(command[4]).parent
                write_wav(out_dir / "1_x.wav")
                self.returncode = None
                self.terminated = False

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.returncode = -15
                return -15

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def kill(self):
                self.returncode = -9

        job = jobs.Job("unpack", "aircraft_gui")
        job.cancel_event.set()
        with mock.patch.object(unpack.vgmstream, "ensure", return_value=Path("fake.exe")), mock.patch.object(
            unpack.vgmstream, "version", return_value="r2117"
        ), mock.patch.object(unpack.subprocess, "Popen", HangingProcess), mock.patch.object(
            unpack.shutil, "disk_usage"
        ) as usage, mock.patch.object(unpack.time, "sleep", return_value=None):
            usage.return_value = type("Usage", (), {"free": 10 * 1024 ** 3})()
            with self.assertRaises(jobs.JobCancelled):
                unpack.unpack_bank(
                    job,
                    self.game,
                    "aircraft_gui",
                    catalog_data=self.catalog,
                    base=self.data_dir,
                )
        leftovers = list(unpack.output_root(self.data_dir).glob(".tmp-*"))
        self.assertEqual(leftovers, [])

    def test_stream_file_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            unpack.stream_file("aircraft_gui", "1", self.data_dir)


if __name__ == "__main__":
    unittest.main()
