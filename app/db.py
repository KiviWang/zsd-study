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
            (sid, name, color, datetime.now().isoformat(timespec="seconds"),
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
    now = datetime.now().isoformat(timespec="seconds")
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

    now = datetime.now().isoformat(timespec="seconds")
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


def _all_latest_answers(student_id="default", where_extra=""):
    """
    合并"按qid"与"按idx"两条路径的最新作答，避免遗漏历史数据。
    注意：分组键为 (qid, mode)，同一道题在基础/随机下会各返回一行。
    """
    a = _latest_answers_where(where_extra, (student_id, student_id), by_qid=True)
    b = _latest_answers_where(where_extra, (student_id, student_id), by_qid=False)
    return a + b


def get_wrong_questions(student_id="default"):
    """
    错题本数据源。

    规则（对应需求"错题重做答对后保留但标记已订正"）：
      · 只要某题曾经做错过，就留在错题本里
      · 同一道题在基础/随机/重做下都算同一道题，按 qid 去重
        （qid 缺失的历史数据退化为 course_id+idx）
      · 若最近一次作答（任意模式）已答对，则 fixed=True（标记"已订正"）
      · modes 记录该题曾出错过的模式来源，wrong_times 记录出错次数

    这样错题本不会因为做对就消失，学生能看到自己订正过的足迹。
    """
    all_rows = _all_latest_answers(student_id)
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
            "options": options, "answer": r["answer"], "explain": r.get("explain", "")}


def anon_study_time(payload, days=7):
    """按日期聚合匿名记录的条数（近似学习时长：每题折算 40 秒）"""
    rows = _anon_rows(payload)
    per_day = {}
    for r in rows:
        d = (r.get("answered_at") or "")[:10]
        if d:
            per_day[d] = per_day.get(d, 0) + 1
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    out = []
    cur = date.today() - timedelta(days=days - 1)
    while cur <= date.today():
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
    streak, cur = 0, date.today()
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
