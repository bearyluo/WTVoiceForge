from __future__ import annotations

import unittest

from app import translation


def stream(name: str, index: int, duration: float = 1.0) -> dict:
    return {"name": name, "index": index, "duration_sec": duration}


class TranslationTest(unittest.TestCase):
    def test_numeric_templates(self) -> None:
        cases = {
            "voice_message_commander_angle_10": ("commander:angle", "角度 10"),
            "voice_message_commander_angle_360_alt": ("commander:angle", "角度 360"),
            "voice_message_officer_bearing_80": ("officer:bearing", "方位 080"),
            "voice_message_commander_depth_100m": ("commander:depth_m", "深度 100 米"),
            "voice_message_officer_depth_325ft": ("officer:depth_ft", "深度 325 英尺"),
            "voice_message_sailor_1000_m": ("sailor:distance_m", "距离 1000 米"),
            "voice_message_sailor_8000_yd_1": ("sailor:distance_yd", "距离 8000 码"),
        }
        for name, (key, draft) in cases.items():
            parsed = translation.parse_name(name)
            self.assertEqual(parsed["group_key"], key, name)
            self.assertEqual(parsed["draft"], draft, name)

    def test_ground_roles_and_pilot_zones(self) -> None:
        self.assertEqual(
            translation.parse_name("voice_message_driver_engine_ready_v1_1")["role"],
            "driver",
        )
        self.assertEqual(
            translation.parse_name("voice_message_loader_load_guided_missile_v1")["role"],
            "loader",
        )
        self.assertEqual(
            translation.parse_name("voice_message_chief_battle_win_v1_2")["role"],
            "chief",
        )
        ally1 = translation.parse_name("pilot_tickets_lead_zone_ally_1_v1")
        ally2 = translation.parse_name("pilot_tickets_lead_zone_ally_2_v2")
        enemy1 = translation.parse_name("pilot_tickets_lead_zone_enemy_1_v3")
        self.assertNotEqual(ally1["group_key"], ally2["group_key"])
        self.assertNotEqual(ally1["group_key"], enemy1["group_key"])
        self.assertEqual(ally1["role"], "other")
        self.assertEqual(ally1["category"], "战况")

    def test_variants_share_group(self) -> None:
        parsed = translation.parse_name("voice_message_commander_we_lost_v7")
        self.assertEqual(parsed["group_key"], "commander:we_lost")
        self.assertEqual(parsed["kind"], "phrase")
        self.assertEqual(parsed["category"], "战况")

    def test_build_groups_deduplicates_and_counts(self) -> None:
        sources = {
            "en": {
                "bank": "_crew_dialogs_naval_en",
                "streams": [
                    stream("voice_message_commander_we_lost_v1", 1, 2.0),
                    stream("voice_message_commander_we_lost_v2", 2, 2.5),
                    stream("voice_message_commander_angle_10", 3, 1.0),
                    stream("voice_message_commander_angle_20", 4, 1.0),
                    stream("voice_message_commander_we_lost_v1", 1, 2.0),
                ],
            },
            "de": {
                "bank": "_crew_dialogs_naval_de",
                "streams": [
                    stream("voice_message_commander_we_lost_v1", 10, 2.2),
                    stream("voice_message_commander_angle_10", 11, 1.1),
                ],
            },
        }
        result = translation.build(sources, prefix="X", scope_label="海战")
        self.assertEqual(result["stats"]["streams"], 6)
        self.assertEqual(result["stats"]["unique_names"], 4)
        self.assertEqual(result["stats"]["groups"], 2)
        self.assertEqual(result["stats"]["numeric_groups"], 1)
        groups = {group["group_key"]: group for group in result["groups"]}
        self.assertEqual(groups["commander:we_lost"]["variant_count"], 2)
        self.assertEqual(groups["commander:we_lost"]["language_count"], 2)
        self.assertEqual(groups["commander:angle"]["variant_count"], 2)
        self.assertIn("数字档位", groups["commander:angle"]["note"])
        names = {row["event"]: row for row in result["names"]}
        self.assertEqual(names["voice_message_commander_angle_10"]["en_index"], 3)
        self.assertEqual(names["voice_message_commander_angle_10"]["de_index"], 11)
        # 每张表的重复流只保留一条任务行。
        self.assertEqual(result["stats"]["streams"], len(result["rows"]))
        en_rows = [row for row in result["rows"] if row["language"] == "en"]
        self.assertEqual(len(en_rows), 4)
        self.assertEqual(len({row["event"] for row in en_rows}), 4)

    def test_csv_has_bom_and_headers(self) -> None:
        text = translation.csv_text(
            [{"task_id": "T001", "text": "收到"}],
            ["task_id", "text"],
        )
        self.assertTrue(text.startswith("\ufeff"))
        self.assertIn("task_id,text", text)
        self.assertIn("T001,收到", text)

    def test_readme_mentions_scope_and_counts(self) -> None:
        result = translation.build(
            {
                "en": {
                    "bank": "_crew_dialogs_naval_en",
                    "streams": [stream("voice_message_commander_we_lost_v1", 1)],
                }
            },
            prefix="NV",
            scope_label="海战",
        )
        text = translation.readme_text(result, title="海战翻译")
        self.assertIn("海战没有官方中文语音表", text)
        self.assertIn("《海战翻译_分组任务.csv》", text)
        self.assertIn("流名并集：1 条", text)


if __name__ == "__main__":
    unittest.main()
