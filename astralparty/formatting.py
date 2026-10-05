"""Chat-friendly bounded pages. External nicknames are kept to a single line."""

import math
from datetime import datetime, timezone, timedelta

from .errors import UserError
from .profile import HERO, map_name
from .review import SOURCE_TEXT

TZ = timezone(timedelta(hours=8))


def clean(value, limit=60):
    return str(value).replace("\n", " ").replace("\r", " ").replace("\x00", "")[:limit]


def stamp(value):
    try:
        value = float(value)
        if value > 1e11:
            value /= 1000
        return (
            datetime.fromtimestamp(value, TZ).strftime("%Y-%m-%d %H:%M")
            if value
            else "时间未知"
        )
    except (ValueError, TypeError, OverflowError, OSError):
        return "时间未知"


def page(items, number, size):
    try:
        n = int(number)
    except (ValueError, TypeError):
        raise UserError("页码必须是正整数。") from None
    count = max(1, math.ceil(len(items) / size))
    if not 1 <= n <= count:
        raise UserError(f"页码超出范围，共 {count} 页。")
    return items[(n - 1) * size : n * size], n, count


def profile_text(p):
    skin = p.get("skinCount", p.get("skins", {}).get("sum", 0))
    return (
        f"星趴档案 · {clean(p['nick'])}\nUID {p['uid']} · 等级 {p.get('level') if p.get('level') is not None else '未知'}\n"
        f"累计 {p['total']} 局 · 胜场 {p['wins']} · 胜率 {p['winrate']}%\n"
        f"角色 {p['heroCount']} · 皮肤 {skin} · 获赞 {p['praise']}\n"
        f"资料获取：{stamp(p['fetched_at'])}（北京时间）\n自己的资料更新：请私聊 /星趴 刷新"
    )


def records_text(p, number=1):
    rows, n, count = page(p["recent"], number, 5)
    lines = [
        f"近期战绩 · {clean(p['nick'])} · UID {p['uid']}",
        f"第 {n}/{count} 页 · 共 {len(p['recent'])} 局",
    ]
    if not rows:
        lines.append("暂无可查询战绩。")
    for index, r in enumerate(rows, (n - 1) * 5 + 1):
        rank = f"第 {r['rank']} 名" if r.get("rank") else "名次未知"
        lines.extend(
            [
                f"{index}. {stamp(r.get('time'))} · {clean(r.get('hero', '未知角色'))} · {rank}",
                f"   {clean(r.get('map') or '模式未知')} · 回放 {r['replayId']}",
            ]
        )
    lines += [
        f"资料获取：{stamp(p['fetched_at'])}（北京时间）",
        "查看单局：/星趴 对局 序号；序号有效期 10 分钟。",
    ]
    return "\n".join(lines)


def heroes_text(p, number=1):
    rows, n, count = page([h for h in p["heroes"] if h["owned"]], number, 8)
    lines = [f"角色排行 · {clean(p['nick'])} · 第 {n}/{count} 页"]
    if not rows:
        lines.append("暂无已拥有角色。")
    for h in rows:
        rate = round(100 * h["w"] / h["n"], 1) if h["n"] else 0
        lines.append(
            f"{clean(h['name'])}（{h['id']}）· {h['n']} 局 / {h['w']} 胜 / {rate}%\n  PVE 等级 {h['lv']} · 潜能 {h['brk']}"
        )
    lines.append(f"资料获取：{stamp(p['fetched_at'])}；名称与潜能开放状态参考静态表。")
    return "\n".join(lines)


def skins_text(p, query="", number=1):
    items = [s for s in p["skins"]["list"] if s["owns"]]
    if query:
        items = [s for s in items if query == str(s["id"]) or query in s["name"]]
        if not items:
            raise UserError("未找到已拥有的匹配角色，请检查角色名或角色 ID。")
    rows, n, count = page(items, number, 3)
    lines = [f"皮肤 · {clean(p['nick'])} · 第 {n}/{count} 页", "✓ 已拥有 / ○ 未拥有"]
    for s in rows:
        lines.append(
            f"{clean(s['name'])}（{s['id']}）· 契约{'已缔结' if s['contract'] else '未缔结'}"
        )
        lines.extend(
            f"  {'✓' if sk['has'] else '○'} {clean(sk['name'])}" for sk in s["skins"]
        )
    if not rows:
        lines.append("暂无已拥有角色。")
    lines.append(f"资料获取：{stamp(p['fetched_at'])}；皮肤名称参考静态表。")
    return "\n".join(lines)


