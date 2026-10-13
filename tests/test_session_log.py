#!/usr/bin/env python3
"""
学习记录明细（家长端「学习记录」面板）专项测试。

覆盖：
  A. get_session_log 基本输出：起止时间、时长、按天倒序
  B. 跨天会话标注（23:40 → 00:10(+1天)）
  C. 未结束会话标记 ongoing，end 用当前时间兜底（仅当仍在心跳）
  C2. 已失联的僵尸会话不再标 ongoing，结束点收敛为「开始 + 上报时长」
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


def add(st_dt, et_dt=None, secs=0, beat=None):
    """
    插入一条会话。

    beat: 最后一次心跳时刻，默认与 started 相同。
          活跃（未结束且仍在心跳）的会话必须显式传入「刚刚」的时间，
          因为 ongoing 判定现在以 last_beat_at 为唯一依据。
    """
    b = beat if beat is not None else st_dt
    db.execute(
        "INSERT INTO sessions(student_id,started_at,ended_at,duration,page,last_beat_at) "
        "VALUES(?,?,?,?,?,?)",
        (STU, st_dt.isoformat(timespec="seconds"),
         et_dt.isoformat(timespec="seconds") if et_dt else None,
         secs, "course", b.isoformat(timespec="seconds")))


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
    print("【C】未结束会话标 ongoing（仍在心跳才算进行中）")
    print("=" * 60)
    reset()
    st = now - timedelta(minutes=10)
    # 关键：活跃会话必须带「刚刚」的心跳，否则按新口径会被判为僵尸
    add(st, None, 600, beat=now - timedelta(seconds=15))
    logs = db.get_session_log(STU, 7)
    chk("返回 1 条", len(logs) == 1, str(len(logs)))
    chk("ongoing 为 True", logs[0]["ongoing"] is True, str(logs[0]["ongoing"]))
    chk("end 已用当前时间兜底（非空）", bool(logs[0]["end"]), logs[0]["end"])

    print()
    print("=" * 60)
    print("【C2】已失联的会话不再标 ongoing（iOS 杀进程场景）")
    print("=" * 60)
    reset()
    # 取一个「今天之内」的起点，避免在午夜零点后跑测试时跨到昨天。
    # 当前时刻若不足 1 小时（如 00:15），则退到 00:00 之后 5 分钟，保证仍在今天。
    st = now - timedelta(minutes=15)
    if st.date() != now.date():
        st = now.replace(hour=0, minute=0, second=0, microsecond=0)
    add(st, None, 600, beat=st)
    logs = db.get_session_log(STU, 7)
    chk("返回 1 条", len(logs) == 1, str(len(logs)))
    if logs:
        chk("ongoing 为 False（不再挂「进行中」）", logs[0]["ongoing"] is False,
            str(logs[0]["ongoing"]))
        chk("时长保持上报的 10 分", logs[0]["duration"] == "10分", logs[0]["duration"])
        exp_end = (st + timedelta(seconds=600)).strftime("%H:%M")
        chk("结束时间收敛为 开始+10分", logs[0]["end"].startswith(exp_end),
            logs[0]["end"])

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
