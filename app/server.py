"""
政史地学习平台 · Flask 后端服务
学生端：免登录，直接学习
家长端：密码登录（默认 888888），查看学习数据
"""
import os
import sys
import json
from flask import Flask, jsonify, request, send_from_directory, session

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import (init_db, get_config, set_config, record_answer, get_wrong_questions,
                get_stats, start_session, end_session, get_study_time, get_streak,
                record_video, get_video_stats, set_task, get_tasks, get_course_answers,
                reset_course_answers, USE_TURSO as _USE_TURSO,
                list_students, get_student, create_student, update_student,
                touch_student, delete_student, create_token, resolve_token, revoke_token,
                register_student, verify_login, get_student_by_username,
                ensure_anonymous_student,
                anon_stats, anon_course_answers, anon_wrong_questions,
                anon_study_time, anon_streak, anon_tasks, anon_reset_course)
from courses import COURSES, DAILY_PLAN, get_course
import bank
from bank import (seed_from_courses, get_questions, get_question, count_questions,
                  build_quiz, available_modes, MODE_BASIC, MODE_RANDOM)
from bank import qid_by_idx as _resolve_qid_by_idx

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "zsd-study-platform-2026-local")

DEFAULT_STUDENT = "default"   # 旧数据的归属者（首个学生）
TOKEN_HEADER = "X-ZSD-Token"
ANON_HEADER = "X-ZSD-Anon"    # 匿名模式：本地记录随请求带上，不落库

# 关键：模块加载时即建表，保证 gunicorn 等 WSGI 启动方式也能初始化数据库
init_db()

# 题库播种：把 courses.py 里的题目同步进数据库（幂等，已存在的跳过）
try:
    _added, _skipped = seed_from_courses(COURSES)
    if _added:
        print(f"[题库] 新导入 {_added} 道题（已存在 {_skipped} 道）")
except Exception as _e:  # 播种失败不应阻断服务启动
    print(f"[题库] 播种异常（忽略）：{_e}")


# =========================================================
# 当前学生解析（多用户核心）
# =========================================================
ANON_SID = "__anon__"   # 匿名哨兵：匿名请求不落库，只走内存计算


def _anon_payload():
    """匿名用户把本地记录通过请求体 or 头带上来（不落库）"""
    body = request.get_json(silent=True) or {}
    if isinstance(body, dict):
        p = body.get("anon")
        if p:
            return p
    hdr = request.headers.get(ANON_HEADER)
    if hdr:
        try:
            return json.loads(hdr)
        except Exception:
            return None
    return None


def current_student():
    """
    从请求头 X-ZSD-Token 解析当前学生。
    匿名体验（anon: 前缀）返回哨兵 ANON_SID，统计由 anon_* 系列函数内存计算。
    未登录时返回 DEFAULT_STUDENT（保证旧用法/未登录访问不崩）。
    """
    token = request.headers.get(TOKEN_HEADER) or request.args.get("token") \
        or (request.get_json(silent=True) or {}).get("token")
    if token and str(token).startswith("anon:"):
        return ANON_SID
    sid = resolve_token(token) if token else None
    return sid or DEFAULT_STUDENT


def _require_student():
    """需要登录的接口用这个：返回 sid 或 None"""
    token = request.headers.get(TOKEN_HEADER)
    if token and str(token).startswith("anon:"):
        return None
    return resolve_token(token) if token else None


def _is_anon():
    """当前请求是否为匿名模式"""
    return current_student() == ANON_SID


def _stu_brief(stu):
    """返回给前端的精简学生信息（不含密码散列等敏感字段）"""
    if not stu:
        return None
    return {"sid": stu["sid"], "name": stu["name"],
            "color": stu.get("color", "#2f6fed"),
            "phone": stu.get("phone") or "",
            "username": stu.get("username") or "",
            "anonymous": bool(stu.get("is_anonymous"))}


# ================= 页面 =================
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/admin")
def admin_page():
    return send_from_directory(app.static_folder, "admin.html")


