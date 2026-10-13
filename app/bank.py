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
MODE_REDO = "redo"       # 错题重做
MODE_ESSAY = "essay"     # 每日论述题（材料题择优，独立统计）

# 分层（题库难度）
TIER_BASIC = "basic"     # 基础题库：跟课本，必做
TIER_ADVANCED = "advanced"  # 强化题库：加深，选做
TIER_SPRINT = "sprint"   # 冲刺题库：考前（预留）
TIER_ESSAY = "essay"     # 论述题库：每天一题，独立于自测


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
        "subject": r.get("subject", "政治"),
        "tier": r.get("tier", TIER_BASIC),
        "qtype": r.get("qtype", "choice"),
        "q": r["stem"],
        "o": options,
        "a": r["answer"],
        "e": r.get("explain", ""),
        "img": r.get("img") or "",
        "svg": r.get("svg") or "",
        # 论述题专用：材料原文（普通选择题为空）
        "material": r.get("material") or "",
        # 论述题专用：择优解析（为什么这个选项最贴切，其余差在哪）
        "why": r.get("why") or "",
    }
# =========================================================
# 播种：把代码里的题库写入数据库（幂等，重复执行不会重复插入）
# =========================================================
def purge_legacy_courses(courses):
    """
    幂等清理：删除题库中"已不在当前 COURSES 里"的历史课程。

    背景：人教版《中外历史纲要（上）》2024 版目录调整，原 h11/h12 被合并为
    新的 h11，原 h13-h15 前移为 h12-h14，总课数由 25 变为 39。旧课程若
    残留在 courses 表里，会继续出现在课程列表中，因此需要按 cid 差集清理。

    只删 courses 表中多余的课程行，不动 questions/answers 表 —— 旧题的
    qid 仍可通过错题本追溯，且清理是幂等的（重复执行结果一致）。
    返回被清理的 cid 列表。
    """
    valid = {c["id"] for c in courses}
    if not valid:
        return []
    try:
        rows = query("SELECT cid FROM courses")
    except Exception:
        return []
    stale = [r["cid"] for r in rows if r.get("cid") not in valid]
    for cid in stale:
        execute("DELETE FROM courses WHERE cid=?", (cid,))
    return stale


