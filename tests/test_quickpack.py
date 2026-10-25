from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from app import descriptions, jobs, quickpack, voice


def make_wav(path: Path, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))


class DescriptionTest(unittest.TestCase):
    def test_keywords_are_human_readable(self) -> None:
        kill = descriptions.describe_stream("voice_message_commander_target_destroyed_v2")
        self.assertEqual(kill["category"], "击杀/战果")
        self.assertIn("击毁目标", kill["label"])
        self.assertIn("版本 2", kill["label"])

        rwr = descriptions.describe_stream("voice_message_rwr_missile_launch_warning")
        self.assertEqual(rwr["category"], "告警/RWR")
        self.assertIn("雷达告警", rwr["label"])

        fire = descriptions.describe_stream("voice_message_crew_fire_extinguish")
        self.assertEqual(fire["category"], "损伤/灭火")
        self.assertIn("灭火", fire["label"])

        unknown = descriptions.describe_stream("some_unknown_stream")
        self.assertTrue(unknown["label"])

    def test_phrases_name_common_voice_events(self) -> None:
        rwr = descriptions.describe_stream("voice_message_rwr_missile_launch_warning")
        self.assertEqual(rwr["label"], "RWR 雷达告警（导弹发射）")
        launch = descriptions.describe_stream("voice_message_missile_launch")
        self.assertEqual(launch["label"], "导弹发射告警")
        self.assertEqual(launch["category"], "告警/导弹")

        # 真实表里的 missile/launch 是装填或无人机指令，不能标成告警。
        load = descriptions.describe_stream("voice_message_loader_load_guided_missile_v1")
        self.assertEqual(load["category"], "装填/弹药")
        self.assertIn("装填制导导弹", load["label"])
        ucav = descriptions.describe_stream("voice_message_commander_ucav_launch_v1_1")
        self.assertEqual(ucav["category"], "目标/指令")
        self.assertEqual(ucav["label"], "无人机发射")
        base = descriptions.describe_stream("pilot_base_destroyed_enemy_v2")
        self.assertEqual(base["label"], "敌方基地被摧毁 · 版本 2")

    def test_non_voice_names_known_sound_effects(self) -> None:
        mission = descriptions.describe_non_voice(
            "mission_complete_fx", bank="_crew_dialogs_common_en"
        )
        self.assertIsNotNone(mission)
        self.assertEqual(mission["category"], "音效/任务完成")
        self.assertIn("任务完成音效", mission["label"])
        self.assertIn("保留游戏原音", mission["note"])

        point = descriptions.describe_non_voice(
            "voice_message_attention_to_point_2_3", bank="_crew_dialogs_common_zh"
        )
        self.assertIsNotNone(point)
        self.assertEqual(point["category"], "音效/目标点提示")
        self.assertIn("2 号点", point["label"])
        self.assertIn("变体 3", point["label"])
        self.assertIn("占位提示音", point["note"])

        # 英文/德文等同名流是真人语音，不能标成非语音。
        self.assertIsNone(
            descriptions.describe_non_voice(
                "voice_message_attention_to_point_2_3",
                bank="_crew_dialogs_common_en",
            )
        )
        voice = descriptions.describe_stream("voice_message_attention_to_point_2_3")
        self.assertEqual(voice["category"], "目标/指令")
        self.assertIn("目标点提示语音", voice["label"])

        # 击杀/告警类台词是语音，不能被误判成非语音音效。
        self.assertIsNone(
            descriptions.describe_non_voice("voice_message_chieftain_target_destroyed")
        )
        self.assertIsNone(descriptions.describe_non_voice("voice_message_rwr_missile_launch_warning"))


