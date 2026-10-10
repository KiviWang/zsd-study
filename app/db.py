"""
政史地学习平台 · 数据访问层
支持两种后端，自动切换：
  1) Turso 云数据库（设置环境变量 TURSO_URL + TURSO_TOKEN 时启用）
  2) 本地 SQLite（未设置环境变量时使用）

对外暴露统一的 query / execute 接口，业务层无需关心底层。
Turso 通过 HTTP API（/v2/pipeline）调用，不依赖任何第三方库。
"""
import os
import sqlite3
import json
import requests
from datetime import datetime, date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLITE_PATH = os.path.join(BASE, "data", "study.db")

TURSO_URL = os.environ.get("TURSO_URL", "").rstrip("/")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "")
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)


# =========================================================
# 底层适配
# =========================================================
def _turso_pipeline(statements):
    """
    向 Turso 发送一批语句。
    statements: [{"sql": "...", "args": [...]}, ...]
    返回: [result_dict, ...]，每个 result 含 cols/rows
    """
    reqs = []
    for st in statements:
        stmt = {"sql": st["sql"]}
        if st.get("args"):
            stmt["args"] = [_to_arg(v) for v in st["args"]]
        reqs.append({"type": "execute", "stmt": stmt})
    reqs.append({"type": "close"})

    resp = requests.post(
        TURSO_URL + "/v2/pipeline",
        headers={
            "Authorization": "Bearer " + TURSO_TOKEN,
            "Content-Type": "application/json",
        },
        data=json.dumps({"requests": reqs}),
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    out = []
    for item in data.get("results", []):
        if item.get("type") == "error":
            raise RuntimeError("Turso error: " + json.dumps(item, ensure_ascii=False))
        if "response" in item:
            r = item["response"]["result"]
            cols = [c.get("name") for c in r.get("cols", [])]
            rows = []
            for row in r.get("rows", []):
                rows.append({cols[i]: _from_arg(cell) for i, cell in enumerate(row)})
            out.append({
                "cols": cols,
                "rows": rows,
                "affected": r.get("affected_row_count", 0),
                "last_id": r.get("last_insert_rowid"),
            })
    return out


def _to_arg(v):
    """Python 值 → Turso 参数格式"""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": str(v)}
    return {"type": "text", "value": str(v)}


def _from_arg(cell):
    """Turso 返回值 → Python 值"""
    if not isinstance(cell, dict):
        return cell
    t = cell.get("type")
    val = cell.get("value")
    if t == "null":
        return None
    if t == "integer":
        try:
            return int(val)
        except (TypeError, ValueError):
            return val
    if t == "float":
        try:
            return float(val)
        except (TypeError, ValueError):
            return val
    return val


def query(sql, args=()):
    """执行查询，返回 list[dict]"""
    if USE_TURSO:
        res = _turso_pipeline([{"sql": sql, "args": list(args)}])
        return res[0]["rows"] if res else []
    # 本地 sqlite
    _ensure_dir()
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def execute(sql, args=()):
    """执行写操作，返回 {affected, last_id}"""
    if USE_TURSO:
        res = _turso_pipeline([{"sql": sql, "args": list(args)}])
        if res:
            return {"affected": res[0].get("affected"), "last_id": res[0].get("last_id")}
        return {"affected": 0, "last_id": None}
    _ensure_dir()
    conn = sqlite3.connect(SQLITE_PATH)
    try:
        cur = conn.execute(sql, tuple(args))
        conn.commit()
        return {"affected": cur.rowcount, "last_id": cur.lastrowid}
    finally:
        conn.close()


def _ensure_dir():
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)


