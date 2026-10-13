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
from datetime import datetime, date, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 本地 SQLite 位置：
#   默认 <项目>/data/study.db
#   可用环境变量 ZSD_SQLITE_DIR 覆盖（PythonAnywhere 部署时指向可持久化目录）
_SQLITE_DIR = os.environ.get("ZSD_SQLITE_DIR") or os.path.join(BASE, "data")
SQLITE_PATH = os.path.join(_SQLITE_DIR, "study.db")

TURSO_URL = os.environ.get("TURSO_URL", "").rstrip("/")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN", "")
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

# 业务时区：固定中国标准时间（UTC+8）。
# 服务器（PythonAnywhere 免费版）系统时区是 UTC，若直接用 datetime.now()，
# 学生在北京时间早上 8 点之前学习，会被记成「前一天」，导致看板当日时长为 0。
# 因此所有入库时间统一走 now_cn()，不依赖服务器本地时区。
CN_TZ = timezone(timedelta(hours=8), "CST")


def now_cn():
    """当前中国标准时间（UTC+8，naive，便于与历史数据直接比较/截取前 10 位）"""
    return datetime.now(CN_TZ).replace(tzinfo=None)


def _parse_dt(s):
    """解析库里存的时间字符串；老数据可能是 UTC 写法，统一按 naive 处理"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt



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
    # ---- 学生账号（多用户核心：sid 即身份，注册用户另有 username/phone/password_hash）----
    """CREATE TABLE IF NOT EXISTS students (
        sid           TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        color         TEXT DEFAULT '#2f6fed',
        active        INTEGER DEFAULT 1,
        created_at    TEXT NOT NULL,
        last_active   TEXT,
        username      TEXT,
        phone         TEXT,
        password_hash TEXT,
        is_anonymous  INTEGER DEFAULT 0
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
        img        TEXT DEFAULT '',
        svg        TEXT DEFAULT '',
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
        page        TEXT DEFAULT '',
        last_beat_at TEXT
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

    # students 表新增注册字段（老库只有登录码，升级后兼容）
    cols = _columns_of("students")
    if cols:
        if "username" not in cols:
            execute("ALTER TABLE students ADD COLUMN username TEXT")
        if "phone" not in cols:
            execute("ALTER TABLE students ADD COLUMN phone TEXT")
        if "password_hash" not in cols:
            execute("ALTER TABLE students ADD COLUMN password_hash TEXT")
        if "is_anonymous" not in cols:
            execute("ALTER TABLE students ADD COLUMN is_anonymous INTEGER DEFAULT 0")

    # questions 表新增图片/示意图字段（老题目这两列为空，正常显示）
    cols = _columns_of("questions")
    if cols:
        if "img" not in cols:
            execute("ALTER TABLE questions ADD COLUMN img TEXT DEFAULT ''")
        if "svg" not in cols:
            execute("ALTER TABLE questions ADD COLUMN svg TEXT DEFAULT ''")
        # 论述题专用：材料原文（选择题为空）
        if "material" not in cols:
            execute("ALTER TABLE questions ADD COLUMN material TEXT DEFAULT ''")
        # 论述题专用：择优解析（说明为什么这个选项最贴切）
        if "why" not in cols:
            execute("ALTER TABLE questions ADD COLUMN why TEXT DEFAULT ''")

    # sessions 表新增 last_beat_at（最后一次心跳时刻）。
    # 用途：懒结算时判断「这条会话是不是已经没心跳了」——
    # 只有最后心跳超过宽限期的会话才会被判为僵尸并结算，
    # 正在学的活跃会话不会被误关。
    cols = _columns_of("sessions")
    if cols and "last_beat_at" not in cols:
        execute("ALTER TABLE sessions ADD COLUMN last_beat_at TEXT")


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
    # 注册用户唯一性（NULL 不参与唯一约束，所以老数据/匿名账号不受影响）
    execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stu_username "
            "ON students(username) WHERE username IS NOT NULL")

    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("admin_password", "888888"))
    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("student_name", "同学"))
    execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", ("daily_target_minutes", "25"))

    _ensure_default_student()
    ensure_anonymous_student()


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
    now = now_cn().isoformat(timespec="seconds")
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


def create_student(sid, name, color="#2f6fed", is_anonymous=False):
    """新建学生。sid 即登录码（拼音）。返回是否成功（重名则失败）。"""
    sid = (sid or "").strip()
    name = (name or "").strip() or sid
    if not sid:
        return False, "登录码不能为空"
    if get_student(sid):
        return False, "这个登录码已经有人用了"
    # last_active 留空：只有真正使用过才算"活跃"，保证列表排序有意义
    execute("INSERT INTO students"
            "(sid,name,color,active,created_at,last_active,is_anonymous) "
            "VALUES(?,?,?,1,?,NULL,?)",
            (sid, name, color, now_cn().isoformat(timespec="seconds"),
             1 if is_anonymous else 0))
    return True, "创建成功"


def ensure_anonymous_student():
    """
    匿名体验用的公共账号（所有匿名用户共用一个 sid）。
    数据按浏览器本地隔离：前端匿名时只在本地缓存作答，不上传服务器。
    """
    sid = "anonymous"
    stu = get_student(sid)
    if stu:
        return stu
    now = now_cn().isoformat(timespec="seconds")
    execute("INSERT INTO students"
            "(sid,name,color,active,created_at,last_active,is_anonymous) "
            "VALUES(?,?,?,1,?,NULL,1)",
            (sid, "匿名体验", "#9aa4b2", now))
    return get_student(sid)


# ---------------- 注册账号（用户名 + 密码） ----------------
import hashlib as _hashlib


def hash_password(password):
    """口令散列：盐 + SHA256，避免明文落库。"""
    salt = "zsd2026"
    return _hashlib.sha256((salt + str(password)).encode("utf-8")).hexdigest()


def get_student_by_username(username):
    """按用户名查学生（用户名不区分大小写）"""
    u = (username or "").strip().lower()
    if not u:
        return None
    rows = query("SELECT * FROM students WHERE lower(username)=?", (u,))
    return rows[0] if rows else None


