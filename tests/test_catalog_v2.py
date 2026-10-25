from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app import catalog

BANK_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BANK2_GUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
EVENT1_GUID = "11111111-1111-1111-1111-111111111111"
EVENT2_GUID = "22222222-2222-2222-2222-222222222222"
EVENT3_GUID = "33333333-3333-3333-3333-333333333333"
EVENT4_GUID = "44444444-4444-4444-4444-444444444444"
AUDIO1_GUID = "aaaaaaaa-1111-1111-1111-111111111111"
AUDIO2_GUID = "aaaaaaaa-2222-2222-2222-222222222222"
MISSING_AUDIO_GUID = "aaaaaaaa-3333-3333-3333-333333333333"


class CatalogV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kit = self.root / "kit"
        self.kit.mkdir()
        (self.kit / "sounds.fspro").write_text("{}", encoding="utf-8")
        (self.kit / "Assets" / "gui").mkdir(parents=True)
        (self.kit / "Assets" / "gui" / "enemy_destroyed.wav").write_bytes(b"OggS" + b"0" * 64)
        bank_dir = self.kit / "Metadata" / "Bank"
        event_dir = self.kit / "Metadata" / "Event"
        audio_dir = self.kit / "Metadata" / "AudioFile"
        bank_dir.mkdir(parents=True)
        event_dir.mkdir(parents=True)
        audio_dir.mkdir(parents=True)

        self.write_bank(BANK_GUID, "aircraft_gui")
        self.write_bank(BANK2_GUID, "tanks_effects")
        self.write_audio(AUDIO1_GUID, "gui/enemy_destroyed.wav", 3.185)
        self.write_audio(AUDIO2_GUID, "gui/missing.wav", 1.5)

        self.write_event(
            EVENT1_GUID,
            "enemy_destroyed",
            BANK_GUID,
            [AUDIO1_GUID, AUDIO2_GUID, AUDIO1_GUID],
        )
        self.write_event(EVENT2_GUID, "missing_only", BANK_GUID, [MISSING_AUDIO_GUID])
        self.write_event(EVENT3_GUID, "nested_event", None, [])
        self.write_event(EVENT4_GUID, "no_assets", BANK_GUID, [])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_bank(self, guid: str, name: str) -> None:
        (self.kit / "Metadata" / "Bank" / f"{{{guid}}}.xml").write_text(
            f'<objects><object class="Bank" id="{{{guid}}}">'
            f'<property name="name"><value>{name}</value></property></object></objects>',
            encoding="utf-8",
        )

    def write_audio(self, guid: str, path: str, length: float) -> None:
        (self.kit / "Metadata" / "AudioFile" / f"{{{guid}}}.xml").write_text(
            f'<objects><object class="AudioFile" id="{{{guid}}}">'
            f'<property name="assetPath"><value>{path}</value></property>'
            f'<property name="length"><value>{length}</value></property>'
            f'<property name="channelCount"><value>2</value></property>'
            f'<property name="frequencyInKHz"><value>44.1</value></property>'
            f"</object></objects>",
            encoding="utf-8",
        )

    def write_event(self, guid: str, name: str, bank_guid: str | None, audio_guids: list[str]) -> None:
        bank_xml = (
            f'<relationship name="banks"><destination>{{{bank_guid}}}</destination></relationship>'
            if bank_guid
            else ""
        )
        sounds = "".join(
            f'<object class="SingleSound" id="{{{audio_guid}}}">'
            f'<relationship name="audioFile"><destination>{{{audio_guid}}}</destination></relationship>'
            f'<property name="length"><value>1</value></property></object>'
            for audio_guid in audio_guids
        )
        (self.kit / "Metadata" / "Event" / f"{{{guid}}}.xml").write_text(
            f'<objects><object class="Event" id="{{{guid}}}">'
            f'<property name="name"><value>{name}</value></property>'
            f"{bank_xml}{sounds}</object></objects>",
            encoding="utf-8",
        )

    def test_build_maps_events_assets_and_missing(self) -> None:
        data = catalog.build_full_catalog(self.kit)
        stats = data["stats"]
        self.assertEqual(stats["banks"], 2)
        self.assertEqual(stats["event_files"], 4)
        self.assertEqual(stats["assigned_events"], 3)
        self.assertEqual(stats["unassigned_events"], 1)
        self.assertEqual(stats["events_with_refs"], 2)
        self.assertEqual(stats["events_with_assets"], 1)
        self.assertEqual(stats["events_missing_assets"], 1)
        self.assertEqual(stats["asset_refs"], 3)
        self.assertEqual(stats["asset_refs_raw"], 4)
        self.assertEqual(stats["unique_assets_assigned"], 3)
        self.assertEqual(stats["existing_assets_assigned"], 1)
        self.assertEqual(stats["missing_files_assigned"], 1)
        self.assertEqual(stats["missing_metadata"], 1)

        event = next(item for item in data["events"] if item["event"] == "enemy_destroyed")
        self.assertEqual(event["bank"], "aircraft_gui")
        self.assertEqual(event["asset_count"], 2)
        self.assertEqual(event["existing_asset_count"], 1)
        self.assertEqual(event["duration_sec"], 3.185)
        self.assertTrue(data["assets"][AUDIO1_GUID]["exists"])
        self.assertFalse(data["assets"][AUDIO2_GUID]["exists"])
        self.assertTrue(data["assets"][MISSING_AUDIO_GUID]["missing_metadata"])

        bank = next(item for item in data["banks"] if item["name"] == "aircraft_gui")
        self.assertEqual(bank["event_count"], 3)
        self.assertEqual(bank["with_assets"], 1)
        self.assertEqual(bank["asset_refs"], 3)
        self.assertEqual(bank["missing_assets"], 2)

    def test_query_filters_and_sorts(self) -> None:
        data = catalog.build_full_catalog(self.kit)
        matched = catalog.query_catalog(data, q="enemy")
        self.assertEqual(matched["page"]["total"], 1)
        self.assertEqual(matched["events"][0]["event"], "enemy_destroyed")
        self.assertEqual(matched["events"][0]["assets"][0]["path"], "gui/enemy_destroyed.wav")

        missing = catalog.query_catalog(data, has_assets="missing")
        self.assertEqual([item["event"] for item in missing["events"]], ["missing_only"])

        none = catalog.query_catalog(data, has_assets="none")
        self.assertEqual([item["event"] for item in none["events"]], ["no_assets"])

        self.assertEqual(catalog.query_catalog(data, bank="aircraft_gui")["page"]["total"], 3)
        self.assertEqual(catalog.query_catalog(data, include_unassigned=True)["page"]["total"], 4)

        first = catalog.query_catalog(data, sort="duration", order="desc", page_size=1)
        self.assertEqual(first["events"][0]["event"], "enemy_destroyed")
        self.assertEqual(first["page"]["pages"], 3)
        second = catalog.query_catalog(data, sort="duration", order="desc", page_size=1, page=2)
        self.assertEqual(second["page"]["page"], 2)
        self.assertEqual(second["page"]["total"], 3)

        facets = matched["facets"]
        self.assertEqual(facets["languages"], [])
        self.assertTrue(any(item["name"] == "aircraft_gui" for item in facets["banks"]))

    def test_cache_roundtrip_and_staleness(self) -> None:
        data = catalog.build_full_catalog(self.kit)
        target = catalog.save_full_catalog(data, self.root / "data")
        self.assertTrue(target.is_file())
        loaded = catalog.load_full_catalog(self.root / "data")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["stats"], data["stats"])

        usable, stale, reason = catalog.validate_cache(loaded, self.kit)
        self.assertTrue(usable)
        self.assertFalse(stale)
        self.assertEqual(reason, "")

        future = (self.kit / "sounds.fspro").stat().st_mtime + 30
        os.utime(self.kit / "sounds.fspro", (future, future))
        usable, stale, reason = catalog.validate_cache(loaded, self.kit)
        self.assertTrue(usable)
        self.assertTrue(stale)

        other = self.root / "other-kit"
        other.mkdir()
        usable, stale, reason = catalog.validate_cache(loaded, other)
        self.assertFalse(usable)
        self.assertIn("另一个工程", reason)

    def test_store_loads_and_clears(self) -> None:
        data = catalog.build_full_catalog(self.kit)
        catalog.save_full_catalog(data, self.root / "data")
        store = catalog.CatalogStore()
        status = store.status(self.kit, self.root / "data")
        self.assertTrue(status["available"])
        self.assertFalse(status["stale"])
        self.assertIsNotNone(store.snapshot())
        store.clear()
        self.assertIsNone(store.snapshot())
        status = store.status(self.kit, self.root / "data")
        self.assertTrue(status["available"])
        self.assertFalse(status["stale"])

    def test_legacy_catalog_is_unchanged(self) -> None:
        legacy = catalog.build_catalog(self.kit, limit=10)
        self.assertEqual(legacy.bank_count, 2)
        self.assertEqual(legacy.event_count, 3)
        self.assertEqual(legacy.unassigned_event_count, 1)
        self.assertFalse(legacy.asset_mapping)
        self.assertEqual(legacy.events[0].bank, "aircraft_gui")


if __name__ == "__main__":
    unittest.main()
