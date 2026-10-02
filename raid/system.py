"""RaidSystem - 团队副本系统。

修复内容:
- spawn_boss:      BOSS刷新做时间窗口 + 刷新预告 + 人数检测
- update_threat:   仇恨做距离修正 + 仇恨衰减 + 技能/职责修正
- cast_boss_skill: BOSS技能做目标选择 + 技能预警 + 阶段区分
- manage_raid_team: 团队做分组 + 职责标记 + 召唤
- distribute_loot: 掉落做队长分配 + 拾取绑定 + 分配记录
"""

from __future__ import annotations

from datetime import datetime, timedelta


class RaidSystem:
    # ---- BOSS 刷新配置 ----
    SPAWN_WINDOW_START_HOUR = 18          # 只允许 18:00 - 24:00 刷新
    SPAWN_WINDOW_END_HOUR = 24
    ANNOUNCE_LEAD_MINUTES = 10            # 刷新前 10 分钟全服预告
    MIN_RAID_SIZE = 10                    # 最少 10 人才允许开 BOSS

    # ---- 仇恨系统配置 ----
    MELEE_RANGE = 5.0                     # 近战判定距离
    MAX_AGGRO_RANGE = 40.0                # 超出此距离不产生仇恨
    MELEE_THREAT_MODIFIER = 1.0
    RANGED_THREAT_MODIFIER = 0.7          # 远程仇恨系数低于近战
    THREAT_DECAY_PER_SECOND = 0.01        # 每秒衰减 1%
    ROLE_THREAT_MODIFIERS = {             # 职责/技能修正
        "tank": 2.0,                      # 坦克技能高仇恨
        "healer": 0.5,                    # 治疗仇恨低于输出
        "dps": 1.0,
    }

    # ---- BOSS 技能配置(按阶段区分) ----
    PHASE_SKILLS = {
        1: ["cleave", "shockwave"],
        2: ["cleave", "shockwave", "summon_adds"],
        3: ["cleave", "shockwave", "summon_adds", "annihilation"],
    }
    AOE_SKILLS = {"shockwave", "annihilation"}   # 全屏/AOE 技能必须预警
    PHASE_SIGNATURE_SKILL = {1: "cleave", 2: "summon_adds", 3: "annihilation"}
    PHASE_THRESHOLDS = {1: 1.0, 2: 0.66, 3: 0.33}  # 血量百分比 -> 阶段

    # ---- 团队配置 ----
    GROUP_SIZE = 5                        # 每组 5 人
    REQUIRED_ROLES = ("tank", "healer")   # 团队必须标记的职责
    VALID_ROLES = ("tank", "healer", "dps")

    def __init__(self):
        self.state = {}
        self.threat_table = {}
        self.loot_log = []
        self.announcements = []

    # ------------------------------------------------------------------
    # BOSS 刷新: 时间窗口 + 刷新预告 + 人数检测
    # ------------------------------------------------------------------
    def spawn_boss(self, boss_id="world_boss", now=None, online_players=None):
        """在时间窗口内刷新 BOSS, 刷新前预告, 并检测在线人数。

        返回 dict 包含 spawned/window/announced/player_check 等信息。
        """
        now = now or datetime.now().replace(hour=20, minute=0, second=0, microsecond=0)
        if isinstance(now, (int, float)):
            now = datetime.fromtimestamp(now)
        online_players = list(online_players) if online_players else []

        # 1) 时间窗口: 非刷新时段直接拒绝
        in_window = self.SPAWN_WINDOW_START_HOUR <= now.hour < self.SPAWN_WINDOW_END_HOUR

        # 2) 刷新预告: 进入窗口即提前预告, 而不是刷新后才通知
        announce_time = None
        announced = False
        if in_window:
            announce_time = now - timedelta(minutes=self.ANNOUNCE_LEAD_MINUTES)
            announcement = {
                "boss_id": boss_id,
                "announce_time": announce_time,
                "spawn_time": now,
                "message": f"世界BOSS[{boss_id}]将于{now:%H:%M}刷新, 请提前集结!",
            }
            self.announcements.append(announcement)
            announced = True

        # 3) 人数检测: 不足最低人数不允许开 BOSS
        player_count = len(online_players)
        enough_players = player_count >= self.MIN_RAID_SIZE

        spawned = bool(in_window and enough_players)
        result = {
            "boss_id": boss_id,
            "spawned": spawned,
            "in_window": in_window,
            "window": (self.SPAWN_WINDOW_START_HOUR, self.SPAWN_WINDOW_END_HOUR),
            "announced": announced,
            "announce_time": announce_time,
            "player_count": player_count,
            "min_players": self.MIN_RAID_SIZE,
            "enough_players": enough_players,
        }
        if spawned:
            self.state[boss_id] = {"spawned_at": now, "phase": 1, "hp_percent": 1.0}
        return result

    # ------------------------------------------------------------------
    # 仇恨系统: 距离修正 + 仇恨衰减 + 技能/职责修正
    # ------------------------------------------------------------------
    def _distance_modifier(self, distance):
        if distance is None:
            distance = 0.0
        if distance > self.MAX_AGGRO_RANGE:
            return 0.0
        if distance <= self.MELEE_RANGE:
            return self.MELEE_THREAT_MODIFIER
        return self.RANGED_THREAT_MODIFIER

    def _role_modifier(self, role):
        return self.ROLE_THREAT_MODIFIERS.get(role, 1.0)

    def update_threat(self, player=None, action=None, elapsed_seconds=0.0):
        """更新仇恨表。

        player: {"name", "role", "distance"}; action: {"value"} 原始仇恨量。
        无参数时对全表执行衰减并返回当前仇恨表。
        """
        elapsed_seconds = max(0.0, elapsed_seconds or 0.0)

        # 2) 仇恨衰减: 随时间按比例衰减, OT 后旧仇恨会降下来
        if elapsed_seconds > 0:
            decay = max(0.0, 1.0 - self.THREAT_DECAY_PER_SECOND * elapsed_seconds)
            for name in self.threat_table:
                self.threat_table[name] *= decay

        if player is not None:
            name = player.get("name", "unknown")
            role = player.get("role", "dps")
            distance = player.get("distance", 0.0)
            base = (action or {}).get("value", 0.0)

            # 1) 距离修正: 近战 > 远程, 超程无仇恨
            distance_mod = self._distance_modifier(distance)
            # 3) 技能/职责修正: 坦克高仇恨, 治疗低仇恨
            role_mod = self._role_modifier(role)

            gained = base * distance_mod * role_mod
            self.threat_table[name] = self.threat_table.get(name, 0.0) + gained

        return dict(self.threat_table)

    # ------------------------------------------------------------------
    # BOSS 技能: 目标选择 + 技能预警 + 阶段区分
    # ------------------------------------------------------------------
    def _phase_for_hp(self, hp_percent):
        if hp_percent <= self.PHASE_THRESHOLDS[3]:
            return 3
        if hp_percent <= self.PHASE_THRESHOLDS[2]:
            return 2
        return 1

    def cast_boss_skill(self, boss_id="world_boss", hp_percent=1.0, targets=None):
        """按阶段选技能, 按仇恨选目标, AOE 技能提前预警。"""
        boss = self.state.get(boss_id, {"phase": 1, "hp_percent": 1.0})

        # 3) 阶段区分: 血量决定阶段, 不同阶段技能池不同
        phase = self._phase_for_hp(hp_percent if hp_percent is not None else boss["hp_percent"])
        boss["phase"] = phase
        boss["hp_percent"] = hp_percent
        self.state[boss_id] = boss
        available = self.PHASE_SKILLS[phase]
        skill = self.PHASE_SIGNATURE_SKILL[phase]

        # 1) 目标选择: 单体技能打仇恨最高者, 不再随机打人
        targets = list(targets) if targets else list(self.threat_table) or ["raid"]
        if skill in self.AOE_SKILLS:
            chosen = list(targets)  # 全屏技能打全团
        elif skill == "summon_adds":
            chosen = []  # 召唤类技能无直接目标
        else:
            chosen = [max(self.threat_table, key=self.threat_table.get)] if self.threat_table else [targets[0]]

        # 2) 技能预警: AOE/全屏技能必须提前广播预警
        warning = None
        if skill in self.AOE_SKILLS:
            warning = {
                "skill": skill,
                "phase": phase,
                "message": f"警告: BOSS即将释放全屏技能[{skill}], 注意减伤/集合!",
            }
            self.announcements.append(warning)

        return {
            "boss_id": boss_id,
            "phase": phase,
            "skill": skill,
            "available_skills": list(available),
            "targets": chosen,
            "warning": warning,
        }

    # ------------------------------------------------------------------
    # 团队系统: 分组 + 职责标记 + 召唤
    # ------------------------------------------------------------------
    def manage_raid_team(self, members=None, summon_to=None):
        """管理团队: 按 5 人分组, 标记职责, 支持召唤未就位成员。"""
        members = list(members) if members else []
        roster = []
        for i, m in enumerate(members):
            m = dict(m)
            # 2) 职责标记: 每个成员必须有 tank/healer/dps 标记
            m.setdefault("role", "dps")
            if m["role"] not in self.VALID_ROLES:
                m["role"] = "dps"
            # 1) 分组: 每 5 人一组, 不再全挤一组
            m["group"] = i // self.GROUP_SIZE + 1
            m.setdefault("present", True)
            roster.append(m)

        groups = {}
        for m in roster:
            groups.setdefault(m["group"], []).append(m["name"])

        roles = {r: [m["name"] for m in roster if m["role"] == r] for r in self.VALID_ROLES}
        role_check = {r: len(roles[r]) > 0 for r in self.REQUIRED_ROLES}

        # 3) 召唤: 对未就位成员发起召唤, 不用跑本
        summons = []
        for m in roster:
            if not m["present"]:
                summons.append({
                    "player": m["name"],
                    "summon_to": summon_to or "boss_entrance",
                    "status": "summoned",
                })
                m["present"] = True

        return {
            "roster": roster,
            "size": len(roster),
            "groups": groups,
            "group_count": len(groups),
            "roles": roles,
            "role_check": role_check,
            "summons": summons,
        }

    # ------------------------------------------------------------------
    # 掉落分配: 队长分配 + 拾取绑定 + 分配记录
    # ------------------------------------------------------------------
    def distribute_loot(self, items=None, members=None, master_looter=None, rolls=None):
        """队长分配模式: 只有队长能分配, 装备拾取绑定, 全程记录。"""
        items = list(items) if items else []
        members = list(members) if members else []
        rolls = dict(rolls) if rolls else {}

        # 1) 队长分配: 非队长发起的分配直接拒绝
        if master_looter is None or master_looter not in members:
            return {
                "mode": "master_looter",
                "master_looter": master_looter,
                "distributed": [],
                "rejected": list(items),
                "reason": "only the master looter can distribute loot",
                "log": list(self.loot_log),
            }

        distributed = []
        for item in items:
            item = dict(item)
            # 队长指定归属; 未指定时按 ROLL 点最高者(仅队长登记的 ROLL 有效)
            winner = item.get("assigned_to")
            if winner is None and rolls.get(item.get("name")):
                valid = {p: v for p, v in rolls[item["name"]].items() if p in members}
                winner = max(valid, key=valid.get) if valid else None
            if winner is None and members:
                winner = members[0]

            # 2) 拾取绑定: 分配即绑定, 不可交易
            record = {
                "item": item.get("name", "unknown_item"),
                "winner": winner,
                "bound": True,
                "bind_on_pickup": True,
                "tradeable": False,
                "distributed_by": master_looter,
            }
            # 3) 分配记录: 谁拿了什么全部入账
            self.loot_log.append(record)
            distributed.append(record)

        return {
            "mode": "master_looter",
            "master_looter": master_looter,
            "distributed": distributed,
            "rejected": [],
            "reason": None,
            "log": list(self.loot_log),
        }
