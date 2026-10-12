#!/usr/bin/env python3
"""
清理「服务重启结算虚增」产生的异常学习时长。

背景：
  旧版 settle_stale_sessions() 按 SESSION_MAX_SECONDS（2 小时）封顶结算残留会话。
  每次 PythonAnywhere Reload Web App，所有未结束会话都会被灌成 7200 秒，
  反复几轮后当日时长就会累加到几百上千分钟（如 960 分 / 6856.8 分）。

本脚本做的事（幂等、可重复执行）：
  1. 把「明显异常」的会话时长压回合理值：
     - 单条 > 单次合理上限（默认 2 小时=7200s，但重启虚增产物通常正好是 7200）
     - 且该会话没有心跳痕迹（无独立心跳字段，用 ended_at - started_at 判断）
  2. 更稳妥的做法：直接按「会话真实跨度」重算 duration，
     即 duration = min(ended_at - started_at, 真实可见上限)，
     这样把 7200 的虚增值还原成真实跨度。
  3. 输出 before/after 对比。

用法（本地）：
    python3 scripts/fix_inflated_time.py --db data/study.db --dry-run
    python3 scripts/fix_inflated_time.py --db data/study.db

用法（PythonAnywhere，先备份再执行）：
    cp ~/zsd-study/data/study.db ~/study_backup_$(date +%Y%m%d_%H%M%S).db
    python3 ~/zsd-study/scripts/fix_inflated_time.py --db ~/zsd-study/data/study.db
"""
import argparse
import os
import sqlite3
from datetime import datetime, timedelta

# 与 app/db.py 保持一致
SESSION_MAX_SECONDS = 7200
SESSION_MIN_SECONDS = 5
# 「可疑虚增值」特征：时长 >= 这个值，且会话真实跨度远小于它
SUSPICIOUS_SEC = 1800


def parse(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite 数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"❌ 数据库不存在：{args.db}")
        return 1

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

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
        print(f"  {sid:<18} {a['n']:>3} 次  {a['tot']:>8} 秒 = {round(a['tot']/60,1):>8} 分")

    fixes = []
    for r in rows:
        dur = int(r["duration"] or 0)
        if dur < SUSPICIOUS_SEC:
            continue
        st = parse(r["started_at"])
        et = parse(r["ended_at"])
        if not st:
            continue
        # 真实跨度（会话实际持续多久）
        span = int((et - st).total_seconds()) if et else dur
        span = max(0, span)
        # 若真实跨度显著小于记录时长，说明是虚增，压回真实跨度
        if span < dur:
            new_dur = span
            # 跨度太短的直接记 0（误点/秒退）
            if new_dur < SESSION_MIN_SECONDS:
                new_dur = 0
            new_dur = min(new_dur, SESSION_MAX_SECONDS)
            if new_dur != dur:
                fixes.append((r["id"], r["student_id"], dur, new_dur))
                if not args.dry_run:
                    con.execute("UPDATE sessions SET duration=? WHERE id=?",
                                (new_dur, r["id"]))

    print(f"\n=== 待修正 {len(fixes)} 条（duration 虚增）===")
    for i, sid, old, new in fixes[:40]:
        print(f"  id={i:<4} {sid:<16} {old:>6}s → {new:>6}s  ({round(old/60,1)}分 → {round(new/60,1)}分)")
    if len(fixes) > 40:
        print(f"  ... 其余 {len(fixes)-40} 条略")

    if not args.dry_run:
        con.commit()
        print("\n✅ 已写库")

    # after
    rows2 = [dict(r) for r in con.execute(
        "SELECT id, student_id, duration FROM sessions")]
    print(f"\n=== 修复后：各生时长 ===")
    agg2 = {}
    for r in rows2:
        a = agg2.setdefault(r["student_id"], {"n": 0, "tot": 0})
        a["n"] += 1
        a["tot"] += int(r["duration"] or 0)
    for sid, a in sorted(agg2.items(), key=lambda x: -x[1]["tot"]):
        print(f"  {sid:<18} {a['n']:>3} 次  {a['tot']:>8} 秒 = {round(a['tot']/60,1):>8} 分")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