def get_student_by_phone(phone):
    p = (phone or "").strip()
    if not p:
        return None
    rows = query("SELECT * FROM students WHERE phone=?", (p,))
    return rows[0] if rows else None


def register_student(username, phone, name, password):
    """
    开放注册。sid 直接用用户名（小写），这样注册用户天然与登录码用户共存。
    返回 (是否成功, 消息, 学生记录)
    """
    import re
    import uuid as _uuid

    username = (username or "").strip()
    phone = (phone or "").strip()
    name = (name or "").strip()
    password = str(password or "")

    if not username:
        return False, "请填写用户名", None
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
        return False, "用户名需 3-20 位字母、数字或下划线", None
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        return False, "请填写正确的 11 位手机号", None
    if not name:
        return False, "请填写姓名", None
    if len(password) < 6:
        return False, "密码至少 6 位", None

    key = username.lower()
    if get_student_by_username(key):
        return False, "这个用户名已经被注册了", None
    if get_student_by_phone(phone):
        return False, "这个手机号已经被注册了", None

    sid = key
    if get_student(sid):
        # 极端情况：与老登录码撞名，加后缀避免覆盖
        sid = key + "_" + _uuid.uuid4().hex[:4]

    now = now_cn().isoformat(timespec="seconds")
    execute("INSERT INTO students"
            "(sid,name,color,active,created_at,last_active,username,phone,password_hash,is_anonymous) "
            "VALUES(?,?,?,1,?,NULL,?,?,?,0)",
            (sid, name, "#2f6fed", now, key, phone, hash_password(password)))
    return True, "注册成功", get_student(sid)


def verify_login(username, password):
    """用户名 + 密码校验。返回 (是否成功, 消息, 学生记录)"""
    stu = get_student_by_username(username)
    if not stu:
        return False, "用户名不存在，请先注册", None
    if not stu.get("password_hash"):
        return False, "该账号不支持密码登录，请使用登录码", None
    if stu.get("active") in (0, "0"):
        return False, "该账号已被停用", None
    if stu["password_hash"] != hash_password(password):
        return False, "密码不正确", None
    return True, "登录成功", stu


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
            (now_cn().isoformat(timespec="seconds"), sid))


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
    now = now_cn().isoformat(timespec="seconds")
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
    now = now_cn().isoformat(timespec="seconds")
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
         now_cn().isoformat(timespec="seconds"), mode)
    )


def reset_course_answers(course_id, student_id="default"):
    execute("DELETE FROM answers WHERE student_id=? AND course_id=?",
            (student_id, course_id))


def _latest_answers_where(where_extra="", params=(), by_qid=True):
    """
    取每题每次练习模式下的最近一次作答。

    重要：分组键是 (qid, mode)，不是单独的 qid。
    这样"基础练习"和"随机测试"里同一道题的作答互不干扰——
    基础答过不影响随机，随机答对也不覆盖基础。

    by_qid=True  → 按 qid+mode 分组（题库模式）
    by_qid=False → 按 course_id+idx+mode 分组（兼容未迁移的历史数据）
    """
    if by_qid:
        sql = """
            SELECT a.* FROM answers a
            INNER JOIN (
                SELECT qid, COALESCE(mode,'basic') AS m, MAX(id) AS max_id
                FROM answers WHERE student_id=? AND qid IS NOT NULL
                GROUP BY qid, m
            ) t ON a.id = t.max_id
            WHERE a.student_id = ? %s
        """ % where_extra
    else:
        sql = """
            SELECT a.* FROM answers a
            INNER JOIN (
                SELECT course_id, question_idx, COALESCE(mode,'basic') AS m, MAX(id) AS max_id
                FROM answers WHERE student_id=? AND qid IS NULL
                GROUP BY course_id, question_idx, m
            ) t ON a.id = t.max_id
            WHERE a.student_id = ? %s
        """ % where_extra
    return query(sql, tuple(params))


def get_course_answers(course_id, student_id="default"):
    """
    取本课每题的作答状态，按模式分开返回。

    返回 {"byQid": {qid: {...}}, "byIdx": {idx: {...}},
          "byQidBasic": {...}, "byQidRandom": {...}}
      · byQid / byIdx      —— 兼容旧前端：取该题"任意模式"下最近一次
      · byQidBasic         —— 仅基础练习的状态
      · byQidRandom        —— 仅随机测试的状态
    这样基础练习和随机测试互不干扰。
    """
    rows = query("""
        SELECT a.qid, a.question_idx, a.chosen, a.correct, COALESCE(a.mode,'basic') AS mode, a.id
        FROM answers a
        INNER JOIN (
            SELECT CASE
                     WHEN qid IS NOT NULL THEN 'q:' || qid
                     ELSE 'i:' || course_id || ':' || question_idx
                   END || '#' || COALESCE(mode,'basic') AS k,
                   MAX(id) AS max_id
            FROM answers
            WHERE student_id=? AND course_id=?
            GROUP BY k
        ) t ON a.id = t.max_id
        WHERE a.student_id=? AND a.course_id=?
    """, (student_id, course_id, student_id, course_id))

    by_qid, by_idx = {}, {}
    by_qid_basic, by_qid_random = {}, {}
    # rows 已按"每个 (题,模式)"唯一，再挑各模式最近一条
    sorted_rows = sorted(rows, key=lambda r: r.get("id") or 0)
    for r in sorted_rows:
        info = {"chosen": r["chosen"], "correct": bool(r["correct"]),
                "mode": r.get("mode") or "basic"}
        if r.get("qid"):
            by_qid[r["qid"]] = info                      # 任意模式最近一条
            if info["mode"] == "random":
                by_qid_random[r["qid"]] = info
            elif info["mode"] == "basic":
                by_qid_basic[r["qid"]] = info
        if r.get("question_idx") is not None:
            by_idx[str(r["question_idx"])] = info
    return {"byQid": by_qid, "byIdx": by_idx,
            "byQidBasic": by_qid_basic, "byQidRandom": by_qid_random}