# ================= 登录 / 身份 =================
@app.route("/api/login", methods=["POST"])
def api_login():
    """
    学生登录：登录码即身份（如 xiaoming）。
    成功后返回 token，前端保存后随请求头 X-ZSD-Token 携带。
    """
    data = request.get_json() or {}
    code = (data.get("code") or "").strip().lower()
    if not code:
        return jsonify({"ok": False, "msg": "请输入登录码"}), 400

    stu = get_student(code)
    if not stu:
        return jsonify({"ok": False, "msg": "登录码不存在，请向家长确认"}), 404
    if not stu.get("active"):
        return jsonify({"ok": False, "msg": "该账号已停用"}), 403

    token = create_token(code)
    touch_student(code)
    return jsonify({"ok": True, "token": token, "student": _stu_brief(stu)})


@app.route("/api/register", methods=["POST"])
def api_register():
    """开放注册：用户名 + 手机号 + 姓名 + 密码"""
    data = request.get_json() or {}
    ok, msg, stu = register_student(
        data.get("username"), data.get("phone"),
        data.get("name"), data.get("password"))
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 400
    token = create_token(stu["sid"])
    touch_student(stu["sid"])
    return jsonify({"ok": True, "msg": "注册成功", "token": token,
                    "student": _stu_brief(stu)})


@app.route("/api/login_pwd", methods=["POST"])
def api_login_pwd():
    """账号密码登录：用户名 + 密码"""
    data = request.get_json() or {}
    ok, msg, stu = verify_login(data.get("username"), data.get("password"))
    if not ok:
        return jsonify({"ok": False, "msg": msg}), 401
    token = create_token(stu["sid"])
    touch_student(stu["sid"])
    return jsonify({"ok": True, "msg": "登录成功", "token": token,
                    "student": _stu_brief(stu)})


@app.route("/api/check_username")
def api_check_username():
    """注册时实时校验用户名是否可用"""
    u = (request.args.get("u") or "").strip()
    if not u:
        return jsonify({"ok": False, "msg": "请输入用户名"})
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_]{3,20}", u):
        return jsonify({"ok": False, "msg": "用户名需 3-20 位字母、数字或下划线"})
    if get_student_by_username(u):
        return jsonify({"ok": False, "msg": "这个用户名已经被注册了"})
    return jsonify({"ok": True, "msg": "可以使用"})


@app.route("/api/anonymous", methods=["POST"])
def api_anonymous():
    """
    匿名体验：只返回一个前端本地 token（不存服务端），用于浏览器本地隔离模式。
    匿名用户的所有作答、学习时长、错题本等数据只存在浏览器本地，不上传服务器。
    """
    import uuid as _uuid
    token = "anon:" + _uuid.uuid4().hex
    return jsonify({"ok": True, "token": token,
                    "student": {"sid": token, "name": "匿名体验",
                                "color": "#9aa4b2", "anonymous": True,
                                "username": "", "phone": ""}})


@app.route("/api/me")
def api_me():
    """当前登录学生信息（未登录返回已登录名单，供选择）"""
    sid = _require_student()
    if sid:
        stu = get_student(sid)
        if stu:
            return jsonify({"ok": True, "loggedIn": True,
                            "student": _stu_brief(stu)})
    # 未登录：返回可用学生名单（不含敏感信息，仅用于展示"谁在用"）
    return jsonify({"ok": True, "loggedIn": False,
                    "count": len(list_students()),
                    "needCode": True})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    token = request.headers.get(TOKEN_HEADER) or (request.get_json(silent=True) or {}).get("token")
    revoke_token(token)
    return jsonify({"ok": True})


# ================= 学生端 API =================
@app.route("/api/courses")
def api_courses():
    """课程列表（含个人进度）。匿名模式下进度来自本地记录。"""
    if _is_anon():
        stats = anon_stats(_anon_payload())
        streak = anon_streak(_anon_payload())
    else:
        stats = get_stats(current_student())
        streak = get_streak(current_student())
    by_course = stats["by_course"]
    out = []
    for c in COURSES:
        cs = by_course.get(c["id"], {"total": 0, "right": 0})
        out.append({
            "id": c["id"], "no": c["no"], "title": c["title"],
            "subtitle": c["subtitle"], "color": c["color"],
            "subject": c.get("subject", "政治"),
            "quizCount": count_questions(c["id"]) or len(c["quiz"]),
            "answered": cs["total"],
            "right": cs["right"]
        })
    return jsonify({"courses": out, "stats": stats,
                    "studentName": get_config("student_name", "同学"),
                    "streak": streak})


