"""Structured card models with embedded local Pixel assets and escaped HTML fields."""

import base64
from functools import lru_cache

from .errors import UserError
from .formatting import clean, stamp
from .paths import asset
from .profile import HERO, map_name
from .review import SOURCE_TEXT

CARD_TEMPLATE = asset("assets", "templates", "card.html").read_text(encoding="utf-8")


@lru_cache(maxsize=128)
def pixel(hero_id):
    try:
        hero_id = int(hero_id)
    except (TypeError, ValueError):
        return ""
    if not 100 <= hero_id <= 999:
        return ""
    path = asset("assets", "pixel", f"{hero_id}.png")
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def metrics(*pairs):
    return [{"label": label, "value": str(value)} for label, value in pairs]


def row(title, hero_id=0, **kwargs):
    result = {
        "title": clean(title),
        "hero": HERO.get(hero_id, f"角色#{hero_id}") if hero_id else "",
        "avatar": pixel(hero_id),
        "ordinal": "",
        "meta": [],
        "badge": "",
        "badge_kind": "",
        "metrics": [],
        "chips": [],
        "details": [],
    }
    result.update(kwargs)
    return result


def card(title, subtitle, eyebrow, **kwargs):
    result = {
        "title": clean(title),
        "subtitle": clean(subtitle, 200),
        "eyebrow": eyebrow,
        "avatar": "",
        "metrics": [],
        "section": "",
        "rows": [],
        "layout": "",
        "footer": "",
        "tip": "",
        "empty": "暂无可查询数据。",
    }
    result.update(kwargs)
    return result


def profile_card(p):
    heroes = [h for h in p.get("heroes", []) if h["owned"] and h["n"]]
    heroes.sort(key=lambda h: (-h["n"], h["id"]))
    primary = (
        heroes[0]["id"]
        if heroes
        else next((r["heroId"] for r in p.get("recent", []) if r.get("heroId")), 0)
    )
    items = []
    for h in heroes[:3]:
        rate = round(100 * h["w"] / h["n"], 1) if h["n"] else 0
        items.append(
            row(
                h["name"],
                h["id"],
                meta=[f"{h['n']} 局 · {h['w']} 胜 · 胜率 {rate}%"],
                badge=f"PVE 等级 {h['lv']}",
            )
        )
    return card(
        p["nick"],
        f"UID {p['uid']} · 账号等级 {p['level'] if p.get('level') is not None else '未知'}",
        "PLAYER PROFILE",
        avatar=pixel(primary),
        metrics=metrics(
            ("累计场次", p["total"]),
            ("胜场", p["wins"]),
            ("胜率", f"{p['winrate']}%"),
            ("拥有角色", p["heroCount"]),
            ("拥有皮肤", p.get("skinCount", p.get("skins", {}).get("sum", 0))),
            ("获赞", p["praise"]),
        ),
        section="常用角色" if items else "",
        rows=items,
        footer=f"资料获取 {stamp(p['fetched_at'])} · 北京时间",
        tip="需要更新资料时，请私聊 ~星趴 刷新。",
    )


def records_card(p):
    records = p["recent"]
    items = []
    for index, r in enumerate(records, 1):
        rank = r.get("rank", 0)
        items.append(
            row(
                r.get("hero") or HERO.get(r.get("heroId"), "未知角色"),
                r.get("heroId", 0),
                ordinal=f"{index:02d}",
                meta=[
                    f"{stamp(r.get('time'))} · {clean(r.get('map') or '模式未知')}",
                    f"回放 {r['replayId']}",
                ],
                badge=f"第 {rank} 名" if rank else "名次未知",
                badge_kind="gold" if rank == 1 else "",
            )
        )
    return card(
        "近期战绩",
        f"{clean(p['nick'])} · UID {p['uid']} · 最近 {len(p['recent'])} 局",
        "RECENT MATCHES",
        section="对局记录",
        rows=items,
        layout="grid",
        footer=f"资料获取 {stamp(p['fetched_at'])} · 北京时间",
        tip="查看单局：~星趴 对局 序号 · 序号有效期 10 分钟。",
    )


def heroes_card(p):
    heroes = [h for h in p["heroes"] if h["owned"]]
    items = []
    for index, h in enumerate(heroes, 1):
        rate = round(100 * h["w"] / h["n"], 1) if h["n"] else 0
        items.append(
            row(
                h["name"],
                h["id"],
                ordinal=f"{index:02d}",
                meta=[f"角色 {h['id']} · PVE 等级 {h['lv']}"],
                badge=f"潜能 {h['brk']}",
                metrics=metrics(
                    ("场次", h["n"]), ("胜场", h["w"]), ("胜率", f"{rate}%")
                ),
            )
        )
    return card(
        "角色使用排行",
        f"{clean(p['nick'])} · 按使用场次排序",
        "HERO COLLECTION",
        section="已拥有角色",
        rows=items,
        layout="grid",
        footer=f"资料获取 {stamp(p['fetched_at'])} · 名称及潜能开放状态参考静态表。",
    )