def _all_latest_answers(student_id="default", where_extra="", include_essay=False):
    """
    合并"按qid"与"按idx"两条路径的最新作答，避免遗漏历史数据。
    注意：分组键为 (qid, mode)，同一道题在基础/随机下会各返回一行。

    include_essay=False（默认）时排除论述题（mode='essay'）。
    论述题有自己的累计/正确率口径，混进自测统计会让「每课 20 题」
    的进度和正确率全部失真，所以默认隔离。
    """
    if include_essay:
        extra = where_extra
    else:
        extra = (where_extra + " AND COALESCE(a.mode,'basic')<>'essay'") if where_extra \
            else " AND COALESCE(a.mode,'basic')<>'essay'"
    a = _latest_answers_where(extra, (student_id, student_id), by_qid=True)
    b = _latest_answers_where(extra, (student_id, student_id), by_qid=False)
    return a + b


def get_wrong_questions(student_id="default", include_essay=True):
    """
    错题本数据源。

    规则（对应需求"错题重做答对后保留但标记已订正"）：
      · 只要某题曾经做错过，就留在错题本里
      · 同一道题在基础/随机/重做/论述下都算同一道题，按 qid 去重
        （qid 缺失的历史数据退化为 course_id+idx）
      · 若最近一次作答（任意模式）已答对，则 fixed=True（标记"已订正"）
      · modes 记录该题曾出错过的模式来源，wrong_times 记录出错次数

    这样错题本不会因为做对就消失，学生能看到自己订正过的足迹。

    include_essay=True（默认）：**每日论述题做错也一并收进错题本**，
    对应需求"答错的论述题一并在错题本里管理"。论述题的 qid 是 <cid>_eNN，
    与自测题不冲突，会各自成条。
    """
    all_rows = _all_latest_answers(student_id, include_essay=include_essay)
    wrong_rows = [r for r in all_rows if not r["correct"]]

    # 按 key 归并"最近一次作答"（用于判断是否已订正）
    latest_all = {}
    for r in all_rows:
        key = r.get("qid") or "idx:%s:%s" % (r.get("course_id"), r.get("question_idx"))
        cur = latest_all.get(key)
        if cur is None or (r.get("id") or 0) > (cur.get("id") or 0):
            latest_all[key] = r

    merged, order = {}, []
    for r in sorted(wrong_rows, key=lambda x: x.get("id") or 0, reverse=True):
        key = r.get("qid") or "idx:%s:%s" % (r.get("course_id"), r.get("question_idx"))
        if key not in merged:
            item = dict(r)
            item["modes"] = set()
            item["wrong_times"] = 0
            merged[key] = item
            order.append(key)
        merged[key]["modes"].add(r.get("mode") or "basic")
        merged[key]["wrong_times"] += 1

    out = []
    for k in order:
        item = merged[k]
        latest = latest_all.get(k) or item
        item["modes"] = sorted(item["modes"])
        item["mode"] = item["modes"][0] if item["modes"] else "basic"
        # 最近一次（任意模式）答对 → 已订正
        item["fixed"] = bool(latest.get("correct"))
        item["fixed_at"] = latest.get("answered_at") if item["fixed"] else None
        out.append(item)
    return out


def get_stats(student_id="default"):
    """
    总体统计。同一道题在基础/随机下各算各的（模式隔离），
    因此 total 会大于题目总数——这是刻意设计。
    额外返回 by_mode / by_course_mode，供前端分模式展示。
    """
    rows = _all_latest_answers(student_id)
    by_course = {}
    by_mode = {"basic": {"total": 0, "right": 0},
               "random": {"total": 0, "right": 0},
               "redo": {"total": 0, "right": 0}}
    by_course_mode = {}
    total = 0
    right = 0
    for r in rows:
        cid = r["course_id"]
        mode = r.get("mode") or "basic"
        if mode not in by_mode:
            by_mode[mode] = {"total": 0, "right": 0}
        by_course.setdefault(cid, {"total": 0, "right": 0})
        by_course_mode.setdefault(cid, {}).setdefault(
            mode, {"total": 0, "right": 0})
        by_course[cid]["total"] += 1
        by_course_mode[cid][mode]["total"] += 1
        by_mode[mode]["total"] += 1
        total += 1
        if r["correct"]:
            by_course[cid]["right"] += 1
            by_course_mode[cid][mode]["right"] += 1
            by_mode[mode]["right"] += 1
            right += 1
    for d in list(by_mode.values()) + list(by_course.values()) + \
             [v for m in by_course_mode.values() for v in m.values()]:
        d["rate"] = round(d["right"] / d["total"] * 100) if d["total"] else 0
    return {
        "total": total,
        "right": right,
        "rate": round(right / total * 100) if total else 0,
        "by_course": by_course,
        "by_mode": by_mode,
        "by_course_mode": by_course_mode,
        "wrong_count": total - right,
    }


# =========================================================
# 每日论述题（独立统计，不混入自测题的正确率）
# =========================================================
def get_essay_answered_order(course_id, student_id="default"):
    """
    返回该学生在某课**已做过的论述题 qid 列表，按作答时间从早到晚**。

    用途：
      · 判断"这道题是否做过"，从而每日推一道没做过的
      · 题库全部做完需要循环时，优先选最早做过的，把重复间隔拉到最大
    """
    rows = query("""
        SELECT qid, MIN(answered_at) AS first_at
        FROM answers
        WHERE student_id=? AND course_id=? AND mode='essay' AND qid IS NOT NULL
        GROUP BY qid
        ORDER BY first_at ASC
    """, (student_id, course_id))
    return [r["qid"] for r in rows if r.get("qid")]


