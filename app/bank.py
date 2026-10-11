"""
政史地学习平台 · 题库层（内容与逻辑分离）

设计目标：
  - 题目存数据库，代码只负责"读题、出题、判分"
  - 以后加题库（强化/冲刺）、加科目，只加数据不改代码
  - 支持两种出题模式：
      basic    基础：按固定顺序出题（跟着课本走的巩固题）
      random   强化：随机抽题（每次不同，用于加深训练）

qid 命名规则：<课程id>_<序号>，如 p1_01、p2_10
  —— 题目一旦入库，qid 永不改变，保证答题记录可追溯。
"""
import json
import random
from datetime import datetime

from db import query, execute

# 出题模式
MODE_BASIC = "basic"     # 固定顺序（基础巩固）
MODE_RANDOM = "random"   # 随机抽题（强化训练）

# 分层（题库难度）
TIER_BASIC = "basic"     # 基础题库：跟课本，必做
TIER_ADVANCED = "advanced"  # 强化题库：加深，选做
TIER_SPRINT = "sprint"   # 冲刺题库：考前（预留）


def _row_to_q(r):
    """数据库行 → 题目字典"""
    opts = r.get("options") or "[]"
    try:
        options = json.loads(opts)
    except (ValueError, TypeError):
        options = []
    return {
        "qid": r["qid"],
        "courseId": r["course_id"],
        "tier": r.get("tier", TIER_BASIC),
        "qtype": r.get("qtype", "choice"),
        "q": r["stem"],
        "o": options,
        "a": r["answer"],
        "e": r.get("explain", ""),
    }


# =========================================================
# 播种：把代码里的题库写入数据库（幂等，重复执行不会重复插入）
# =========================================================
def seed_from_courses(courses):
    """
    从 courses.py 的 COURSES 结构导入题目。
    已有同 qid 的题目跳过（不覆盖），保证人工在库里改过的题不被冲掉。
    返回 (新增数, 跳过数)
    """
    added = skipped = 0
    now = datetime.now().isoformat(timespec="seconds")
    for c in courses:
        cid = c["id"]
        # 课程元信息
        execute(
            "INSERT OR IGNORE INTO courses(cid,no,subject,title,subtitle,color,sort_order,active) "
            "VALUES(?,?,?,?,?,?,?,1)",
            (cid, c.get("no", ""), c.get("subject", "政治"), c.get("title", ""),
             c.get("subtitle", ""), c.get("color", "#2f6fed"), _course_order(cid))
        )
        for i, q in enumerate(c.get("quiz", [])):
            qid = f"{cid}_{i+1:02d}"
            exists = query("SELECT qid FROM questions WHERE qid=?", (qid,))
            if exists:
                skipped += 1
                continue
            execute(
                "INSERT INTO questions(qid,course_id,subject,tier,qtype,stem,options,answer,"
                "explain,sort_order,active,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)",
                (qid, cid, c.get("subject", "政治"), TIER_BASIC, "choice",
                 q["q"], json.dumps(q["o"], ensure_ascii=False), q["a"],
                 q.get("e", ""), i, now)
            )
            added += 1
    return added, skipped


def _course_order(cid):
    """从 p1/p2 里取数字作为排序依据"""
    digits = "".join(ch for ch in cid if ch.isdigit())
    return int(digits) if digits else 0


# =========================================================
# 读取题目
# =========================================================
def get_questions(course_id, tier=None):
    """取某课程题目（按 sort_order 固定顺序）"""
    if tier:
        rows = query("SELECT * FROM questions WHERE course_id=? AND tier=? AND active=1 "
                     "ORDER BY sort_order, qid", (course_id, tier))
    else:
        rows = query("SELECT * FROM questions WHERE course_id=? AND active=1 "
                     "ORDER BY sort_order, qid", (course_id,))
    return [_row_to_q(r) for r in rows]


def get_question(qid):
    """按 qid 取单题"""
    rows = query("SELECT * FROM questions WHERE qid=? AND active=1", (qid,))
    return _row_to_q(rows[0]) if rows else None


def count_questions(course_id, tier=None):
    """统计题目数"""
    if tier:
        rows = query("SELECT COUNT(*) AS n FROM questions WHERE course_id=? AND tier=? AND active=1",
                     (course_id, tier))
    else:
        rows = query("SELECT COUNT(*) AS n FROM questions WHERE course_id=? AND active=1",
                     (course_id,))
    return rows[0]["n"] if rows else 0


# =========================================================
# 出题（核心：支持固定 / 随机两种模式）
# =========================================================
def build_quiz(course_id, mode=MODE_BASIC, limit=None, tier=None):
    """
    生成一套题目。
      mode=basic   → 固定顺序（默认全部基础题）
      mode=random  → 随机抽取 limit 道（优先强化题，不足则用基础题补）
      tier         → 指定只出某一层
    返回题目列表（列表顺序即出题顺序）
    """
    if mode == MODE_RANDOM:
        pool = []
        if tier:
            pool = get_questions(course_id, tier)
        else:
            # 强化题优先，基础题补足
            adv = get_questions(course_id, TIER_ADVANCED)
            bas = get_questions(course_id, TIER_BASIC)
            pool = adv + bas
        if not pool:
            return []
        random.shuffle(pool)
        return pool[:limit] if limit else pool

    # basic：固定顺序
    qs = get_questions(course_id, tier)
    return qs[:limit] if limit else qs


def available_modes(course_id):
    """该课程可用的出题模式（供前端展示）"""
    total = count_questions(course_id)
    advanced = count_questions(course_id, TIER_ADVANCED)
    modes = []
    if total:
        modes.append({"mode": MODE_BASIC, "label": "基础巩固",
                      "desc": "按课本顺序做固定题", "count": total})
    if advanced:
        modes.append({"mode": MODE_RANDOM, "label": "强化训练",
                      "desc": "从强化题中随机抽题，每次不同", "count": advanced})
    elif total:
        # 还没有强化题时，随机模式也可用（从现有题库抽）
        modes.append({"mode": MODE_RANDOM, "label": "随机练习",
                      "desc": "从题库中随机抽题，每次不同", "count": total})
    return modes


# =========================================================
# 课程列表（来自题库表，便于以后动态增科）
# =========================================================
def get_course_meta(course_id=None):
    if course_id:
        rows = query("SELECT * FROM courses WHERE cid=? AND active=1", (course_id,))
        return rows[0] if rows else None
    return query("SELECT * FROM courses WHERE active=1 ORDER BY sort_order, cid")


# =========================================================
# 统计（每题正确率，用于以后做"易错题"分析）
# =========================================================
def question_stats(student_id="default"):
    """每道题的作答次数与正确次数"""
    rows = query("""
        SELECT qid, COUNT(*) AS times, SUM(correct) AS right_n
        FROM answers WHERE student_id=? AND qid IS NOT NULL
        GROUP BY qid
    """, (student_id,))
    out = {}
    for r in rows:
        t = r["times"] or 0
        rt = r["right_n"] or 0
        out[r["qid"]] = {"times": t, "right": rt,
                         "rate": round(rt / t * 100) if t else 0}
    return out
