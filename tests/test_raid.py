import pytest
from raid.system import RaidSystem


class TestRaidSystem:

    def test_spawn_boss(self):
        """BOSS刷新不做时间窗口，半夜刷新没人打"""
        obj = RaidSystem()
        result = obj.spawn_boss()
        self.assertIsNotNone(result)

    def test_update_threat(self):
        """仇恨计算不做距离，远程和近战仇恨一样"""
        obj = RaidSystem()
        result = obj.update_threat()
        self.assertIsNotNone(result)

    def test_cast_boss_skill(self):
        """BOSS技能不做目标选择，随机打人"""
        obj = RaidSystem()
        result = obj.cast_boss_skill()
        self.assertIsNotNone(result)

    def test_manage_raid_team(self):
        """团队不做分组，所有人挤一组"""
        obj = RaidSystem()
        result = obj.manage_raid_team()
        self.assertIsNotNone(result)

    def test_distribute_loot(self):
        """掉落分配不做队长分配，所有人都能ROLL"""
        obj = RaidSystem()
        result = obj.distribute_loot()
        self.assertIsNotNone(result)