def get_essay_answers(course_id, student_id="default"):
    """
    该课论述题的作答状态：{qid: {chosen, correct, answered_at}}（取每道题最近一次）。
    """
    rows = query("""
        SELECT a.qid, a.chosen, a.correct, a.answered_at
        FROM answers a
        INNER JOIN (
            SELECT qid, MAX(id) AS max_id FROM answers
            WHERE student_id=? AND course_id=? AND mode='essay' AND qid IS NOT NULL
            GROUP BY qid
        ) t ON a.id = t.max_id
        WHERE a.student_id=?
    """, (student_id, course_id, student_id))
    out = {}
    for r in rows:
        out[r["qid"]] = {"chosen": r["chosen"], "correct": bool(r["correct"]),
                         "answeredAt": r["answered_at"]}
    return out


def get_essay_stats(student_id="default"):
    """
    论述题总体统计（与自测题完全隔离）。

    口径说明：
      · total   = 答过的**不同题目数**（同一道题只算一次，按最近一次判分）
      · right   = 其中最近一次答对的题数
      · rate    = right / total
      · days    = 有论述题作答记录的天数（用于"连续/累计打卡"展示）
    """
    rows = query("""
        SELECT a.qid, a.correct, a.answered_at
        FROM answers a
        INNER JOIN (
            SELECT qid, MAX(id) AS max_id FROM answers
            WHERE student_id=? AND mode='essay' AND qid IS NOT NULL
            GROUP BY qid
        ) t ON a.id = t.max_id
        WHERE a.student_id=?
    """, (student_id, student_id))
    total = len(rows)
    right = sum(1 for r in rows if r["correct"])
    days = len({str(r["answered_at"] or "")[:10] for r in rows if r.get("answered_at")})
    return {
        "total": total,
        "right": right,
        "wrong": total - right,
        "rate": round(right / total * 100) if total else 0,
        "days": days,
    }


def get_essay_log(course_id, student_id="default", limit=60):
    """
    历史题目列表：该课论述题的作答轨迹，按作答时间倒序。

    每条含：qid、作答日期（YYYY-MM-DD）、我的选择、是否正确、是否已订正。
    题目内容由调用方（server）按 qid 取，保证只返回有作答记录的题。
    """
    rows = query("""
        SELECT a.qid, a.chosen, a.correct, a.answered_at
        FROM answers a
        INNER JOIN (
            SELECT qid, MAX(id) AS max_id FROM answers
            WHERE student_id=? AND course_id=? AND mode='essay' AND qid IS NOT NULL
            GROUP BY qid
        ) t ON a.id = t.max_id
        WHERE a.student_id=?
        ORDER BY a.answered_at DESC
        LIMIT ?
    """, (student_id, course_id, student_id, limit))
    return [{"qid": r["qid"], "chosen": r["chosen"],
             "correct": bool(r["correct"]),
             "answeredAt": r["answered_at"],
             "date": str(r["answered_at"] or "")[:10]} for r in rows]


def get_essay_day_answered(course_id, day_key, student_id="default"):
    """
    查某一天（YYYY-MM-DD，中国时区）该课论述题是否已作答，返回作答记录或 None。
    用于"今天这道题做没做"的判断：做过就不再重复推。
    """
    row = query("""
        SELECT a.qid, a.chosen, a.correct, a.answered_at
        FROM answers a
        INNER JOIN (
            SELECT qid, MAX(id) AS max_id FROM answers
            WHERE student_id=? AND course_id=? AND mode='essay'
              AND SUBSTR(answered_at,1,10)=?
            GROUP BY qid
        ) t ON a.id = t.max_id
        WHERE a.student_id=?
        ORDER BY a.answered_at DESC LIMIT 1
    """, (student_id, course_id, day_key, student_id))
    if not row:
        return None
    r = row[0]
    return {"qid": r["qid"], "chosen": r["chosen"],
            "correct": bool(r["correct"]), "answeredAt": r["answered_at"]}


# =========================================================
# 学习会话
# =========================================================
SESSION_MAX_SECONDS = 7200        # 单次会话封顶 2 小时（与前端说明一致）
SESSION_HEARTBEAT_GRACE = 180     # 心跳间隔 60s，超过 3 分钟没心跳视为已离开
SESSION_MIN_SECONDS = 5           # 小于 5 秒的会话不计时长（避免误点产生噪声）


def start_session(page="", student_id="default"):
    """开启一次学习会话。同一学生若已有未结束会话，先把它结算掉，避免僵尸会话堆积。"""
    _settle_open_sessions(student_id, exclude_sid=None)
    now = now_cn().isoformat(timespec="seconds")
    r = execute("INSERT INTO sessions(student_id,started_at,page,last_beat_at) "
                "VALUES(?,?,?,?)",
                (student_id, now, page, now))
    return r.get("last_id")


def heartbeat_session(sid, student_id="default", seconds=None):
    """
    心跳：把「本次会话已产生的时长」写入数据库。

    为什么需要：旧实现只在页面关闭/切后台（pagehide / visibilitychange）时才结算，
    手机浏览器经常不触发这些事件，导致整段学习的 duration 一直是 0。
    改为前端每 60 秒上报一次，时长就能边学边落库。

    seconds 由前端按「实际可见时长」计算并传入。
    重要：**不再用「now - started_at」的墙钟差作为兜底**。
    因为页面可能挂机很久（iOS 杀进程不触发事件），墙钟差会把挂机时间算成学习时间；
    而前端每秒都在自己累加"可见时长"，它的数字才是可信的。
    没有 seconds 时取 max(prev, 一个心跳周期)，只做最小补偿。

    返回累计时长（秒）。
    """
    if not sid:
        return 0
    rows = query("SELECT started_at, duration FROM sessions WHERE id=?", (sid,))
    if not rows:
        return 0
    prev = int(rows[0].get("duration") or 0)

    # 懒结算：顺手把同学生名下其他「已失联」的会话结清。
    # 注意排除当前 sid —— 它刚发来心跳，显然是活的，绝不能关。
    try:
        _settle_stale_except(student_id, sid)
    except Exception:
        pass

    if seconds is None:
        # 无上报值时，只补一个心跳周期（60s），绝不用墙钟差（可能是几小时）
        elapsed = prev + 60
    else:
        try:
            elapsed = int(seconds)
        except (TypeError, ValueError):
            elapsed = prev

    # 取「正推值」与「累计上报值」的较大者，但不超过封顶
    dur = max(prev, elapsed)
    dur = max(0, min(dur, SESSION_MAX_SECONDS))
    # 同时盖上「最后心跳时刻」：懒结算据此判断会话是否已失联。
    # 注意 duration 用 max(prev, elapsed)，但 last_beat_at 一律取当前时间——
    # 只要收到了心跳，这条会话就是「活着」的。
    execute("UPDATE sessions SET duration=?, last_beat_at=? WHERE id=?",
            (dur, now_cn().isoformat(timespec="seconds"), sid))
    return dur


