#!/usr/bin/env python3
"""
压缩历史学习时长：把所有会话时长整体缩小指定倍数（默认 ÷10）。

背景：
  旧版实现中，页面切后台只暂停计时、不结束会话，导致一个会话可以
  跨几小时甚至一整天。例如早 8 点打开、晚 10 点关，一次会话记录 14 小时，
  累计下来就是几百上千分钟。这些数据只积累了一两天，直接等比例压缩即可。

本脚本做的事（幂等、可重复执行）：
  1. 所有会话时长 ÷ N（默认 10），未结束的会话先按真实跨度结算再除
  2. 压缩后小于 5 秒的记 0（避免噪声）
  3. 列出「会话数/总时长异常」的账号，提示可能是测试数据
  4. 可选：删除某个学生的全部会话（用于清理测试账号）

⚠️ 注意：本脚本非幂等（执行两次会再除一次）。只跑一次即可。

用法（PythonAnywhere，务必先备份）：
    cp ~/zsd-study/data/study.db ~/study_backup_$(date +%Y%m%d_%H%M%S).db

    # 1) 预览（默认 ÷10）
    python3 ~/zsd-study/scripts/fix_inflated_time.py \
        --db ~/zsd-study/data/study.db --dry-run

    # 2) 执行
    python3 ~/zsd-study/scripts/fix_inflated_time.py \
        --db ~/zsd-study/data/study.db

    # 3) 想再狠一点（÷20）
    python3 ~/zsd-study/scripts/fix_inflated_time.py \
        --db ~/zsd-study/data/study.db --divide 20

    # 4) 删除某个测试账号（如 wkh/default）的全部会话
    python3 ~/zsd-study/scripts/fix_inflated_time.py \
        --db ~/zsd-study/data/study.db --reset-student wkh
"""
import argparse
import os
import sqlite3
from datetime import datetime, timedelta

SESSION_MIN_SECONDS = 5
DEFAULT_DIVIDE = 10        # 默认把所有会话时长整体缩小 10 倍


