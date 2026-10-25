from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import banks


class BanksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sound = Path(self.tmp.name) / "sound"
        self.sound.mkdir()
        entries = {
            "_crew_dialogs_common_zh": (1024, True),
            "_crew_dialogs_ground_zh": (2048, True),
            "aircraft_gui": (4096, True),
            "aircraft_guns": (8192, True),
            "weird_bank": (100, False),
        }
        for name, (size, with_meta) in entries.items():
            (self.sound / f"{name}.assets.bank").write_bytes(b"x" * size)
            if with_meta:
                (self.sound / f"{name}.bank").write_bytes(b"y")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_banks_classifies_crew_and_global(self) -> None:
        items = banks.scan_banks(self.sound)
        by_name = {item.name: item for item in items}

        self.assertEqual(len(items), 5)
        self.assertEqual(by_name["_crew_dialogs_ground_zh"].category, "crew_ground")
        self.assertEqual(by_name["_crew_dialogs_ground_zh"].language, "zh")
        self.assertEqual(by_name["_crew_dialogs_ground_zh"].language_label, "中文")
        self.assertEqual(by_name["_crew_dialogs_common_zh"].category, "crew_common")
        self.assertEqual(by_name["aircraft_gui"].scope, "air")
        self.assertEqual(by_name["aircraft_gui"].label, "座舱 VWS / 告警 / 击杀提示")
        self.assertTrue(by_name["aircraft_gui"].has_meta)
        self.assertEqual(by_name["weird_bank"].category, "other")
        self.assertFalse(by_name["weird_bank"].has_meta)
        self.assertEqual(by_name["aircraft_guns"].size_bytes, 8192)

    def test_summary(self) -> None:
        items = banks.scan_banks(self.sound)
        summary = banks.summarize_banks(items)

        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["total_size_mb"], 0.0)
        self.assertEqual(len(summary["crew_languages"]), 1)
        self.assertEqual(summary["crew_languages"][0]["code"], "zh")
        self.assertTrue(summary["crew_languages"][0]["common"])
        self.assertTrue(summary["crew_languages"][0]["ground"])
        key_names = {item["name"] for item in summary["key_banks"]}
        self.assertIn("aircraft_gui", key_names)
        self.assertIn("aircraft_guns", key_names)
        self.assertIn("tanks_effects", key_names)
        self.assertEqual(summary["other_count"], 1)

    def test_sm_banks_are_variants(self) -> None:
        meta = banks.classify_bank("_crew_dialogs_ground_sm_zh")
        self.assertEqual(meta["category"], "crew_variant")
        self.assertEqual(meta["language"], "sm_zh")
        self.assertEqual(meta["language_label"], "备用 · 中文")

    def test_empty_directory(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(banks.scan_banks(empty), [])
        self.assertEqual(banks.scan_banks(Path(self.tmp.name) / "missing"), [])


if __name__ == "__main__":
    unittest.main()