def end_session(sid, student_id="default"):
    """
    结束会话。

    关键（僵尸会话修复）：不再用「now - started_at」的墙钟差值。
    iOS 上 beforeunload/pagehide 常常迟到很久（甚至等到第二天才触发），
    用墙钟差值会把「页面挂着没关」的时间也算成学习时间。
    这里只信任前端心跳累计上报的 duration；若前端传了更准确的值则采用。
    """
    if not sid:
        return
    rows = query("SELECT started_at, duration FROM sessions WHERE id=?", (sid,))
    if not rows:
        return
    start = _parse_dt(rows[0]["started_at"])
    try:
        sent = int(rows[0].get("duration") or 0)
    except (TypeError, ValueError):
        sent = 0
    # 以已上报的心跳累计值为准；不再与墙钟差值取 max
    dur = max(0, min(sent, SESSION_MAX_SECONDS))
    # 结束时间用「开始 + 实际学习时长」，避免明细里出现"学了2小时却跨了7小时"的矛盾
    end_dt = (start + timedelta(seconds=dur)) if start else now_cn()
    now = now_cn()
    if end_dt > now:
        end_dt = now
    execute("UPDATE sessions SET ended_at=?, duration=? WHERE id=?",
            (end_dt.isoformat(timespec="seconds"), dur, sid))


def _settle_open_sessions(student_id=None, exclude_sid=None, cap=None,
                          keep_reported=True, only_ids=None):
    """
    保底结算：把「没有正常结束」的会话补上时长并标记结束。

    核心原则：**前端心跳是唯一有效的时长依据**。
      没有心跳 = 没有有效学习，宁可少记，绝不虚增。

    为什么定这条原则（两轮踩坑的结论）：
      1) 第一轮：曾用「now - started_at」墙钟差当兜底。iOS 杀进程/关机/锁屏
         不触发 pagehide，会话挂到第二天，墙钟差已是 7 小时，整晚被记成学习。
      2) 第二轮（本次修复）：只在"有心跳上报"时才用上报值，但"零心跳"仍回退到
         墙钟差。结果——页面打开不到 60 秒（第一次心跳还没发出）就被杀掉的会话，
         duration 一直是 0，下次打开时走兜底分支，把整段间隔记成了学习时间，
         凭空多出「1 小时」。这正是用户在家长端看到的那条异常记录。

    现在的规则：
      · 有心跳上报（reported > 0）→ 采用上报值（真实可见时长），不再叠加墙钟时间
      · 零心跳（reported == 0）   → 记 0，绝不用墙钟差

    参数:
      cap: 仅在 keep_reported=False 的旧路径下生效（保留兼容）。
      keep_reported: 是否信任已上报的心跳累计值（默认 True）。
      only_ids: 只结算这些会话 id（懒结算时用，避免误伤活跃会话）。
    """
    _cap = cap if cap is not None else SESSION_MAX_SECONDS
    sql = ("SELECT id, student_id, started_at, duration FROM sessions "
           "WHERE ended_at IS NULL")
    args = []
    if student_id:
        sql += " AND student_id=?"
        args.append(student_id)
    if exclude_sid:
        sql += " AND id<>?"
        args.append(exclude_sid)
    try:
        rows = query(sql, tuple(args))
    except Exception:
        return 0

    if only_ids is not None:
        allowed = set(only_ids)
        rows = [r for r in rows if r["id"] in allowed]

    n = 0
    now = now_cn()
    for r in rows:
        start = _parse_dt(r["started_at"])
        if not start:
            continue
        try:
            reported = int(r.get("duration") or 0)
        except (TypeError, ValueError):
            reported = 0

        if keep_reported:
            # 统一信任心跳：有心跳上报就用上报值，零心跳记 0，绝不用墙钟差。
            # 这里**不做下限过滤**——那属于展示层的职责（get_session_log 的
            # min_seconds 已负责过滤误点噪声）。否则会把「学了 2~4 秒就关页面」
            # 这种真实上报错杀成 0，与 get_study_time 的 5 秒口径互相打架。
            dur = min(reported, SESSION_MAX_SECONDS) if reported > 0 else 0
            # 结束时间取「开始 + 实际学习时长」，而不是当前墙钟时间，
            # 否则明细里会显示成"23:40 → 次日06:23 学了2小时"这种矛盾记录
            end_dt = start + timedelta(seconds=dur)
            if end_dt > now:
                end_dt = now
        else:
            # 兼容旧路径（keep_reported=False）：仍按墙钟差 + cap，但会被调用方
            # 明确指定 cap，默认不启用。
            wall = int((now - start).total_seconds())
            dur = max(0, min(wall, _cap))
            end_dt = start + timedelta(seconds=dur)

        execute("UPDATE sessions SET ended_at=?, duration=? WHERE id=?",
                (end_dt.isoformat(timespec="seconds"), dur, r["id"]))
        n += 1
    return n