@app.route("/api/course/<cid>")
def api_course(cid):
    """
    单课详情。
    查询参数 mode：basic（默认，固定顺序）| random（随机抽题）
    返回题目来自数据库题库，并附带本人已作答状态。
    """
    c = get_course(cid)
    if not c:
        return jsonify({"error": "not found"}), 404

    mode = request.args.get("mode", MODE_BASIC)
    limit = request.args.get("limit", type=int)
    quiz = build_quiz(cid, mode=mode, limit=limit)
    if not quiz:  # 题库为空时回退到代码内置题目，保证可用
        quiz = [dict(q, qid=f"{cid}_{i+1:02d}") for i, q in enumerate(c["quiz"])]

    data = dict(c)
    data["quiz"] = quiz
    data["mode"] = mode
    data["modes"] = available_modes(cid)
    if _is_anon():
        data["myAnswers"] = anon_course_answers(cid, _anon_payload())
    else:
        data["myAnswers"] = get_course_answers(cid, current_student())
    return jsonify(data)


@app.route("/api/question/<qid>")
def api_question(qid):
    """按 qid 取单题（错题本重做时，题目可能不在当前出题集合里，需要单独取）"""
    q = get_question(qid)
    if not q:
        return jsonify({"error": "not found"}), 404
    return jsonify(q)


@app.route("/api/answer", methods=["POST"])
def api_answer():
    """提交答案，服务端判分。优先按 qid 判分（题库模式）。"""
    data = request.get_json() or {}
    cid = data.get("courseId")
    qid = data.get("qid")
    chosen = data.get("chosen")
    mode = data.get("mode", MODE_BASIC)

    if not get_course(cid):
        return jsonify({"error": "invalid course"}), 400

    # 优先按 qid 取题（题库）
    q = get_question(qid) if qid else None
    qi = data.get("questionIdx")
    if not q:
        # 兼容旧调用：按下标取题。此时尽量把 qid 补全为题库里同题对应的 qid，
        # 否则同一道题会因 key 不同在错题本里重复出现（qid 与 idx: 两套 key）。
        c = get_course(cid)
        if qi is None or not (0 <= qi < len(c["quiz"])):
            return jsonify({"error": "invalid"}), 400
        q = c["quiz"][qi]
        q = dict(q, qid=_resolve_qid_by_idx(cid, qi, mode))

    if chosen is None:
        return jsonify({"error": "no answer"}), 400

    correct = (chosen == q["a"])
    if not _is_anon():   # 匿名不落库，记录由前端存在本地
        record_answer(cid, qi if qi is not None else -1, chosen, correct,
                      current_student(), qid=q.get("qid"), mode=mode)
    return jsonify({"correct": correct, "answer": q["a"], "explain": q["e"]})


@app.route("/api/wrongbook")
def api_wrongbook():
    """
    电子错题本：按课程分组。

    设计要点（对应需求"错题本不直接给答案"）：
      · 前端进入时默认收起答案，由学生点"查看答案/我的选项"才展开
      · 所以这里仍然把 answer/explain/myAnswer 一并返回，但由前端控制显隐
      · 同题多模式做错只出现一次，用 modes 标明来源（基础/随机）
    """
    if _is_anon():
        items = anon_wrong_questions(_anon_payload())
        groups = {}
        for it in items:
            cid = it["course_id"]
            c = get_course(cid)
            if not c:
                continue
            groups.setdefault(cid, {
                "courseId": cid, "no": c["no"], "title": c["title"],
                "color": c["color"], "items": []
            })
            groups[cid]["items"].append({
                "qid": it["qid"], "questionIdx": None,
                "q": it["stem"], "o": it["options"],
                "answer": it["answer"], "explain": it["explain"],
                "img": it.get("img", ""), "svg": it.get("svg", ""),
                "myAnswer": it["chosen"], "answeredAt": it["answered_at"],
                "mode": it.get("mode", "basic"), "modes": [it.get("mode", "basic")],
                "fixed": False, "wrongTimes": 1,
            })
        return jsonify({"groups": list(groups.values()), "total": len(items)})

    wrongs = get_wrong_questions(current_student())
    groups = {}
    for w in wrongs:
        cid = w["course_id"]
        c = get_course(cid)
        if not c:
            continue
        # 取题目内容：优先题库，其次内置
        q = get_question(w.get("qid")) if w.get("qid") else None
        if not q:
            idx = w.get("question_idx")
            if idx is None or not (0 <= idx < len(c["quiz"])):
                continue
            q = dict(c["quiz"][idx], qid=None)
        groups.setdefault(cid, {
            "courseId": cid, "no": c["no"], "title": c["title"],
            "color": c["color"], "items": []
        })
        groups[cid]["items"].append({
            "qid": q.get("qid"),
            "questionIdx": w.get("question_idx"),
            "q": q["q"], "o": q["o"], "answer": q["a"], "explain": q["e"],
            "img": q.get("img", ""), "svg": q.get("svg", ""),
            "myAnswer": w["chosen"], "answeredAt": w["answered_at"],
            "mode": w.get("mode", "basic"), "modes": w.get("modes", []),
            "fixed": bool(w.get("fixed")), "wrongTimes": w.get("wrong_times", 1),
        })
    return jsonify({"groups": list(groups.values()), "total": len(wrongs)})


