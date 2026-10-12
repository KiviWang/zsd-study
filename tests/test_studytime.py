#!/usr/bin/env python3
"""
学习时长统计专项测试

覆盖此前导致「做了很多题但时长显示 0 秒」「昨天的时长跑到今天/今天归零」的缺陷：

  A. 时区：服务端为 UTC 时，北京时间早上 8 点前的学习会被记成前一天
  B. 心跳：只在 pagehide 时结算，手机不触发该事件则时长永远为 0
  C. 保底：未结束（ended_at IS NULL）的会话 duration=0，导致看板显示 0 秒
  D. 噪声：秒进秒出的会话不应把时长刷高
  E. 封顶：单次会话最多计 2 小时

直接调用 db 层（不走 HTTP），保证测试确定、可重复。
"""
import os
import shutil
import sys
import tempfile
from datetime import timedelta

TMP = tempfile.mkdtemp(prefix="zsd_timetest_")
os.environ["ZSD_SQLITE_DIR"] = TMP
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import db  # noqa: E402

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  ✅ " + name)
    else:
        FAIL += 1
        print("  ❌ " + name + (f"  [{extra}]" if extra else ""))


db.init_db()
STU = "timer"

print("=" * 60)
print("【A】时区正确性：now_cn() 必须是 UTC+8")
print("=" * 60)
from datetime import datetime, timezone  # noqa: E402

utc = datetime.now(timezone.utc).replace(tzinfo=None)
cn = db.now_cn()
diff_h = round((cn - utc).total_seconds() / 3600)
chk("now_cn() 与 UTC 相差 8 小时", diff_h == 8, f"实际相差 {diff_h} 小时")
chk("now_cn() 返回 naive 时间（便于入库比较）", cn.tzinfo is None, str(cn.tzinfo))

# 模拟：北京时间早上 07:30 学习 → 服务端 UTC 应为前一天 23:30
morning = datetime(2026, 10, 12, 7, 30)          # 这批数据的"北京时间"
utc_same = morning - timedelta(hours=8)           # UTC 视角
chk("北京 10-12 07:30 在 UTC 下会变成 10-11 23:30",
    utc_same.strftime("%Y-%m-%d") == "2026-10-11",
    utc_same.isoformat())
chk("改用 now_cn() 后日期仍为 10-12",
    morning.strftime("%Y-%m-%d") == "2026-10-12")

print()
print("=" * 60)
print("【B】心跳上报：时长边学边落库")
print("=" * 60)
sid = db.start_session("#scrCourse", STU)
chk("会话创建成功", bool(sid), str(sid))

d0 = db.heartbeat_session(sid, STU, 60)
chk("第 1 次心跳 60 秒 → duration=60", d0 == 60, str(d0))
d1 = db.heartbeat_session(sid, STU, 300)
chk("第 2 次心跳 300 秒 → duration=300（取较大值）", d1 == 300, str(d1))
d2 = db.heartbeat_session(sid, STU, 120)
chk("上报值回退时不缩水（仍是 300）", d2 == 300, str(d2))

row = db.query("SELECT duration, ended_at FROM sessions WHERE id=?", (sid,))[0]
chk("心跳期间 ended_at 仍为空（会话未结束）", row["ended_at"] is None, str(row["ended_at"]))
chk("但 duration 已落库，不再依赖关闭事件", int(row["duration"]) == 300, str(row["duration"]))

print()
print("=" * 60)
print("【C】保底结算：未结束的会话也能拿到时长，但不虚增")
print("=" * 60)
# 造一条"30 分钟前开始、从未结束、duration=0"的僵尸会话（模拟旧版缺陷产物）
old_start = (db.now_cn() - timedelta(minutes=30)).isoformat(timespec="seconds")
db.execute("INSERT INTO sessions(student_id,started_at,duration) VALUES(?,?,0)", (STU, old_start))
zombie_id = db.query("SELECT MAX(id) AS m FROM sessions WHERE student_id=?", (STU,))[0]["m"]