def settle_stale_sessions():
    """
    结算「没有正常结束」的残留会话。服务启动时调用，另有关键路径懒结算。

    规则（统一以心跳为唯一依据）：
      1. 有心跳上报值的会话 → 直接采用上报值（真实可见学习时长），
         不叠加任何墙钟时间。
      2. 完全没有上报值的会话（纯僵尸 / 误点秒退）→ **记 0**。
         以前这里会回退到「墙钟差封顶 3 分钟」，正是它导致"页面打开不到 60 秒
         被杀"的会话在下次打开时凭空多记一小时。既然浏览器关了就不会再有心跳，
         零心跳就应当记 0。

    注意：不能全按 SESSION_MAX_SECONDS 处理，否则每次 Reload 都会把僵尸
    会话虚增成 2 小时，累计出几百上千分钟。
    """
    return _settle_open_sessions(keep_reported=True)


def settle_zombie_sessions(student_id=None):
    """
    懒结算：只把「心跳已断」的僵尸会话结算掉，不动正在学的活跃会话。

    背景：settle_stale_sessions() 只在 Flask 进程启动时跑一次。
    如果服务一直不 Reload，一条被浏览器杀掉、又没触发 end 的会话，
    就会永远挂着 ended_at=NULL，直到用户下次打开页面。
    为了让数据随时都是干净的，在读路径（心跳 / 学习记录 / 学习时长）
    上顺手做一次懒结算。

    判定"僵尸"的依据是 **last_beat_at**（最后一次心跳时刻）：
      · 距最后一次心跳已超过 SESSION_HEARTBEAT_GRACE（3 分钟）→ 判为僵尸，结算
      · 3 分钟内还在心跳 → 视为活跃，跳过（否则会把正在学的会话误关）
      · last_beat_at 为空（老数据 / 调度任务建的）→ 属于历史遗留，
        按同样规则用 started_at 兜底判断

    返回结算掉的会话数。
    """
    now = now_cn()
    sql = ("SELECT id, started_at, duration, last_beat_at FROM sessions "
           "WHERE ended_at IS NULL")
    args = []
    if student_id:
        sql += " AND student_id=?"
        args.append(student_id)
    try:
        rows = query(sql, tuple(args))
    except Exception:
        return 0

    stale_ids = []
    for r in rows:
        beat = _parse_dt(r.get("last_beat_at")) or _parse_dt(r.get("started_at"))
        if not beat:
            continue
        if (now - beat).total_seconds() <= SESSION_HEARTBEAT_GRACE:
            continue          # 还在心跳，活跃会话，跳过
        stale_ids.append(r["id"])

    if not stale_ids:
        return 0
    return _settle_open_sessions(
        student_id=student_id,
        exclude_sid=None,
        keep_reported=True,
        only_ids=stale_ids,
    )


def _settle_stale_except(student_id, keep_sid):
    """
    settle_zombie_sessions 的变体：结算时**排除 keep_sid**（刚发心跳的那条）。

    用在心跳处理里——每收到一次心跳，顺手把同学生名下已失联的旧会话结清，
    但绝不碰正在发心跳的这一条。
    """
    now = now_cn()
    try:
        rows = query("SELECT id, started_at, last_beat_at FROM sessions "
                     "WHERE ended_at IS NULL AND student_id=?", (student_id,))
    except Exception:
        return 0
    stale_ids = []
    for r in rows:
        if r["id"] == keep_sid:
            continue
        beat = _parse_dt(r.get("last_beat_at")) or _parse_dt(r.get("started_at"))
        if not beat:
            continue
        if (now - beat).total_seconds() <= SESSION_HEARTBEAT_GRACE:
            continue
        stale_ids.append(r["id"])
    if not stale_ids:
        return 0
    return _settle_open_sessions(student_id=student_id, keep_reported=True,
                                 only_ids=stale_ids)


def get_study_time(student_id="default", days=7):
    # 懒结算：把已失联的僵尸会话先结清，保证统计口径随时正确
    try:
        settle_zombie_sessions(student_id)
    except Exception:
        pass
    since = (now_cn().date() - timedelta(days=days - 1)).isoformat()
    rows = query("""
        SELECT SUBSTR(started_at,1,10) AS d, SUM(duration) AS secs, COUNT(*) AS rounds
        FROM sessions
        WHERE student_id=? AND SUBSTR(started_at,1,10) >= ?
        GROUP BY SUBSTR(started_at,1,10)
        ORDER BY d
    """, (student_id, since))
    out = []
    for r in rows:
        secs = int(r.get("secs") or 0)
        # 剔除「误点/秒退」噪声：单次会话过短不计，但如果当天总量已达标则保留
        disp = f"{secs}秒" if secs < 60 else f"{round(secs/60,1)}分"
        out.append({
            "date": r["d"],
            "seconds": secs,
            "minutes": round(secs / 60, 1),
            "display": disp,
            "rounds": r["rounds"],
        })
    return out