@app.route("/api/course/reset", methods=["POST"])
def api_course_reset():
    """清空本课作答记录（重新作答）。匿名模式返回"清完后的本地记录"。"""
    data = request.get_json() or {}
    cid = data.get("courseId")
    if not get_course(cid):
        return jsonify({"error": "not found"}), 404
    if _is_anon():
        return jsonify({"ok": True, "anon": anon_reset_course(cid, _anon_payload())})
    reset_course_answers(cid, current_student())
    return jsonify({"ok": True})


@app.route("/api/redo", methods=["POST"])
def api_redo():
    """错题重做，答对则从错题本移除（记录新作答）"""
    data = request.get_json() or {}
    cid = data.get("courseId")
    qid = data.get("qid")
    chosen = data.get("chosen")
    qi = data.get("questionIdx")

    if not get_course(cid):
        return jsonify({"error": "invalid"}), 400

    q = get_question(qid) if qid else None
    if not q:
        c = get_course(cid)
        if qi is None or not (0 <= qi < len(c["quiz"])):
            return jsonify({"error": "invalid"}), 400
        q = dict(c["quiz"][qi], qid=_resolve_qid_by_idx(cid, qi, "basic"))

    if chosen is None:
        return jsonify({"error": "no answer"}), 400

    correct = (chosen == q["a"])
    if not _is_anon():
        record_answer(cid, qi if qi is not None else -1, chosen, correct,
                      current_student(), qid=q.get("qid"), mode="redo")
    return jsonify({"correct": correct, "answer": q["a"], "explain": q["e"]})


@app.route("/api/plan")
def api_plan():
    if _is_anon():
        return jsonify({"plan": DAILY_PLAN, "done": anon_tasks(_anon_payload())})
    return jsonify({"plan": DAILY_PLAN, "done": get_tasks(current_student())})


@app.route("/api/plan/toggle", methods=["POST"])
def api_plan_toggle():
    data = request.get_json() or {}
    if _is_anon():   # 匿名：回显，实际记录由前端存本地
        return jsonify({"ok": True, "done": anon_tasks(_anon_payload())})
    set_task(data.get("day"), data.get("taskIdx"), data.get("done"), current_student())
    return jsonify({"ok": True, "done": get_tasks(current_student())})


@app.route("/api/session/start", methods=["POST"])
def api_session_start():
    data = request.get_json() or {}
    if _is_anon():   # 匿名不计时（避免占用服务器资源）
        return jsonify({"sessionId": None, "anon": True})
    sid = start_session(data.get("page", ""), current_student())
    return jsonify({"sessionId": sid})


@app.route("/api/session/end", methods=["POST"])
def api_session_end():
    data = request.get_json() or {}
    if data.get("sessionId") and not _is_anon():
        end_session(data["sessionId"], current_student())
    return jsonify({"ok": True})


@app.route("/api/video/view", methods=["POST"])
def api_video_view():
    data = request.get_json() or {}
    if not _is_anon():
        record_video(data.get("courseId"), data.get("videoName"), current_student())
    return jsonify({"ok": True})


# ================= 家长端 API =================
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json() or {}
    pwd = str(data.get("password", ""))
    if pwd == get_config("admin_password", "888888"):
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "密码错误"}), 401


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.route("/api/admin/check")
def api_admin_check():
    return jsonify({"loggedIn": bool(session.get("admin"))})


