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
                reset_course_answers, USE_TURSO as _USE_TURSO)
from courses import COURSES, DAILY_PLAN, get_course
import bank
from bank import (seed_from_courses, get_questions, get_question, count_questions,
                  build_quiz, available_modes, MODE_BASIC, MODE_RANDOM)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder=os.path.join(BASE, "static"), static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "zsd-study-platform-2026-local")

STUDENT = "default"   # 当前仅一个学生，后续可扩展为多学生

# 关键：模块加载时即建表，保证 gunicorn 等 WSGI 启动方式也能初始化数据库
init_db()

# 题库播种：把 courses.py 里的题目同步进数据库（幂等，已存在的跳过）
try:
    _added, _skipped = seed_from_courses(COURSES)
    if _added:
        print(f"[题库] 新导入 {_added} 道题（已存在 {_skipped} 道）")
except Exception as _e:  # 播种失败不应阻断服务启动
    print(f"[题库] 播种异常（忽略）：{_e}")


# ================= 页面 =================
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/admin")
def admin_page():
    return send_from_directory(app.static_folder, "admin.html")


# ================= 学生端 API =================
@app.route("/api/courses")
def api_courses():
    """课程列表（含个人进度）"""
    stats = get_stats(STUDENT)
    by_course = stats["by_course"]
    out = []
    for c in COURSES:
        cs = by_course.get(c["id"], {"total": 0, "right": 0})
        out.append({
            "id": c["id"], "no": c["no"], "title": c["title"],
            "subtitle": c["subtitle"], "color": c["color"],
            "quizCount": count_questions(c["id"]) or len(c["quiz"]),
            "answered": cs["total"],
            "right": cs["right"]
        })
    return jsonify({"courses": out, "stats": stats,
                    "studentName": get_config("student_name", "同学"),
                    "streak": get_streak(STUDENT)})


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
    data["myAnswers"] = get_course_answers(cid, STUDENT)
    return jsonify(data)


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
        # 兼容旧调用：按下标取内置题目
        c = get_course(cid)
        if qi is None or not (0 <= qi < len(c["quiz"])):
            return jsonify({"error": "invalid"}), 400
        q = c["quiz"][qi]
        q = dict(q, qid=None)

    if chosen is None:
        return jsonify({"error": "no answer"}), 400

    correct = (chosen == q["a"])
    record_answer(cid, qi if qi is not None else -1, chosen, correct, STUDENT,
                  qid=q.get("qid"), mode=mode)
    return jsonify({"correct": correct, "answer": q["a"], "explain": q["e"]})


@app.route("/api/wrongbook")
def api_wrongbook():
    """电子错题本：按课程分组。优先用题库取题信息，兜底用内置题目。"""
    wrongs = get_wrong_questions(STUDENT)
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
            "myAnswer": w["chosen"], "answeredAt": w["answered_at"]
        })
    return jsonify({"groups": list(groups.values()), "total": len(wrongs)})


@app.route("/api/course/reset", methods=["POST"])
def api_course_reset():
    """清空本课作答记录（重新作答）"""
    data = request.get_json() or {}
    cid = data.get("courseId")
    if not get_course(cid):
        return jsonify({"error": "not found"}), 404
    reset_course_answers(cid, STUDENT)
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
        q = c["quiz"][qi]

    if chosen is None:
        return jsonify({"error": "no answer"}), 400

    correct = (chosen == q["a"])
    record_answer(cid, qi if qi is not None else -1, chosen, correct, STUDENT,
                  qid=q.get("qid"), mode="redo")
    return jsonify({"correct": correct, "answer": q["a"], "explain": q["e"]})


@app.route("/api/plan")
def api_plan():
    return jsonify({"plan": DAILY_PLAN, "done": get_tasks(STUDENT)})


@app.route("/api/plan/toggle", methods=["POST"])
def api_plan_toggle():
    data = request.get_json() or {}
    set_task(data.get("day"), data.get("taskIdx"), data.get("done"), STUDENT)
    return jsonify({"ok": True, "done": get_tasks(STUDENT)})


@app.route("/api/session/start", methods=["POST"])
def api_session_start():
    data = request.get_json() or {}
    sid = start_session(data.get("page", ""), STUDENT)
    return jsonify({"sessionId": sid})


@app.route("/api/session/end", methods=["POST"])
def api_session_end():
    data = request.get_json() or {}
    if data.get("sessionId"):
        end_session(data["sessionId"], STUDENT)
    return jsonify({"ok": True})


@app.route("/api/video/view", methods=["POST"])
def api_video_view():
    data = request.get_json() or {}
    record_video(data.get("courseId"), data.get("videoName"), STUDENT)
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
    """家长看板：学习时长、正确率、错题、视频观看、打卡"""
    if not session.get("admin"):
        return jsonify({"error": "unauthorized"}), 401

    stats = get_stats(STUDENT)
    # 各课标题补全
    by_course = []
    for c in COURSES:
        cs = stats["by_course"].get(c["id"])
        if cs:
            by_course.append({
                "no": c["no"], "title": c["title"], "color": c["color"],
                "total": cs["total"], "right": cs["right"],
                "rate": round(cs["right"] / cs["total"] * 100) if cs["total"] else 0
            })

    tasks = get_tasks(STUDENT)
    done_tasks = sum(1 for d in tasks.values() for v in d.values() if v)
    total_tasks = sum(len(d["tasks"]) for d in DAILY_PLAN)

    return jsonify({
        "studentName": get_config("student_name", "同学"),
        "stats": stats,
        "byCourse": by_course,
        "studyTime": get_study_time(STUDENT, 7),
        "streak": get_streak(STUDENT),
        "videos": get_video_stats(STUDENT),
        "tasks": {"done": done_tasks, "total": total_tasks, "detail": tasks},
        "dailyTarget": int(get_config("daily_target_minutes", "25")),
    })


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