# =========================================================
# 建表（两种后端通用）
# =========================================================
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS answers (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id   TEXT NOT NULL DEFAULT 'default',
        course_id    TEXT NOT NULL,
        question_idx INTEGER NOT NULL,
        chosen       INTEGER NOT NULL,
        correct      INTEGER NOT NULL,
        answered_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id  TEXT NOT NULL DEFAULT 'default',
        started_at  TEXT NOT NULL,
        ended_at    TEXT,
        duration    INTEGER DEFAULT 0,
        page        TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS video_views (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id  TEXT NOT NULL DEFAULT 'default',
        course_id   TEXT NOT NULL,
        video_name  TEXT NOT NULL,
        viewed_at   TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS task_done (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id  TEXT NOT NULL DEFAULT 'default',
        day_no      INTEGER NOT NULL,
        task_idx    INTEGER NOT NULL,
        done        INTEGER NOT NULL DEFAULT 1,
        updated_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
]


def init_db():
    """初始化表结构与默认配置"""
    for stmt in SCHEMA:
        execute(stmt)
    execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_task_done_unique "
            "ON task_done(student_id, day_no, task_idx)")
    execute("CREATE INDEX IF NOT EXISTS idx_answers_stu ON answers(student_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_sessions_stu ON sessions(student_id)")

    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("admin_password", "888888"))
    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("student_name", "同学"))
    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("daily_target_minutes", "25"))