def get_session_log(student_id="default", days=7, min_seconds=5):
    """
    学习记录明细：返回最近 N 天每次学习会话的起止时间与时长。

    每条记录形如：
        {"date": "2026-10-12", "start": "09:15", "end": "09:42",
         "seconds": 1620, "duration": "27分", "ongoing": False}

    - start/end 为北京时间 HH:MM（跨天会话的 end 会带次日标记）
    - 过滤掉时长低于 min_seconds 的误点噪声
    - 未结束的会话 ongoing=True；但若「最后心跳」已超过宽限期（3 分钟），
      视为页面已被杀掉（iOS 常见），改按「开始 + 已上报时长」显示，不再挂着"进行中"
    """
    # 懒结算：把已失联的僵尸会话先结清（只动超时未心跳的，不碰活跃会话）
    try:
        settle_zombie_sessions(student_id)
    except Exception:
        pass
    since = (now_cn().date() - timedelta(days=days - 1)).isoformat()
    rows = query("""
        SELECT id, started_at, ended_at, duration, last_beat_at
        FROM sessions
        WHERE student_id=? AND SUBSTR(started_at,1,10) >= ?
        ORDER BY started_at DESC
    """, (student_id, since))

    now = now_cn()
    out = []
    for r in rows:
        secs = int(r.get("duration") or 0)
        if secs < min_seconds:
            continue
        st = _parse_dt(r["started_at"])
        if not st:
            continue
        et = _parse_dt(r["ended_at"])
        ongoing = et is None
        if ongoing:
            # 判定僵尸同样以 **last_beat_at（最后一次心跳）** 为准，
            # 与 settle_zombie_sessions() 口径完全一致：
            #   最后心跳距今 > 宽限期 → 页面已被杀 / 手机关机，视为已结束，
            #   结束点 = 开始 + 已上报时长（绝不显示成跨夜长条）
            #   最后心跳仍在宽限期内 → 真的还在学，结束点取当前时间
            beat = _parse_dt(r.get("last_beat_at")) or st
            if (now - beat).total_seconds() > SESSION_HEARTBEAT_GRACE:
                et = st + timedelta(seconds=secs)   # 僵尸会话
                ongoing = False
            else:
                et = now                            # 真的还在学

        # 时长展示
        if secs < 60:
            disp = f"{secs}秒"
        elif secs < 3600:
            disp = f"{round(secs/60,1):g}分"
        else:
            h = secs // 3600
            m = round((secs % 3600) / 60)
            disp = f"{h}时{m}分" if m else f"{h}时"

        # 结束时间若落在次日，加 "+1" 标记
        day = st.strftime("%Y-%m-%d")
        end_label = et.strftime("%H:%M")
        if et.strftime("%Y-%m-%d") != day:
            end_label += "(+%d天)" % ((et.date() - st.date()).days)

        out.append({
            "date": day,
            "start": st.strftime("%H:%M"),
            "end": end_label,
            "seconds": secs,
            "duration": disp,
            "ongoing": ongoing,
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
    # 用中国时区的"今天"，否则服务器 UTC 时早上学习会被判成昨天，连续天数断档
    today_d = now_cn().date()
    today = today_d.isoformat()
    yest = (today_d - timedelta(days=1)).isoformat()
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
             now_cn().isoformat(timespec="seconds")))


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
    now = now_cn().isoformat(timespec="seconds")
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


# =========================================================
# 匿名体验（数据只存浏览器本地，后端不做持久化）
# ---------------------------------------------------------
# 设计：匿名模式下前端把作答记录放在 localStorage，
# 并在每次请求时通过 X-ZSD-Anon 头把这份记录带给后端，
# 后端在"内存里临时算一遍"统计结果返回，不落库、不与人共享。
# 这样既能让匿名用户看到进度/错题，又保证数据彼此隔离。
# =========================================================
def _anon_rows(payload):
    """把前端传来的匿名记录规整成统一行格式"""
    rows = []
    for a in (payload or {}).get("answers", []) or []:
        try:
            rows.append({
                "course_id": str(a.get("cid") or ""),
                "qid": a.get("qid"),
                "question_idx": a.get("idx"),
                "chosen": int(a.get("chosen", -1)),
                "correct": 1 if a.get("correct") else 0,
                "answered_at": a.get("at") or "",
                "mode": a.get("mode") or "basic",
            })
        except (TypeError, ValueError):
            continue
    return rows


def _anon_latest(rows):
    """同一题同一模式只保留最后一次作答（qid 优先，否则按 idx）"""
    latest = {}
    for r in rows:
        base = ("q", r["qid"]) if r.get("qid") else \
               ("i", r["course_id"], r.get("question_idx"))
        key = base + (r.get("mode") or "basic",)
        old = latest.get(key)
        if old is None or (r.get("answered_at") or "") >= (old.get("answered_at") or ""):
            latest[key] = r
    return list(latest.values())


def anon_stats(payload):
    rows = _anon_latest(_anon_rows(payload))
    by_course, by_mode, total, right = {}, {}, 0, 0
    for r in rows:
        cid = r["course_id"]
        mode = r.get("mode") or "basic"
        by_course.setdefault(cid, {"total": 0, "right": 0})
        by_mode.setdefault(mode, {"total": 0, "right": 0})
        by_course[cid]["total"] += 1
        by_mode[mode]["total"] += 1
        total += 1
        if r["correct"]:
            by_course[cid]["right"] += 1
            by_mode[mode]["right"] += 1
            right += 1
    for d in list(by_course.values()) + list(by_mode.values()):
        d["rate"] = round(d["right"] / d["total"] * 100) if d["total"] else 0
    return {"total": total, "right": right,
            "rate": round(right / total * 100) if total else 0,
            "by_course": by_course, "by_mode": by_mode,
            "wrong_count": total - right}


def anon_course_answers(course_id, payload):
    rows = _anon_latest(_anon_rows(payload))
    by_qid, by_idx = {}, {}
    by_qid_basic, by_qid_random = {}, {}
    for r in rows:
        if r["course_id"] != course_id:
            continue
        mode = r.get("mode") or "basic"
        info = {"chosen": r["chosen"], "correct": bool(r["correct"]), "mode": mode}
        if r.get("qid"):
            by_qid[r["qid"]] = info
            if mode == "random":
                by_qid_random[r["qid"]] = info
            elif mode == "basic":
                by_qid_basic[r["qid"]] = info
        if r.get("question_idx") is not None:
            by_idx[str(r["question_idx"])] = info
    return {"byQid": by_qid, "byIdx": by_idx,
            "byQidBasic": by_qid_basic, "byQidRandom": by_qid_random}


def anon_wrong_questions(payload):
    """
    错题列表：带上题干/选项/解析，方便前端直接渲染（按 qid 去重）。
    与登录用户一致：曾做错即保留，最近一次答对则标记 fixed=True。
    """
    all_latest = _anon_latest(_anon_rows(payload))
    latest_by_qid = {}
    for r in all_latest:
        if not r.get("qid"):
            continue
        cur = latest_by_qid.get(r["qid"])
        if cur is None or (r.get("answered_at") or "") >= (cur.get("answered_at") or ""):
            latest_by_qid[r["qid"]] = r

    rows = [r for r in all_latest if not r["correct"]]
    rows.sort(key=lambda r: r.get("answered_at") or "", reverse=True)
    out, seen = [], set()
    for r in rows:
        if not r.get("qid") or r["qid"] in seen:
            continue
        seen.add(r["qid"])
        q = _get_question_row(r["qid"])
        if not q:
            continue
        latest = latest_by_qid.get(r["qid"]) or r
        out.append({
            "qid": q["qid"], "course_id": q["course_id"],
            "courseTitle": _course_title(q["course_id"]),
            "stem": q["stem"], "options": q["options"],
            "answer": q["answer"], "explain": q.get("explain", ""),
            "img": q.get("img", ""), "svg": q.get("svg", ""),
            "chosen": r["chosen"], "answered_at": r["answered_at"],
            "mode": r.get("mode") or "basic",
            "fixed": bool(latest.get("correct")),
        })
    return out


