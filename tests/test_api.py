"""政史地学习平台 · 端到端测试"""
import requests, json, time

BASE = "http://127.0.0.1:5097"
results = []
def ck(name, cond, extra=""):
    results.append((name, cond, extra))
    print(f"{'✓' if cond else '✗'} {name} {extra}")

s = requests.Session()

# ---------- 1. 学生端 API ----------
r = s.get(f"{BASE}/api/courses")
d = r.json()
ck("课程列表返回 39 课", len(d["courses"]) == 39, f"{len(d['courses'])}")
ck("课程含 quizCount=20", all(c["quizCount"]==20 for c in d["courses"]))
ck("初始进度为0", d["stats"]["total"] == 0)

# 课程详情
r = s.get(f"{BASE}/api/course/p1")
c = r.json()
ck("课程详情 8 个知识点", len(c["points"]) == 8, f"{len(c['points'])}")
ck("课程详情 4 个视频", len(c["videos"]) == 4, f"{len(c['videos'])}")
ck("视频含 level 字段", all("level" in v for v in c["videos"]))
ck("视频含 seg 时间戳", all("seg" in v for v in c["videos"]))
lvls = [v["level"] for v in c["videos"]]
ck("含必看视频", "必看" in lvls, str(lvls))

# ---------- 2. 答题（对4题、错2题）----------
# p1 正确答案索引: [1,2,2,1,1,3,1,1,1,2]
correct_plan = [(0,1),(1,2),(2,2),(3,1)]   # 答对4题
wrong_plan   = [(4,0),(5,0)]               # 答错2题
for qi, ch in correct_plan + wrong_plan:
    r = s.post(f"{BASE}/api/answer", json={"courseId":"p1","questionIdx":qi,"chosen":ch})
    dd = r.json()
    expect = (qi,ch) in correct_plan
    ck(f"第{qi+1}题判分{'正确' if expect else '错误'}", dd["correct"]==expect, f"服务端返回{dd['correct']}")

# 统计校验
d = s.get(f"{BASE}/api/courses").json()
ck("总答题数=6", d["stats"]["total"]==6, str(d["stats"]["total"]))
ck("正确数=4", d["stats"]["right"]==4, str(d["stats"]["right"]))
ck("正确率=67%", d["stats"]["rate"]==67, str(d["stats"]["rate"]))
ck("错题数=2", d["stats"]["wrong_count"]==2, str(d["stats"]["wrong_count"]))

# ---------- 3. 错题本 ----------
wb = s.get(f"{BASE}/api/wrongbook").json()
ck("错题本 2 道错题", wb["total"]==2, str(wb["total"]))
ck("错题本按课分组", len(wb["groups"])==1, str(len(wb["groups"])))
g = wb["groups"][0]
ck("错题含原题与解析", all("q" in it and "explain" in it for it in g["items"]))
ck("错题含我的答案", all("myAnswer" in it for it in g["items"]))
ck("错题第5题在列", any(it["questionIdx"]==4 for it in g["items"]))

# ---------- 4. 错题重做（答对→保留但标记"已订正"）----------
# 需求语义：错题答对后不消失，留在错题本里并标记 fixed=True，保留订正足迹
d_course = s.get(f"{BASE}/api/course/p1").json()
q4 = d_course["quiz"][4]
r = s.post(f"{BASE}/api/redo", json={"courseId":"p1","qid":q4["qid"],"chosen":q4["a"]})
ck("重做答对", r.json()["correct"]==True, str(r.json()["correct"]))
wb2 = s.get(f"{BASE}/api/wrongbook").json()
ck("错题答对后仍保留（共 2 道）", wb2["total"]==2, str(wb2["total"]))
_items4 = [it for g in wb2["groups"] for it in g["items"] if it.get("qid")==q4["qid"]]
ck("第5题标记已订正", bool(_items4) and _items4[0]["fixed"] is True,
   str(_items4[0]["fixed"] if _items4 else "缺失"))

# 重做再答错
q5 = d_course["quiz"][5]
r = s.post(f"{BASE}/api/redo", json={"courseId":"p1","qid":q5["qid"],"chosen":(q5["a"]+1)%4})
ck("重做再错", r.json()["correct"]==False)
wb3 = s.get(f"{BASE}/api/wrongbook").json()
ck("错题仍在错题本（共 2 道）", wb3["total"]==2, str(wb3["total"]))
_i5 = [it for g in wb3["groups"] for it in g["items"] if it.get("qid")==q5["qid"]]
ck("第6题未订正", bool(_i5) and _i5[0]["fixed"] is False, str(_i5[0]["fixed"] if _i5 else "缺失"))