# =========================================================
# 配置
# =========================================================
def get_config(key, default=""):
    rows = query("SELECT value FROM config WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def set_config(key, value):
    # 兼容 upsert：先删后插，两种数据库都支持
    execute("DELETE FROM config WHERE key=?", (key,))
    execute("INSERT INTO config(key,value) VALUES(?,?)", (key, str(value)))


# =========================================================
# 答题
# =========================================================
def record_answer(course_id, question_idx, chosen, correct, student_id="default"):
    execute(
        "INSERT INTO answers(student_id,course_id,question_idx,chosen,correct,answered_at) "
        "VALUES(?,?,?,?,?,?)",
        (student_id, course_id, question_idx, chosen, 1 if correct else 0,
         datetime.now().isoformat(timespec="seconds"))
    )


def reset_course_answers(course_id, student_id="default"):
    execute("DELETE FROM answers WHERE student_id=? AND course_id=?",
            (student_id, course_id))


def _latest_answers_where(where_extra="", params=()):
    """取每题最近一次作答（用子查询兼容两种数据库）"""
    sql = """
        SELECT a.* FROM answers a
        INNER JOIN (
            SELECT course_id, question_idx, MAX(id) AS max_id
            FROM answers WHERE student_id=?
            GROUP BY course_id, question_idx
        ) t ON a.id = t.max_id
        WHERE a.student_id = ? %s
    """ % where_extra
    return query(sql, tuple(params))


def get_course_answers(course_id, student_id="default"):
    rows = query("""
        SELECT a.question_idx, a.chosen, a.correct FROM answers a
        INNER JOIN (
            SELECT question_idx, MAX(id) AS max_id FROM answers
            WHERE student_id=? AND course_id=?
            GROUP BY question_idx
        ) t ON a.id = t.max_id
        WHERE a.student_id=? AND a.course_id=?
    """, (student_id, course_id, student_id, course_id))
    return {str(r["question_idx"]): {"chosen": r["chosen"], "correct": bool(r["correct"])}
            for r in rows}


def get_wrong_questions(student_id="default"):
    rows = _latest_answers_where("AND a.correct = 0",
                                 (student_id, student_id))
    rows.sort(key=lambda r: r.get("answered_at") or "", reverse=True)
    return rows


def get_stats(student_id="default"):
    rows = _latest_answers_where("", (student_id, student_id))
    by_course = {}
    total = 0
    right = 0
    for r in rows:
        cid = r["course_id"]
        by_course.setdefault(cid, {"total": 0, "right": 0})
        by_course[cid]["total"] += 1
        total += 1
        if r["correct"]:
            by_course[cid]["right"] += 1
            right += 1
    return {
        "total": total,
        "right": right,
        "rate": round(right / total * 100) if total else 0,
        "by_course": by_course,
        "wrong_count": total - right,
    }


# =========================================================
# 学习会话
# =========================================================
def start_session(page="", student_id="default"):
    r = execute("INSERT INTO sessions(student_id,started_at,page) VALUES(?,?,?)",
                (student_id, datetime.now().isoformat(timespec="seconds"), page))
    return r.get("last_id")


def end_session(sid, student_id="default"):
    rows = query("SELECT started_at FROM sessions WHERE id=?", (sid,))
    if not rows:
        return
    try:
        start = datetime.fromisoformat(rows[0]["started_at"])
    except (ValueError, TypeError):
        return
    dur = min(int((datetime.now() - start).total_seconds()), 7200)
    execute("UPDATE sessions SET ended_at=?, duration=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), dur, sid))


def get_study_time(student_id="default", days=7):
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = query("""
        SELECT SUBSTR(started_at,1,10) AS d, SUM(duration) AS secs, COUNT(*) AS rounds
        FROM sessions
        WHERE student_id=? AND SUBSTR(started_at,1,10) >= ?
        GROUP BY SUBSTR(started_at,1,10)
        ORDER BY d
    """, (student_id, since))
    out = []
    for r in rows:
        secs = r.get("secs") or 0
        disp = f"{secs}秒" if secs < 60 else f"{round(secs/60,1)}分"
        out.append({
            "date": r["d"],
            "seconds": secs,
            "minutes": round(secs / 60, 1),
            "display": disp,
            "rounds": r["rounds"],
        })
    return out


def get_streak(student_id="default"):
    rows = query("""
        SELECT DISTINCT SUBSTR(started_at,1,10) AS d FROM sessions
        WHERE student_id=? ORDER BY d DESC
    """, (student_id,))
    days = [r["d"] for r in rows]
    if not days:
        return 0
    today = date.today().isoformat()
    yest = (date.today() - timedelta(days=1)).isoformat()
    if days[0] not in (today, yest):
        return 0
    streak = 0
    cur = date.fromisoformat(days[0])
    for d in days:
        if d == cur.isoformat():
            streak += 1
            cur = cur - timedelta(days=1)
        else:
            break
    return streak


# =========================================================
# 视频观看
# =========================================================
def record_video(course_id, video_name, student_id="default"):
    execute("INSERT INTO video_views(student_id,course_id,video_name,viewed_at) "
            "VALUES(?,?,?,?)",
            (student_id, course_id, video_name,
             datetime.now().isoformat(timespec="seconds")))


def get_video_stats(student_id="default"):
    rows = query("""
        SELECT course_id, video_name, COUNT(*) AS times, MAX(viewed_at) AS last_at
        FROM video_views WHERE student_id=?
        GROUP BY course_id, video_name
    """, (student_id,))
    rows.sort(key=lambda r: r.get("last_at") or "", reverse=True)
    return rows


# =========================================================
# 任务打卡
# =========================================================
def set_task(day_no, task_idx, done, student_id="default"):
    now = datetime.now().isoformat(timespec="seconds")
    existing = query("SELECT id FROM task_done WHERE student_id=? AND day_no=? AND task_idx=?",
                     (student_id, day_no, task_idx))
    if existing:
        execute("UPDATE task_done SET done=?, updated_at=? WHERE id=?",
                (1 if done else 0, now, existing[0]["id"]))
    else:
        execute("INSERT INTO task_done(student_id,day_no,task_idx,done,updated_at) "
                "VALUES(?,?,?,?,?)",
                (student_id, day_no, task_idx, 1 if done else 0, now))


def get_tasks(student_id="default"):
    rows = query("SELECT day_no,task_idx,done FROM task_done WHERE student_id=?",
                 (student_id,))
    result = {}
    for r in rows:
        result.setdefault(str(r["day_no"]), {})[str(r["task_idx"])] = bool(r["done"])
    return result