def parse(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def fmt_sec(s):
    s = int(s or 0)
    if s <= 0:
        return "0秒"
    if s < 60:
        return f"{s}秒"
    if s < 3600:
        v = round(s / 60, 1)
        return f"{v:g}分"
    return f"{round(s/3600,1):g}时"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite 数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument("--divide", type=int, default=DEFAULT_DIVIDE,
                    help=f"压缩倍数：所有会话时长 ÷ 该值，默认 {DEFAULT_DIVIDE}（即缩小 10 倍）")
    ap.add_argument("--reset-student", help="删除指定学生的全部会话（用于清理测试账号）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"❌ 数据库不存在：{args.db}")
        return 1

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    # 0) 删除测试账号
    if args.reset_student:
        n = con.execute("DELETE FROM sessions WHERE student_id=?",
                        (args.reset_student,)).rowcount
        if not args.dry_run:
            con.commit()
        print(f"🗑️ {'（预览）' if args.dry_run else ''}已删除学生 '{args.reset_student}' 的 {n} 条会话")
        if args.dry_run:
            con.close()
            return 0

    rows = [dict(r) for r in con.execute(
        "SELECT id, student_id, started_at, ended_at, duration, page FROM sessions ORDER BY id")]

    print(f"=== 修复前：共 {len(rows)} 条会话 ===")
    print("\n按学生汇总：")
    agg = {}
    for r in rows:
        a = agg.setdefault(r["student_id"], {"n": 0, "tot": 0})
        a["n"] += 1
        a["tot"] += int(r["duration"] or 0)
    for sid, a in sorted(agg.items(), key=lambda x: -x[1]["tot"]):
        print(f"  {sid:<18} {a['n']:>3} 次  {a['tot']:>8} 秒 = {fmt_sec(a['tot']):>10}")

    # 提示异常账号
    print("\n⚠️ 以下账号会话数或总时长异常，可能是测试/调试数据：")
    flagged = False
    for sid, a in sorted(agg.items(), key=lambda x: -x[1]["tot"]):
        if a["n"] > 20 or a["tot"] > 120*60:
            flagged = True
            print(f"  · {sid}: {a['n']} 次，累计 {fmt_sec(a['tot'])}")
            print(f"    如需清空可执行：--reset-student {sid}")
    if not flagged:
        print("  未发现明显异常账号")

    # 1) 整体压缩：所有会话时长按比例缩小（默认除以 10）
    div = max(1, args.divide)
    fixes = []
    for r in rows:
        dur = int(r["duration"] or 0)
        st = parse(r["started_at"])
        if not st:
            continue
        # 未结束会话按真实跨度先算出一个基准值
        if r["ended_at"]:
            base = dur
        else:
            span = max(0, int((datetime.now() - st).total_seconds()))
            base = dur if dur > 0 else span

        if base <= 0:
            continue
        new_dur = int(round(base / div))
        if new_dur < SESSION_MIN_SECONDS:
            new_dur = 0
        if new_dur != base:
            fixes.append((r["id"], r["student_id"], base, new_dur, bool(r["ended_at"])))
            if not args.dry_run:
                end_at = r["ended_at"] or datetime.now().isoformat(timespec="seconds")
                con.execute("UPDATE sessions SET ended_at=?, duration=? WHERE id=?",
                            (end_at, new_dur, r["id"]))

    print(f"\n=== 待压缩 {len(fixes)} 条（时长 ÷ {div}）===")
    for i, sid, old, new, was_ended in fixes[:40]:
        tag = "已结束" if was_ended else "未结束"
        print(f"  id={i:<4} {sid:<16} {fmt_sec(old):>10} → {fmt_sec(new):>10}  ({tag})")
    if len(fixes) > 40:
        print(f"  ... 其余 {len(fixes)-40} 条略")

    # 2) 处理未结束会话：统一结算（时长沿用上面已算好的压缩值）
    opens = [dict(r) for r in con.execute(
        "SELECT id, student_id, started_at, duration FROM sessions WHERE ended_at IS NULL")]
    open_fixes = []
    now = datetime.now().isoformat(timespec="seconds")
    for r in opens:
        st = parse(r["started_at"])
        if not st:
            continue
        span = max(0, int((datetime.now() - st).total_seconds()))
        new_dur = int(round(span / div))
        if new_dur < SESSION_MIN_SECONDS:
            new_dur = 0
        open_fixes.append((r["id"], r["student_id"], int(r["duration"] or 0), new_dur))
        if not args.dry_run:
            con.execute("UPDATE sessions SET ended_at=?, duration=? WHERE id=?",
                        (now, new_dur, r["id"]))

    if open_fixes:
        print(f"\n=== 待结算 {len(open_fixes)} 条未结束会话（同样 ÷ {div}）===")
        for i, sid, old, new in open_fixes[:20]:
            print(f"  id={i:<4} {sid:<16} {fmt_sec(old):>10} → {fmt_sec(new):>10}")
        if len(open_fixes) > 20:
            print(f"  ... 其余 {len(open_fixes)-20} 条略")

    if not args.dry_run and (fixes or open_fixes):
        con.commit()
        print("\n✅ 已写库")
    elif args.dry_run:
        print("\n🏃 仅预览，未写库")

    # after
    rows2 = [dict(r) for r in con.execute("SELECT id, student_id, duration FROM sessions")]
    print(f"\n=== 修复后：各生时长 ===")
    agg2 = {}
    for r in rows2:
        a = agg2.setdefault(r["student_id"], {"n": 0, "tot": 0})
        a["n"] += 1
        a["tot"] += int(r["duration"] or 0)
    for sid, a in sorted(agg2.items(), key=lambda x: -x[1]["tot"]):
        print(f"  {sid:<18} {a['n']:>3} 次  {a['tot']:>8} 秒 = {fmt_sec(a['tot']):>10}")

    print(f"\n未结束会话：{con.execute('SELECT COUNT(*) FROM sessions WHERE ended_at IS NULL').fetchone()[0]} 条")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
