"""Profile parsing adapted from AstralParty_Dex (MIT); no network or credentials."""

import json
import time
from .paths import asset

MODE = {1: "标准对战", 2: "合作挑战", 3: "限时玩法", 4: "合作挑战"}
MAPS = {
    81005: "龙宫游乐园（旧）",
    81007: "梦想号-上层甲板",
    82007: "星趴·梦想号",
    82008: "御魂庆典",
    82010: "水乡古镇",
    82012: "魔法学院",
    82013: "龙宫游乐园",
    82014: "幽魂暗巷",
    82015: "园林中庭",
    82016: "异变图书馆",
    83001: "海选赛运动场",
    83002: "淘汰赛运动场",
    83003: "决赛大赛场",
}
POT_WIKI_FILE = asset("assets", "data", "potential.json")
POT_LIVE_FILE = asset("assets", "data", "potential_live.json")
SKIN_WIKI_FILE = asset("assets", "data", "skins.json")
COLLAB_IDS = {301, 302, 303, 304, 305, 306}
COLLAB_SKIN_SLOTS = {
    100301001: (301, 1),
    100301002: (302, 1),
    100301003: (301, 2),
    100301004: (302, 2),
}


def _bondless():
    return set()


def _load_tables():
    hero, title = {}, {}
    f = asset("assets", "data", "character_ids.json")
    if f.exists():
        for h in json.loads(f.read_text(encoding="utf-8")):
            hero[h["id"]] = h["name"]
    f2 = asset("assets", "data", "heroes.json")
    if f2.exists():
        for h in json.loads(f2.read_text(encoding="utf-8")):
            try:
                title[int(h["id"])] = h.get("title") or ""
            except (TypeError, ValueError):
                pass
    return hero, title


def map_name(mid):
    """地图 ID → 名字。仅查已知映射表，查不到时显示「地图8xxxx」。"""
    if not mid:
        return ""
    try:
        mid = int(mid)
    except Exception:
        return ""
    return MAPS.get(mid) or ("地图%d" % mid)


