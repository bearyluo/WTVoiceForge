from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import game


class GameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "War Thunder"
        self.root.mkdir()
        (self.root / "aces.exe").write_bytes(b"stub")
        (self.root / "sound").mkdir()
        (self.root / "sound" / "aircraft_gui.assets.bank").write_bytes(b"bank")
        (self.root / "sound" / "aircraft_gui.bank").write_bytes(b"meta")
        (self.root / "config.blk").write_text(
            "sound{\n  fmod_sound_enable:b=yes\n  speakerMode:t=\"auto\"\n  enable_mod:b=no\n}\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_is_warthunder_dir(self) -> None:
        self.assertTrue(game.is_warthunder_dir(self.root))
        self.assertFalse(game.is_warthunder_dir(Path(self.tmp.name) / "missing"))

        broken = Path(self.tmp.name) / "broken"
        broken.mkdir()
        (broken / "aces.exe").write_bytes(b"stub")
        self.assertFalse(game.is_warthunder_dir(broken))

    def test_win64_layout(self) -> None:
        root = Path(self.tmp.name) / "WT win64"
        root.mkdir()
        (root / "win64").mkdir()
        (root / "win64" / "aces.exe").write_bytes(b"stub")
        (root / "sound").mkdir()
        (root / "config.blk").write_text("sound{\n enable_mod:b=yes\n}\n", encoding="utf-8")

        self.assertTrue(game.is_warthunder_dir(root))
        info = game.scan_game(root)
        self.assertIn("win64", Path(info.aces_exe).parts)
        self.assertTrue(info.enable_mod)

    def test_read_config_blk(self) -> None:
        config = game.read_config_blk(self.root)
        self.assertTrue(config["has_sound_section"])
        self.assertFalse(config["enable_mod"])
        self.assertTrue(config["fmod_sound_enable"])
        self.assertEqual(config["error"], "")

    def test_scan_game(self) -> None:
        info = game.scan_game(self.root)
        self.assertEqual(info.bank_count, 1)
        self.assertEqual(info.mod_file_count, 0)
        self.assertFalse(info.enable_mod)
        self.assertTrue(info.fmod_sound_enable)
        self.assertEqual(info.path, str(self.root.resolve()))

    def test_detect_installs_with_extra_path(self) -> None:
        installs = game.detect_installs(extra_paths=[self.root])
        self.assertTrue(any(item.path == str(self.root.resolve()) for item in installs))


if __name__ == "__main__":
    unittest.main()
