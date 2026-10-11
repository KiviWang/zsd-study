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
# 本地 SQLite 位置：
#   默认 <项目>/data/study.db
#   可用环境变量 ZSD_SQLITE_DIR 覆盖（PythonAnywhere 部署时指向可持久化目录）
_SQLITE_DIR = os.environ.get("ZSD_SQLITE_DIR") or os.path.join(BASE, "data")
SQLITE_PATH = os.path.join(_SQLITE_DIR, "study.db")

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
        # 错误响应：把完整内容抛出，便于定位（SQL错误/认证失败等）
        if item.get("type") == "error":
            raise RuntimeError("Turso error: " + json.dumps(item, ensure_ascii=False))
        resp = item.get("response") or {}
        # 跳过 close 等非 execute 响应（它们没有 result 字段）
        if resp.get("type") != "execute":
            continue
        if resp.get("type") == "error" or "result" not in resp:
            raise RuntimeError("Turso error: " + json.dumps(item, ensure_ascii=False))
        r = resp.get("result") or {}
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
    # ---- 学生账号（多用户核心：登录码即身份）----
    """CREATE TABLE IF NOT EXISTS students (
        sid         TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        color       TEXT DEFAULT '#2f6fed',
        active      INTEGER DEFAULT 1,
        created_at  TEXT NOT NULL,
        last_active TEXT
    )""",
    # ---- 登录态（记住登录用；token → sid）----
    """CREATE TABLE IF NOT EXISTS tokens (
        token      TEXT PRIMARY KEY,
        sid        TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used  TEXT
    )""",
    # ---- 题库（内容与逻辑分离：题目存库，可动态增删/随机抽题）----
    """CREATE TABLE IF NOT EXISTS questions (
        qid        TEXT PRIMARY KEY,
        course_id  TEXT NOT NULL,
        subject    TEXT NOT NULL DEFAULT '政治',
        tier       TEXT NOT NULL DEFAULT 'basic',
        qtype      TEXT NOT NULL DEFAULT 'choice',
        stem       TEXT NOT NULL,
        options    TEXT NOT NULL,
        answer     INTEGER NOT NULL,
        explain    TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        active     INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    # ---- 课程元信息（原来写死在 courses.py 的课程列表，改为可入库）----
    """CREATE TABLE IF NOT EXISTS courses (
        cid        TEXT PRIMARY KEY,
        no         TEXT NOT NULL,
        subject    TEXT NOT NULL DEFAULT '政治',
        title      TEXT NOT NULL,
        subtitle   TEXT DEFAULT '',
        color      TEXT DEFAULT '#2f6fed',
        sort_order INTEGER DEFAULT 0,
        active     INTEGER DEFAULT 1
    )""",
    # ---- 答题记录（新增 qid 列，指向题库；question_idx 保留兼容）----
    """CREATE TABLE IF NOT EXISTS answers (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id   TEXT NOT NULL DEFAULT 'default',
        course_id    TEXT NOT NULL,
        question_idx INTEGER,
        qid          TEXT,
        chosen       INTEGER NOT NULL,
        correct      INTEGER NOT NULL,
        answered_at  TEXT NOT NULL,
        mode         TEXT DEFAULT 'basic'
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


def _columns_of(table):
    """取表的现有列名集合（兼容 SQLite / Turso）"""
    try:
        rows = query(f"PRAGMA table_info({table})")
        return {r.get("name") for r in rows}
    except Exception:
        return set()


def _migrate():
    """
    兼容旧数据库：给已存在的表补上新增列。
    CREATE TABLE IF NOT EXISTS 不会给旧表加列，所以必须显式 ALTER。
    """
    # answers 表新增 qid / mode（历史数据该列为 NULL，不影响读取）
    cols = _columns_of("answers")
    if cols:
        if "qid" not in cols:
            execute("ALTER TABLE answers ADD COLUMN qid TEXT")
        if "mode" not in cols:
            execute("ALTER TABLE answers ADD COLUMN mode TEXT DEFAULT 'basic'")


def init_db():
    """初始化表结构与默认配置"""
    for stmt in SCHEMA:
        execute(stmt)
    _migrate()   # 兼容旧库：补齐新增列
    execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_task_done_unique "
            "ON task_done(student_id, day_no, task_idx)")
    execute("CREATE INDEX IF NOT EXISTS idx_answers_stu ON answers(student_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_sessions_stu ON sessions(student_id)")
    # 题库索引
    execute("CREATE INDEX IF NOT EXISTS idx_q_course ON questions(course_id, tier, active)")
    execute("CREATE INDEX IF NOT EXISTS idx_answers_qid ON answers(student_id, qid)")

    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("admin_password", "888888"))
    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("student_name", "同学"))
    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("daily_target_minutes", "25"))

    _ensure_default_student()