class SelectionTest(unittest.TestCase):
    def test_selected_banks_dedupe_shared_english_common(self) -> None:
        banks = quickpack.selected_banks(["gb", "us"], ["common", "ground"])
        ids = [item["bank"] for item in banks]
        self.assertEqual(ids.count("_crew_dialogs_common_en"), 1)
        self.assertIn("_crew_dialogs_ground_en", ids)
        self.assertIn("_crew_dialogs_ground_en_us", ids)

    def test_rejects_empty_selection(self) -> None:
        with self.assertRaises(voice.VoiceError):
            quickpack.selected_banks([], ["ground"])
        with self.assertRaises(voice.VoiceError):
            quickpack.selected_banks(["de"], [])

    def test_bank_scopes_for_line_scopes(self) -> None:
        self.assertEqual(quickpack.bank_scopes_for_lines(["common"]), ["common"])
        self.assertEqual(quickpack.bank_scopes_for_lines(["air"]), ["common"])
        self.assertEqual(quickpack.bank_scopes_for_lines(["other"]), ["common"])
        self.assertEqual(quickpack.bank_scopes_for_lines(["ground"]), ["ground"])
        self.assertEqual(quickpack.bank_scopes_for_lines(["naval"]), ["naval"])
        self.assertEqual(
            quickpack.bank_scopes_for_lines(["cockpit"]),
            ["cockpit"],
        )
        self.assertEqual(
            quickpack.bank_scopes_for_lines(
                ["common", "air", "other", "ground", "naval", "cockpit"]
            ),
            ["common", "ground", "naval", "cockpit"],
        )
        self.assertEqual(quickpack._line_scope("voice_message_fire", "common"), "common")
        self.assertEqual(
            quickpack._line_scope("pilot_base_destroyed_enemy_v1", "common"), "other"
        )
        self.assertEqual(
            quickpack._line_scope("mission_complete_victory_01_1_v1", "common"),
            "other",
        )
        self.assertEqual(
            quickpack._line_scope("voice_message_landing_0_1", "common"), "air"
        )
        self.assertEqual(
            quickpack._line_scope("voice_message_gear_down_v1", "common"), "air"
        )
        self.assertEqual(
            quickpack._line_scope("voice_message_driver_running_gear_damaged_v1", "common"),
            "common",
        )
        self.assertEqual(
            quickpack._line_scope("voice_message_10000_m", "common"), "air"
        )
        self.assertEqual(
            quickpack._line_scope("base_player_inZone_ground_v1", "common"), "ground"
        )
        self.assertEqual(
            quickpack._line_scope("base_player_inZone_leave_v1", "common"), "common"
        )
        self.assertEqual(
            quickpack._line_scope("vws_pull_up", "cockpit"), "cockpit"
        )
        self.assertEqual(
            quickpack._line_scope("voice_message_commander_fire_v1", "naval"), "naval"
        )

    def test_naval_banks_and_scope_inference(self) -> None:
        banks = quickpack.selected_banks(["jp", "us"], ["naval"])
        ids = [item["bank"] for item in banks]
        self.assertEqual(ids, ["_crew_dialogs_naval_jp", "_crew_dialogs_naval_en_us"])
        with self.assertRaises(voice.VoiceError):
            quickpack.selected_banks(["cn"], ["naval"])

        project = {
            "quick": {"banks": ["_crew_dialogs_common_en", "_crew_dialogs_naval_en"]},
            "lines": [
                {"id": "1", "source": {"bank": "_crew_dialogs_common_en", "event": "voice_message_landing_0_1"}},
                {"id": "2", "source": {"bank": "_crew_dialogs_common_en", "event": "pilot_base_destroyed_enemy_v1"}},
                {"id": "3", "source": {"bank": "_crew_dialogs_naval_en", "event": "voice_message_commander_fire_v1"}},
                {"id": "4", "source": {"bank": "aircraft_gui", "event": "vws_pull_up"}},
            ],
        }
        self.assertEqual(
            quickpack.infer_line_scopes(project),
            ["air", "other", "naval", "cockpit"],
        )

    def test_naval_translation_data_is_bundled(self) -> None:
        texts = quickpack._naval_texts()
        self.assertGreaterEqual(len(texts), 7)
        entry = texts["_crew_dialogs_naval_en"]["voice_message_commander_fire_v1"]
        self.assertIn("火", entry["text"])
        self.assertIn(entry["confidence"], {"high", "medium", "low"})

    def test_reloading_variants_are_labelled(self) -> None:
        meta = descriptions.describe_stream("voice_message_reloading_0_2")
        self.assertEqual(meta["category"], "装填/弹药")
        self.assertIn("装填", meta["label"])
        self.assertIn("录音变体 0-2", meta["label"])

    def test_vws_names_are_cockpit_alerts_not_tones(self) -> None:
        meta = descriptions.describe_stream("vws_pull_up")
        self.assertEqual(meta["category"], "座舱告警")
        self.assertIn("拉起", meta["label"])
        # 雷达锁定是音效，不是 VWS 人声。
        self.assertIsNotNone(
            descriptions.describe_non_voice("radar_lock", bank="aircraft_gui")
        )

    def test_options_lists_ten_nations_and_all_game_languages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            sound = game / "sound"
            sound.mkdir()
            for bank in (
                "_crew_dialogs_common_fr",
                "_crew_dialogs_ground_fr",
                "_crew_dialogs_ground_sv",
                "_crew_dialogs_ground_sm_de",
                "_crew_dialogs_naval_jp",
                "aircraft_gui",
            ):
                (sound / f"{bank}.assets.bank").write_bytes(b"bank")
            data = game / "data"
            data.mkdir()
            options = quickpack.options(data, None, game)
        self.assertEqual(len(options["nations"]), 10)
        self.assertEqual([mode["id"] for mode in options["modes"]], ["zh", "original"])
        ids = [item["bank"] for item in options["languages"]]
        self.assertIn("_crew_dialogs_common_fr", ids)
        self.assertIn("_crew_dialogs_ground_sm_de", ids)
        self.assertIn("_crew_dialogs_naval_jp", ids)
        self.assertNotIn("aircraft_gui", ids)
        self.assertTrue(options["game_ready"])
        de = next(n for n in options["nations"] if n["id"] == "de")
        de_banks = {b["bank"]: b for b in de["banks"]}
        self.assertTrue(de_banks["_crew_dialogs_ground_sm_de"]["available"])
        self.assertFalse(de_banks["_crew_dialogs_ground_de"]["available"])
        us = next(n for n in options["nations"] if n["id"] == "us")
        us_banks = {b["bank"]: b for b in us["banks"]}
        self.assertFalse(us_banks["_crew_dialogs_ground_sm_us"]["available"])
        self.assertEqual(
            us_banks["_crew_dialogs_ground_sm_us"]["note"],
            "当前游戏版本里没有这张表",
        )
        cn = next(n for n in options["nations"] if n["id"] == "cn")
        self.assertIn("不需要翻译", cn["note"])
        self.assertIn("中国表直接使用中文文本", options["translation_note"])


class QuickPrepareTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = voice.create_project(self.root, "测试音色")
        refs = self.root / "refs"
        make_wav(refs / "a.wav")
        make_wav(refs / "b.wav", 0.3)
        voice.import_reference(
            self.root,
            self.source["id"],
            refs,
            prompt_text="参考音频文字",
            prompt_lang="zh",
        )
        self.source = voice.load_project(self.root, self.source["id"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_clone_copies_reference_and_keeps_source_clean(self) -> None:
        model_dir = voice.project_dir(self.root, self.source["id"]) / "train" / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        sovits = model_dir / "fake_e2.pth"
        gpt = model_dir / "fake-e8.ckpt"
        sovits.write_bytes(b"fake-sovits")
        gpt.write_bytes(b"fake-gpt")
        voice.set_model(
            self.root,
            self.source["id"],
            mode="fine_tuned",
            state="ready",
            sovits_path=str(sovits),
            gpt_path=str(gpt),
            detail="测试权重",
        )
        source = voice.load_project(self.root, self.source["id"])
        cloned = quickpack.clone_voice_project(self.root, source["id"], "快速包")
        self.assertNotEqual(cloned["id"], source["id"])
        self.assertEqual(len(cloned["reference"]["files"]), 2)
        self.assertEqual(cloned["model"]["mode"], "fine_tuned")
        self.assertTrue(Path(cloned["model"]["sovits_path"]).is_file())
        self.assertTrue(Path(cloned["model"]["gpt_path"]).is_file())
        self.assertEqual(cloned["quick"]["source_project"], source["id"])
        self.assertEqual(len(voice.load_project(self.root, source["id"])["lines"]), 0)

    def test_naval_lines_use_bundled_first_pass_text(self) -> None:
        project = {
            "lines": [
                {
                    "id": "naval-1",
                    "text": "",
                    "source": {
                        "bank": "_crew_dialogs_naval_en",
                        "event": "voice_message_commander_fire_v1",
                    },
                },
                {
                    "id": "naval-2",
                    "text": "",
                    "source": {
                        "bank": "_crew_dialogs_naval_en",
                        "event": "unknown_naval_stream",
                    },
                },
            ]
        }
        with mock.patch.object(quickpack.text_catalog, "load", return_value={"scopes": {}}), \
             mock.patch.object(quickpack, "_gaps", return_value={"banks": {}}):
            result = quickpack._fill_texts(project, self.root)
        first, second = project["lines"]
        self.assertEqual(first["text_source"], "naval_first_pass")
        self.assertIn("火", first["text"])
        self.assertEqual(second["text"], "")
        self.assertEqual(result["counts"]["naval_first_pass"], 1)
        self.assertEqual(result["counts"]["missing"], 1)

    def test_prepare_fills_texts_and_descriptions(self) -> None:
        streams = [
            {
                "event_id": "at:_crew_dialogs_ground_de:enemy_destroyed",
                "event": "enemy_destroyed",
                "target_file": "enemy_destroyed.wav",
                "assets": [{"path": "x/enemy_destroyed.wav", "file": "enemy_destroyed.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_ground_de:voice_message_fire",
                "event": "voice_message_fire",
                "target_file": "voice_message_fire.wav",
                "assets": [{"path": "x/fire.wav", "file": "fire.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_ground_de:voice_message_rwr",
                "event": "voice_message_rwr",
                "target_file": "voice_message_rwr.wav",
                "assets": [{"path": "x/rwr.wav", "file": "rwr.wav"}],
            },
        ]
        catalog = {
            "scopes": {
                "ground": {
                    "enemy_destroyed": "击毁敌方目标",
                    "voice_message_fire": "起火，准备灭火",
                }
            }
        }
        gaps = {
            "banks": {
                "_crew_dialogs_ground_de": {
                    "voice_message_rwr": {
                        "text": "雷达告警，注意规避",
                        "review_note": "首轮译文",
                    }
                }
            }
        }
        job = jobs.Job("quick_prepare")
        with mock.patch.object(quickpack.text_catalog, "ensure", return_value=catalog), \
             mock.patch.object(quickpack.text_catalog, "load", return_value=catalog), \
             mock.patch.object(quickpack, "_gaps", return_value=gaps), \
             mock.patch.object(quickpack.localized, "streams", return_value=streams):
            result = quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["de"],
                ["ground"],
                "测试快速包",
            )
        project = result["project"]
        self.assertEqual(len(project["lines"]), 3)
        by_name = {line["source"]["event"]: line for line in project["lines"]}
        self.assertEqual(by_name["enemy_destroyed"]["text"], "击毁敌方目标")
        self.assertEqual(by_name["voice_message_fire"]["text"], "起火，准备灭火")
        self.assertEqual(by_name["voice_message_rwr"]["text"], "雷达告警，注意规避")
        self.assertEqual(by_name["voice_message_rwr"]["text_source"], "gap_translation")
        self.assertEqual(by_name["enemy_destroyed"]["source"]["category"], "击杀/战果")
        self.assertIn("击毁", by_name["enemy_destroyed"]["source"]["description"])
        self.assertEqual(result["text_counts"]["missing"], 0)
        self.assertEqual(result["text_counts"]["asr"], 2)
        self.assertEqual(result["text_counts"]["gap_translation"], 1)
        # 源训练项目不应被塞入包台词
        self.assertEqual(len(voice.load_project(self.root, self.source["id"])["lines"]), 0)

    def test_prepare_splits_common_and_air_lines(self) -> None:
        common_rows = [
            {
                "event_id": "at:_crew_dialogs_common_de:voice_message_fire",
                "event": "voice_message_fire",
                "target_file": "voice_message_fire.wav",
                "assets": [{"path": "x/fire.wav", "file": "fire.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_common_de:base_player_inZone_leave_v1",
                "event": "base_player_inZone_leave_v1",
                "target_file": "base_player_inZone_leave_v1.wav",
                "assets": [{"path": "x/leave.wav", "file": "leave.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_common_de:pilot_base_destroyed_enemy_v1",
                "event": "pilot_base_destroyed_enemy_v1",
                "target_file": "pilot_base_destroyed_enemy_v1.wav",
                "assets": [{"path": "x/pilot.wav", "file": "pilot.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_common_de:mission_complete_victory_01_1_v1",
                "event": "mission_complete_victory_01_1_v1",
                "target_file": "mission_complete_victory_01_1_v1.wav",
                "assets": [{"path": "x/victory.wav", "file": "victory.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_common_de:voice_message_landing_0_1",
                "event": "voice_message_landing_0_1",
                "target_file": "voice_message_landing_0_1.wav",
                "assets": [{"path": "x/landing.wav", "file": "landing.wav"}],
            },
            {
                "event_id": "at:_crew_dialogs_common_de:base_player_inZone_ground_v1",
                "event": "base_player_inZone_ground_v1",
                "target_file": "base_player_inZone_ground_v1.wav",
                "assets": [{"path": "x/ground.wav", "file": "ground.wav"}],
            },
        ]
        ground_rows = [
            {
                "event_id": "at:_crew_dialogs_ground_de:voice_message_fire",
                "event": "voice_message_fire",
                "target_file": "voice_message_fire.wav",
                "assets": [{"path": "x/gfire.wav", "file": "gfire.wav"}],
            }
        ]

        def fake_streams(_kit, bank, _game):
            if bank == "_crew_dialogs_common_de":
                return common_rows
            if bank == "_crew_dialogs_ground_de":
                return ground_rows
            return []

        catalog = {"scopes": {"common": {}, "ground": {}}}
        job = jobs.Job("quick_prepare")
        with mock.patch.object(quickpack.text_catalog, "ensure", return_value=catalog), \
             mock.patch.object(quickpack.text_catalog, "load", return_value=catalog), \
             mock.patch.object(quickpack, "_gaps", return_value={}), \
             mock.patch.object(quickpack.localized, "streams", side_effect=fake_streams):
            common = quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["de"],
                ["common"],
                "只做通用",
                line_scopes=["common"],
            )
        common_names = {line["source"]["event"] for line in common["project"]["lines"]}
        self.assertEqual(common_names, {"voice_message_fire", "base_player_inZone_leave_v1"})
        self.assertEqual(common["project"]["quick"]["line_scopes"], ["common"])

        with mock.patch.object(quickpack.text_catalog, "ensure", return_value=catalog), \
             mock.patch.object(quickpack.text_catalog, "load", return_value=catalog), \
             mock.patch.object(quickpack, "_gaps", return_value={}), \
             mock.patch.object(quickpack.localized, "streams", side_effect=fake_streams):
            air = quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["de"],
                ["common"],
                "只做空战",
                line_scopes=["air"],
            )
        air_names = {line["source"]["event"] for line in air["project"]["lines"]}
        self.assertEqual(air_names, {"voice_message_landing_0_1"})
        self.assertEqual(air["project"]["quick"]["line_scopes"], ["air"])

        with mock.patch.object(quickpack.text_catalog, "ensure", return_value=catalog), \
             mock.patch.object(quickpack.text_catalog, "load", return_value=catalog), \
             mock.patch.object(quickpack, "_gaps", return_value={}), \
             mock.patch.object(quickpack.localized, "streams", side_effect=fake_streams):
            other = quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["de"],
                ["common"],
                "只做其他",
                line_scopes=["other"],
            )
        other_names = {line["source"]["event"] for line in other["project"]["lines"]}
        self.assertEqual(
            other_names,
            {
                "pilot_base_destroyed_enemy_v1",
                "mission_complete_victory_01_1_v1",
            },
        )
        self.assertEqual(other["project"]["quick"]["line_scopes"], ["other"])

    def test_prepare_keeps_non_voice_sound_and_fills_placeholder_text(self) -> None:
        streams = {
            "_crew_dialogs_common_de": [
                {
                    "event_id": "at:_crew_dialogs_common_de:mission_complete_fx",
                    "event": "mission_complete_fx",
                    "target_file": "mission_complete_fx.wav",
                    "assets": [{"path": "x/a.wav", "file": "a.wav"}],
                },
                {
                    "event_id": "at:_crew_dialogs_common_de:voice_message_attention_to_point_0_1",
                    "event": "voice_message_attention_to_point_0_1",
                    "target_file": "voice_message_attention_to_point_0_1.wav",
                    "assets": [{"path": "x/a2.wav", "file": "a2.wav"}],
                },
            ],
            "_crew_dialogs_ground_de": [
                {
                    "event_id": "at:_crew_dialogs_ground_de:voice_message_driver_driver_controls_damaged",
                    "event": "voice_message_driver_driver_controls_damaged",
                    "target_file": "voice_message_driver_driver_controls_damaged.wav",
                    "assets": [{"path": "x/b.wav", "file": "b.wav"}],
                },
                {
                    "event_id": "at:_crew_dialogs_ground_de:enemy_destroyed",
                    "event": "enemy_destroyed",
                    "target_file": "enemy_destroyed.wav",
                    "assets": [{"path": "x/c.wav", "file": "c.wav"}],
                },
            ],
        }
        catalog = {"scopes": {"common": {}, "ground": {"enemy_destroyed": "击毁敌方目标"}}}

        def fake_streams(_kit, bank, _game):
            return streams[bank]

        job = jobs.Job("quick_prepare")
        with mock.patch.object(quickpack.text_catalog, "ensure", return_value=catalog), \
             mock.patch.object(quickpack.text_catalog, "load", return_value=catalog), \
             mock.patch.object(quickpack, "_gaps", return_value={}), \
             mock.patch.object(quickpack.localized, "streams", side_effect=fake_streams):
            result = quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["de"],
                ["common", "ground"],
                "占位音测试",
            )
        by_name = {line["source"]["event"]: line for line in result["project"]["lines"]}
        tone = by_name["mission_complete_fx"]
        self.assertEqual(tone["text_source"], "non_voice")
        self.assertEqual(tone["text"], "")
        self.assertIn("任务完成音效", tone["source"]["description"])
        self.assertIn("保留原音", tone["source"]["description"])
        damaged = by_name["voice_message_driver_driver_controls_damaged"]
        self.assertEqual(damaged["text"], "驾驶操控受损")
        self.assertEqual(damaged["text_source"], "placeholder_translation")
        attention = by_name["voice_message_attention_to_point_0_1"]
        self.assertEqual(attention["text_source"], "")
        self.assertEqual(attention["text"], "")
        self.assertEqual(attention["source"]["category"], "目标/指令")
        self.assertIn("目标点提示语音", attention["source"]["description"])
        self.assertEqual(result["text_counts"]["non_voice"], 1)
        self.assertEqual(result["text_counts"]["placeholder_translation"], 1)
        self.assertEqual(result["text_counts"]["missing"], 1)


    def test_prepare_original_mode_skips_text_and_marks_lines(self) -> None:
        model = voice.project_dir(self.root, self.source["id"]) / "train" / "rvc" / "voice.pth"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"fake-model")
        voice.set_rvc_model(self.root, self.source["id"], model=str(model))
        streams = [
            {
                "event_id": "at:_crew_dialogs_ground_fr:enemy_destroyed",
                "event": "enemy_destroyed",
                "target_file": "enemy_destroyed.wav",
                "assets": [
                    {
                        "path": "dialogs_dynamic/_crew_dialogs_ground_fr/1/enemy_destroyed.wav",
                        "file": "enemy_destroyed.wav",
                        "game_index": 1,
                    }
                ],
            },
            {
                "event_id": "at:_crew_dialogs_ground_fr:voice_message_attention_to_point_0_1",
                "event": "voice_message_attention_to_point_0_1",
                "target_file": "voice_message_attention_to_point_0_1.wav",
                "assets": [
                    {
                        "path": "dialogs_dynamic/_crew_dialogs_ground_fr/2/tone.wav",
                        "file": "tone.wav",
                        "game_index": 2,
                    }
                ],
            },
        ]
        job = jobs.Job("quick_prepare")
        with mock.patch.object(quickpack.text_catalog, "ensure") as ensure, \
             mock.patch.object(quickpack.localized, "streams", return_value=streams):
            result = quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["fr"],
                ["ground"],
                "法语换声包",
                "original",
            )
        ensure.assert_not_called()
        project = result["project"]
        self.assertEqual(project["quick"]["mode"], "original")
        self.assertEqual(project["quick"]["text_counts"]["original"], 1)
        self.assertEqual(project["quick"]["text_counts"]["non_voice"], 1)
        by_name = {line["source"]["event"]: line for line in project["lines"]}
        self.assertEqual(by_name["enemy_destroyed"]["text_source"], "original")
        self.assertEqual(by_name["enemy_destroyed"]["text"], "")
        self.assertIn("不朗读中文", by_name["enemy_destroyed"]["text_note"])
        self.assertEqual(
            by_name["voice_message_attention_to_point_0_1"]["text_source"],
            "non_voice",
        )
        self.assertTrue(project["rvc"]["model"])

    def test_prepare_original_requires_rvc_model(self) -> None:
        job = jobs.Job("quick_prepare")
        with self.assertRaises(voice.VoiceError):
            quickpack.prepare(
                job,
                self.root,
                "fake-kit",
                "fake-game",
                self.source["id"],
                ["fr"],
                ["ground"],
                "缺模型",
                "original",
            )


if __name__ == "__main__":
    unittest.main()