def _course_title(cid):
    rows = query("SELECT title FROM courses WHERE cid=?", (cid,))
    return rows[0]["title"] if rows else cid


def _get_question_row(qid):
    """按 qid 取题目原文（db 层自用，避免依赖 bank.py）"""
    rows = query("SELECT * FROM questions WHERE qid=? AND active=1", (qid,))
    if not rows:
        return None
    r = rows[0]
    try:
        options = json.loads(r.get("options") or "[]")
    except (ValueError, TypeError):
        options = []
    return {"qid": r["qid"], "course_id": r["course_id"], "stem": r["stem"],
            "options": options, "answer": r["answer"], "explain": r.get("explain", ""),
            "img": r.get("img") or "", "svg": r.get("svg") or ""}


def anon_study_time(payload, days=7):
    """按日期聚合匿名记录的条数（近似学习时长：每题折算 40 秒）"""
    rows = _anon_rows(payload)
    per_day = {}
    for r in rows:
        d = (r.get("answered_at") or "")[:10]
        if d:
            per_day[d] = per_day.get(d, 0) + 1
    since = (now_cn().date() - timedelta(days=days - 1)).isoformat()
    out = []
    cur = now_cn().date() - timedelta(days=days - 1)
    while cur <= now_cn().date():
        ds = cur.isoformat()
        n = per_day.get(ds, 0)
        secs = n * 40 if ds >= since else 0
        disp = f"{secs}秒" if secs < 60 else f"{round(secs/60,1)}分"
        out.append({"date": ds, "minutes": round(secs / 60), "seconds": secs,
                    "rounds": n, "display": disp})
        cur += timedelta(days=1)
    return out


def anon_streak(payload):
    """连续答题天数"""
    days = sorted({(r.get("answered_at") or "")[:10] for r in _anon_rows(payload)} - {""})
    if not days:
        return 0
    streak, cur = 0, now_cn().date()
    s = set(days)
    while cur.isoformat() in s:
        streak += 1
        cur -= timedelta(days=1)
    return streak


def anon_tasks(payload):
    result = {}
    for t in (payload or {}).get("tasks", []) or []:
        result.setdefault(str(t.get("day")), {})[str(t.get("idx"))] = bool(t.get("done"))
    return result


def anon_reset_course(course_id, payload):
    """返回"清除该课记录后"的新记录（前端覆盖本地缓存即可）"""
    keep = [a for a in (payload or {}).get("answers", []) or []
            if str(a.get("cid")) != str(course_id)]
    return {"answers": keep}


# ---------------- 匿名模式：论述题 ----------------
def _anon_essay_rows(course_id, payload):
    """匿名记录里，某课论述题（mode='essay'）的所有行，按时间正序"""
    rows = [r for r in _anon_rows(payload)
            if r["course_id"] == course_id and (r.get("mode") or "") == "essay"
            and r.get("qid")]
    rows.sort(key=lambda r: r.get("answered_at") or "")
    return rows


def anon_essay_order(course_id, payload):
    """该课已做过的论述题 qid（按首次作答时间正序），用于每日选题避开重复"""
    seen, order = set(), []
    for r in _anon_essay_rows(course_id, payload):
        if r["qid"] not in seen:
            seen.add(r["qid"])
            order.append(r["qid"])
    return order


def anon_essay_day(course_id, day_key, payload):
    """某天该课论述题的作答记录（匿名），没有则 None"""
    rows = [r for r in _anon_essay_rows(course_id, payload)
            if str(r.get("answered_at") or "")[:10] == day_key]
    if not rows:
        return None
    # 取当天最后一条（同一题当天重复作答时以最后一次为准）
    latest = {}
    for r in rows:
        latest[r["qid"]] = r
    chosen = sorted(latest.values(), key=lambda r: r["answered_at"])[-1]
    return {"qid": chosen["qid"], "chosen": chosen["chosen"],
            "correct": bool(chosen["correct"]), "answeredAt": chosen["answered_at"]}


def anon_essay_stats(payload):
    """匿名用户的论述题统计（同一道题只算最近一次）"""
    latest = {}
    for r in _anon_rows(payload):
        if (r.get("mode") or "") != "essay" or not r.get("qid"):
            continue
        old = latest.get(r["qid"])
        if old is None or (r.get("answered_at") or "") >= (old.get("answered_at") or ""):
            latest[r["qid"]] = r
    vals = list(latest.values())
    total = len(vals)
    right = sum(1 for r in vals if r["correct"])
    days = len({(r.get("answered_at") or "")[:10] for r in vals} - {""})
    return {"total": total, "right": right, "wrong": total - right,
            "rate": round(right / total * 100) if total else 0, "days": days}


def anon_essay_log(course_id, payload, limit=60):
    """匿名用户该课论述题的历史作答（按时间倒序，同一题取最近一次）"""
    latest = {}
    for r in _anon_essay_rows(course_id, payload):
        old = latest.get(r["qid"])
        if old is None or (r.get("answered_at") or "") >= (old.get("answered_at") or ""):
            latest[r["qid"]] = r
    vals = sorted(latest.values(), key=lambda r: r.get("answered_at") or "", reverse=True)
    return [{"qid": r["qid"], "chosen": r["chosen"], "correct": bool(r["correct"]),
             "answeredAt": r["answered_at"],
             "date": str(r.get("answered_at") or "")[:10]} for r in vals[:limit]]