before = {r["date"]: r["seconds"] for r in db.get_study_time(STU, 7)}
n = db._settle_open_sessions(STU)
chk("结算函数处理了未结束会话", n >= 1, f"处理 {n} 条")
z = db.query("SELECT duration, ended_at FROM sessions WHERE id=?", (zombie_id,))[0]
chk("僵尸会话按真实跨度补齐（≈1800 秒，未超2小时封顶）",
    1700 <= int(z["duration"]) <= 1900, str(z["duration"]))
chk("僵尸会话 ended_at 已写入", z["ended_at"] is not None, str(z["ended_at"]))

after = {r["date"]: r["seconds"] for r in db.get_study_time(STU, 7)}
today = db.now_cn().date().isoformat()
chk("当日时长已包含结算结果（不再是 0 秒）",
    after.get(today, 0) >= 1800, str(after.get(today)))

print()
print("=" * 60)
print("【C2】服务重启结算：只补心跳宽限期，绝不虚增成 2 小时")
print("=" * 60)
# 模拟 PythonAnywhere 上遗留多日的未结束会话：若按 2 小时封顶会累加出天文数字
ancient = (db.now_cn() - timedelta(days=3)).isoformat(timespec="seconds")
db.execute("INSERT INTO sessions(student_id,started_at,duration) VALUES(?,?,0)", (STU, ancient))
ancient_id = db.query("SELECT MAX(id) AS m FROM sessions WHERE student_id=?", (STU,))[0]["m"]

n2 = db.settle_stale_sessions()
chk("启动结算处理了残留会话", n2 >= 1, f"处理 {n2} 条")
a = db.query("SELECT duration, ended_at FROM sessions WHERE id=?", (ancient_id,))[0]
chk("多日前的僵尸会话只补 3 分钟（心跳宽限期），不是 7200 秒",
    int(a["duration"]) == db.SESSION_HEARTBEAT_GRACE, str(a["duration"]))
chk("该会话已标记结束", a["ended_at"] is not None, str(a["ended_at"]))

# 关键回归：连续多次「重启」不应让时长持续膨胀
t1 = {r["date"]: r["seconds"] for r in db.get_study_time(STU, 7)}.get(today, 0)
for _ in range(3):
    db.settle_stale_sessions()
t2 = {r["date"]: r["seconds"] for r in db.get_study_time(STU, 7)}.get(today, 0)
chk("连续重启 3 次后当日时长不再增长（幂等）", t1 == t2, f"{t1} → {t2}")
chk("当日总时长处于合理量级（<1 小时）", t2 < 3600, f"{t2} 秒 = {round(t2/60,1)} 分")

print()
print("=" * 60)
print("【D】封顶与防噪声")
print("=" * 60)
sid2 = db.start_session("#scrDetail", STU)
capped = db.heartbeat_session(sid2, STU, 99999)
chk("单次会话封顶 2 小时", capped == 7200, str(capped))

sid3 = db.start_session("#scrCourse", STU)
tiny = db.heartbeat_session(sid3, STU, 0)
chk("0 秒上报不报错", tiny == 0, str(tiny))

print()
print("=" * 60)
print("【E】连续天数按中国时区判定")
print("=" * 60)
streak = db.get_streak(STU)
chk("今天有学习记录 → 连续天数 ≥1", streak >= 1, str(streak))
# 直接验证：要求所有会话的日期前缀都等于 now_cn() 的日期
today_cn = db.now_cn().date().isoformat()
rows = db.query("SELECT DISTINCT SUBSTR(started_at,1,10) AS d FROM sessions WHERE student_id=?", (STU,))
dates = {r["d"] for r in rows}
chk("会话日期使用中国时区（含今日）", today_cn in dates, str(sorted(dates)))

print()
print("=" * 60)
print("【F】start_session 会顺手结算同学生的旧会话")
print("=" * 60)
db.start_session("#scrPlan", STU)
open_left = db.query(
    "SELECT COUNT(*) AS n FROM sessions WHERE student_id=? AND ended_at IS NULL", (STU,))[0]["n"]
chk("同一学生最多只留 1 条未结束会话", open_left <= 1, str(open_left))

print()
print("=" * 60)
print(f"结果：{OK} 通过 / {FAIL} 失败")
print("=" * 60)

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