def _load_potential():
    """角色「潜能是否已开放」清单。

    来源优先级（游戏会持续开放新角色的潜能，Wiki 数据存在滞后）：
      ① potential_live.json  —— 从游戏进程内存读取的客户端配置表，优先使用，
                                游戏运行时自动同步，见 sync_potential_from_game
      ② potential.json  —— Wiki 抓取，兜底（滞后于游戏更新）
    返回 ({角色名: True/False}, 来源说明)
    """
    for f, tag in ((POT_LIVE_FILE, "游戏"), (POT_WIKI_FILE, "Wiki")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out = {}
            for k, v in (d or {}).items():
                if isinstance(v, dict):
                    r = v.get("released")
                else:
                    r = v
                if r is not None:
                    out[k] = bool(r)
            if out:
                return out, tag
        except Exception:
            continue
    return {}, "未知"


def _load_skin_wiki():
    try:
        return json.loads(SKIN_WIKI_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_skins(p):
    """皮肤清单：直接从背包的 100<角色ID><序号> 空间读取，不做推断。

    契约状态取自 roleCard.isBreakThrough（游戏里的「缔结契约」按钮）。
    """
    wiki = _load_skin_wiki()
    by_id = {}
    for nm, v in wiki.items():
        if v.get("id"):
            by_id[int(v["id"])] = (nm, v)

    # ① 背包里 100 + 3位角色ID + 3位序号
    owned = {}
    for it in getattr(p, "bag_items", []) or []:
        iid = int(getattr(it, "item_id", 0))
        if iid in COLLAB_SKIN_SLOTS:  # 联动批次号段 ⇒ 按登记表还原到真正的角色
            cid, slot = COLLAB_SKIN_SLOTS[iid]
            owned.setdefault(cid, set()).add(slot)
            continue
        s = str(iid)
        if len(s) == 9 and s.startswith("100"):
            owned.setdefault(int(s[3:6]), set()).add(int(s[6:]))

    out, tot_orig, tot_bond, tot_buy, n_ctr = [], 0, 0, 0, 0
    cards = getattr(p, "roleCard", {}) or {}
    # 未拥有的角色也一并列出（全清单）：它们没有背包记录 ⇒ 所有格子自然是灰的
    for hid in sorted(set(by_id.keys()) | set(cards.keys()) | set(owned.keys())):
        card = cards.get(hid)
        owns = card is not None
        has = owned.get(hid, set())
        nm, v = by_id.get(hid, ("角色#%d" % hid, {}))
        names = v.get("skins") or []
        contract = bool(getattr(card, "isBreakThrough", False))
        collab = (hid in COLLAB_IDS) or (hid in _bondless())  # 名单打底 + 本机立绘证据
        # 联动角色没有羁绊皮肤（不显示这一格）；且背包里没有 <角色>001 这条初始皮肤
        # 记录，但「拥有角色」本身就等于拥有其初始外观 ⇒ 已拥有时初始皮肤直接算已拥有。
        # 计数仍按背包（与游戏内显示一致），不为联动角色额外 +1。
        # ── 这一行怎么排格子 ──
        # 按序号顺序拼：001 初始 → 002 羁绊（联动没有立绘就不设这一格）→ 003+ 名皮
        # → 【背包里有、名单里没有的序号一律补一格「皮肤 00X」】。
        # 补格子这条是关键：以前只画"名单里有的"，背包里多出来的条目会被计入统计却看不见，
        # 于是顶部统计与框里亮格数对不上。任何一条都不允许凭空消失。
        # 联动角色没有羁绊皮肤 ⇒ 名皮紧贴初始皮肤排（002 起）；普通角色 002 是羁绊，名皮从 003 起
        _base = 2 if collab else 3
        slots = {1: "初始皮肤"}
        if not collab:
            slots[2] = "羁绊皮肤"
        for i, sname in enumerate(names):
            slots[_base + i] = sname
        rows = []
        for _seq in sorted(set(slots) | set(has)):
            if _seq in slots:
                rows.append(
                    {
                        "name": slots[_seq],
                        "has": (_seq in has) or (_seq == 1 and collab and owns),
                    }
                )
            else:
                rows.append({"name": "皮肤 %03d" % _seq, "has": True})
        have = sum(1 for r in rows if r["has"])
        # 计数严格按「框里亮了几格」来算 ⇒ 顶部统计与列表永远对得上
        _a = 1 if rows[0]["has"] else 0  # 初始那一格
        _b = 1 if (not collab and 2 in has) else 0  # 羁绊那一格
        tot_orig += _a
        tot_bond += _b
        tot_buy += have - _a - _b
        n_ctr += 1 if contract else 0
        out.append(
            {
                "id": hid,
                "name": nm,
                "title": v.get("title", ""),
                "contract": contract,
                "owns": owns,
                "have": have,
                "total": len(rows),
                "skins": rows,
            }
        )
    # 已拥有的排前面（按拥有数降序），未拥有的按角色 ID 排在后面
    out.sort(key=lambda x: (0 if x["owns"] else 1, -x["have"], x["id"]))
    # 游戏内「全部皮肤」= 每角色(初始+羁绊) + Wiki 命名皮肤；联动角色没有羁绊那一格
    _bl = COLLAB_IDS | _bondless()  # 分母同样用「名单 + 本机立绘证据」
    sk_total = sum(
        (1 if int(v.get("id") or 0) in _bl else 2) + len(v.get("skins") or [])
        for v in wiki.values()
    )
    return {
        "list": out,
        "orig": tot_orig,
        "bond": tot_bond,
        "buy": tot_buy,
        "contract": n_ctr,
        "named": tot_bond + tot_buy,
        "hero_have": sum(1 for x in out if x["owns"]),
        "hero_total": len(out),
        "sk_total": sk_total,
        "sum": tot_orig + tot_bond + tot_buy,
    }


def _pairs(p, idx):
    """task.condition1[idx].params → {角色ID: 场次}。"""
    try:
        items = p.task.condition1[idx].params
    except Exception:
        return {}
    out = {}
    for x in items:
        fc_, hid_ = getattr(x, "param", None), getattr(x, "param1", None)
        if fc_ is not None and hid_ is not None:
            out[int(hid_)] = int(fc_)
    return out


def _heroes_of(p):
    """角色卡 → 使用排行（含 PVE 等级与潜能三态）。

    未拥有的角色也一并列出（名字取自静态角色表），带 owned=False，界面里发灰显示。
    这样新角色一上线就能看见「有这个人，但我还没有」，而不是整行消失。
    未拥有的行不参与排行统计：场次/胜率/PVE 等级留空，潜能显示为「—」。
    """
    fc, wc = _pairs(p, 17), _pairs(p, 18)
    cards = getattr(p, "roleCard", {}) or {}
    heroes = []
    for hid in sorted(set(cards) | set(HERO)):
        card = cards.get(hid)
        # ── 未拥有：只列名字与称号，其余留空 ──
        if card is None:
            heroes.append(
                {
                    "id": hid,
                    "name": HERO.get(hid, "角色#%d" % hid),
                    "title": TITLE.get(hid, ""),
                    "n": 0,
                    "w": 0,
                    "lv": None,
                    "brk": "—",
                    "owned": False,
                }
            )
            continue
        n = fc.get(hid, 0)
        if not n and not card.isBreakThrough:
            n = 0
        # 等级 = pve_strengthen.level（PVE 英雄等级，协议里有 PveHeroUpLv 升级）
        #   角色卡等级 card.lv 与游戏显示不一致（如 105/129 卡等级 5、PVE 等级 6）
        #   pve 等级为 0 时（例如该角色从未培养）回退用角色卡等级
        try:
            _lv = int(getattr(card.pve_strengthen, "level", 0) or 0)
        except Exception:
            _lv = 0
        # 潜能三态（「已激发」来自数据包；「是否已开放」来自客户端配置表）
        #   talent 非空        → 已激发
        #   已开放 + talent 空 → 未
        #   未开放             → 未开放
        try:
            _has = len(list(getattr(card.pve_strengthen, "talent", []) or [])) > 0
        except Exception:
            _has = False
        _rel = POTENT.get(HERO.get(hid, ""), None)
        if _rel is False:
            _pot = "未开放"
        elif _has:
            _pot = "已激发"
        else:
            _pot = "未"
        heroes.append(
            {
                "id": hid,
                "name": HERO.get(hid, "角色#%d" % hid),
                "title": TITLE.get(hid, ""),
                "n": n,
                "w": wc.get(hid, 0),
                "lv": _lv,
                "brk": _pot,
                "owned": True,
            }
        )
    # 已拥有在前（按场次、编号），未拥有在后（按编号）
    heroes.sort(key=lambda h: (0 if h.get("owned") else 1, -h["n"], h["id"]))

    return heroes


def _recent_of(p, my_uid):
    """最近对局（取自握手包 showPlayer.record[10]）。

    不对服务器发 5153（查自己会被丢弃）；也不走 5155 兜底（该接口限流取不到数据）。
    """
    recent = []
    sp = getattr(p, "showPlayer", None)
    for r in list(getattr(sp, "record", []) or []):
        # record 有 10 局；每局的 data 是**4 个玩家**的列表（model.PlayerFightData）
        #   可用字段：playerId/name/lv/gold/heroId/slot/rank/mapType/isGiveUp/
        #             headIcon/background/playerLevel
        #   伤害/承伤/击杀/治疗量/转账不在此处，而在**对局结算包**
        #     GameFinishS2C.NewAchieveEntry → model.PlayerFinishAchieve
        #     （killCount/totalDamage/totalInjured/treatmentScore/pveTransferGold/
        #       totalGold/totalDie/pkDamageMax/relics/finalKillBoss）
        #   接口已预留：将数值填入 players[i]['stats'] 即可显示。
        players = []
        for it in list(getattr(r, "data", []) or []):
            ph = int(getattr(it, "heroId", 0))
            players.append(
                {
                    "id": int(getattr(it, "playerId", 0)),
                    "name": getattr(it, "name", "") or "—",
                    "heroId": ph,
                    "hero": HERO.get(ph, "角色#%d" % ph),
                    "title": TITLE.get(ph, ""),
                    "lv": int(getattr(it, "lv", 0) or 0),
                    "plv": int(getattr(it, "playerLevel", 0) or 0),
                    "gold": int(getattr(it, "gold", 0) or 0),
                    "rank": int(getattr(it, "rank", 0) or 0),
                    "slot": int(getattr(it, "slot", 0) or 0),
                    "mapType": int(getattr(it, "mapType", 0) or 0),
                    "giveUp": bool(getattr(it, "isGiveUp", False)),
                    "me": int(getattr(it, "playerId", 0)) == my_uid,
                    "stats": None,  # ← 结算数值填入此处
                }
            )
        players.sort(key=lambda x: x["slot"])
        d0 = next((x for x in players if x["me"]), None)
        if d0 is None:
            continue
        hid = d0["heroId"]
        mt = int(d0.get("mapType", 0) or 0)
        recent.append(
            {
                "time": getattr(r, "time", 0),
                "rank": d0["rank"],
                "heroId": hid,
                "hero": d0["hero"],
                "title": d0["title"],
                "mapType": mt,
                "map": MODE.get(mt, "")
                if mt in MODE
                else ("模式%d" % mt if mt else ""),
                "replayId": getattr(r, "replayId", ""),
                "version": getattr(r, "version", ""),
                "gold": d0["gold"],
                "giveUp": d0["giveUp"],
                "players": players,
            }
        )
        # 不使用 5153 查询：查自己会被服务端丢弃，且置于循环中会阻塞约 10×12 秒；
        #   最近对局 / 获赞数据握手包里已包含。

    return recent


HERO, TITLE = _load_tables()
POTENT, _ = _load_potential()


def profile_from_player(p):
    cond = dict(p.task.condition)
    total, wins = cond.get(14, 0), cond.get(13, 0)
    return {
        "uid": int(p.id),
        "nick": p.nick or "未命名玩家",
        "level": int(p.level),
        "total": total,
        "wins": wins,
        "winrate": round(100 * wins / total, 1) if total else 0,
        "praise": int(p.showPlayer.praiseNum),
        "heroCount": len(p.roleCard),
        "heroes": _heroes_of(p),
        "skins": build_skins(p),
        "recent": sorted(
            _recent_of(p, int(p.id)), key=lambda r: r["time"], reverse=True
        ),
        "fetched_at": time.time(),
    }


def public_profile(show, simple, uid):
    sd = show.showData
    stat = sd.statistics
    recent = [
        {
            "replayId": str(r.replayId),
            "time": int(r.time),
            "rank": int(r.rank),
            "heroId": int(r.heroId),
            "hero": HERO.get(r.heroId, f"角色#{r.heroId}"),
            "map": MODE.get(r.mapType, f"模式{r.mapType}"),
        }
        for r in sd.record
        if r.replayId
    ]
    total, wins = int(stat.fightCount), int(stat.winFightCount)
    return {
        "uid": uid,
        "nick": simple.PlayerInfo.name if simple else f"玩家 {uid}",
        "level": int(simple.PlayerInfo.lv) if simple else None,
        "total": total,
        "wins": wins,
        "winrate": round(100 * wins / total, 1) if total else 0,
        "praise": int(sd.praiseNum),
        "heroCount": int(stat.roleCardCount),
        "skinCount": int(stat.skinCount),
        "recent": sorted(recent, key=lambda r: r["time"], reverse=True),
        "fetched_at": time.time(),
    }
