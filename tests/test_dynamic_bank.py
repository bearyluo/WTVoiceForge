from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from app import dynamic_bank, localized
from tests.test_voice import make_wav


class ParseTest(unittest.TestCase):
    def test_parse_variants(self) -> None:
        self.assertEqual(
            dynamic_bank.parse_bank("_crew_dialogs_ground_fr"),
            ("ground", "fr", False),
        )
        self.assertEqual(
            dynamic_bank.parse_bank("_crew_dialogs_ground_sm_de"),
            ("ground", "de", True),
        )
        self.assertEqual(
            dynamic_bank.parse_bank("_crew_dialogs_common_en_au"),
            ("common", "en_au", False),
        )
        self.assertIsNone(dynamic_bank.parse_bank("aircraft_gui"))
        self.assertTrue(dynamic_bank.is_crew_bank("_crew_dialogs_naval_jp"))
        self.assertEqual(
            dynamic_bank.label("_crew_dialogs_ground_sm_fr"),
            f"{localized.LANG_LABELS['fr']} · 陆战补充",
        )


class GameRowsTest(unittest.TestCase):
    def test_groups_duplicates_and_keeps_variants(self) -> None:
        streams = [
            {"index": 4, "name": "voice_message_a", "duration_sec": 0.4},
            {"index": 2, "name": "voice_message_a", "duration_sec": 0.9},
            {"index": 7, "name": "voice_message_b", "duration_sec": 0.5},
        ]
        with mock.patch.object(
            dynamic_bank.localized, "game_streams", return_value=streams
        ), mock.patch.object(
            dynamic_bank.localized,
            "game_bank_path",
            return_value=Path("x/assets.bank"),
        ):
            rows = dynamic_bank.game_rows("_crew_dialogs_ground_fr", "game")
        self.assertEqual([row["event"] for row in rows], ["voice_message_a", "voice_message_b"])
        first = rows[0]
        self.assertEqual(first["duration_sec"], 0.9)
        self.assertEqual([asset["game_index"] for asset in first["assets"]], [4, 2])
        self.assertEqual(
            first["assets"][0]["path"],
            "dialogs_dynamic/_crew_dialogs_ground_fr/4/voice_message_a.wav",
        )
        self.assertTrue(first["game_only"])

    def test_missing_bank_raises(self) -> None:
        with mock.patch.object(dynamic_bank.localized, "game_bank_path", return_value=None):
            with self.assertRaises(dynamic_bank.DynamicBankError):
                dynamic_bank.game_rows("_crew_dialogs_ground_fr", "game")


class MetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "base"
        (self.base / "Metadata" / "Bank").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_audio_table_bank(self) -> None:
        rel = dynamic_bank.ensure_metadata(self.base, "_crew_dialogs_ground_sm_de")
        self.assertEqual(rel, "dialogs_dynamic/_crew_dialogs_ground_sm_de")
        files = list((self.base / "Metadata" / "Bank").glob("*.xml"))
        self.assertEqual(len(files), 1)
        tree = ET.parse(files[0])
        root = tree.getroot()
        bank = next(
            obj for obj in root.findall("object")
            if obj.get("class") == "Bank"
        )
        self.assertEqual(
            bank.findtext("property[@name='name']/value"),
            "_crew_dialogs_ground_sm_de",
        )
        rel_node = bank.find("relationship[@name='audioTable']")
        self.assertIsNotNone(rel_node)
        table_id = rel_node.findtext("destination")
        table = next(
            obj for obj in root.findall("object")
            if obj.get("class") == "AudioTable" and obj.get("id") == table_id
        )
        self.assertEqual(
            table.findtext("property[@name='sourceDirectory']/value"),
            "Assets/dialogs_dynamic/_crew_dialogs_ground_sm_de",
        )
        self.assertEqual(
            table.findtext("property[@name='includeSubDirectories']/value"),
            "true",
        )

    def test_updates_existing_matching_bank_without_duplicates(self) -> None:
        path = self.base / "Metadata" / "Bank" / "{abc}.xml"
        path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="Studio.02.02.00">
  <object class="Bank" id="{abc}">
    <property name="name"><value>_crew_dialogs_ground_fr</value></property>
  </object>
