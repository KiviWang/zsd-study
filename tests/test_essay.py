#!/usr/bin/env python3
"""
端到端测试：每日论述题。

覆盖：题库存在、按日期选题稳定、答案不泄露、提交判分、当天刷新题不变、
      独立统计不污染自测、历史列表、错题本收录、匿名模式。

要求测试前已经启动 app/server.py（默认端口 5097）。
"""
import json
import urllib.request
import urllib.error
import time as _t

BASE = "http://127.0.0.1:5097"
OK = FAIL = 0
TOKEN = None


def req(path, method="GET", body=None, token=None, anon=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-ZSD-Token", token)
    if anon:
        r.add_header("X-ZSD-Anon", json.dumps(anon))
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def check(cond, name, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


# 生成一个独立测试账号
_seed = int(_t.time()) % 100000
_uname = "essaytest" + str(_seed)
_pno = "137" + str(10000000 + _seed)[:8]

print("=" * 60)
print("【1】题库与课程元数据")
print("=" * 60)
st, d = req("/api/courses")
check(st == 200, "课程列表接口可用")
p1 = next((c for c in d.get("courses", []) if c["id"] == "p1"), {})
check(p1.get("essayTotal", 0) == 10, "p1 有 10 道论述题", str(p1.get("essayTotal")))
check("essay" in d, "课程列表含 essay 汇总", str(d.get("essay")))

st, d = req("/api/course/p1")
check(st == 200 and d.get("essay", {}).get("total") == 10, "单课详情含 essay 元数据")
# 自测题里 material 字段应为空串（论述题专属字段不污染自测题）
check(all(not q.get("material") for q in d.get("quiz") or []),
      "自测题的 material 字段为空")
check(all(q.get("tier") != "essay" for q in d.get("quiz") or []),
      "自测题列表不含论述题（tier=essay）")

print()
print("=" * 60)
print("【2】注册并登录测试账号")
print("=" * 60)
st, d = req("/api/register", "POST", {
    "username": _uname, "phone": _pno,
    "name": "论述题测试", "password": "test1234"})
check(st == 200 and d.get("ok"), "注册成功", str(d))
TOKEN = d.get("token")
check(bool(TOKEN), "拿到 token")

print()
print("=" * 60)
print("【3】每日论述题接口")
print("=" * 60)
st, e = req("/api/essay/p1", token=TOKEN)
check(st == 200, "取今日题接口可用")
check("question" in e and e["question"], "返回今日题")
check(e["question"].get("qid", "").startswith("p1_e"), "题号格式 p1_eNN", e["question"].get("qid"))
check("a" not in e["question"], "未答时不泄露正确答案")
check("e" not in e["question"], "未答时不泄露解析")
check(len(e["question"]["o"]) == 4, "4 个角度选项")
check("material" in e["question"] and len(e["question"]["material"]) > 20, "含材料")
check(e["stats"]["total"] == 0, "初始累计 0 道")
check(e["total"] == 10, "题库总数 10 道")
check(e["remaining"] == 10, "剩余 10 道未做")

_today_qid = e["question"]["qid"]

# 连续取两次：同天同一题
st, e2 = req("/api/essay/p1", token=TOKEN)
check(e2["question"]["qid"] == _today_qid, "同一天多次刷新题号不变")

print()
print("=" * 60)
print("【4】提交答案与判分")
print("=" * 60)
q = e["question"]
# 安全题不含答案，先从单题接口取正确答案（用于构造"故意选错"）
st, raw = req("/api/question/" + q["qid"], token=TOKEN)
right_idx = raw["a"]
wrong_idx = 0 if right_idx != 0 else 1   # 保证选错
st, ans = req("/api/essay/answer", "POST",
              {"courseId": "p1", "qid": q["qid"], "chosen": wrong_idx}, TOKEN)
check(st == 200 and ans["correct"] is False, "选择错误项判为错")
check(ans["answer"] == right_idx, "返回的正确项与题库一致")
check("explain" in ans and "why" in ans, "返回解析与择优说明")
check(ans["stats"]["total"] == 1 and ans["stats"]["wrong"] == 1, "统计更新为 1 答错")

# 再取今日题：已答状态
st, e3 = req("/api/essay/p1", token=TOKEN)
check(e3["question"]["qid"] == _today_qid, "答完后当天题号仍不变")
check(e3["answered"]["chosen"] == wrong_idx, "已答记录显示所选")
check(e3["answered"]["correct"] is False, "已答记录显示错误")
check(e3["stats"]["wrong"] == 1, "统计保持 1 错")

# 历史题目
st, h = req("/api/essay/history/p1", token=TOKEN)
check(st == 200 and h["total"] == 1, "历史题目 1 条")
check(h["items"][0]["qid"] == _today_qid, "历史题目题号正确")
check("material" in h["items"][0]["question"], "历史题目含材料")

print()
print("=" * 60)
print("【5】错题本收录")
print("=" * 60)
st, wb = req("/api/wrongbook", token=TOKEN)
items = [it for g in wb.get("groups", []) for it in g["items"]]
essay_items = [it for it in items if it["qid"] == _today_qid]
check(bool(essay_items), "错题本收录了论述题")
if essay_items:
    it = essay_items[0]
    check(it["mode"] == "essay", "错题来源 mode=essay")
    check(it["fixed"] is False, "错题标记为待订正")

print()
print("=" * 60)
print("【6】自测统计不被污染")
print("=" * 60)
st, cs = req("/api/courses", token=TOKEN)
stats = cs["stats"]
# 论述题作答应不影响自测题（basic/random/redo）统计
check(stats["total"] == 0, "自测总题数仍为 0（论述题未混入）")
by_mode = stats.get("by_mode", {})
check(all(by_mode.get(m, {}).get("total", 0) == 0 for m in ("basic", "random", "redo")),
      "自测各模式答题数均为 0", str(by_mode))

print()
print("=" * 60)
print("【7】匿名模式论述题")
print("=" * 60)
st, ae = req("/api/essay/p1", token="anon:test-" + str(_seed))
check(st == 200, "匿名模式可取今日题")
_anon_qid = ae["question"]["qid"]
check(ae["stats"]["total"] == 0, "匿名初始统计 0")

# 匿名作答：答案存在本地，下次请求带 anon 头
st, raw2 = req("/api/question/" + _anon_qid)
wrong2 = 0 if raw2["a"] != 0 else 1
anon_payload = {
    "answers": [{
        "cid": "p1", "qid": _anon_qid, "idx": -1, "chosen": wrong2,
        "correct": False, "mode": "essay", "at": "2026-10-13T10:00:00"
    }]
}
st, ans2 = req("/api/essay/answer", "POST",
               {"courseId": "p1", "qid": _anon_qid, "chosen": wrong2}, token="anon:test-" + str(_seed))
check(st == 200 and ans2["correct"] is False, "匿名提交判分")

st, ae2 = req("/api/essay/p1", token="anon:test-" + str(_seed), anon=anon_payload)
check(ae2["question"]["qid"] == _anon_qid, "匿名带记录后当天题号不变")
check(ae2["answered"]["chosen"] == wrong2, "匿名已答记录生效")
check(ae2["stats"]["total"] == 1 and ae2["stats"]["wrong"] == 1, "匿名统计生效")

st, awb = req("/api/wrongbook", token="anon:test-" + str(_seed), anon=anon_payload)
aitems = [it for g in awb.get("groups", []) for it in g["items"]]
check(any(it["qid"] == _anon_qid for it in aitems), "匿名错题本收录论述题")

print()
print("=" * 60)
print(f"结果：{OK} 通过 / {FAIL} 失败")
print("=" * 60)
exit(1 if FAIL else 0)
