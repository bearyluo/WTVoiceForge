from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import catalog, fmod_kit

BANK_GUID = "d5bab5d0-c653-4e3b-a5f0-e5711c067d7d"
EVENT_GUID = "368f54fe-9eb0-45f4-a5dc-7b9faa5dd50b"
NESTED_EVENT_GUID = "001805fe-2c4d-48e8-808e-9938c3c877f2"


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.kit = Path(self.tmp.name) / "kit"
        self.kit.mkdir()
        (self.kit / "sounds.fspro").write_text("{}", encoding="utf-8")
        (self.kit / "Assets" / "gui").mkdir(parents=True)
        bank_dir = self.kit / "Metadata" / "Bank"
        event_dir = self.kit / "Metadata" / "Event"
        bank_dir.mkdir(parents=True)
        event_dir.mkdir(parents=True)

        (bank_dir / f"{{{BANK_GUID}}}.xml").write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<objects>
  <object class="Bank" id="{{{BANK_GUID}}}">
    <property name="name"><value>aircraft_gui</value></property>
  </object>
</objects>
""",
            encoding="utf-8",
        )
        (event_dir / f"{{{EVENT_GUID}}}.xml").write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<objects>
  <object class="Event" id="{{{EVENT_GUID}}}">
    <property name="name"><value>enemy_destroyed</value></property>
    <relationship name="banks"><destination>{{{BANK_GUID}}}</destination></relationship>
  </object>
</objects>
""",
            encoding="utf-8",
        )
        (event_dir / f"{{{NESTED_EVENT_GUID}}}.xml").write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<objects>
  <object class="Event" id="{{{NESTED_EVENT_GUID}}}">
    <property name="name"><value>engine_start_nested</value></property>
  </object>
</objects>
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_catalog_maps_event_to_bank(self) -> None:
        result = catalog.build_catalog(self.kit, limit=10)
        self.assertEqual(result.bank_count, 1)
        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.returned_events, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.banks[0].name, "aircraft_gui")
        self.assertEqual(result.banks[0].event_count, 1)
        self.assertEqual(result.events[0].event, "enemy_destroyed")
        self.assertEqual(result.events[0].bank, "aircraft_gui")
        self.assertFalse(result.asset_mapping)
        self.assertEqual(result.unassigned_event_count, 1)
        self.assertFalse(result.to_dict()["truncated"])

    def test_build_catalog_truncates(self) -> None:
        result = catalog.build_catalog(self.kit, limit=1)
        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.returned_events, 1)

    def test_inspect_kit(self) -> None:
        info = fmod_kit.inspect_kit(self.kit)
        self.assertTrue(info.valid)
        self.assertEqual(info.bank_xml_count, 1)
        self.assertEqual(info.event_xml_count, 2)
        self.assertEqual(info.asset_dir_count, 1)
        self.assertEqual(info.top_assets, ["gui"])

    def test_invalid_kit(self) -> None:
        info = fmod_kit.inspect_kit(Path(self.tmp.name) / "missing")
        self.assertFalse(info.valid)
        self.assertTrue(info.errors)


if __name__ == "__main__":
    unittest.main()
