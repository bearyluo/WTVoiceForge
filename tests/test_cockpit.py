from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from app import cockpit, jobs, localized, quickpack, voice


def make_wav(path: Path, seconds: float = 0.2, rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


def make_kit(root: Path) -> Path:
    kit = root / "kit"
    # 三套原声时长不同，用来验证“不能只取游戏 bank 第一条流”。
    make_wav(kit / "Assets" / "vws" / "betty" / "vws_pull_up.wav", 0.8)
    make_wav(kit / "Assets" / "vws" / "rita" / "vws_pull_up.wav", 0.9)
    make_wav(kit / "Assets" / "vws" / "xiao906" / "vws_pull_up.wav", 1.0)
    make_wav(kit / "Assets" / "vws" / "betty" / "vws_gear_down.wav", 0.7)
    make_wav(kit / "Assets" / "gui" / "radar_lock.wav", 0.2)
    return kit


def fake_streams(*_args, **_kwargs):
    return [
        {"index": 1, "name": "vws_pull_up", "duration_sec": 0.8},
        {"index": 2, "name": "vws_pull_up", "duration_sec": 0.9},
        {"index": 3, "name": "vws_pull_up", "duration_sec": 1.0},
        {"index": 4, "name": "vws_gear_down", "duration_sec": 0.7},
        {"index": 5, "name": "radar_lock", "duration_sec": 0.2},
        {"index": 6, "name": "ui_kill_assist", "duration_sec": 0.4},
    ]


class CockpitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kit = make_kit(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_rows_group_voice_variants(self) -> None:
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams):
            rows = cockpit.rows(self.kit, "fake-game")
        by_name = {row["event"]: row for row in rows if row["kind"] == "vws"}
        self.assertEqual(set(by_name), {"vws_pull_up", "vws_gear_down"})
        pull = by_name["vws_pull_up"]
        self.assertEqual(pull["scope"], "cockpit")
        self.assertEqual(len(pull["assets"]), 3)
        self.assertEqual(pull["variant_count"], 3)
        self.assertEqual(pull["duration_sec"], 1.0)
        self.assertEqual(pull["primary_voice"], "xiao906")
        self.assertEqual(pull["assets"][0]["path"], "vws/xiao906/vws_pull_up.wav")
        self.assertEqual(pull["assets"][0]["voice_label"], "晓906（中）")
        by_voice = {item["voice"]: item for item in pull["assets"]}
        self.assertEqual(by_voice["xiao906"]["game_index"], 3)
        self.assertEqual(by_voice["betty"]["game_index"], 1)
        self.assertEqual(by_voice["rita"]["game_index"], 2)
        gear = by_name["vws_gear_down"]
        self.assertEqual(gear["assets"][0]["voice"], "betty")
        self.assertEqual(gear["assets"][0]["game_index"], 4)
        self.assertEqual(cockpit.text("vws_pull_up"), "拉起，拉起")
        self.assertIn("拉起", pull["label"])

    def test_summary_marks_tones_and_missing_effects(self) -> None:
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams):
            info = cockpit.summary(self.kit, "fake-game")
        self.assertTrue(info["available"])
        self.assertEqual(info["total_streams"], 6)
        self.assertEqual(info["voice_streams"], 4)
        self.assertEqual(info["voice_names"], 2)
        self.assertEqual(info["extra_streams"], 1)
        self.assertEqual(info["preserved_streams"], 1)
        self.assertEqual(info["text_missing"], [])
        self.assertIn("ui_kill_assist", info["kit_missing_effects"])
        categories = {item["id"]: item for item in info["categories"]}
        self.assertEqual(categories["vws"]["streams"], 4)
        self.assertIn("保留原音", categories["radar"]["label"])
        families = {item["id"]: item for item in info["families"]}
        self.assertEqual(families["xiao906"]["names"], 1)
        self.assertEqual(families["betty"]["names"], 2)
        self.assertEqual(families["rita"]["names"], 1)

    def test_summary_reflects_newly_added_effect_without_restart(self) -> None:
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams):
            before = cockpit.summary(self.kit, "fake-game")
            self.assertIn("ui_kill_assist", before["kit_missing_effects"])
            make_wav(self.kit / "Assets" / "gui" / "ui_kill_assist.wav", 0.4)
            after = cockpit.summary(self.kit, "fake-game")
        self.assertNotIn("ui_kill_assist", after["kit_missing_effects"])
        self.assertLess(
            len(after["kit_missing_effects"]), len(before["kit_missing_effects"])
        )

    def test_extra_voice_groups_and_kill_effect(self) -> None:
        make_wav(self.kit / "Assets" / "gui" / "gunner_injured.wav", 0.5)
        make_wav(self.kit / "Assets" / "gui" / "gunner_reload_1.wav", 0.6)
        make_wav(self.kit / "Assets" / "gui" / "gunner_reload_2.wav", 0.7)
        make_wav(self.kit / "Assets" / "gui" / "enemy_destroyed.wav", 1.2)
        make_wav(self.kit / "Assets" / "gui" / "obj_complete.wav", 1.768)
        streams = fake_streams() + [
            {"index": 20, "name": "gunner_injured", "duration_sec": 0.5},
            {"index": 21, "name": "gunner_reload_1", "duration_sec": 0.6},
            {"index": 22, "name": "gunner_reload_2", "duration_sec": 0.7},
            {"index": 23, "name": "enemy_destroyed", "duration_sec": 1.2},
            {"index": 85, "name": "obj_complete", "duration_sec": 1.768},
        ]
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", return_value=streams):
            rows = cockpit.rows(self.kit, "fake-game")
            info = cockpit.summary(self.kit, "fake-game")
            catalog = {item["event_id"]: item for item in cockpit.catalog(self.kit, "fake-game")}
        by_name = {row["event"]: row for row in rows}
        reload_row = by_name["gunner_reload"]
        self.assertEqual(reload_row["kind"], "voice")
        self.assertFalse(reload_row["default_selected"])
        self.assertEqual([asset["path"] for asset in reload_row["assets"]], [
            "gui/gunner_reload_1.wav",
            "gui/gunner_reload_2.wav",
        ])
        self.assertEqual(reload_row["variant_count"], 2)
        kill = by_name["enemy_destroyed"]
        self.assertEqual(kill["kind"], "effect")
        self.assertFalse(kill["default_selected"])
        self.assertEqual(kill["text"], "击毁敌机")
        self.assertEqual(kill["label"], "座舱告警 · 击毁效果（旧事件）")
        self.assertNotIn("叮", kill["label"])
        self.assertNotIn("叮", kill["note"])
        self.assertEqual(kill["assets"][0]["path"], "gui/enemy_destroyed.wav")
        self.assertEqual(kill["assets"][0]["game_index"], 23)
        plane = by_name["enemy_destr_by_plane"]
        self.assertEqual(plane["kind"], "effect")
        self.assertFalse(plane["default_selected"])
        self.assertEqual(plane["text"], "击落敌机")
        self.assertEqual(plane["label"], "座舱告警 · 空战击杀提示音（已确认原音）")
        self.assertEqual(plane["event_names"], ["obj_complete"])
        self.assertEqual(len(plane["assets"]), 1)
        self.assertEqual(plane["assets"][0]["path"], "gui/obj_complete.wav")
        self.assertEqual(plane["assets"][0]["game_index"], 85)
        self.assertEqual(plane["source_file"], "obj_complete.wav")
        self.assertEqual(plane["target_file"], "wtvf_enemy_destr_by_plane.wav")
        self.assertEqual(info["extra_streams"], 6)
        self.assertEqual(info["extra_names"], 5)
        self.assertEqual(by_name["enemy_kill_assist"]["kind"], "effect")
        self.assertFalse(by_name["enemy_kill_assist"]["default_selected"])
        kill_assist_asset = by_name["enemy_kill_assist"]["assets"][0]
        self.assertEqual(kill_assist_asset["path"], "gui/ui_kill_assist.wav")
        self.assertEqual(kill_assist_asset["game_index"], 6)
        self.assertEqual(by_name["enemy_kill_assist"]["text"], "助攻")
        self.assertIn("助攻", by_name["enemy_kill_assist"]["label"])
        self.assertIn("不是击落敌机的", by_name["enemy_kill_assist"]["note"])
        self.assertEqual(
            catalog["fx:aircraft_gui:enemy_destroyed"]["kind"], "effect"
        )
        self.assertEqual(
            catalog["fx:aircraft_gui:enemy_destr_by_plane"]["text"], "击落敌机"
        )
        self.assertEqual(
            catalog["fx:aircraft_gui:gunner_reload"]["variant_count"], 2
        )

    def test_merges_same_phrase_variants(self) -> None:
        make_wav(self.kit / "Assets" / "vws" / "betty" / "vws_jammer.wav", 0.8)
        make_wav(self.kit / "Assets" / "vws" / "rita" / "vws_jammer.wav", 0.9)
        make_wav(self.kit / "Assets" / "vws" / "xiao906" / "vws_jammer.wav", 1.0)
        make_wav(self.kit / "Assets" / "vws" / "rita" / "vws_jammer_v2.wav", 1.1)
        streams = fake_streams() + [
            {"index": 30, "name": "vws_jammer", "duration_sec": 0.8},
            {"index": 31, "name": "vws_jammer", "duration_sec": 0.9},
            {"index": 32, "name": "vws_jammer", "duration_sec": 1.0},
            {"index": 33, "name": "vws_jammer_v2", "duration_sec": 1.1},
        ]
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", return_value=streams):
            rows = cockpit.rows(self.kit, "fake-game")
        jammer = next(row for row in rows if row["event"] == "vws_jammer")
        self.assertEqual(jammer["event_names"], ["vws_jammer", "vws_jammer_v2"])
        self.assertEqual(len(jammer["assets"]), 4)
        self.assertEqual(jammer["variant_count"], 4)
        self.assertEqual(sum(1 for row in rows if row["event"].startswith("vws_jammer")), 1)

    def test_source_index_picks_language_by_duration(self) -> None:
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams):
            self.assertEqual(
                cockpit.source_index(
                    self.kit, "fake-game", "vws_pull_up", "vws/betty/vws_pull_up.wav"
                ),
                1,
            )
            self.assertEqual(
                cockpit.source_index(
                    self.kit, "fake-game", "vws_pull_up", "vws/xiao906/vws_pull_up.wav"
                ),
                3,
            )
        self.assertEqual(
            cockpit.primary_asset_index(
                [
                    {"path": "vws/betty/vws_pull_up.wav"},
                    {"path": "vws/xiao906/vws_pull_up.wav"},
                    {"path": "vws/rita/vws_pull_up.wav"},
                ]
            ),
            1,
        )

    def test_adjust_event_lengths_and_reset(self) -> None:
        base = self.root / "base"
        event_dir = base / "Metadata" / "Event"
        event_dir.mkdir(parents=True)
        event_id = "{11111111-1111-1111-1111-111111111111}.xml"
        asset_id = "{22222222-2222-2222-2222-222222222222}"
        event_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<objects serializationModel="Studio.02.02.00">'
            f'<object class="Event" id="{event_id}"/>'
            '<object class="SingleSound" id="{33333333-3333-3333-3333-333333333333}">'
            '<property name="length"><value>1.0</value></property>'
            f'<relationship name="audioFile"><destination>{asset_id}</destination></relationship>'
            "</object></objects>"
        )
        (event_dir / event_id).write_text(event_xml, encoding="utf-8")
        kit_event = self.kit / "Metadata" / "Event" / event_id
        kit_event.parent.mkdir(parents=True)
        kit_event.write_text(event_xml, encoding="utf-8")
        audio = self.root / "new.wav"
        make_wav(audio, 0.65)
        data = {
            "events": [
                {
                    "bank": "aircraft_gui",
                    "source_file": event_id,
                    "asset_guids": [asset_id],
                }
            ],
            "assets": {asset_id: {"path": "vws/betty/vws_pull_up.wav"}},
        }
        mapping = {
            "vws/betty/vws_pull_up.wav": {"audio": str(audio)},
        }
        updated = cockpit.adjust_event_lengths(base, data, mapping)
        self.assertEqual(updated, 1)
        text = (event_dir / event_id).read_text(encoding="utf-8")
        self.assertIn("0.650000", text)
        self.assertEqual(cockpit.reset_event_metadata(base, self.kit, data), 1)
        self.assertIn("1.0", (event_dir / event_id).read_text(encoding="utf-8"))

    def test_adjust_event_lengths_for_gui_broadcast(self) -> None:
        base = self.root / "base"
        event_dir = base / "Metadata" / "Event"
        event_dir.mkdir(parents=True)
        event_id = "{44444444-4444-4444-4444-444444444444}.xml"
        asset_id = "{55555555-5555-5555-5555-555555555555}"
        event_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<objects serializationModel="Studio.02.02.00">'
            f'<object class="Event" id="{event_id}"/>'
            '<object class="SingleSound" id="{66666666-6666-6666-6666-666666666666}">'
            '<property name="length"><value>3.2</value></property>'
            f'<relationship name="audioFile"><destination>{asset_id}</destination></relationship>'
            "</object></objects>"
        )
        (event_dir / event_id).write_text(event_xml, encoding="utf-8")
        audio = self.root / "kill.wav"
        make_wav(audio, 0.9)
        data = {
            "events": [
                {
                    "bank": "aircraft_gui",
                    "source_file": event_id,
                    "asset_guids": [asset_id],
                }
            ],
            "assets": {asset_id: {"path": "gui/enemy_destroyed.wav"}},
        }
        updated = cockpit.adjust_event_lengths(
            base, data, {"gui/enemy_destroyed.wav": {"audio": str(audio)}}
        )
        self.assertEqual(updated, 1)
        text = (event_dir / event_id).read_text(encoding="utf-8")
        self.assertIn("0.900000", text)

    def test_selected_banks_and_prepare_cockpit_only(self) -> None:
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams):
            chosen = quickpack.selected_banks(
                [], ["cockpit"], kit=self.kit, game_path="fake-game"
            )
        self.assertEqual([item["bank"] for item in chosen], ["aircraft_gui"])
        self.assertEqual(chosen[0]["scope"], "cockpit")

        source = voice.create_project(self.root, "座舱音色")
        refs = self.root / "refs"
        make_wav(refs / "a.wav")
        voice.import_reference(
            self.root,
            source["id"],
            refs,
            prompt_text="参考文字",
            prompt_lang="zh",
        )
        job = jobs.Job("quick_prepare")
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams), \
             mock.patch.object(quickpack.text_catalog, "ensure") as ensure:
            result = quickpack.prepare(
                job,
                self.root,
                self.kit,
                "fake-game",
                source["id"],
                [],
                ["cockpit"],
                "座舱告警测试",
                line_scopes=["cockpit"],
            )
        ensure.assert_not_called()
        project = result["project"]
        self.assertEqual(len(project["lines"]), 2)
        self.assertEqual(project["quick"]["banks"], ["aircraft_gui"])
        self.assertEqual(project["quick"]["text_counts"]["cockpit_text"], 2)
        self.assertEqual(project["quick"]["text_counts"]["missing"], 0)
        by_name = {line["source"]["event"]: line for line in project["lines"]}
        self.assertEqual(by_name["vws_pull_up"]["text"], "拉起，拉起")
        self.assertEqual(by_name["vws_pull_up"]["source"]["category"], "座舱告警")
        assets = by_name["vws_pull_up"]["source"]["assets"]
        self.assertEqual([item["voice"] for item in assets], ["xiao906", "betty", "rita"])
        self.assertEqual(by_name["vws_pull_up"]["source"]["path"], assets[0]["path"])

        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams), \
             mock.patch.object(quickpack.text_catalog, "ensure") as ensure:
            selected = quickpack.prepare(
                jobs.Job("quick_prepare"),
                self.root,
                self.kit,
                "fake-game",
                source["id"],
                [],
                ["cockpit"],
                "座舱告警单选测试",
                line_scopes=["cockpit"],
                cockpit_events=["fx:aircraft_gui:vws_gear_down"],
            )
        ensure.assert_not_called()
        self.assertEqual(len(selected["project"]["lines"]), 1)
        self.assertEqual(
            selected["project"]["lines"][0]["source"]["event"], "vws_gear_down"
        )
        self.assertEqual(
            selected["project"]["quick"]["cockpit_events"],
            ["fx:aircraft_gui:vws_gear_down"],
        )

    def test_prepare_optional_kill_assist_without_kit_asset(self) -> None:
        source = voice.create_project(self.root, "座舱音色")
        refs = self.root / "refs"
        make_wav(refs / "a.wav")
        voice.import_reference(
            self.root, source["id"], refs, prompt_text="参考文字", prompt_lang="zh"
        )
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", side_effect=fake_streams), \
             mock.patch.object(quickpack.text_catalog, "ensure") as ensure:
            result = quickpack.prepare(
                jobs.Job("quick_prepare"),
                self.root,
                self.kit,
                "fake-game",
                source["id"],
                [],
                ["cockpit"],
                "击杀提示音测试",
                line_scopes=["cockpit"],
                cockpit_events=["fx:aircraft_gui:enemy_kill_assist"],
            )
        ensure.assert_not_called()
        lines = result["project"]["lines"]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["source"]["event"], "enemy_kill_assist")
        self.assertEqual(line["source"]["kind"], "effect")
        self.assertEqual(line["text"], "助攻")
        self.assertEqual(line["source"]["assets"][0]["path"], "gui/ui_kill_assist.wav")
        self.assertEqual(line["source"]["assets"][0]["game_index"], 6)
        self.assertEqual(result["text_counts"]["cockpit_text"], 1)
        self.assertEqual(result["text_counts"]["missing"], 0)

    def test_prepare_legacy_enemy_destroyed_effect(self) -> None:
        make_wav(self.kit / "Assets" / "gui" / "enemy_destroyed.wav", 3.1)
        streams = fake_streams() + [
            {"index": 23, "name": "enemy_destroyed", "duration_sec": 3.1},
        ]
        source = voice.create_project(self.root, "座舱音色")
        refs = self.root / "refs"
        make_wav(refs / "a.wav")
        voice.import_reference(
            self.root, source["id"], refs, prompt_text="参考文字", prompt_lang="zh"
        )
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", return_value=streams), \
             mock.patch.object(quickpack.text_catalog, "ensure") as ensure:
            result = quickpack.prepare(
                jobs.Job("quick_prepare"),
                self.root,
                self.kit,
                "fake-game",
                source["id"],
                [],
                ["cockpit"],
                "旧击毁事件测试",
                line_scopes=["cockpit"],
                cockpit_events=["fx:aircraft_gui:enemy_destroyed"],
            )
        ensure.assert_not_called()
        lines = result["project"]["lines"]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["source"]["event"], "enemy_destroyed")
        self.assertEqual(line["source"]["kind"], "effect")
        self.assertEqual(line["text"], "击毁敌机")
        self.assertEqual(line["source"]["assets"][0]["path"], "gui/enemy_destroyed.wav")
        self.assertEqual(line["source"]["assets"][0]["game_index"], 23)
        self.assertEqual(line["source"]["label"], "座舱告警 · 击毁效果（旧事件）")
        self.assertEqual(line["target_file"], "enemy_destroyed.wav")

    def test_prepare_plane_kill_targets_shared_obj_complete(self) -> None:
        make_wav(self.kit / "Assets" / "gui" / "obj_complete.wav", 1.768)
        streams = fake_streams() + [
            {"index": 85, "name": "obj_complete", "duration_sec": 1.768},
        ]
        source = voice.create_project(self.root, "座舱音色")
        refs = self.root / "refs"
        make_wav(refs / "a.wav")
        voice.import_reference(
            self.root, source["id"], refs, prompt_text="参考文字", prompt_lang="zh"
        )
        with mock.patch.object(localized, "game_bank_path", return_value=Path("x.bank")), \
             mock.patch.object(localized, "game_streams", return_value=streams), \
             mock.patch.object(quickpack.text_catalog, "ensure") as ensure:
            result = quickpack.prepare(
                jobs.Job("quick_prepare"),
                self.root,
                self.kit,
                "fake-game",
                source["id"],
                [],
                ["cockpit"],
                "空战击杀提示音测试",
                line_scopes=["cockpit"],
                cockpit_events=["fx:aircraft_gui:enemy_destr_by_plane"],
            )
        ensure.assert_not_called()
        lines = result["project"]["lines"]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["source"]["event"], "enemy_destr_by_plane")
        self.assertEqual(line["source"]["assets"][0]["path"], "gui/obj_complete.wav")
        self.assertEqual(line["source"]["assets"][0]["game_index"], 85)
        self.assertEqual(line["text"], "击落敌机")
        self.assertEqual(line["target_file"], "wtvf_enemy_destr_by_plane.wav")


if __name__ == "__main__":
    unittest.main()
