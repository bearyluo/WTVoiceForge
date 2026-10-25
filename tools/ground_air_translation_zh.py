"""陆战/通用（空战）中文缺口的第一轮译法。

只覆盖官方中文表里没有的 58 个陆战流名和 18 个通用流名。按流名/术语整理，
不是听写结果；low 条目必须听原音重点校对。
"""
from __future__ import annotations

BODY_ZH = {
    # 陆战
    "target_light_tank": "目标：轻型坦克",
    "target_sam_": "目标：防空导弹",
    "drone": "发现无人机",
    "engine_prepare": "准备启动发动机",
    "engine_start": "启动发动机",
    "flipped_over": "车辆翻覆",
    "shovel_off_v2_2": "铲开泥土",
    "shovel_off_v2_3": "铲开障碍",
    "battle_loose": "战斗失利",
    "battle_loose_nuke": "战斗失利，遭核弹袭击",
    "battle_win": "战斗胜利",
    "correction_target_destroyed_revenge": "目标已摧毁，为战友复仇",
    "engine_ready": "发动机就绪",
    "load_guided_missile": "装填制导导弹",
    # 通用/空战：占点票数领先播报
    "pilot_zone_ally_1": "我方在1号区域票数领先",
    "pilot_zone_ally_2": "我方在2号区域票数领先",
    "pilot_zone_ally_3": "我方在3号区域票数领先",
    "pilot_zone_enemy_1": "敌方在1号区域票数领先",
    "pilot_zone_enemy_2": "敌方在2号区域票数领先",
    "pilot_zone_enemy_3": "敌方在3号区域票数领先",
}

LOW_CONFIDENCE = {
    "shovel_off_v2_2",
    "shovel_off_v2_3",
    "battle_loose_nuke",
    "pilot_zone_ally_1",
    "pilot_zone_ally_2",
    "pilot_zone_ally_3",
    "pilot_zone_enemy_1",
    "pilot_zone_enemy_2",
    "pilot_zone_enemy_3",
}
