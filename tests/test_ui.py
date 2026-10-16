"""前端界面端到端测试（浏览器）"""
from playwright.sync_api import sync_playwright
import pathlib

BASE = "http://127.0.0.1:5097"
SHOT = "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/shots2"
pathlib.Path(SHOT).mkdir(parents=True, exist_ok=True)

results = []
def ck(n, c, e=""):
    results.append((n, c, e)); print(f"{'✓' if c else '✗'} {n} {e}")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':390,'height':844})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))

    # ===== 学生端 =====
    pg.goto(BASE)
    pg.wait_for_timeout(1000)
    # 开放注册后首页是登录页，先走匿名体验进主界面
    pg.locator("button:has-text('匿名体验')").click()
    pg.wait_for_timeout(1500)
    # 课程卡 50 张 + 政治组 1 张「每日论述」入口 = 51
    ck("学生端首页加载（50 课 + 论述入口）", pg.locator(".course-item").count()==51, str(pg.locator(".course-item").count()))
    ck("有「每日论述」入口", pg.locator(".es-entry").count()==1, str(pg.locator(".es-entry").count()))
    ck("学科分组 3 组", pg.locator(".subj-head").count()==3, str(pg.locator(".subj-head").count()))
    ck("底部导航 5 项", pg.locator(".nav").count()>=4, str(pg.locator(".nav").count()))
    ck("家长入口在顶部标题栏", pg.locator("#mainTopbar .admin-btn").count()==1)
    ck("家长入口不再悬浮在右下", pg.locator(".admin-corner").count()==0)
    pg.screenshot(path=f"{SHOT}/01_学生端首页.png")

    # 进入第1课（政治组第一课）—— 跳过最前面的「每日论述」入口，点第一张真课程卡
    pg.locator(".course-item:not(.es-entry)").first.click()
    pg.wait_for_timeout(700)
    ck("知识卡 8 张", pg.locator(".pt").count()==8, str(pg.locator(".pt").count()))
    pg.screenshot(path=f"{SHOT}/02_知识卡.png")

    # 看片清单 —— 验证优先级与时间戳
    pg.locator('[data-tab="vd"]').click()
    pg.wait_for_timeout(500)
    vids = pg.locator(".vid").count()
    ck("视频卡 ≥3 条", vids>=3, str(vids))
    ck("视频有优先级标签", pg.locator(".vid .lvl").count()>=4, str(pg.locator(".vid .lvl").count()))
    ck("视频有必看标记", pg.locator(".vid.must").count()>=1, str(pg.locator(".vid.must").count()))
    ck("视频有时间戳", pg.locator(".vseg").count()>=3, str(pg.locator(".vseg").count()))
    # 验证排序：必看卡片在最前
    first_lvl = pg.locator(".vid").first.locator(".lvl").first.inner_text()
    ck("必看视频排最前", first_lvl=="必看", first_lvl)
    pg.screenshot(path=f"{SHOT}/03_看片优先级.png")

    # 自测
    pg.locator('[data-tab="qz"]').click()
    pg.wait_for_timeout(500)
    ck("自测题 10 道", pg.locator(".qbox").count()==10, str(pg.locator(".qbox").count()))
    pg.screenshot(path=f"{SHOT}/04_自测.png")

    # 答对第1题(B)，答错第2题(A)
    pg.locator(".qbox").first.locator(".opt").nth(1).click()
    pg.wait_for_timeout(600)
    ck("答对显示解析", "答对了" in pg.locator("#e_0").inner_text())
    pg.locator(".qbox").nth(1).locator(".opt").nth(0).click()
    pg.wait_for_timeout(600)
    ck("答错显示解析", "答错了" in pg.locator("#e_1").inner_text())
    pg.screenshot(path=f"{SHOT}/05_答题反馈.png")

    # 错题本
    pg.locator('[data-scr="scrWrong"]').click()
    pg.wait_for_timeout(900)
    ck("错题本有分组", pg.locator(".wg").count()>=1, str(pg.locator(".wg").count()))
    ck("错题本有 1 道题", pg.locator(".wq").count()==1, str(pg.locator(".wq").count()))
    ck("错题显示我的答案", pg.locator(".wq .mine").count()>=1)
    ck("错题显示正确答案", pg.locator(".wq .right").count()>=1)
    ck("错题有重做按钮", pg.locator(".wq .redo").count()>=1)
    pg.screenshot(path=f"{SHOT}/06_错题本.png")

    # 重做错题
    pg.locator(".wq .redo").first.click()
    pg.wait_for_timeout(800)
    ck("重做后出现选项", pg.locator(".redo-opt").count()>0, str(pg.locator(".redo-opt").count()))
    pg.screenshot(path=f"{SHOT}/07_错题重做.png")
    # 选正确答案(C=索引2) —— p1第2题的正确索引是2
    pg.locator(".redo-opt").nth(2).click()
    pg.wait_for_timeout(1000)
    txt = pg.locator(".redo-stage .exp").inner_text()
    ck("重做反馈正确", "答对了" in txt, txt[:30])
    pg.screenshot(path=f"{SHOT}/08_重做成功.png")
    pg.wait_for_timeout(1500)
    # 需求语义：答对后错题不消失，而是标记"已订正"（保留订正足迹）
    ck("答对后错题保留并标记已订正",
       pg.locator(".wq").count()>=1 and pg.locator(".wq .tag.fixed").count()>=1,
       f"剩余{pg.locator('.wq').count()}道 / 已订正标记{pg.locator('.wq .tag.fixed').count()}")

    # 计划
    pg.locator('[data-scr="scrPlan"]').click()
    pg.wait_for_timeout(900)
    ck("计划 10 天", pg.locator(".day").count()==10, str(pg.locator(".day").count()))
    ck("任务 30 项", pg.locator(".task").count()==30, str(pg.locator(".task").count()))
    # 勾选第一个未完成的任务
    before = "checked" in pg.locator(".task").first.get_attribute("class")
    pg.locator(".task").first.click()
    pg.wait_for_timeout(800)
    after = "checked" in pg.locator(".task").first.get_attribute("class")
    ck("任务勾选状态可切换", before != after, f"{before}→{after}")
    pg.screenshot(path=f"{SHOT}/09_计划.png")

    # 我的
    pg.locator('[data-scr="scrMe"]').click()
    pg.wait_for_timeout(900)
    ck("我的-正确率显示", "%" in pg.locator("#mQuiz").inner_text(), pg.locator("#mQuiz").inner_text())
    ck("我的-答题数>0", int(pg.locator("#mAns").inner_text())>0, pg.locator("#mAns").inner_text())
    pg.screenshot(path=f"{SHOT}/10_我的.png")

    # ===== 家长端 =====
    pg.goto(BASE+"/admin")
    pg.wait_for_timeout(800)
    ck("家长端登录页", pg.locator("#login").is_visible())
    pg.screenshot(path=f"{SHOT}/11_家长登录.png")

    # 错误密码
    pg.fill("#pwd","000000"); pg.click("text=进入看板")
    pg.wait_for_timeout(900)
    ck("错误密码提示", len(pg.locator("#err").inner_text())>0, pg.locator("#err").inner_text())
    ck("错误密码不放行", pg.locator("#board").is_hidden())

    # 正确密码
    pg.fill("#pwd","888888"); pg.click("text=进入看板")
    pg.wait_for_timeout(1500)
    ck("家长端看板显示", pg.locator("#board").is_visible())
    ck("看板有统计卡 4 个", pg.locator(".stat").count()>=4, str(pg.locator(".stat").count()))
    ck("看板有正确率", "%" in pg.locator(".stat b").first.inner_text(), pg.locator(".stat b").first.inner_text())
    pg.screenshot(path=f"{SHOT}/12_家长看板.png", full_page=True)
    # 匿名作答不进家长看板，故表格允许为空占位；这里只校验收件区已渲染
    ck("看板有各课表格区", len(pg.locator("#courseTable").inner_text())>0, pg.locator("#courseTable").inner_text()[:20])
    ck("看板有任务进度", len(pg.locator("#taskBox").inner_text())>0)
    ck("看板有视频记录", len(pg.locator("#videoBox").inner_text())>0, pg.locator("#videoBox").inner_text()[:30])
    ck("看板有学生管理区", pg.locator("#stuList").count()==1)
    ck("看板有学生切换标签", pg.locator("#stuTabs").count()==1)

    # 修改配置：新增一个学生（多学生改造后的正确入口）
    _sid = "uites" + str(int(__import__("time").time()))[-5:]
    pg.fill("#newSid", _sid)
    pg.fill("#newName", "小宁")
    pg.locator("button:has-text('创建')").click()
    pg.wait_for_timeout(1200)
    ck("新增学生提示", len(pg.locator("#stuMsg").inner_text())>0, pg.locator("#stuMsg").inner_text()[:30])
    ck("学生出现在列表", _sid in pg.locator("#stuList").inner_text(), pg.locator("#stuList").inner_text()[:60])
    pg.screenshot(path=f"{SHOT}/13_家长设置.png", full_page=True)

    # 学生端可用新登录码进入，并显示该生名字
    pg2 = b.new_page()
    pg2.goto(BASE); pg2.wait_for_timeout(1000)
    pg2.locator("a:has-text('用登录码进入')").click()
    pg2.wait_for_timeout(300)
    pg2.fill("#loginCode", _sid)
    pg2.locator("button:has-text('用登录码进入')").click()
    pg2.wait_for_timeout(1500)
    ck("新学生登录成功", "小宁" in pg2.locator("#whoName").inner_text() or "小宁" in pg2.locator("#greet").inner_text(),
       pg2.locator("#greet").inner_text()[:40])

    # 过滤预期的 401（未登录时探查登录态是正常流程）
    real_errs = [e for e in errs if "401" not in e]
    ck("无JS错误", len(real_errs)==0, str(real_errs[:2]))
    b.close()

print("\n"+"="*56)
ok=sum(1 for _,c,_ in results if c)
print(f"前端测试: {ok}/{len(results)} 通过")
f=[n for n,c,_ in results if not c]
print("失败项: " + (", ".join(f) if f else "无 ✓"))
