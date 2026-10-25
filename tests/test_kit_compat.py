from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import kit_compat
from tests.test_voice import make_wav


class KitCompatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kit = self.root / "kit"
        self.kit.mkdir()
        self.base = self.root / "base"
        self.assets = self.root / "assets"
        self.assets.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_restores_event_metadata_and_audio_from_game(self) -> None:
        def fake_extract(_bank, _index, target: Path) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"audio")
            return target

        with mock.patch.object(
            kit_compat.localized, "game_bank_path", return_value=Path("aircraft_gui.assets.bank")
        ), mock.patch.object(
            kit_compat.localized,
            "game_streams",
            return_value=[{"index": 163, "name": "ui_kill_assist", "duration_sec": 2.0}],
        ), mock.patch.object(
            kit_compat.localized, "extract_stream", side_effect=fake_extract
        ):
            result = kit_compat.restore_aircraft_gui(
                self.base, self.kit, self.assets, "game"
            )

        self.assertEqual(result["error"], "")
        self.assertEqual(
            sorted(result["restored"]), ["audio", "audio_meta", "event"]
        )
        event = self.base / "Metadata" / "Event" / kit_compat.KILL_EVENT_FILE
        audio_meta = self.base / "Metadata" / "AudioFile" / kit_compat.KILL_AUDIO_FILE
        self.assertIn("enemy_kill_assist", event.read_text(encoding="utf-8"))
        self.assertIn("gui/ui_kill_assist.wav", audio_meta.read_text(encoding="utf-8"))
        self.assertEqual(
            (self.assets / kit_compat.KILL_ASSET).read_bytes(), b"audio"
        )

    def test_complete_kit_is_untouched(self) -> None:
        event = self.kit / "Metadata" / "Event" / kit_compat.KILL_EVENT_FILE
        event.parent.mkdir(parents=True)
        event.write_text("<old/>", encoding="utf-8")
        audio_meta = self.kit / "Metadata" / "AudioFile" / kit_compat.KILL_AUDIO_FILE
        audio_meta.parent.mkdir(parents=True)
        audio_meta.write_text("<old/>", encoding="utf-8")
        audio = self.kit / "Assets" / kit_compat.KILL_ASSET
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"audio")

        result = kit_compat.restore_aircraft_gui(
            self.base, self.kit, self.assets, "game"
        )
        self.assertEqual(result, {"restored": [], "error": ""})
        self.assertEqual(event.read_text(encoding="utf-8"), "<old/>")
        self.assertEqual(audio_meta.read_text(encoding="utf-8"), "<old/>")

    def test_missing_game_bank_keeps_build_untouched(self) -> None:
        with mock.patch.object(
            kit_compat.localized, "game_bank_path", return_value=None
        ):
            result = kit_compat.restore_aircraft_gui(
                self.base, self.kit, self.assets, None
            )
        self.assertIn("aircraft_gui", result["error"])
        self.assertEqual(result["restored"], [])
        self.assertFalse(
            (self.base / "Metadata" / "Event" / kit_compat.KILL_EVENT_FILE).exists()
        )

    def test_adjust_kill_assist_event_length(self) -> None:
        event = self.base / "Metadata" / "Event" / kit_compat.KILL_EVENT_FILE
        event.parent.mkdir(parents=True)
        event.write_text(kit_compat.KILL_EVENT_XML, encoding="utf-8")
        audio = self.root / "voice.wav"
        make_wav(audio, 0.9)
        changed = kit_compat.adjust_kill_assist_length(
            self.base, {kit_compat.KILL_ASSET: {"audio": audio}}
        )
        self.assertEqual(changed, 1)
        text = event.read_text(encoding="utf-8")
        self.assertIn("<value>0.900000</value>", text)
        self.assertEqual(
            kit_compat.adjust_kill_assist_length(self.base, {}), 0
        )


if __name__ == "__main__":
    unittest.main()
