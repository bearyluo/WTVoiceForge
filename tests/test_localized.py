from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import localized, voice
from tests.test_voice import make_wav


BANK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<objects serializationModel="Studio.02.02.00">
  <object class="Bank" id="{{{guid}}}">
    <property name="name"><value>{name}</value></property>
    <object class="AudioTable" id="{{{table}}}">
      <property name="name"><value>Audio Table</value></property>
      <property name="sourceDirectory"><value>{source}</value></property>
    </object>
  </object>
</objects>
"""


def write_bank(kit: Path, guid: str, name: str, source: str) -> None:
    path = kit / "Metadata" / "Bank" / f"{{{guid}}}.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        BANK_XML.format(guid=guid, table=guid[::-1], name=name, source=source),
        encoding="utf-8",
    )


class LocalizedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kit = self.root / "kit"
        self.kit.mkdir()
        (self.kit / "sounds.fspro").write_text("{}", encoding="utf-8")
        self.game = self.root / "game"
        (self.game / "sound").mkdir(parents=True)
        (self.game / "sound" / "_crew_dialogs_ground_en_us.assets.bank").write_bytes(
            b"bank"
        )
        write_bank(
            self.kit,
            "11111111-1111-1111-1111-111111111111",
            "_crew_dialogs_ground_en_us",
            "Assets/crew/en_us",
        )
        write_bank(
            self.kit,
            "22222222-2222-2222-2222-222222222222",
            "_crew_dialogs_ground_en",
            "Assets/crew/en",
        )
        assets = self.kit / "Assets" / "crew"
        make_wav(assets / "en_us" / "med" / "commander" / "a.wav", 0.5)
        make_wav(assets / "en_us" / "high" / "commander" / "a.wav", 0.6)
        make_wav(assets / "en_us" / "med" / "commander" / "b.wav", 0.7)
        make_wav(assets / "en" / "med" / "commander" / "c.wav", 0.8)
        self.game_streams = [
            {"index": 1, "name": "a", "duration_sec": 0.5},
            {"index": 2, "name": "b", "duration_sec": 0.7},
            {"index": 3, "name": "c", "duration_sec": 0.8},
            {"index": 4, "name": "d", "duration_sec": 0.9},
        ]
        localized.clear_cache()
        self.zh_streams = [
            {"index": 1, "name": "a", "duration_sec": 0.5},
            {"index": 2, "name": "b", "duration_sec": 0.7},
        ]
        self.sm_zh_streams = [
            {"index": 7, "name": "b", "duration_sec": 0.7},
            {"index": 8, "name": "c", "duration_sec": 0.8},
        ]

        def fake_game_streams(path):
            name = Path(path).name
            if "_ground_sm_zh." in name:
                return list(self.sm_zh_streams)
            return list(self.zh_streams) if "_zh." in name else list(self.game_streams)

        self.patch = mock.patch.object(
            localized, "game_streams", side_effect=fake_game_streams
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        localized.clear_cache()
        self.tmp.cleanup()

    def test_banks_and_streams(self) -> None:
        banks = localized.banks(self.kit)
        self.assertEqual(len(banks), 2)
        self.assertIn("_crew_dialogs_ground_en_us", {item["id"] for item in banks})

        items = localized.streams(
            self.kit, "_crew_dialogs_ground_en_us", self.game
        )
        self.assertEqual([item["event"] for item in items], ["a", "b", "c", "d"])
        by_name = {item["event"]: item for item in items}
        self.assertEqual(len(by_name["a"]["assets"]), 2)
        self.assertFalse(by_name["a"]["game_only"])
        self.assertFalse(by_name["b"]["game_only"])
        self.assertTrue(by_name["c"]["game_only"])
        # c 在英语工程里有同名声线，用英国原声做备用。
        self.assertTrue(by_name["c"]["assets"][0]["origin"].endswith("c.wav"))
        # d 只能从游戏 bank 提取，asset 记录流序号。
        self.assertEqual(by_name["d"]["assets"][0]["game_index"], 4)
        self.assertEqual(by_name["a"]["duration_sec"], 0.5)

    def test_source_audio_fallback_and_game_extract(self) -> None:
        fallback = localized.source_audio(
            self.kit, "_crew_dialogs_ground_en_us", "c", game_path=self.game
        )
        self.assertEqual(fallback.name, "c.wav")
        self.assertIn("crew/en/", fallback.as_posix())

        target = self.root / "cache" / "d.wav"

        def fake_extract(game_bank, index, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            make_wav(destination, 0.9)
            return destination

        with mock.patch.object(localized, "extract_stream", side_effect=fake_extract):
            audio = localized.source_audio(
                self.kit,
                "_crew_dialogs_ground_en_us",
                "d",
                game_path=self.game,
                data_dir=self.root,
            )
        self.assertIsNotNone(audio)
        self.assertTrue(audio.is_file())
        self.assertNotEqual(audio.resolve(), target.resolve())

    def test_add_localized_lines_and_copy(self) -> None:
        target = voice.create_project(self.root / "data", "美军中文")
        source = voice.create_project(self.root / "data", "中文原包")
        line = voice.add_line(
            self.root / "data", source["id"], "收到", event="a"
        )["line"]
        out = voice.line_audio_path(self.root / "data", source["id"], line["id"])
        make_wav(out, 0.5)
        voice.mark_line(
            self.root / "data",
            source["id"],
            line["id"],
            status="ready",
            audio=f"audio/{line['id']}.wav",
            duration_sec=0.5,
        )

        items = localized.streams(
            self.kit, "_crew_dialogs_ground_en_us", self.game
        )
        result = voice.add_localized_lines(
            self.root / "data",
            target["id"],
            "_crew_dialogs_ground_en_us",
            items,
            source_project=source["id"],
        )
        self.assertEqual(result["added"], 4)
        project = result["project"]
        matched = next(row for row in project["lines"] if row["event"] == "a")
        self.assertEqual(matched["text"], "收到")
        self.assertTrue(matched["audio"])
        self.assertEqual(matched["status"], "ready")
        self.assertEqual(len(result["missing"]), 3)
        self.assertTrue(project["lines"][0]["source"]["localized"])

        again = voice.add_localized_lines(
            self.root / "data",
            target["id"],
            "_crew_dialogs_ground_en_us",
            items,
        )
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped"], 4)

    def test_extract_indexes_bulk(self) -> None:
        bank = self.game / "sound" / "_crew_dialogs_ground_en_us.assets.bank"

        def fake_run(command, **kwargs):
            out = Path(command[command.index("-o") + 1]).parent
            out.mkdir(parents=True, exist_ok=True)
            (out / "134_a.wav").write_bytes(b"RIFF")
            (out / "135_b.wav").write_bytes(b"RIFF")
            return SimpleNamespace(returncode=0)

        targets = {
            135: self.root / "out" / "b.wav",
            134: self.root / "out" / "a.wav",
        }
        with mock.patch.object(
            localized.vgmstream, "find", return_value=Path("vgmstream-cli")
        ), mock.patch.object(localized.subprocess, "run", side_effect=fake_run):
            done = localized.extract_indexes(bank, targets)
        self.assertEqual(done, 2)
        self.assertTrue(targets[134].is_file())
        self.assertTrue(targets[135].is_file())

    def test_copy_official_audio(self) -> None:
        cn_bank = self.game / "sound" / "_crew_dialogs_ground_zh.assets.bank"
        cn_bank.write_bytes(b"bank")
        project = voice.create_project(self.root / "data", "官方中文")
        items = localized.streams(
            self.kit, "_crew_dialogs_ground_en_us", self.game
        )
        voice.add_localized_lines(
            self.root / "data", project["id"], "_crew_dialogs_ground_en_us", items
        )
        def fake_extract(game_bank, targets):
            for destination in targets.values():
                make_wav(destination, 0.5)
            return len(targets)

        with mock.patch.object(localized, "extract_indexes", side_effect=fake_extract):
            result = localized.copy_official_audio(
                self.root / "data",
                project["id"],
                "_crew_dialogs_ground_en_us",
                self.game,
            )
        self.assertEqual(result["source_bank"], "_crew_dialogs_ground_zh")
        self.assertEqual(result["copied"], 2)
        self.assertEqual(sorted(result["missing"]), ["c", "d"])
        lines = {line["event"]: line for line in result["project"]["lines"]}
        self.assertTrue(lines["a"]["audio"])
        self.assertEqual(lines["a"]["audio_source"], "official_cn")
        self.assertEqual(lines["a"]["status"], "ready")
        self.assertFalse(lines["c"]["audio"])

    def test_copy_official_audio_ground_supplement(self) -> None:
        (self.game / "sound" / "_crew_dialogs_ground_zh.assets.bank").write_bytes(b"bank")
        (self.game / "sound" / "_crew_dialogs_ground_sm_zh.assets.bank").write_bytes(b"bank")
        project = voice.create_project(self.root / "data", "官方中文补充")
        items = localized.streams(
            self.kit, "_crew_dialogs_ground_en_us", self.game
        )
        voice.add_localized_lines(
            self.root / "data", project["id"], "_crew_dialogs_ground_en_us", items
        )

        def fake_extract(game_bank, targets):
            for destination in targets.values():
                make_wav(destination, 0.5)
            return len(targets)

        with mock.patch.object(localized, "extract_indexes", side_effect=fake_extract):
            result = localized.copy_official_audio(
                self.root / "data",
                project["id"],
                "_crew_dialogs_ground_en_us",
                self.game,
            )
        self.assertEqual(
            result["source_banks"],
            ["_crew_dialogs_ground_zh", "_crew_dialogs_ground_sm_zh"],
        )
        self.assertEqual(result["copied"], 3)
        self.assertEqual(result["supplemented"], 1)
        self.assertEqual(result["missing"], ["d"])
        lines = {line["event"]: line for line in result["project"]["lines"]}
        self.assertEqual(lines["b"]["audio_source"], "official_cn")
        self.assertEqual(lines["c"]["audio_source"], "official_cn_extra")
        self.assertFalse(lines["d"]["audio"])

    def test_official_cn_names_includes_supplement(self) -> None:
        (self.game / "sound" / "_crew_dialogs_ground_zh.assets.bank").write_bytes(b"bank")
        (self.game / "sound" / "_crew_dialogs_ground_sm_zh.assets.bank").write_bytes(b"bank")
        names = localized.official_cn_names(self.game, "ground")
        self.assertEqual(names, {"a", "b", "c"})

    def test_coverage_report(self) -> None:
        (self.game / "sound" / "_crew_dialogs_ground_en.assets.bank").write_bytes(b"bank")
        (self.game / "sound" / "_crew_dialogs_ground_zh.assets.bank").write_bytes(b"bank")
        (self.game / "sound" / "_crew_dialogs_ground_sm_zh.assets.bank").write_bytes(b"bank")
        (self.game / "sound" / "_crew_dialogs_ground_fr.assets.bank").write_bytes(b"bank")
        result = localized.coverage(self.kit, self.game)
        self.assertEqual(
            result["zh_banks"]["ground"],
            ["_crew_dialogs_ground_zh", "_crew_dialogs_ground_sm_zh"],
        )
        self.assertTrue(result["has_game"])
        rows = {row["bank"]: row for row in result["banks"]}
        us = rows["_crew_dialogs_ground_en_us"]
        self.assertEqual(us["kit"], 2)
        self.assertEqual(us["game"], 4)
        self.assertEqual(us["game_only"], 2)
        self.assertEqual(us["zh_cover"], 3)
        self.assertEqual(us["missing"], ["d"])
        self.assertEqual(us["zh_missing"], 1)
        self.assertTrue(us["has_game_bank"])
        self.assertIn(
            "_crew_dialogs_ground_fr",
            {item["bank"] for item in result["extra_game_banks"]},
        )
        self.assertNotIn(
            "_crew_dialogs_ground_zh",
            {item["bank"] for item in result["extra_game_banks"]},
        )

    def test_invalid_bank(self) -> None:
        with self.assertRaises(localized.LocalizedError):
            localized.streams(self.kit, "not_a_table", self.game)


if __name__ == "__main__":
    unittest.main()
