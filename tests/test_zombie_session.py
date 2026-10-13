#!/usr/bin/env python3
"""
僵尸会话（iOS 杀进程导致的跨夜超长计时）专项测试。

背景（真实线上问题）：
    家长端截图显示 10-12 23:40 → 次日 06:23 记成 2 小时，且一直显示「学习中」。
    用户确认：当晚 23 点并没有学习，浏览器是关着的，第二天早上也是杀进程关浏览器、
    并且关机了，计时却还在继续。

根因：
    iOS Safari 在「杀进程 / 关机 / 锁屏」时不触发 pagehide / beforeunload，
    前端 endSession() 没能执行，会话以「进行中」挂到第二天；
    旧版后端结算时用「现在 - 开始时间」的墙钟差值补时长，把整夜算成了学习时间。

修复要点：
    1. 服务端只信任前端心跳上报的累计秒数（duration），不再用墙钟差值
    2. 结束时间 = 开始时间 + 已上报时长（避免"学了2小时却跨了7小时"的矛盾记录）
    3. get_session_log 依据 last_beat_at 判定僵尸，不再显示"进行中"
    4. 零上报的会话一律记 0（第二轮修复：原先回退墙钟差，导致"打开不到 60 秒
       就被杀"的会话下次打开时凭空多记 1 小时）
    5. 新增 sessions.last_beat_at + 懒结算（settle_zombie_sessions /
       _settle_stale_except），僵尸会话不必等 Reload 才被清理

覆盖：
  A. _settle_open_sessions：有上报值的僵尸会话 → 采用上报值，结束点收回
  B. _settle_open_sessions：零上报的纯僵尸 → 记 0，绝不回退墙钟差
  C. end_session：延迟到第二天才触发时，不再取墙钟差值
  D. heartbeat_session：无 seconds 上报时只补 60 秒，不按墙钟算
  E. get_session_log：僵尸会话不再显示 ongoing，时长/结束时间收敛
  F. get_session_log：仍在正常心跳的活跃会话，保持 ongoing=True
  G. 回归：真实学习会话（有上报值）时长不被误伤
  H. 懒结算：心跳路径不误关当前会话，只清失联的旧会话
  I. 懒结算：无 last_beat_at 的历史数据（老库迁移）按 started_at 兜底
  J. 回归：用户报告的真实场景——打开不到 60 秒被杀，不得虚记 1 小时
"""
import os
import sys
import tempfile
from datetime import timedelta

TMP = tempfile.mkdtemp(prefix="zsd_zombie_")
os.environ["ZSD_SQLITE_DIR"] = TMP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import db  # noqa: E402

STU = "zombie"
PASS = []


def chk(name, cond, extra=""):
    PASS.append(bool(cond))
    print(f"  {'✅' if cond else '❌'} {name} {extra}")


def reset():
    db.init_db()
    db.execute("DELETE FROM sessions WHERE student_id=?", (STU,))


def add(started, ended=None, secs=0, page="course", beat=None):
    """
    插入一条会话。

    beat: 最后一次心跳时刻。默认与 started 相同（模拟"从开始后再没心跳"）。
          活跃会话必须显式传入「刚刚」的时间，否则会被判为僵尸。
    """
    b = beat if beat is not None else started
    r = db.execute(
        "INSERT INTO sessions(student_id,started_at,ended_at,duration,page,last_beat_at) "
        "VALUES(?,?,?,?,?,?)",
        (STU, started.isoformat(timespec="seconds"),
         ended.isoformat(timespec="seconds") if ended else None, secs, page,
         b.isoformat(timespec="seconds")))
    return r.get("last_id")


def one(sid):
    rows = db.query("SELECT id, started_at, ended_at, duration FROM sessions WHERE id=?", (sid,))
    return rows[0] if rows else None