def _ensure_default_student():
    """
    保证至少有 1 个学生账号，且历史数据（student_id='default'）能被继承。

    做法：保留 'default' 作为首个学生的 sid，昵称取 config.student_name，
    这样此前所有作答记录无需搬运即可归到该学生名下。
    """
    rows = query("SELECT COUNT(*) AS n FROM students")
    if rows and rows[0]["n"] > 0:
        return
    name = get_config("student_name", "同学")
    now = datetime.now().isoformat(timespec="seconds")
    execute("INSERT INTO students(sid,name,color,active,created_at,last_active) "
            "VALUES(?,?,?,1,?,NULL)",
            ("default", name, "#e8524a", now))


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
# 学生账号（多用户）
# =========================================================
def list_students(active_only=True):
    """学生列表。默认按最后使用时间倒序（最晚用的排最前）。"""
    if active_only:
        rows = query("SELECT * FROM students WHERE active=1")
    else:
        rows = query("SELECT * FROM students")
    # last_active 为空（从未使用）排最后；否则按时间倒序
    rows.sort(key=lambda r: (r.get("last_active") or "0000"), reverse=True)
    return rows


def get_student(sid):
    rows = query("SELECT * FROM students WHERE sid=?", (sid,))
    return rows[0] if rows else None


def create_student(sid, name, color="#2f6fed"):
    """新建学生。sid 即登录码（拼音）。返回是否成功（重名则失败）。"""
    sid = (sid or "").strip()
    name = (name or "").strip() or sid
    if not sid:
        return False, "登录码不能为空"
    if get_student(sid):
        return False, "这个登录码已经有人用了"
    # last_active 留空：只有真正使用过才算"活跃"，保证列表排序有意义
    execute("INSERT INTO students(sid,name,color,active,created_at,last_active) "
            "VALUES(?,?,?,1,?,NULL)",
            (sid, name, color, datetime.now().isoformat(timespec="seconds")))
    return True, "创建成功"


def update_student(sid, name=None, color=None, active=None):
    if name is not None:
        execute("UPDATE students SET name=? WHERE sid=?", (name, sid))
    if color is not None:
        execute("UPDATE students SET color=? WHERE sid=?", (color, sid))
    if active is not None:
        execute("UPDATE students SET active=? WHERE sid=?", (1 if active else 0, sid))


def touch_student(sid):
    """更新最后活跃时间（每次登录/操作时调用）"""
    execute("UPDATE students SET last_active=? WHERE sid=?",
            (datetime.now().isoformat(timespec="seconds"), sid))


def delete_student(sid, wipe=False):
    """删除学生。wipe=True 时连同其所有学习数据一起删除。"""
    execute("DELETE FROM students WHERE sid=?", (sid,))
    execute("DELETE FROM tokens WHERE sid=?", (sid,))
    if wipe:
        for t in ("answers", "sessions", "video_views", "task_done"):
            execute(f"DELETE FROM {t} WHERE student_id=?", (sid,))


# =========================================================
# 登录态（token）
# =========================================================
def create_token(sid):
    """生成一个登录令牌（记住登录用）"""
    import uuid
    token = uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    execute("INSERT INTO tokens(token,sid,created_at,last_used) VALUES(?,?,?,?)",
            (token, sid, now, now))
    return token