def match_text(review, metadata=None):
    metadata = metadata or {}
    ranks = {int(p["id"]): p.get("rank", 0) for p in metadata.get("players", [])}
    lines = [
        f"对局 · 回放 {review['replay_id']}",
        f"{map_name(review.get('map_id')) or '地图未知'} · 难度 {review.get('difficulty') or '未知'} · {review['rounds']} 轮",
    ]
    for p in review["players"]:
        rank = ranks.get(p["uid"])
        if not rank and any(
            x.get("me") and x["id"] == p["uid"] for x in metadata.get("players", [])
        ):
            rank = metadata.get("rank")
        hero = HERO.get(p["hero_id"], "角色#%s" % p["hero_id"])
        lines.append(
            f"{clean(p['nick'])} · UID {p['uid']}\n  {hero} · 等级 {p['level']} · 名次 {rank or '未知'}"
        )
        t = p["totals"]
        if t:
            lines.append(
                f"  击杀 {t['kill']} · 伤害 {t['dmg']} · 承伤 {t['inj']} · 星币 {t['gold']} · 步数 {t['move']} · 阵亡轮数 {t['died']}"
            )
        else:
            lines.append("  回放缺少战况统计。")
        lines.append(
            "  筹码：" + ("、".join(clean(c["name"]) for c in p["chips"]) or "无记录")
        )
    lines += [
        "统计根据回放快照推导；名次缺失时不推测。",
        f"逐轮复盘：/星趴 复盘 {review['replay_id']} 玩家UID",
    ]
    return "\n".join(lines)


def review_text(review, uid, number=1):
    target = next((p for p in review["players"] if p["uid"] == uid), None)
    if target is None:
        raise UserError("该 UID 不在这局回放中，请先使用 /星趴 对局 回放号 查看玩家。")
    # Include event-only rounds and opening events whose recorded round is zero/unknown.
    events = target["chip_events"]
    rounds = {r["round"]: r for r in target["rounds"]}
    for e in events:
        rounds.setdefault(e.get("round") or 0, None)
    rows, n, count = page(sorted(rounds), number, 5)
    lines = [
        f"逐轮复盘 · {clean(target['nick'])} · UID {uid}",
        f"回放 {review['replay_id']} · 第 {n}/{count} 页",
    ]
    if not rows:
        lines.append("回放缺少逐轮记录。")
    for rnd in rows:
        row = rounds[rnd]
        lines.append(f"第 {rnd} 轮" if rnd else "开局/轮次未知")
        if row:
            lines.append(
                f"击杀 {row['kill']} · 伤害 {row['dmg']} · 承伤 {row['inj']}\n星币 {row['gold']} · 步数 {row['move']} · {'本轮阵亡' if row['died'] else '无阵亡记录'}"
            )
        for e in (e for e in events if (e.get("round") or 0) == rnd):
            candidates = " / ".join(clean(c) for c in e.get("candidates_name", []))
            lines.append(
                f"筹码：{clean(e['name'])} · 来源 {SOURCE_TEXT.get(e['source'], '未知')} · 刷新 {e.get('refresh', 0)} 次"
            )
            if candidates:
                lines.append(f"候选：{candidates}")
            lines.append(f"选中：{clean(e['name'])}")
    lines.append(
        f"下一页：/星趴 复盘 {review['replay_id']} {uid} {n + 1}"
        if n < count
        else "已到最后一页。"
    )
    lines.append("筹码来源根据回放推导，可能存在未知项。")
    return "\n".join(lines)


def split_text(text, max_chars=1800):
    parts, chunk = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:
            if chunk:
                parts.append(chunk.rstrip())
                chunk = ""
            parts.append(line[:max_chars])
            line = line[max_chars:]
        if len(chunk) + len(line) > max_chars:
            parts.append(chunk.rstrip())
            chunk = ""
        chunk += line
    if chunk:
        parts.append(chunk.rstrip())
    return parts or ["暂无数据。"]
