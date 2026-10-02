"""
pairwise-raid-system - RaidSystem

This module contains a deliberately broken implementation.
Fix all bugs so that tests pass.
"""


class RaidSystem:
    def __init__(self):
        self.state = {}

    def spawn_boss(self, *args, **kwargs):
        """BUG: BOSS刷新不做时间窗口，半夜刷新没人打"""
        return None

    def update_threat(self, *args, **kwargs):
        """BUG: 仇恨计算不做距离，远程和近战仇恨一样"""
        return None

    def cast_boss_skill(self, *args, **kwargs):
        """BUG: BOSS技能不做目标选择，随机打人"""
        return None

    def manage_raid_team(self, *args, **kwargs):
        """BUG: 团队不做分组，所有人挤一组"""
        return None

    def distribute_loot(self, *args, **kwargs):
        """BUG: 掉落分配不做队长分配，所有人都能ROLL"""
        return None