@app.route("/api/admin/dashboard")
def api_admin_dashboard():
    """
    家长看板：学习时长、正确率、错题、视频观看、打卡。
    查询参数 sid：指定查看哪个学生（不传则看第一个/最近使用的）。
    """
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401

    # 选择要查看的学生
    target = request.args.get("sid")
    if not target:
        students = list_students()
        target = students[0]["sid"] if students else DEFAULT_STUDENT
    stu = get_student(target)

    stats = get_stats(target)
    by_course = []
    for c in COURSES:
        cs = stats["by_course"].get(c["id"])
        if cs:
            by_course.append({
                "no": c["no"], "title": c["title"], "color": c["color"],
                "total": cs["total"], "right": cs["right"],
                "rate": round(cs["right"] / cs["total"] * 100) if cs["total"] else 0
            })

    tasks = get_tasks(target)
    done_tasks = sum(1 for d in tasks.values() for v in d.values() if v)
    total_tasks = sum(len(d["tasks"]) for d in DAILY_PLAN)

    return jsonify({
        "student": {"sid": target, "name": (stu or {}).get("name", "同学"),
                    "color": (stu or {}).get("color", "#e8524a"),
                    "lastActive": (stu or {}).get("last_active")},
        "stats": stats,
        "byCourse": by_course,
        "studyTime": get_study_time(target, 7),
        "streak": get_streak(target),
        "videos": get_video_stats(target),
        "tasks": {"done": done_tasks, "total": total_tasks, "detail": tasks},
        "dailyTarget": int(get_config("daily_target_minutes", "25")),
    })


# ================= 家长端：学生管理 =================
@app.route("/api/admin/students", methods=["GET", "POST"])
def api_admin_students():
    """学生列表 / 新建学生"""
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401

    if request.method == "GET":
        rows = list_students(active_only=False)
        out = []
        for s in rows:
            st = get_stats(s["sid"])
            anon = bool(s.get("is_anonymous"))
            out.append({
                "sid": s["sid"], "name": s["name"],
                "color": s.get("color", "#2f6fed"), "active": bool(s.get("active")),
                "lastActive": s.get("last_active"),
                "answered": st["total"], "rate": st["rate"],
                "phone": s.get("phone") or "",
                "username": s.get("username") or "",
                "anonymous": anon,
                # 账号类型：注册 / 匿名 / 登录码（早期手动开的账号）
                "kind": "匿名" if anon else ("注册" if s.get("username") else "登录码"),
            })
        return jsonify({"students": out})

    # 新建
    data = request.get_json() or {}
    code = (data.get("sid") or "").strip().lower()
    name = (data.get("name") or "").strip()
    color = data.get("color") or "#2f6fed"
    ok, msg = create_student(code, name or code, color)
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 400)


@app.route("/api/admin/students/<sid>", methods=["POST", "DELETE"])
def api_admin_student_edit(sid):
    """修改（改名/停用/启用）或删除学生"""
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401

    if request.method == "DELETE":
        wipe = str(request.args.get("wipe", "")).lower() in ("1", "true", "yes")
        delete_student(sid, wipe=wipe)
        return jsonify({"ok": True})

    data = request.get_json() or {}
    update_student(sid,
                   name=data.get("name"),
                   color=data.get("color"),
                   active=data.get("active"))
    return jsonify({"ok": True})


@app.route("/api/admin/config", methods=["GET", "POST"])
def api_admin_config():
    """查看/修改配置"""
    if request.method == "GET":
        if not session.get("admin"):
            return jsonify({"error": "unauthorized"}), 401
        return jsonify({
            "studentName": get_config("student_name", "同学"),
            "dailyTarget": get_config("daily_target_minutes", "25"),
        })
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    if "studentName" in data:
        set_config("student_name", data["studentName"])
    if "dailyTarget" in data:
        set_config("daily_target_minutes", data["dailyTarget"])
    if data.get("newPassword"):
        set_config("admin_password", data["newPassword"])
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("=" * 56)
    print("  政史地学习平台 · 服务已启动")
    print("  学生端：http://127.0.0.1:5000")
    print("  家长端：http://127.0.0.1:5000/admin  （默认密码 888888）")
    print("  数据库：" + ("Turso 云端" if _USE_TURSO else "本地 SQLite"))
    print("=" * 56)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
