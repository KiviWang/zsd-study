#!/usr/bin/env python3
"""
修正「僵尸会话」造成的历史脏数据。

问题背景
    iOS Safari 在杀进程 / 关机 / 锁屏时不触发 pagehide/beforeunload，
    前端 endSession() 没能执行，会话就以「进行中」状态挂到第二天。
    旧版后端结算时用「现在 - 开始时间」的墙钟差值补时长，
    于是一整夜被算成了学习时间（例如 23:40 → 次日 06:23 记成 2 小时）。

本脚本做什么
    对每条会话，比较两个数：
      wall = 结束时间(或当前时间) - 开始时间     ← 墙钟跨度
      dur  = 会话记录的 duration               ← 前端心跳真实累计
    若 wall 明显大于 dur（超过 --gap 秒），说明中间有大量时间页面并不活跃，
    就把结束时间改成「开始 + dur」，时长保持 dur 不变。
    这样明细就变成「23:40 → 23:42 2分」这种符合事实的记录，
    而不是「23:40 → 06:23 2时」。

用法
    # 先预览（不写库）
    python3 scripts/fix_zombie_sessions.py --db data/study.db --dry-run
    # 确认无误后执行
    python3 scripts/fix_zombie_sessions.py --db data/study.db

可选项
    --gap N       墙钟与记录值相差超过 N 秒才处理（默认 180，即 3 分钟心跳宽限）
    --days N      只处理最近 N 天（默认 30）
    --zero-idle   把「完全没有心跳记录(duration=0)」的会话时长清零（默认开启）
    --no-zero-idle 保留这类会话的墙钟差值
    --cap N       对「墙钟跨度远超时长」的会话，把时长压到 N 分钟（默认 0 = 不压缩）
                  旧版后端可能已把僵尸会话的 duration 污染成 7200，
                  此时只能靠上限来纠正，建议用 --cap 5

⚠️ 本脚本只改 ended_at 与 duration 的一致性，不会删除任何记录，可重复执行（幂等）。
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8), "CST")
SESSION_MIN_SECONDS = 5
# 单次会话超过这个时长就可疑（连续学习 2 小时已属极长，且旧逻辑封顶就是 2 小时）
SUSPICIOUS_SECONDS = 3600


def now_cn():
    return datetime.now(CN_TZ).replace(tzinfo=None)


def parse_dt(s):
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
        return f"{round(s/60,1):g}分"
    h, m = s // 3600, round((s % 3600) / 60)
    return f"{h}时{m}分" if m else f"{h}时"


def main():
    ap = argparse.ArgumentParser(description="修正僵尸会话造成的历史时长脏数据")
    ap.add_argument("--db", required=True, help="SQLite 数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument("--gap", type=int, default=180, help="墙钟与记录值相差多少秒才处理（默认180）")
    ap.add_argument("--days", type=int, default=30, help="只处理最近 N 天（默认30）")
    ap.add_argument("--zero-idle", dest="zero_idle", action="store_true", default=True,
                    help="完全没有心跳记录的会话时长清零（默认开启）")
    ap.add_argument("--no-zero-idle", dest="zero_idle", action="store_false",
                    help="保留这类会话的墙钟差值")
    ap.add_argument("--cap", type=int, default=0,
                    help="对超长的可疑会话，把时长压到 N 分钟（默认 0=不压缩，建议 5）")
    ap.add_argument("--pick", type=str, default="",
                    help="只处理指定的会话 id（逗号分隔，如 12 或 12,15）。"
                         "配合 --set 精确指定为多少分钟，不做全局扫描")
    ap.add_argument("--set", dest="set_min", type=int, default=0,
                    help="与 --pick 合用：把这些会话的时长设为 N 分钟（0=清零）")
    ap.add_argument("--list", action="store_true",
                    help="列出最近会话的 id 与概况，便于挑选要处理的（不修改数据）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[错误] 数据库不存在：{args.db}")
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    since = (now_cn().date() - timedelta(days=args.days - 1)).isoformat()

    # ---------- 模式 A：列出会话，便于挑选 ----------
    if args.list:
        cur.execute("""
            SELECT id, student_id, started_at, ended_at, duration
            FROM sessions WHERE SUBSTR(started_at,1,10) >= ?
            ORDER BY started_at DESC
        """, (since,))
        # 注意：sqlite3 的 cur.rowcount 对 SELECT 不可靠（常返回 -1），
        # 必须先 fetchall() 再取 len()，否则会打印成「共 ? 条」。
        all_rows = cur.fetchall()
        print(f"最近 {args.days} 天的会话（共 {len(all_rows)} 条）：\n")
        for r in all_rows:
            st = parse_dt(r["started_at"])
            et = parse_dt(r["ended_at"])
            dur = int(r["duration"] or 0)
            now_ = now_cn()
            ref = et if et else now_
            wall = int((ref - st).total_seconds()) if st else 0
            mark = "  ⚠️ 可疑" if (st and wall - dur > args.gap) else ""
            print(f"  #{r['id']:<5} {r['student_id']:<12} "
                  f"{st.strftime('%m-%d %H:%M') if st else '?':<12} → "
                  f"{(et.strftime('%m-%d %H:%M') if et else '进行中'):<12} "
                  f"记 {fmt_sec(dur):>7s}  跨度 {fmt_sec(wall):>8s}{mark}")
        print("\n提示：用 --pick <id> --set <分钟> 精确处理某一条，例如：")
        print("  python3 scripts/fix_zombie_sessions.py --db data/study.db --pick 12 --set 0")
        conn.close()
        return 0

    # ---------- 模式 B：精确处理指定 id ----------
    if args.pick:
        try:
            ids = [int(x.strip()) for x in args.pick.split(",") if x.strip()]
        except ValueError:
            print("[错误] --pick 必须是逗号分隔的数字，如 12,15")
            conn.close()
            return 2
        if not ids:
            print("[错误] --pick 为空")
            conn.close()
            return 2

        set_sec = max(0, args.set_min) * 60
        print(f"目标会话 id: {ids}")
        print(f"处理方式：时长设为 {args.set_min} 分钟（{set_sec} 秒）\n")

        changed = 0
        for sid in ids:
            cur.execute("SELECT id, student_id, started_at, ended_at, duration "
                        "FROM sessions WHERE id=?", (sid,))
            r = cur.fetchone()
            if not r:
                print(f"  #{sid}  不存在，跳过")
                continue
            st = parse_dt(r["started_at"])
            old = int(r["duration"] or 0)
            new_end = (st + timedelta(seconds=set_sec)) if st else None
            if new_end and new_end > now_cn():
                new_end = now_cn()
            print(f"  #{sid:<5} {r['student_id']:<12} "
                  f"{st.strftime('%m-%d %H:%M') if st else '?'}  "
                  f"{fmt_sec(old):>7s}  ⇒  {fmt_sec(set_sec):>7s}")
            if not args.dry_run:
                cur.execute("UPDATE sessions SET duration=?, ended_at=? WHERE id=?",
                            (set_sec,
                             (new_end.isoformat(timespec="seconds") if new_end else None),
                             sid))
            changed += 1

        if args.dry_run:
            print(f"\n[预览模式] 将处理 {changed} 条，未写入。")
            conn.close()
            return 0

        conn.commit()
        print(f"\n✅ 已处理 {changed} 条会话。\n")

        # 打印受影响学生的每日时长，便于核对
        cur.execute("SELECT DISTINCT student_id FROM sessions WHERE id IN (%s)"
                    % ",".join("?" * len(ids)), ids)
        for row in cur.fetchall():
            stu = row["student_id"]
            print(f"=== {stu} 近 {args.days} 天时长 ===")
            cur.execute("""
                SELECT SUBSTR(started_at,1,10) AS d, SUM(duration) AS secs, COUNT(*) AS n
                FROM sessions WHERE student_id=? AND SUBSTR(started_at,1,10) >= ?
                GROUP BY SUBSTR(started_at,1,10) ORDER BY d DESC
            """, (stu, since))
            for x in cur.fetchall():
                print(f"  {x['d']}  {fmt_sec(x['secs']):>8s}  ({x['n']} 段)")
        conn.close()
        return 0

    # ---------- 模式 C：全局扫描（默认） ----------
    cur.execute("""
        SELECT id, student_id, started_at, ended_at, duration
        FROM sessions
        WHERE SUBSTR(started_at,1,10) >= ?
        ORDER BY started_at
    """, (since,))
    rows = cur.fetchall()

    now = now_cn()
    to_fix = []
    for r in rows:
        st = parse_dt(r["started_at"])
        if not st:
            continue
        et = parse_dt(r["ended_at"])
        dur = int(r["duration"] or 0)

        # 墙钟跨度：已结束用 ended_at，未结束用当前时间
        ref = et if et else now
        wall = int((ref - st).total_seconds())
        if wall <= 0:
            continue

        new_dur = dur
        new_end = et

        if dur <= 0 and args.zero_idle:
            # 完全没心跳：纯僵尸，清零
            new_dur = 0
            new_end = st
            reason = "无心跳记录 → 清零"
        elif wall - dur > args.gap:
            # 墙钟远大于记录值：中间页面没在活跃
            # 若时长本身也已超长（很可能被旧版逻辑污染成 7200），按 --cap 压缩
            if args.cap > 0 and dur > args.cap * 60:
                new_dur = args.cap * 60
                reason = (f"墙钟跨度 {fmt_sec(wall)} 远超记录 {fmt_sec(dur)}，"
                          f"时长超上限 → 压到 {args.cap} 分钟")
            else:
                new_dur = dur if dur >= SESSION_MIN_SECONDS else 0
                reason = f"墙钟跨度 {fmt_sec(wall)} 远大于记录 {fmt_sec(dur)} → 收回结束点"
            new_end = st + timedelta(seconds=new_dur)
            if new_end > now:
                new_end = now
        else:
            continue

        to_fix.append({
            "id": r["id"], "sid": r["student_id"],
            "start": st, "old_end": et, "old_dur": dur,
            "new_end": new_end, "new_dur": new_dur, "reason": reason,
            "wall": wall,
        })

    if not to_fix:
        print(f"扫描 {len(rows)} 条会话，无需修正。")
        conn.close()
        return 0

    print(f"扫描 {len(rows)} 条会话，发现 {len(to_fix)} 条需要修正：\n")
    for x in to_fix:
        oend = x["old_end"].strftime("%m-%d %H:%M") if x["old_end"] else "进行中"
        nend = x["new_end"].strftime("%m-%d %H:%M")
        print(f"  #{x['id']:<5} {x['sid']:<12} "
              f"{x['start'].strftime('%m-%d %H:%M')} → {oend:<12} "
              f"{fmt_sec(x['old_dur']):>8s}  ⇒  {nend}  {fmt_sec(x['new_dur']):>8s}")
        print(f"         原因：{x['reason']}")

    if args.dry_run:
        print("\n[预览模式] 未写入数据库。去掉 --dry-run 即可执行。")
        conn.close()
        return 0

    for x in to_fix:
        cur.execute("UPDATE sessions SET ended_at=?, duration=? WHERE id=?",
                    (x["new_end"].isoformat(timespec="seconds"), x["new_dur"], x["id"]))
    conn.commit()
    print(f"\n✅ 已修正 {len(to_fix)} 条会话。")

    # 修正后按天汇总，方便核对
    print("\n=== 修正后每日时长 ===")
    cur.execute("""
        SELECT SUBSTR(started_at,1,10) AS d, SUM(duration) AS secs, COUNT(*) AS n
        FROM sessions WHERE SUBSTR(started_at,1,10) >= ?
        GROUP BY SUBSTR(started_at,1,10) ORDER BY d DESC
    """, (since,))
    for r in cur.fetchall():
        print(f"  {r['d']}  {fmt_sec(r['secs']):>8s}  ({r['n']} 段)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
