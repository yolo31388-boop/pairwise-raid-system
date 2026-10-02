"""
pairwise-raid-system - RaidSystem

Raid 系统核心，覆盖五块功能：

1. spawn_boss        —— 刷新时间窗口 + 刷新预告 + 开 BOSS 人数检测
2. update_threat     —— 距离修正 + 仇恨衰减 + 技能/职责修正（坦克/治疗/输出）
3. cast_boss_skill   —— 仇恨表目标选择 + 全屏技能预警 + 分阶段技能组
4. manage_raid_team  —— 自动分组 + 职责标记 + 跑本召唤
5. distribute_loot   —— 队长分配 + 拾取绑定(BoP) + 分配记录
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# 刷新规则
# ---------------------------------------------------------------------------
SPAWN_WINDOW_START_HOUR = 18   # 允许开 BOSS 的时间窗口（24h 制，小时）
SPAWN_WINDOW_END_HOUR = 24
SPAWN_ANNOUNCE_LEAD_SEC = 300  # 刷新预告提前量（5 分钟）
MIN_RAID_PLAYERS = 10          # 开 BOSS 所需最少人数

# ---------------------------------------------------------------------------
# 仇恨规则
# ---------------------------------------------------------------------------
MELEE_RANGE_YARDS = 5.0        # 近战距离：在此范围内为近战
RANGED_THREAT_MODIFIER = 0.5   # 远程造成的仇恨按近战一半计算
TANK_SKILL_THREAT_MODIFIER = 2.0
DPS_SKILL_THREAT_MODIFIER = 1.0
HEAL_SKILL_THREAT_MODIFIER = 0.5   # 治疗仇恨低于输出
THREAT_DECAY_PER_SEC = 0.02        # 每秒衰减 2%，OT 后脱战可回拉
OT_RATIO = 1.1                     # 超过当前目标仇恨 110% 视为 OT

# ---------------------------------------------------------------------------
# BOSS 技能规则
# ---------------------------------------------------------------------------
PHASE_SKILLS: Dict[int, List[str]] = {
    1: ["普通攻击", "顺劈斩"],
    2: ["普通攻击", "顺劈斩", "烈焰喷吐"],
    3: ["全屏湮灭", "烈焰喷吐", "死亡凝视"],
}

# 全屏/AOE 技能，释放前必须预警
AOE_SKILLS = {"全屏湮灭", "烈焰喷吐"}
WARN_LEAD_SEC = 3  # 全屏技能预警时间（秒）

# ---------------------------------------------------------------------------
# 团队 / 掉落规则
# ---------------------------------------------------------------------------
GROUP_SIZE = 5
BIND_ON_PICKUP = "BoP"   # 拾取绑定，绑定后不可交易


class RaidSystem:
    def __init__(self):
        self.state: Dict[str, Any] = {}
        self.spawn_schedule: List[Dict[str, Any]] = []
        # 仇恨
        self.threat_table: Dict[str, Dict[str, float]] = {}
        self._threat_tick: Dict[str, Dict[str, float]] = {}
        # 团队
        self.groups: Dict[str, List[str]] = {}
        self.roles: Dict[str, str] = {}
        self.summons: List[Dict[str, Any]] = []
        # 掉落
        self.loot_log: List[Dict[str, Any]] = []

    # =======================================================================
    # 1. BOSS 刷新：时间窗口 + 预告 + 人数检测
    # =======================================================================
    def spawn_boss(
        self,
        boss_id: str = "world_boss",
        hour: Optional[int] = None,
        online_players: Optional[int] = None,
        min_players: int = MIN_RAID_PLAYERS,
        announce_lead_sec: int = SPAWN_ANNOUNCE_LEAD_SEC,
        now: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if now is None:
            now = time.time()
        if hour is None:
            hour = time.localtime(now).tm_hour
        if online_players is None:
            online_players = self.state.get("online_players", 0)

        in_window = SPAWN_WINDOW_START_HOUR <= hour < SPAWN_WINDOW_END_HOUR
        enough_players = online_players >= min_players

        # 刷新预告：无论是否到点，先登记一条提前预告
        announce_at = now + announce_lead_sec
        announcement = {
            "type": "spawn_warning",
            "boss": boss_id,
            "announce_at": announce_at,
            "lead_sec": announce_lead_sec,
            "message": f"{boss_id} 将于 {announce_lead_sec // 60} 分钟后刷新",
        }
        self.spawn_schedule.append(announcement)

        result: Dict[str, Any] = {
            "boss": boss_id,
            "hour": hour,
            "in_time_window": in_window,
            "online_players": online_players,
            "min_players": min_players,
            "enough_players": enough_players,
            "announced": True,
            "announcement": announcement,
            "spawned": False,
        }

        if not in_window:
            # 非刷新窗口：只预告，不实际刷新（半夜不刷）
            result["reason"] = "outside_spawn_window"
            return result
        if not enough_players:
            # 窗口内但人数不足：预告，等待集结，不开 BOSS
            result["reason"] = "not_enough_players"
            return result

        self.state["active_boss"] = boss_id
        self.state["spawned_at"] = now
        self.threat_table.setdefault(boss_id, {})
        result["spawned"] = True
        result["spawned_at"] = now
        result["reason"] = "ok"
        return result

    # =======================================================================
    # 2. 仇恨：距离修正 + 衰减 + 技能修正
    # =======================================================================
    def update_threat(
        self,
        player: str = "player",
        boss_id: str = "world_boss",
        amount: float = 0.0,
        distance: float = 0.0,
        skill_type: str = "dps",  # dps / heal / tank
        now: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if now is None:
            now = time.time()
        self.threat_table.setdefault(boss_id, {})
        self._threat_tick.setdefault(boss_id, {})

        table = self.threat_table[boss_id]
        ticks = self._threat_tick[boss_id]

        # 仇恨衰减：所有人按距上次更新的时间统一衰减，OT 后停止输出可被拉回
        elapsed = {p: max(0.0, now - t) for p, t in ticks.items()}
        for p, dt in elapsed.items():
            if dt > 0:
                table[p] *= max(0.0, 1.0 - THREAT_DECAY_PER_SEC * dt)
        ticks.update({p: now for p in ticks})

        # 距离修正：远程仇恨打折，避免远程无脑高仇恨
        if distance <= MELEE_RANGE_YARDS:
            range_modifier = 1.0
            position = "melee"
        else:
            range_modifier = RANGED_THREAT_MODIFIER
            position = "ranged"

        # 技能/职责修正：坦克技能高仇恨，治疗仇恨低于输出
        skill_modifiers = {
            "tank": TANK_SKILL_THREAT_MODIFIER,
            "dps": DPS_SKILL_THREAT_MODIFIER,
            "heal": HEAL_SKILL_THREAT_MODIFIER,
        }
        skill_modifier = skill_modifiers.get(skill_type, DPS_SKILL_THREAT_MODIFIER)

        gained = amount * range_modifier * skill_modifier
        table[player] = table.get(player, 0.0) + gained
        ticks[player] = now

        # 当前目标 = 仇恨最高者；超过其 110% 即 OT
        target = max(table, key=table.get)
        top_threat = table[target]
        has_aggro = target == player
        is_ot = (
            not has_aggro
            and top_threat > 0
            and table[player] >= top_threat * OT_RATIO
        )

        return {
            "boss": boss_id,
            "player": player,
            "gained_threat": gained,
            "raw_amount": amount,
            "range_modifier": range_modifier,
            "skill_modifier": skill_modifier,
            "skill_type": skill_type,
            "position": position,
            "distance": distance,
            "total_threat": table[player],
            "threat_table": dict(table),
            "target": target,
            "has_aggro": has_aggro,
            "is_ot": is_ot,
        }

    # =======================================================================
    # 3. BOSS 技能：目标选择 + 预警 + 阶段区分
    # =======================================================================
    def cast_boss_skill(
        self,
        boss_id: str = "world_boss",
        skill: Optional[str] = None,
        phase: int = 1,
        targets: Optional[List[str]] = None,
        now: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if now is None:
            now = time.time()

        available = PHASE_SKILLS.get(phase, PHASE_SKILLS[1])

        # 技能必须属于当前阶段，杜绝每个阶段技能一样
        if skill is None:
            skill = available[0]
        phase_valid = skill in available
        if not phase_valid:
            return {
                "boss": boss_id,
                "skill": skill,
                "phase": phase,
                "available_skills": available,
                "cast": False,
                "reason": "skill_not_available_in_phase",
            }

        is_aoe = skill in AOE_SKILLS

        # 目标选择：单体技能打仇恨最高者（T 接怪），不再随机打人；
        # 全屏技能目标为全体
        if is_aoe:
            selected: List[str] = list(targets or [])
            table = self.threat_table.get(boss_id, {})
            if not selected and table:
                selected = sorted(table, key=table.get, reverse=True)
            target_type = "all"
        else:
            table = self.threat_table.get(boss_id, {})
            if table:
                selected = [max(table, key=table.get)]
            elif targets:
                selected = [targets[0]]
            else:
                selected = []
            target_type = "highest_threat"

        result: Dict[str, Any] = {
            "boss": boss_id,
            "skill": skill,
            "phase": phase,
            "available_skills": available,
            "is_aoe": is_aoe,
            "target_type": target_type,
            "targets": selected,
            "cast_at": now,
            "cast": True,
        }

        # 全屏技能必须先预警，到点才结算伤害
        if is_aoe:
            result["warning"] = {
                "type": "skill_telegraph",
                "skill": skill,
                "warn_at": now,
                "land_at": now + WARN_LEAD_SEC,
                "lead_sec": WARN_LEAD_SEC,
                "message": f"{boss_id} 正在引导 {skill}，{WARN_LEAD_SEC} 秒后全屏秒杀，立即分散/躲避",
            }
            result["damage_applied"] = False  # 预警阶段不造成伤害
        else:
            result["damage_applied"] = bool(selected)
        return result

    # =======================================================================
    # 4. 团队：分组 + 职责标记 + 召唤
    # =======================================================================
    def manage_raid_team(
        self,
        members: Optional[List[Any]] = None,
        group_size: int = GROUP_SIZE,
        roles: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if members is None:
            members = []

        normalized: List[Dict[str, str]] = []
        for member in members:
            if isinstance(member, dict):
                name = str(member.get("name"))
                role = str(member.get("role", "dps"))
            else:
                name = str(member)
                role = "dps"
            normalized.append({"name": name, "role": role})

        if roles:
            for name, role in roles.items():
                for entry in normalized:
                    if entry["name"] == name:
                        entry["role"] = role

        # 职责标记（坦克/治疗/输出）
        self.roles = {entry["name"]: entry["role"] for entry in normalized}

        # 自动分组：按 GROUP_SIZE 切组，坦克优先排到各组
        order = sorted(normalized, key=lambda e: 0 if e["role"] == "tank" else 1)
        groups: Dict[str, List[str]] = {}
        for index, entry in enumerate(order):
            group_id = f"group_{index // group_size + 1}"
            groups.setdefault(group_id, []).append(entry["name"])
        self.groups = groups

        return {
            "members": [entry["name"] for entry in normalized],
            "roles": dict(self.roles),
            "groups": {gid: list(players) for gid, players in groups.items()},
            "group_count": len(groups),
            "summon_available": True,
        }

    def summon_member(
        self,
        player: str,
        destination: str = "raid_entrance",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """团队召唤：把跑本的成员直接拉到副本门口。"""
        request = {
            "player": player,
            "destination": destination,
            "status": "summoned",
        }
        self.summons.append(request)
        return request

    # =======================================================================
    # 5. 掉落：队长分配 + 拾取绑定 + 记录
    # =======================================================================
    def distribute_loot(
        self,
        item: Optional[str] = None,
        leader: Optional[str] = None,
        actor: Optional[str] = None,
        recipient: Optional[str] = None,
        roll: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        item = item or "未知装备"
        # 发起分配的人，默认按队长处理（无参调用的兼容路径）
        if actor is None:
            actor = leader
        if leader is None:
            leader = actor

        result: Dict[str, Any] = {
            "item": item,
            "leader": leader,
            "actor": actor,
            "recipient": recipient,
            "binding": BIND_ON_PICKUP,
            "tradeable": False,
            "distributed": False,
        }

        # 队长分配：非队长发起的 ROLL 一律拒绝
        if actor is not None and leader is not None and actor != leader:
            result["reason"] = "master_looter_only"
            return result

        if recipient is None:
            # 尚未指定归属：进入队长分配待命，不允许全员自由 ROLL
            result["reason"] = "awaiting_leader_assignment"
            return result

        result["recipient"] = recipient
        result["distributed"] = True
        result["reason"] = "ok"

        # 拾取绑定：归属确定即绑定，记录在案
        record = {
            "item": item,
            "recipient": recipient,
            "assigned_by": leader,
            "binding": BIND_ON_PICKUP,
            "tradeable": False,
            "roll": roll,
        }
        self.loot_log.append(record)
        result["record"] = record
        return result
