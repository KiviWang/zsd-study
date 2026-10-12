#!/usr/bin/env python3
"""
学习记录明细（家长端「学习记录」面板）专项测试。

覆盖：
  A. get_session_log 基本输出：起止时间、时长、按天倒序
  B. 跨天会话标注（23:40 → 00:10(+1天)）
  C. 未结束会话标记 ongoing，end 用当前时间兜底
  D. 噪声过滤（< 5 秒的误点会话不计入）
  E. 天数窗口（超出 7 天的老数据不返回）
"""
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="zsd_log_")
os.environ["ZSD_SQLITE_DIR"] = TMP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import db  # noqa: E402

STU = "logtest"
PASS = []


def chk(name, cond, extra=""):
    PASS.append(bool(cond))
    print(f"  {'✅' if cond else '❌'} {name} {extra}")


def reset():
    db.init_db()
    db.execute("DELETE FROM sessions WHERE student_id=?", (STU,))


def add(st_dt, et_dt=None, secs=0):
    db.execute(
        "INSERT INTO sessions(student_id,started_at,ended_at,duration,page) VALUES(?,?,?,?,?)",
        (STU, st_dt.isoformat(timespec="seconds"),
         et_dt.isoformat(timespec="seconds") if et_dt else None,
         secs, "course"))


def main():
    print("=" * 60)
    print("【A】基本输出：起止时间 / 时长 / 倒序")
    print("=" * 60)
    reset()
    now = db.now_cn()
    today = now.replace(hour=9, minute=15, second=0, microsecond=0)
    add(today, today + timedelta(minutes=27), 1620)
    afternoon = now.replace(hour=14, minute=5, second=0, microsecond=0)
    add(afternoon, afternoon + timedelta(minutes=33), 1980)

    logs = db.get_session_log(STU, 7)
    chk("返回 2 条记录", len(logs) == 2, str(len(logs)))
    chk("按时间倒序（最新在前）", logs[0]["start"] == "14:05", logs[0]["start"])
    chk("下午场结束时间正确", logs[0]["end"] == "14:38", logs[0]["end"])
    chk("时长为 33 分", logs[0]["duration"] == "33分", logs[0]["duration"])
    chk("上午场时长 27 分", logs[1]["duration"] == "27分", logs[1]["duration"])
    chk("日期为今天", logs[0]["date"] == now.date().isoformat(), logs[0]["date"])

    print()
    print("=" * 60)
    print("【B】跨天会话标注")
    print("=" * 60)
    reset()
    d = now - timedelta(days=2)
    st = d.replace(hour=23, minute=40, second=0, microsecond=0)
    add(st, st + timedelta(minutes=30), 1800)
    logs = db.get_session_log(STU, 7)
    chk("跨天会话被返回", len(logs) == 1, str(len(logs)))
    chk("结束时间带 (+1天) 标注", "1天" in logs[0]["end"], logs[0]["end"])
    chk("开始时间为 23:40", logs[0]["start"] == "23:40", logs[0]["start"])

    print()
    print("=" * 60)
    print("【C】未结束会话标 ongoing")
    print("=" * 60)
    reset()
    st = now - timedelta(minutes=10)
    add(st, None, 600)
    logs = db.get_session_log(STU, 7)
    chk("返回 1 条", len(logs) == 1, str(len(logs)))
    chk("ongoing 为 True", logs[0]["ongoing"] is True, str(logs[0]["ongoing"]))
    chk("end 已用当前时间兜底（非空）", bool(logs[0]["end"]), logs[0]["end"])

    print()
    print("=" * 60)
    print("【D】噪声过滤")
    print("=" * 60)
    reset()
    # 正常一段 + 两段误点（< 5 秒）
    add(now.replace(hour=10, minute=0, second=0, microsecond=0),
        now.replace(hour=10, minute=20, second=0, microsecond=0), 1200)
    add(now.replace(hour=11, minute=0, second=0, microsecond=0),
        now.replace(hour=11, minute=0, second=3, microsecond=0), 3)
    add(now.replace(hour=12, minute=0, second=0, microsecond=0),
        now.replace(hour=12, minute=0, second=1, microsecond=0), 0)
    logs = db.get_session_log(STU, 7)
    chk("只返回 1 条（过短被过滤）", len(logs) == 1, str(len(logs)))
    chk("保留的是 20 分那段", logs[0]["duration"] == "20分", logs[0]["duration"])

    print()
    print("=" * 60)
    print("【E】天数窗口")
    print("=" * 60)
    reset()
    old = now - timedelta(days=30)
    add(old, old + timedelta(minutes=30), 1800)
    logs = db.get_session_log(STU, 7)
    chk("30 天前的记录不在 7 天窗口内", len(logs) == 0, str(len(logs)))
    logs2 = db.get_session_log(STU, 60)
    chk("窗口放宽到 60 天后可见", len(logs2) == 1, str(len(logs2)))

    print()
    print("=" * 60)
    ok = sum(PASS)
    print(f"结果：{ok} 通过 / {len(PASS)-ok} 失败")
    print("=" * 60)
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if ok == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