def main():
    NOW = db.now_cn()

    print("=" * 60)
    print("【A】_settle_open_sessions：有上报值的僵尸会话采用上报值")
    print("=" * 60)
    reset()
    # 复刻线上场景：昨晚 23:40 开始，前端上报了 120 秒，然后页面被杀，
    # 会话挂到第二天（现在是第二天早上）。
    # 用「当天零点往回推 7 小时」的方式构造，保证一定落在过去（早上/下午/深夜跑都成立）。
    start = (NOW.replace(hour=0, minute=0, second=0, microsecond=0)
             - timedelta(hours=7) + timedelta(minutes=40))
    sid = add(start, None, 120)
    n = db._settle_open_sessions(student_id=STU, keep_reported=True)
    row = one(sid)
    dur = int(row["duration"])
    end_dt = db._parse_dt(row["ended_at"])
    chk("结算了 1 条", n == 1, str(n))
    chk("时长采用上报值 120 秒（不是墙钟的 7 小时）", dur == 120, str(dur))
    chk("结束时间 = 开始 + 2 分钟", end_dt == start + timedelta(seconds=120),
        f"{row['started_at'][11:16]} → {row['ended_at'][11:16]}")
    wall = (db._parse_dt(row["ended_at"]) - start).total_seconds()
    chk("跨夜 7 小时被消除（跨度 ≤ 3 分钟）", wall <= 180, f"{wall}s")

    print()
    print("=" * 60)
    print("【B】_settle_open_sessions：零上报的纯僵尸一律记 0")
    print("=" * 60)
    reset()
    sid = add(NOW - timedelta(hours=9), None, 0)
    db.settle_stale_sessions()          # 服务重启路径
    row = one(sid)
    dur = int(row["duration"])
    chk("零上报会话时长 = 0（绝不用墙钟差）", dur == 0, str(dur))
    chk("没有按 2 小时封顶虚增", dur < db.SESSION_MAX_SECONDS, str(dur))

    print()
    print("=" * 60)
    print("【C】end_session：延迟到第二天才触发时不取墙钟差值")
    print("=" * 60)
    reset()
    sid = add(NOW - timedelta(hours=8), None, 300)   # 实际只学了 5 分钟
    db.end_session(sid, STU)
    row = one(sid)
    chk("时长保持 300 秒", int(row["duration"]) == 300, str(row["duration"]))
    chk("结束点 = 开始 + 5 分钟",
        db._parse_dt(row["ended_at"]) == db._parse_dt(row["started_at"]) + timedelta(seconds=300),
        f"{row['started_at'][11:16]} → {row['ended_at'][11:16]}")

    print()
    print("=" * 60)
    print("【D】heartbeat_session：无 seconds 时只补 60 秒")
    print("=" * 60)
    reset()
    sid = add(NOW - timedelta(hours=5), None, 180)
    got = db.heartbeat_session(sid, STU, seconds=None)
    row = one(sid)
    chk("返回值 = 240 秒", got == 240, str(got))
    chk("落库值 = 240 秒（前值 180 + 60）", int(row["duration"]) == 240, str(row["duration"]))
    # 显式上报更大的值 → 采用
    got2 = db.heartbeat_session(sid, STU, seconds=900)
    chk("显式上报 900 秒被采用", got2 == 900, str(got2))
    # 显式上报更小的值 → 不回退（时长只增不减）
    got3 = db.heartbeat_session(sid, STU, seconds=100)
    chk("更小的上报值不回退", got3 == 900, str(got3))
    # 封顶
    got4 = db.heartbeat_session(sid, STU, seconds=999999)
    chk("超过 2 小时被封顶", got4 == db.SESSION_MAX_SECONDS, str(got4))

    print()
    print("=" * 60)
    print("【E】get_session_log：僵尸会话不再显示 ongoing")
    print("=" * 60)
    reset()
    # 昨晚 23:40 开始，上报 120 秒，ended_at 为空（iOS 没触发结束）。
    # 注意要落在「昨天」：直接 replace(hour=23) 在早上跑测试时会得到「今天 23:40」（未来时间），
    # 墙钟差为负就测不出僵尸判定了，所以显式减一天。
    z_start = (NOW - timedelta(days=1)).replace(hour=23, minute=40, second=0, microsecond=0)
    if z_start >= NOW:                       # 万一跨月/时区边界，再退一天
        z_start -= timedelta(days=1)
    add(z_start, None, 120)
    logs = db.get_session_log(STU, 7)
    chk("僵尸会话仍在列表里（有 2 分钟真实学习）", len(logs) == 1, str(len(logs)))
    if logs:
        lg = logs[0]
        chk("不再标记 ongoing", lg["ongoing"] is False, str(lg["ongoing"]))
        chk("时长显示 2分", lg["duration"] == "2分", lg["duration"])
        chk("结束时间不跨夜（无 +1天 标记）", "+" not in lg["end"], lg["end"])
        chk("起止同为 23:40 → 23:42", lg["start"] == "23:40" and lg["end"] == "23:42",
            f"{lg['start']} → {lg['end']}")

    print()
    print("=" * 60)
    print("【F】get_session_log：仍在正常心跳的会话保持 ongoing")
    print("=" * 60)
    reset()
    # 起点取「今天之内」，避免午夜后跑测试时跨到昨天（end 会带 (+1天) 标记）
    a_start = NOW - timedelta(minutes=30)
    if a_start.date() != NOW.date():
        a_start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    # 关键：活跃会话必须带「刚刚」的心跳，否则按新口径会被判为僵尸
    add(a_start, None, 180, beat=NOW - timedelta(seconds=20))
    logs = db.get_session_log(STU, 7)
    chk("活跃会话仍在列表", len(logs) == 1, str(len(logs)))
    if logs:
        chk("标记为 ongoing（进行中）", logs[0]["ongoing"] is True, str(logs[0]["ongoing"]))
        chk("end 取当前时间", logs[0]["end"].startswith(NOW.strftime("%H:%M")), logs[0]["end"])

    print()
    print("=" * 60)
    print("【G】回归：真实学习会话不被误伤")
    print("=" * 60)
    reset()
    # 一段正常的完整会话：21:00 → 21:06，上报 360 秒
    n_start = (NOW - timedelta(hours=3)).replace(hour=21, minute=0, second=0, microsecond=0)
    add(n_start, n_start + timedelta(seconds=360), 360)
    # 一段很长的真实学习：10:00 → 10:27，上报 1620 秒
    l_start = (NOW - timedelta(hours=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    add(l_start, l_start + timedelta(seconds=1620), 1620)

    db.settle_stale_sessions()
    db._settle_open_sessions(student_id=STU)   # 已结束的不会被重复处理

    logs = db.get_session_log(STU, 7)
    chk("两段真实会话都在", len(logs) == 2, str(len(logs)))
    got = {(x["start"], x["duration"]) for x in logs}
    chk("21:00 那段保持 6分", ("21:00", "6分") in got, str(got))
    chk("10:00 那段保持 27分", ("10:00", "27分") in got, str(got))
    chk("两段都不是 ongoing", all(not x["ongoing"] for x in logs))

    # 每日汇总也应正确（6 + 27 = 33 分）
    times = db.get_study_time(STU, 7)
    secs = sum(t["seconds"] for t in times)
    chk("每日汇总 = 1980 秒（33 分）", secs == 1980, str(secs))

    print()
    print("=" * 60)
    print("【H】懒结算：心跳路径只清失联旧会话，不碰当前会话")
    print("=" * 60)
    reset()
    live = add(NOW - timedelta(minutes=10), None, 60,
               beat=NOW - timedelta(seconds=5))        # 刚发来心跳
    old = add(NOW - timedelta(hours=2), None, 120,
              beat=NOW - timedelta(hours=2))            # 早就失联
    db._settle_stale_except(STU, live)
    live_row = one(live)
    old_row = one(old)
    chk("当前会话仍开着（未被误关）", live_row["ended_at"] is None, str(live_row["ended_at"]))
    chk("当前会话时长不受影响", int(live_row["duration"]) == 60, str(live_row["duration"]))
    chk("失联旧会话已被结清", old_row["ended_at"] is not None)
    chk("失联旧会话时长 = 上报值 120", int(old_row["duration"]) == 120, str(old_row["duration"]))

    print()
    print("=" * 60)
    print("【I】懒结算：无 last_beat_at 的老数据按 started_at 兜底")
    print("=" * 60)
    reset()
    # 直接走裸 SQL，不写 last_beat_at，模拟老库迁移后的历史行
    old_start = NOW - timedelta(hours=4)
    db.execute("INSERT INTO sessions(student_id,started_at,ended_at,duration,page) "
               "VALUES(?,?,?,?,?)",
               (STU, old_start.isoformat(timespec="seconds"), None, 0, "course"))
    n = db.settle_zombie_sessions(STU)
    row = db.query("SELECT started_at, ended_at, duration FROM sessions "
                   "WHERE student_id=?", (STU,))[0]
    chk("老数据被结算", n == 1, str(n))
    chk("老数据已标结束", row["ended_at"] is not None)
    chk("老数据零心跳记 0（不虚增 4 小时）", int(row["duration"]) == 0, str(row["duration"]))

    print()
    print("=" * 60)
    print("【J】回归：用户报告场景——打开不到 60 秒被杀，不得虚记 1 小时")
    print("=" * 60)
    reset()
    # 复现：页面打开约 40 秒（首次心跳还没发），浏览器被杀，
    # 65 分钟后用户重新打开页面（此时读取路径会做懒结算）。
    # 旧行为：下次打开时走墙钟兜底 → 凭空多出 1 小时。
    s = NOW - timedelta(minutes=65)
    db.execute("INSERT INTO sessions(student_id,started_at,ended_at,duration,page,last_beat_at) "
               "VALUES(?,?,?,?,?,?)",
               (STU, s.isoformat(timespec="seconds"), None, 0, "course",
                s.isoformat(timespec="seconds")))
    logs = db.get_session_log(STU, 7)          # 用户重新打开 → 触发懒结算
    zero = db.query("SELECT duration, ended_at FROM sessions WHERE student_id=?", (STU,))[0]
    chk("全程零心跳的会话时长记 0", int(zero["duration"]) == 0, str(zero["duration"]))
    chk("会话已被标结束", zero["ended_at"] is not None)
    chk("明细里不出现这条噪声（< 5 秒被过滤）", len(logs) == 0, str(logs))
    times = db.get_study_time(STU, 7)
    chk("学习时长统计不被虚增", sum(t["seconds"] for t in times) == 0,
        str(sum(t["seconds"] for t in times)))

    print()
    print("=" * 60)
    total, ok = len(PASS), sum(PASS)
    print(f"结果：{ok}/{total} 通过")
    print("=" * 60)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