def skins_card(p, query=""):
    skins = [s for s in p["skins"]["list"] if s["owns"]]
    if query:
        skins = [s for s in skins if query == str(s["id"]) or query in s["name"]]
        if not skins:
            raise UserError("未找到已拥有的匹配角色，请检查角色名或角色 ID。")
    items = [
        row(
            s["name"],
            s["id"],
            meta=[f"角色 {s['id']} · 已拥有 {s['have']} / {s['total']} 套"],
            badge="契约已缔结" if s["contract"] else "契约未缔结",
            chips=[
                {
                    "label": ("✓ " if sk["has"] else "○ ") + clean(sk["name"]),
                    "owned": sk["has"],
                }
                for sk in s["skins"]
            ],
        )
        for s in skins
    ]
    return card(
        "皮肤收藏",
        f"{clean(p['nick'])} · ✓ 已拥有 / ○ 未拥有",
        "SKIN COLLECTION",
        section="角色与皮肤",
        rows=items,
        layout="grid",
        footer=f"资料获取 {stamp(p['fetched_at'])} · 皮肤名称参考静态表。",
        tip="像素图用于标识角色，不代表对应皮肤外观。",
    )


def match_card(review, metadata=None):
    ranks = {
        int(p["id"]): p.get("rank", 0) for p in (metadata or {}).get("players", [])
    }
    items = []
    for p in review["players"]:
        t, rank = p["totals"], ranks.get(p["uid"])
        values = (
            metrics(
                ("击杀", t["kill"]),
                ("伤害", t["dmg"]),
                ("承伤", t["inj"]),
                ("星币", t["gold"]),
                ("步数", t["move"]),
                ("阵亡轮数", t["died"]),
            )
            if t
            else []
        )
        items.append(
            row(
                p["nick"],
                p["hero_id"],
                meta=[
                    f"UID {p['uid']}",
                    f"{HERO.get(p['hero_id'], '未知角色')} · 玩家等级 {p['level']}",
                ],
                badge=f"第 {rank} 名" if rank else "名次未知",
                badge_kind="gold" if rank == 1 else "",
                metrics=values,
                chips=[{"label": clean(c["name"]), "owned": True} for c in p["chips"]],
            )
        )
    return card(
        "对局详情",
        f"回放 {review['replay_id']}",
        "MATCH REVIEW",
        section="参赛玩家",
        rows=items,
        layout="grid",
        metrics=metrics(
            ("地图", map_name(review.get("map_id")) or "未知"),
            ("难度", review.get("difficulty") or "未知"),
            ("轮次", review["rounds"]),
        ),
        footer="统计由回放快照推导；缺失名次如实显示为未知。",
        tip=f"逐轮复盘：~星趴 复盘 {review['replay_id']} 玩家UID",
    )


def review_card(review, uid):
    target = next((p for p in review["players"] if p["uid"] == uid), None)
    if target is None:
        raise UserError("该 UID 不在这局回放中，请先使用 ~星趴 对局 回放号 查看玩家。")
    rounds = {r["round"]: r for r in target["rounds"]}
    for event in target["chip_events"]:
        rounds.setdefault(event.get("round") or 0, None)
    values = sorted(rounds)
    items = []
    for rnd in values:
        r = rounds[rnd]
        stats = (
            metrics(
                ("击杀", r["kill"]),
                ("伤害", r["dmg"]),
                ("承伤", r["inj"]),
                ("星币", r["gold"]),
                ("步数", r["move"]),
            )
            if r
            else []
        )
        details = []
        for e in (e for e in target["chip_events"] if (e.get("round") or 0) == rnd):
            details.append(
                {
                    "label": clean(e["name"]),
                    "value": f"来源：{SOURCE_TEXT.get(e['source'], '未知')} · 刷新 {e.get('refresh', 0)} 次",
                }
            )
            if e.get("candidates_name"):
                details.append(
                    {
                        "label": "候选",
                        "value": " / ".join(clean(c) for c in e["candidates_name"]),
                    }
                )
            details.append({"label": "选中", "value": clean(e["name"])})
        items.append(
            row(
                f"第 {rnd} 轮" if rnd else "开局 / 轮次未知",
                badge="本轮阵亡" if r and r["died"] else "",
                metrics=stats,
                details=details,
            )
        )
    return card(
        f"{clean(target['nick'])} · 逐轮复盘",
        f"UID {uid} · {HERO.get(target['hero_id'], '未知角色')} · 回放 {review['replay_id']}",
        "ROUND TIMELINE",
        avatar=pixel(target["hero_id"]),
        section="战况与筹码",
        rows=items,
        layout="grid",
        footer="逐轮统计与筹码来源由回放推导，可能存在未知项。",
    )


def text_card(text):
    # Compatibility for plain third-party Reply values; production views use structured data.
    return card(
        "星趴档案",
        "",
        "ASTRAL PARTY",
        rows=[row("查询结果", details=[{"label": "", "value": clean(text, 1800)}])],
    )
