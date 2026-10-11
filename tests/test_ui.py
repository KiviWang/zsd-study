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
    pg.wait_for_timeout(1200)
    ck("学生端首页加载", pg.locator(".course-item").count()==4, str(pg.locator(".course-item").count()))
    ck("底部导航 4 项", pg.locator(".nav").count()==4)
    ck("家长入口存在", pg.locator(".admin-corner").count()==1)
    ck("头像显示学生称呼", "同学" in pg.locator("#greet").inner_text(), pg.locator("#greet").inner_text())
    pg.screenshot(path=f"{SHOT}/01_学生端首页.png")

    # 进入第1课
    pg.locator(".course-item").first.click()
    pg.wait_for_timeout(700)
    ck("知识卡 8 张", pg.locator(".pt").count()==8, str(pg.locator(".pt").count()))
    pg.screenshot(path=f"{SHOT}/02_知识卡.png")

    # 看片清单 —— 验证优先级与时间戳
    pg.locator('[data-tab="vd"]').click()
    pg.wait_for_timeout(500)
    vids = pg.locator(".vid").count()
    ck("视频卡 4 条", vids==4, str(vids))
    ck("视频有优先级标签", pg.locator(".vid .lvl").count()>=4, str(pg.locator(".vid .lvl").count()))
    ck("视频有必看标记", pg.locator(".vid.must").count()>=1, str(pg.locator(".vid.must").count()))
    ck("视频有时间戳", pg.locator(".vseg").count()>=4, str(pg.locator(".vseg").count()))
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
    ck("重做后出现选项", pg.locator(".redo-area .opt").count()>0, str(pg.locator(".redo-area .opt").count()))
    pg.screenshot(path=f"{SHOT}/07_错题重做.png")
    # 选正确答案(C=索引2) —— p1第2题的正确索引是2
    pg.locator(".redo-area .opt").nth(2).click()
    pg.wait_for_timeout(1000)
    txt = pg.locator(".redo-area .exp").inner_text()
    ck("重做反馈正确", "答对了" in txt, txt[:30])
    pg.screenshot(path=f"{SHOT}/08_重做成功.png")
    pg.wait_for_timeout(1500)
    ck("答对后错题本清空", pg.locator(".wq").count()==0 or "空的" in pg.locator("#wrongList").inner_text(),
       f"剩余{pg.locator('.wq').count()}道")

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
    ck("看板有各课表格", pg.locator("#courseTable table").count()>=1)
    ck("看板有任务进度", len(pg.locator("#taskBox").inner_text())>0)
    ck("看板有视频记录", len(pg.locator("#videoBox").inner_text())>0, pg.locator("#videoBox").inner_text()[:30])

    # 修改配置
    pg.fill("#cfgName","小宁")
    pg.locator("button:has-text('保存全部设置')").click()
    pg.wait_for_timeout(1200)
    ck("配置保存提示", "已保存" in pg.locator("#cfgSaved").inner_text(), pg.locator("#cfgSaved").inner_text())
    pg.screenshot(path=f"{SHOT}/13_家长设置.png", full_page=True)

    # 学生端应显示新称呼
    pg2 = b.new_page()
    pg2.goto(BASE); pg2.wait_for_timeout(1200)
    ck("学生端同步新称呼", "小宁" in pg2.locator("#greet").inner_text(), pg2.locator("#greet").inner_text())

    # 过滤预期的 401（未登录时探查登录态是正常流程）
    real_errs = [e for e in errs if "401" not in e]
    ck("无JS错误", len(real_errs)==0, str(real_errs[:2]))
    b.close()

print("\n"+"="*56)
ok=sum(1 for _,c,_ in results if c)
print(f"前端测试: {ok}/{len(results)} 通过")
f=[n for n,c,_ in results if not c]
print("失败项: " + (", ".join(f) if f else "无 ✓"))
