#!/usr/bin/env python3
"""端到端测试：模式隔离 + 错题本新交互 + 题库扩充"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:5097"
OK = FAIL = 0
TOKEN = None


def req(path, method="GET", body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-ZSD-Token", token)
    try:
        with urllib.request.urlopen(r) as resp:
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


print("=" * 60)
print("【1】题库扩充校验")
print("=" * 60)
st, d = req("/api/course/p1?mode=basic")
check(st == 200, "基础模式接口可用")
basic_qids = [q["qid"] for q in d["quiz"]]
check(len(basic_qids) == 10, f"基础题 10 道（实际 {len(basic_qids)}）")
check(all(not qid.split("_")[-1].startswith("a") for qid in basic_qids),
      "基础模式只含基础题（无 _a 系列）")

st, dr = req("/api/course/p1?mode=random&limit=10")
rand_qids = [q["qid"] for q in dr["quiz"]]
check(len(rand_qids) == 10, f"随机题 10 道（实际 {len(rand_qids)}）")
check(all(qid.split("_")[-1].startswith("a") for qid in rand_qids),
      "随机模式抽的是强化题（_a 系列）")
check(len(set(basic_qids) & set(rand_qids)) == 0,
      "基础题与随机题零重叠",
      f"重叠 {set(basic_qids) & set(rand_qids)}")

st, d2 = req("/api/course/p1?mode=random&limit=10")
check([q["qid"] for q in d2["quiz"]] != rand_qids, "随机模式两次抽题不同")

st, dc = req("/api/courses")
check(st == 200, "课程列表接口可用")

print()
print("=" * 60)
print("【2】注册并登录测试账号")
print("=" * 60)
import time as _t
_seed = int(_t.time()) % 100000
uname = "modetest" + str(_seed)
pno = "139" + str(10000000 + _seed)[:8]
st, d = req("/api/register", "POST", {
    "username": uname, "phone": pno,
    "name": "模式测试", "password": "test1234"})
check(st == 200 and d.get("ok"), "注册成功", str(d))
TOKEN = d.get("token")
check(bool(TOKEN), "拿到 token")

print()
print("=" * 60)
print("【3】模式隔离：同题在两模式下结果不互相带入")
print("=" * 60)
common_qid = "p1_01"   # 基础题，也会出现在随机题库外的位置——用强化题 p1_a01 更适合
# 在基础模式做错 p1_01
st, d = req("/api/answer", "POST",
            {"courseId": "p1", "qid": "p1_01", "questionIdx": 0,
             "chosen": 0, "mode": "basic"}, TOKEN)
check(st == 200 and not d["correct"], "基础模式答错 p1_01")

st, c = req("/api/course/p1?mode=basic", token=TOKEN)
check("p1_01" in c["myAnswers"]["byQidBasic"], "基础状态记录了 p1_01")
check("p1_01" not in c["myAnswers"]["byQidRandom"],
      "随机状态未记录 p1_01（隔离）")

# 在随机模式答对同一题
st, d = req("/api/answer", "POST",
            {"courseId": "p1", "qid": "p1_01", "questionIdx": 0,
             "chosen": 1, "mode": "random"}, TOKEN)
check(st == 200, "随机模式作答 p1_01")
st, c = req("/api/course/p1?mode=random", token=TOKEN)
check(c["myAnswers"]["byQidBasic"].get("p1_01", {}).get("correct") is False,
      "随机答对后，基础状态仍为'错'（隔离成立）",
      str(c["myAnswers"]["byQidBasic"].get("p1_01")))

print()
print("=" * 60)
print("【4】错题本：默认不含答案，标记已订正")
print("=" * 60)
st, wb = req("/api/wrongbook", token=TOKEN)
check(st == 200, "错题本接口可用")
items = [it for g in wb["groups"] for it in g["items"]]
p1_01 = [it for it in items if it["qid"] == "p1_01"]
check(len(p1_01) == 1, "同题只出现一次（按 qid 去重）")
if p1_01:
    it = p1_01[0]
    check("answer" in it and "myAnswer" in it, "数据里带了答案与我的选项（供前端显隐控制）")
    check(it["fixed"] is True, "随机答对后标记为已订正")
    check("basic" in it["modes"], "modes 记录了出错模式")

# 重做答错另一题，确认未订正
st, d = req("/api/answer", "POST",
            {"courseId": "p2", "qid": "p2_01", "questionIdx": 0,
             "chosen": 3, "mode": "basic"}, TOKEN)
st, wb = req("/api/wrongbook", token=TOKEN)
items = [it for g in wb["groups"] for it in g["items"]]
p2_01 = [it for it in items if it["qid"] == "p2_01"]
check(bool(p2_01) and p2_01[0]["fixed"] is False, "新错题标记为待订正")

print()
print("=" * 60)
print("【5】单题接口（错题重做用）")
print("=" * 60)
st, q = req("/api/question/p1_a01", token=TOKEN)
check(st == 200 and q.get("qid") == "p1_a01", "按 qid 取强化题成功")
check("o" in q and "a" in q and "e" in q, "单题含选项/答案/解析")
st, q404 = req("/api/question/not_exist", token=TOKEN)
check(st == 404, "不存在的 qid 返回 404")

print()
print("=" * 60)
print("【6】重做接口（mode=redo）")
print("=" * 60)
# 先取 p2_01 的正确答案（不能硬编码下标）
st, qz = req("/api/question/p2_01", token=TOKEN)
right_idx = qz["a"]
wrong_idx = 0 if right_idx != 0 else 1
st, d = req("/api/redo", "POST",
            {"courseId": "p2", "qid": "p2_01", "questionIdx": 0, "chosen": right_idx}, TOKEN)
check(st == 200 and d["correct"] is True, "重做答对", str(d))
st, wb = req("/api/wrongbook", token=TOKEN)
items = [it for g in wb["groups"] for it in g["items"]]
p2_01 = [it for it in items if it["qid"] == "p2_01"]
check(bool(p2_01) and p2_01[0]["fixed"] is True,
      "重做答对后仍在错题本且标记已订正（不消失）")
check(p2_01 and "basic" in p2_01[0]["modes"],
      "modes 记录了当初出错的基础模式（redo 是答对的不计入出错模式）")

print()
print("=" * 60)
print("【7】统计分模式")
print("=" * 60)
st, cs = req("/api/courses", token=TOKEN)
stats = cs["stats"]
check("by_mode" in stats, "统计含 by_mode 分模式数据")
check("by_course_mode" in stats, "统计含 by_course_mode")
check(stats["by_mode"]["basic"]["total"] >= 2, "基础模式计入统计")
check(stats["by_mode"]["random"]["total"] >= 1, "随机模式计入统计")

print()
print("=" * 60)
print(f"结果：{OK} 通过 / {FAIL} 失败")
print("=" * 60)
exit(1 if FAIL else 0)