</objects>
""",
            encoding="utf-8",
        )
        dynamic_bank.ensure_metadata(self.base, "_crew_dialogs_ground_fr")
        dynamic_bank.ensure_metadata(self.base, "_crew_dialogs_ground_fr")
        root = ET.parse(path).getroot()
        self.assertEqual(len(root.findall("object[@class='AudioTable']")), 1)
        self.assertEqual(
            len(root.findall("object[@class='Bank']/relationship[@name='audioTable']")),
            1,
        )


class ExtractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "game"
        (self.game / "sound").mkdir(parents=True)
        bank = self.game / "sound" / "_crew_dialogs_ground_fr.assets.bank"
        bank.write_bytes(b"bank")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extract_originals_creates_indexed_files(self) -> None:
        streams = [
            {"index": 1, "name": "a", "duration_sec": 0.2},
            {"index": 2, "name": "a", "duration_sec": 0.3},
        ]

        def fake_extract(_bank, targets, temp_root=None):
            for target in targets.values():
                make_wav(target, 0.1)
            return len(targets)

        assets = self.root / "assets"
        with mock.patch.object(
            dynamic_bank.localized, "game_streams", return_value=streams
        ), mock.patch.object(
            dynamic_bank.localized, "extract_indexes", side_effect=fake_extract
        ) as extract:
            count = dynamic_bank.extract_originals(
                assets, "_crew_dialogs_ground_fr", self.game
            )
        self.assertEqual(count, 2)
        self.assertTrue(
            (assets / "dialogs_dynamic/_crew_dialogs_ground_fr/1/a.wav").is_file()
        )
        self.assertTrue(
            (assets / "dialogs_dynamic/_crew_dialogs_ground_fr/2/a.wav").is_file()
        )
        targets = extract.call_args.args[1]
        self.assertEqual(sorted(targets), [1, 2])

    def test_extract_originals_reuses_cache_until_bank_changes(self) -> None:
        streams = [
            {"index": 1, "name": "a", "duration_sec": 0.2},
            {"index": 2, "name": "a", "duration_sec": 0.3},
        ]

        def fake_extract(_bank, targets, temp_root=None):
            for target in targets.values():
                make_wav(target, 0.1)
            return len(targets)

        assets = self.root / "assets"
        data = self.root / "data"
        with mock.patch.object(
            dynamic_bank.localized, "game_streams", return_value=streams
        ), mock.patch.object(
            dynamic_bank.localized, "extract_indexes", side_effect=fake_extract
        ) as extract:
            first = dynamic_bank.extract_originals(
                assets, "_crew_dialogs_ground_fr", self.game, data_dir=data
            )
            second = dynamic_bank.extract_originals(
                assets, "_crew_dialogs_ground_fr", self.game, data_dir=data
            )
            self.assertEqual(first, 2)
            self.assertEqual(second, 2)
            self.assertEqual(extract.call_count, 1)
            cached = list(
                (data / "cache/dynamic/_crew_dialogs_ground_fr").glob("*/.source.json")
            )
            self.assertEqual(len(cached), 1)

            bank = self.game / "sound" / "_crew_dialogs_ground_fr.assets.bank"
            bank.write_bytes(b"bank-2")
            bank.touch()
            third = dynamic_bank.extract_originals(
                assets, "_crew_dialogs_ground_fr", self.game, data_dir=data
            )
        self.assertEqual(third, 2)
        self.assertEqual(extract.call_count, 2)

    def test_missing_game_bank_raises(self) -> None:
        with self.assertRaises(dynamic_bank.DynamicBankError):
            dynamic_bank.extract_originals(
                self.root / "assets", "_crew_dialogs_ground_it", self.game
            )

    def test_source_audio_uses_game_cache(self) -> None:
        streams = [
            {"index": 9, "name": "voice_message_test", "duration_sec": 0.2},
            {"index": 10, "name": "voice_message_other", "duration_sec": 0.2},
        ]

        def fake_extract(_bank, index, target):
            make_wav(target, 0.1)
            return target

        with mock.patch.object(
            dynamic_bank.localized, "game_streams", return_value=streams
        ), mock.patch.object(
            dynamic_bank.localized, "extract_stream", side_effect=fake_extract
        ) as extract:
            target = dynamic_bank.source_audio(
                "_crew_dialogs_ground_fr",
                "voice_message_test",
                self.game,
                self.root / "data",
            )
        self.assertIsNotNone(target)
        self.assertTrue(target.is_file())
        self.assertEqual(target.name, "0009_voice_message_test.wav")
        self.assertEqual(extract.call_args.args[1], 9)


if __name__ == "__main__":
    unittest.main()