def seed_from_courses(courses):
    """
    从 courses.py 的 COURSES 结构导入题目。

    每个课程可有三组题：
      · quiz       → tier=basic，qid=<cid>_01、_02…（基础巩固，固定顺序）
      · quiz_adv   → tier=advanced，qid=<cid>_a01、_a02…（强化训练，随机抽题）
      · quiz_essay → tier=essay，qid=<cid>_e01、_e02…（每日论述，独立统计）

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
        # 三组题：基础 + 强化 + 论述
        groups = ((c.get("quiz", []), TIER_BASIC, ""),
                  (c.get("quiz_adv", []), TIER_ADVANCED, "a"),
                  (c.get("quiz_essay", []), TIER_ESSAY, "e"))
        for qlist, tier, prefix in groups:
            for i, q in enumerate(qlist):
                qid = f"{cid}_{prefix}{i+1:02d}"
                exists = query("SELECT qid FROM questions WHERE qid=?", (qid,))
                if exists:
                    skipped += 1
                    continue
                execute(
                    "INSERT INTO questions(qid,course_id,subject,tier,qtype,stem,options,answer,"
                    "explain,img,svg,material,why,sort_order,active,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                    (qid, cid, c.get("subject", "政治"), tier,
                     q.get("qtype", "choice"),
                     q["q"], json.dumps(q["o"], ensure_ascii=False), q["a"],
                     q.get("e", ""), q.get("img", ""), q.get("svg", ""),
                     q.get("material", ""), q.get("why", ""), i, now)
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
    """
    取某课程题目（按 sort_order 固定顺序）。

    注意：不传 tier 时默认**排除论述题**（tier=essay）。
    论述题有自己的每日推送与统计体系，混进自测题里会让
    「每课 20 题」的口径和正确率统计全部失真。
    """
    if tier:
        rows = query("SELECT * FROM questions WHERE course_id=? AND tier=? AND active=1 "
                     "ORDER BY sort_order, qid", (course_id, tier))
    else:
        rows = query("SELECT * FROM questions WHERE course_id=? AND active=1 "
                     "AND tier<>? ORDER BY sort_order, qid", (course_id, TIER_ESSAY))
    return [_row_to_q(r) for r in rows]


def get_question(qid):
    """按 qid 取单题"""
    rows = query("SELECT * FROM questions WHERE qid=? AND active=1", (qid,))
    return _row_to_q(rows[0]) if rows else None


def get_essay_questions(course_id):
    """取某课的论述题库（按 sort_order，供每日轮换与历史查看）"""
    rows = query("SELECT * FROM questions WHERE course_id=? AND tier=? AND active=1 "
                 "ORDER BY sort_order, qid", (course_id, TIER_ESSAY))
    return [_row_to_q(r) for r in rows]


def count_questions(course_id, tier=None):
    """
    统计题目数。
    不传 tier 时排除论述题，与 get_questions 口径保持一致
    （否则课程列表会显示「自测 50 题」，实际自测只有 20 题）。
    """
    if tier:
        rows = query("SELECT COUNT(*) AS n FROM questions WHERE course_id=? AND tier=? AND active=1",
                     (course_id, tier))
    else:
        rows = query("SELECT COUNT(*) AS n FROM questions WHERE course_id=? AND active=1 AND tier<>?",
                     (course_id, TIER_ESSAY))
    return rows[0]["n"] if rows else 0


# =========================================================
# 出题（核心：支持固定 / 随机两种模式）
# =========================================================
def build_quiz(course_id, mode=MODE_BASIC, limit=None, tier=None):
    """
    生成一套题目。

      mode=basic   → 基础巩固：只出 tier=basic 的题，按固定顺序
      mode=random  → 强化训练：随机抽 limit 道。优先从 tier=advanced 抽，
                     不够时再用 basic 补足（保证凑满 limit 道）
      tier         → 显式指定只出某一层（覆盖默认行为）
    返回题目列表（列表顺序即出题顺序）
    """
    if mode == MODE_RANDOM:
        n = limit or 10
        if tier:
            pool = get_questions(course_id, tier)
            random.shuffle(pool)
            return pool[:n]
        adv = get_questions(course_id, TIER_ADVANCED)
        bas = get_questions(course_id, TIER_BASIC)
        # 先抽强化题；不足再用基础题补足，避免凑不满
        if len(adv) >= n:
            chosen = random.sample(adv, n)
        else:
            chosen = adv[:]
            need = n - len(chosen)
            if need > 0:
                chosen += random.sample(bas, min(need, len(bas)))
        random.shuffle(chosen)
        return chosen

    # basic：固定顺序，只出基础层（默认）
    qs = get_questions(course_id, tier if tier else TIER_BASIC)
    return qs[:limit] if limit else qs


def qid_by_idx(course_id, idx, mode=MODE_BASIC):
    """
    按下标反查题库里的 qid（用于把"只传 questionIdx"的旧调用统一到 qid 主键）。

    错题本按 qid 归并同一道题；若作答记录一半带 qid、一半只有 idx，
    同一道题会重复出现。这里尽量补全 qid，补不上返回 None（退化为旧行为）。
    """
    if idx is None or idx < 0:
        return None
    qs = build_quiz(course_id, mode=mode)
    if 0 <= idx < len(qs):
        return qs[idx].get("qid")
    return None


def available_modes(course_id):
    """该课程可用的出题模式（供前端展示）"""
    basic = count_questions(course_id, TIER_BASIC)
    advanced = count_questions(course_id, TIER_ADVANCED)
    modes = []
    if basic:
        modes.append({"mode": MODE_BASIC, "label": "基础巩固",
                      "desc": "按课本顺序做固定题", "count": basic})
    if advanced:
        modes.append({"mode": MODE_RANDOM, "label": "强化训练",
                      "desc": "随机抽题，每次不同", "count": advanced})
    elif basic:
        # 还没有强化题时，随机模式也可用（从基础题库抽）
        modes.append({"mode": MODE_RANDOM, "label": "随机练习",
                      "desc": "从题库中随机抽题，每次不同", "count": basic})
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
# 每日论述题
# =========================================================
def essay_courses():
    """返回所有带论述题库的课程（cid 列表，按课程顺序）"""
    rows = query("SELECT DISTINCT course_id FROM questions WHERE tier=? AND active=1",
                 (TIER_ESSAY,))
    return sorted(r["course_id"] for r in rows)


def pick_daily_essay(course_id, day_key, answered_order=None):
    """
    为某一天挑一道论述题。

    ⚠️ 核心原则：**当天的题完全由日期决定，与学生做过什么无关。**

    这一点必须说清楚，因为踩过坑：
      早先的实现是"从第一道没做过的题里挑"，结果学生答完当天的题，
      再刷新页面就会跳到下一道（那道题进了"已做"集合）——今天的题当天就变了，
      学生看到的是另一道题，解析也对不上号。
      现在的实现把日期直接映射到题库下标，当天怎么刷新都是同一道。

    规则（对应需求"每天的题不要重复"）：
      用「年*372 + 月*31 + 日」对题库长度取模得到一个**稳定下标**。
      题库 N 道时，日期每 +1 天下标 +1，因此连续 N 天各出不重复的一道，
      第 N+1 天才开始下一轮 —— 一个月的题库正好够轮流一个月。

    参数:
      course_id      : 课程 id，如 p1
      day_key        : 日期字符串 YYYY-MM-DD（按中国时区零点切换）
      answered_order : 已做过的 qid 列表。**仅用于统计"还剩多少道没做"**，
                       不参与选题（参与就会破坏当天稳定性）。
    返回题目字典，题库为空时返回 None。
    """
    pool = get_essay_questions(course_id)
    if not pool:
        return None

    n = len(pool)
    try:
        y, m, d = (int(x) for x in str(day_key).split("-")[:3])
        idx = (y * 372 + m * 31 + d) % n
    except (ValueError, TypeError):
        idx = random.randrange(n)
    return pool[idx]


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
