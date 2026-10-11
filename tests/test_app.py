from playwright.sync_api import sync_playwright
import pathlib, json

TEST_FILE = "file://" + str(pathlib.Path("/workspace/政史地复习助手-政治必修1.html").resolve())
SHOT = "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/shots"
pathlib.Path(SHOT).mkdir(parents=True, exist_ok=True)

results = []
def check(name, cond, extra=""):
    results.append((name, cond, extra))
    print(f"{'✓' if cond else '✗'} {name} {extra}")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':390,'height':844})  # 手机尺寸
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))

    pg.goto(TEST_FILE)
    pg.wait_for_timeout(600)

    # 1. 首页课程列表渲染
    items = pg.locator(".course-item").count()
    check("课程列表渲染 39 项", items == 39, f"实际{items}")

    # 2. 总进度初始
    txt = pg.locator("#totalTxt").inner_text()
    check("总进度初始为0", "0%" in txt, txt)

    pg.screenshot(path=f"{SHOT}/01_首页.png")

    # 3. 进入第1课
    pg.locator(".course-item").first.click()
    pg.wait_for_timeout(400)
    check("详情页显示", pg.locator("#scrDetail").is_visible())
    pts = pg.locator(".pt").count()
    check("知识卡渲染 8 张", pts == 8, f"实际{pts}")
    pg.screenshot(path=f"{SHOT}/02_知识卡.png")

    # 4. 切换到看片清单
    pg.locator('[data-tab="vd"]').click()
    pg.wait_for_timeout(300)
    vids = pg.locator(".vid").count()
    links = pg.locator(".vid a").count()
    check("看片清单 4 条", vids == 4, f"实际{vids}")
    check("视频链接 4 个", links == 4, f"实际{links}")
    href = pg.locator(".vid a").first.get_attribute("href")
    check("链接指向B站搜索", "bilibili.com" in href, href[:50])
    pg.screenshot(path=f"{SHOT}/03_看片.png")

    # 5. 切换到自测
    pg.locator('[data-tab="qz"]').click()
    pg.wait_for_timeout(300)
    qs = pg.locator(".qbox").count()
    check("自测题渲染 10 道", qs == 10, f"实际{qs}")
    pg.screenshot(path=f"{SHOT}/04_自测.png")

    # 6. 答对一题（第1题正确答案是 B=索引1）
    pg.locator(".qbox").first.locator(".opt").nth(1).click()
    pg.wait_for_timeout(300)
    exp = pg.locator("#e_p1_0")
    check("答对后显示解析", exp.is_visible() and "答对了" in exp.inner_text())
    right_cls = pg.locator(".qbox").first.locator(".opt").nth(1).get_attribute("class")
    check("正确项标绿", "right" in right_cls, right_cls)

    # 7. 答错一题（第2题正确答案 C=索引2，选 A=0）
    pg.locator(".qbox").nth(1).locator(".opt").nth(0).click()
    pg.wait_for_timeout(300)
    exp2 = pg.locator("#e_p1_1")
    check("答错后显示解析", exp2.is_visible() and "答错了" in exp2.inner_text())
    wrong_cls = pg.locator(".qbox").nth(1).locator(".opt").nth(0).get_attribute("class")
    check("错误项标红", "wrong" in wrong_cls, wrong_cls)
    pg.screenshot(path=f"{SHOT}/05_答题后.png")

    # 8. 返回首页看进度更新
    pg.locator(".back").click()
    pg.wait_for_timeout(400)
    txt2 = pg.locator("#totalTxt").inner_text()
    check("首页进度已更新", "1 / 40" in txt2 or "2%" in txt2, txt2)
    prog = pg.locator(".course-item").first.inner_text()
    check("课程项显示 1/10", "1/10" in prog.replace(" ", ""), prog.replace("\n"," ")[:60])

    # 9. 打卡激励（顶部streak）
    streak = pg.locator("#streakBox").inner_text()
    check("打卡天数已记录为1", "1" in streak, streak)

    # 10. 计划页
    pg.locator('[data-scr="scrPlan"]').click()
    pg.wait_for_timeout(400)
    days = pg.locator(".day").count()
    check("计划页 10 天", days == 10, f"实际{days}")
    tasks = pg.locator(".task").count()
    check("任务项共 30 个", tasks == 30, f"实际{tasks}")
    pg.screenshot(path=f"{SHOT}/06_计划.png")
    # 勾选第一个任务
    pg.locator(".task").first.click()
    pg.wait_for_timeout(300)
    check("勾选任务生效", "checked" in pg.locator(".task").first.get_attribute("class"))
    plan_txt = pg.locator("#planTxt").inner_text()
    check("计划进度更新", "1 / 30" in plan_txt, plan_txt)
    pg.screenshot(path=f"{SHOT}/07_计划勾选.png")

    # 11. 我的页
    pg.locator('[data-scr="scrMe"]').click()
    pg.wait_for_timeout(400)
    mquiz = pg.locator("#mQuiz").inner_text()
    mdays = pg.locator("#mDays").inner_text()
    mdone = pg.locator("#mDone").inner_text()
    check("我的-正确率显示", mquiz == "50%", mquiz)  # 1对1错
    check("我的-打卡天数", mdays == "1", mdays)
    check("我的-完成任务数", mdone == "1", mdone)
    rate = pg.locator(".rate .rt").inner_text()
    check("我的-评级出现", len(rate) > 0, rate)
    pg.screenshot(path=f"{SHOT}/08_我的.png")

    # 12. 刷新后数据持久化
    pg.reload()
    pg.wait_for_timeout(600)
    txt3 = pg.locator("#totalTxt").inner_text()
    check("刷新后进度保留", "1 / 40" in txt3, txt3)
    pg.locator('[data-scr="scrMe"]').click()
    pg.wait_for_timeout(300)
    check("刷新后打卡保留", pg.locator("#mDays").inner_text() == "1")

    # 13. 控制台无错误
    check("无JS控制台错误", len(errors) == 0, str(errors[:3]))

    b.close()

print("\n" + "="*50)
ok = sum(1 for _,c,_ in results if c)
print(f"测试结果: {ok} / {len(results)} 通过")
fails = [n for n,c,_ in results if not c]
if fails:
    print("失败项:", fails)
else:
    print("全部通过 ✓")