def resolve_token(token):
    """token → sid；有效则刷新 last_used，同时更新学生活跃时间"""
    if not token:
        return None
    rows = query("SELECT sid FROM tokens WHERE token=?", (token,))
    if not rows:
        return None
    sid = rows[0]["sid"]
    now = datetime.now().isoformat(timespec="seconds")
    execute("UPDATE tokens SET last_used=? WHERE token=?", (now, token))
    touch_student(sid)
    return sid


def revoke_token(token):
    if token:
        execute("DELETE FROM tokens WHERE token=?", (token,))


# =========================================================
# 答题
# =========================================================
def record_answer(course_id, question_idx, chosen, correct, student_id="default",
                  qid=None, mode="basic"):
    """记录一次作答。qid 指向题库（推荐），question_idx 保留兼容旧调用。"""
    execute(
        "INSERT INTO answers(student_id,course_id,question_idx,qid,chosen,correct,answered_at,mode) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (student_id, course_id, question_idx, qid, chosen, 1 if correct else 0,
         datetime.now().isoformat(timespec="seconds"), mode)
    )


def reset_course_answers(course_id, student_id="default"):
    execute("DELETE FROM answers WHERE student_id=? AND course_id=?",
            (student_id, course_id))


def _latest_answers_where(where_extra="", params=(), by_qid=True):
    """
    取每题最近一次作答。
    by_qid=True  → 按 qid 分组（题库模式，题目顺序变化也不受影响）
    by_qid=False → 按 question_idx 分组（兼容未迁移的历史数据）
    """
    if by_qid:
        sql = """
            SELECT a.* FROM answers a
            INNER JOIN (
                SELECT qid, MAX(id) AS max_id
                FROM answers WHERE student_id=? AND qid IS NOT NULL
                GROUP BY qid
            ) t ON a.id = t.max_id
            WHERE a.student_id = ? %s
        """ % where_extra
    else:
        sql = """
            SELECT a.* FROM answers a
            INNER JOIN (
                SELECT course_id, question_idx, MAX(id) AS max_id
                FROM answers WHERE student_id=? AND qid IS NULL
                GROUP BY course_id, question_idx
            ) t ON a.id = t.max_id
            WHERE a.student_id = ? %s
        """ % where_extra
    return query(sql, tuple(params))


def get_course_answers(course_id, student_id="default"):
    """
    取本课每题最近一次作答状态。
    返回 {"byQid": {qid: {...}}, "byIdx": {idx: {...}}}
      后端的题目列表可直接用 byQid[qid] / byIdx[str(i)] 恢复页面已答状态。
    """
    rows = query("""
        SELECT a.qid, a.question_idx, a.chosen, a.correct FROM answers a
        INNER JOIN (
            SELECT COALESCE(qid, 'idx:' || course_id || ':' || question_idx) AS k,
                   MAX(id) AS max_id
            FROM answers
            WHERE student_id=? AND course_id=?
            GROUP BY k
        ) t ON a.id = t.max_id
        WHERE a.student_id=? AND a.course_id=?
    """, (student_id, course_id, student_id, course_id))
    by_qid, by_idx = {}, {}
    for r in rows:
        info = {"chosen": r["chosen"], "correct": bool(r["correct"])}
        if r.get("qid"):
            by_qid[r["qid"]] = info
        if r.get("question_idx") is not None:
            by_idx[str(r["question_idx"])] = info
    return {"byQid": by_qid, "byIdx": by_idx}


def _all_latest_answers(student_id="default", where_extra=""):
    """合并"按qid"与"按idx"两条路径的最新作答，避免遗漏历史数据"""
    a = _latest_answers_where(where_extra, (student_id, student_id), by_qid=True)
    b = _latest_answers_where(where_extra, (student_id, student_id), by_qid=False)
    return a + b


def get_wrong_questions(student_id="default"):
    """错题（合并题库模式与历史模式）"""
    rows = _all_latest_answers(student_id, "AND a.correct = 0")
    rows.sort(key=lambda r: r.get("answered_at") or "", reverse=True)
    return rows


def get_stats(student_id="default"):
    rows = _all_latest_answers(student_id)
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
