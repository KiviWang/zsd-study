#!/usr/bin/env python3
"""历史/地理内容专项测试：课程接入、题目数量、SVG 配图渲染、学科分组"""
import json, urllib.request, urllib.error, os, time

BASE = "http://127.0.0.1:5097"
OK = FAIL = 0
TOKEN = None


def req(path, method="GET", body=None, token=None):
    r = urllib.request.Request(BASE + path,
        data=json.dumps(body).encode() if body is not None else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-ZSD-Token", token)
    else:
        # 无 token 时走匿名分支：必须带一个空 payload 头，否则会被当成"全答过"
        r.add_header("X-ZSD-Anon", json.dumps({"answers": [], "wrong": []}))
    try:
        with urllib.request.urlopen(r) as resp:
            return json.loads(resp.read() or b"{}"), resp.status
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read() or b"{}"), e.code
        except Exception: return {}, e.code


def fresh_token():
    """注册一个临时学生，拿 token，避免作答状态污染"""
    ts = str(int(time.time() * 1000))[-8:]
    d, st = req("/api/register", "POST", {
        "username": "tc" + ts, "phone": "139" + ts,
        "name": "内容测试", "password": "test1234"})
    return d.get("token") or d.get("code")


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1; print("  ✅ " + name)
    else:
        FAIL += 1; print("  ❌ " + name + (f"  [{extra}]" if extra else ""))


print("=" * 60)
print("【1】课程接入：政治/历史/地理 三科齐全")
print("=" * 60)
d, _ = req("/api/courses")
cs = d.get("courses", [])
subjects = {}
for c in cs:
    subjects.setdefault(c.get("subject", "政治"), []).append(c)
chk("课程总数 = 39", len(cs) == 39, f"实际 {len(cs)}")
chk("政治 4 课", len(subjects.get("政治", [])) == 4)
chk("历史 29 课", len(subjects.get("历史", [])) == 29, f"实际 {len(subjects.get('历史', []))}")
chk("地理 6 课", len(subjects.get("地理", [])) == 6)
chk("每课都有 subject 字段", all("subject" in c for c in cs))
chk("每课题数 20（基础10+强化10）", all(c.get("quizCount") == 20 for c in cs),
    str([(c["id"], c.get("quizCount")) for c in cs if c.get("quizCount") != 20]))

print()
print("=" * 60)
print("【2】各课内容完整性（知识点 / 视频 / 题库）")
print("=" * 60)
for cid in ["h1", "h2", "h5", "h10", "h15", "h20", "h29", "g1", "g2", "g3", "g4", "g5", "g6"]:
    d, st = req(f"/api/course/{cid}")
    pts = d.get("points", [])
    vids = d.get("videos", [])
    chk(f"{cid} 接口可用", st == 200, f"status={st}")
    chk(f"{cid} 知识点 ≥5 条", len(pts) >= 5, f"{len(pts)}")
    chk(f"{cid} 有视频/看片清单", len(vids) >= 1, f"{len(vids)}")
    chk(f"{cid} 返回题库 10 题", len(d.get("quiz", [])) == 10, str(len(d.get("quiz", []))))

print()
print("=" * 60)
print("【3】SVG 配图题：内容非空且是合法 SVG")
print("=" * 60)
expect_svg = {
    "h1_01": "旧石器时间轴", "h2_01": "东周流程图", "g1_01": "八大行星",
    "g2_05": "太阳大气分层", "g3_02": "地质年代", "g5_03": "大气垂直分层",
    "g6_02": "大气受热过程",
}
for qid, desc in expect_svg.items():
    d, st = req(f"/api/question/{qid}")
    q = d.get("question", d)
    svg = q.get("svg") or ""
    chk(f"{qid} ({desc}) 含 SVG", st == 200 and svg.startswith("<svg"), f"status={st} len={len(svg)}")
    if svg:
        chk(f"{qid} SVG 带 viewBox", "viewBox" in svg)
        chk(f"{qid} SVG 闭合", svg.rstrip().endswith("</svg>"))

print()
print("=" * 60)
print("【4】模式隔离：基础题与随机题不重叠")
print("=" * 60)
tk = fresh_token()
chk("临时学生注册并拿到 token", bool(tk))
for cid in ["h1", "g1", "g5"]:
    b, _ = req(f"/api/course/{cid}?mode=basic&limit=10", token=tk)
    r, _ = req(f"/api/course/{cid}?mode=random&limit=10", token=tk)
    bq = {q["qid"] for q in b.get("quiz", [])}
    rq = {q["qid"] for q in r.get("quiz", [])}
    chk(f"{cid} 基础10题", len(bq) == 10, str(len(bq)))
    chk(f"{cid} 随机10题", len(rq) == 10, str(len(rq)))
    chk(f"{cid} 基础/随机零重叠", len(bq & rq) == 0, f"重叠 {bq & rq}")

print()
print("=" * 60)
print("【5】题干与选项质量：无占位符、无乱码")
print("=" * 60)
allq = []
for cid in ["h1", "h2", "h5", "h10", "h15", "h20", "h29", "g1", "g2", "g3", "g4", "g5", "g6"]:
    for mode in ["basic", "random"]:
        d, _ = req(f"/api/course/{cid}?mode={mode}&limit=10", token=tk)
        allq += d.get("quiz", [])
chk("累计取题 ≥200", len(allq) >= 200, str(len(allq)))
bad_word = [q for q in allq if any(w in json.dumps(q, ensure_ascii=False)
            for w in ["TODO", "占位", "待补", "restore"])]
chk("无占位符/英文残留", len(bad_word) == 0, str([q["qid"] for q in bad_word[:5]]))
bad_opt = [q for q in allq if len(q.get("o", [])) != 4]
chk("每题恰好 4 个选项", len(bad_opt) == 0, str([q["qid"] for q in bad_opt[:5]]))
bad_ans = [q for q in allq if not (0 <= q.get("a", -1) <= 3)]
chk("答案下标在 0-3", len(bad_ans) == 0, str([q["qid"] for q in bad_ans[:5]]))
bad_exp = [q for q in allq if not (q.get("e") or "").strip()]
chk("每题都有解析", len(bad_exp) == 0, str([q["qid"] for q in bad_exp[:5]]))

print()
print("=" * 60)
print(f"结果：{OK} 通过 / {FAIL} 失败")
print("=" * 60)