# ---------- 5. 计划打卡 ----------
pl = s.get(f"{BASE}/api/plan").json()
ck("计划 10 天", len(pl["plan"])==10, f"{len(pl['plan'])}")
ck("总任务 30 项", sum(len(p["tasks"]) for p in pl["plan"])==30)
r = s.post(f"{BASE}/api/plan/toggle", json={"day":1,"taskIdx":0,"done":True})
ck("打卡成功", r.json()["ok"]==True)
done = r.json()["done"]
ck("打卡记录已存", done.get("1",{}).get("0")==True, str(done))

# ---------- 6. 学习会话（时长）----------
sr = s.post(f"{BASE}/api/session/start", json={"page":"course"}).json()
ck("会话创建", "sessionId" in sr, str(sr.get("sessionId")))
sid = sr["sessionId"]

# ---------- 7. 视频观看 ----------
r = s.post(f"{BASE}/api/video/view", json={"courseId":"p1","videoName":"《社会主义五百年》第1集"})
ck("视频观看记录", r.json()["ok"]==True)

# ---------- 8. 家长端：未登录拒绝 ----------
r = requests.get(f"{BASE}/api/admin/dashboard")
ck("未登录访问看板被拒", r.status_code==401, str(r.status_code))

# ---------- 9. 家长端：错误密码 ----------
r = requests.post(f"{BASE}/api/admin/login", json={"password":"123456"})
ck("错误密码被拒", r.status_code==401, str(r.status_code))

# ---------- 10. 家长端：正确密码 ----------
adm = requests.Session()
r = adm.post(f"{BASE}/api/admin/login", json={"password":"888888"})
ck("默认密码888888登录成功", r.json()["ok"]==True)
ck("登录状态已保持", adm.get(f"{BASE}/api/admin/check").json()["loggedIn"]==True)

# ---------- 11. 家长看板数据 ----------
time.sleep(1.2)   # 让会话持续一下
adm.post(f"{BASE}/api/session/end", json={"sessionId":sid})
db = adm.get(f"{BASE}/api/admin/dashboard").json()
ck("看板含统计", "stats" in db and db["stats"]["total"]>=6, str(db["stats"].get("total")))
ck("看板含学习时长", len(db["studyTime"])>=1, f"{len(db['studyTime'])}天")
ck("今日时长>0", any(x["seconds"]>0 for x in db["studyTime"]),
   str([(x["display"],x["seconds"]) for x in db["studyTime"]]))
ck("看板含各课明细", len(db["byCourse"])>=1, str(len(db["byCourse"])))
ck("看板含视频记录", len(db["videos"])>=1, str(len(db["videos"])))
ck("看板含任务进度", db["tasks"]["done"]>=1, str(db["tasks"]))
ck("连续天数>=1", db["streak"]>=1, str(db["streak"]))

# ---------- 12. 家长配置 ----------
r = adm.post(f"{BASE}/api/admin/config", json={"studentName":"小宁","dailyTarget":"30"})
ck("保存配置成功", r.json()["ok"]==True)
cfg = adm.get(f"{BASE}/api/admin/config").json()
ck("配置已生效-称呼", cfg["studentName"]=="小宁", cfg["studentName"])
ck("配置已生效-目标", cfg["dailyTarget"]=="30", cfg["dailyTarget"])
# 学生端应显示新称呼
d2 = s.get(f"{BASE}/api/courses").json()
ck("学生端显示新称呼", d2["studentName"]=="小宁", d2["studentName"])
# 改回
adm.post(f"{BASE}/api/admin/config", json={"studentName":"同学","dailyTarget":"25"})

# ---------- 13. 修改密码流程 ----------
adm.post(f"{BASE}/api/admin/config", json={"newPassword":"666666"})
adm.post(f"{BASE}/api/admin/logout")
r = adm.post(f"{BASE}/api/admin/login", json={"password":"888888"})
ck("旧密码已失效", r.status_code==401)
r = adm.post(f"{BASE}/api/admin/login", json={"password":"666666"})
ck("新密码可用", r.json()["ok"]==True)
adm.post(f"{BASE}/api/admin/config", json={"newPassword":"888888"})  # 还原

# ---------- 14. 页面可访问 ----------
ck("学生端页面", requests.get(f"{BASE}/").status_code==200)
ck("家长端页面", requests.get(f"{BASE}/admin").status_code==200)

# ---------- 15. 非法输入 ----------
ck("非法课程返回404", requests.get(f"{BASE}/api/course/xxx").status_code==404)
ck("越界题目返回400", s.post(f"{BASE}/api/answer", json={"courseId":"p1","questionIdx":99,"chosen":0}).status_code==400)

print("\n" + "="*56)
ok = sum(1 for _,c,_ in results if c)
print(f"测试结果: {ok} / {len(results)} 通过")
fails = [n for n,c,_ in results if not c]
print("失败项: " + (", ".join(fails) if fails else "无 ✓"))
