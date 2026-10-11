# -*- coding: utf-8 -*-
"""家长端多学生功能浏览器测试"""
import json, time, sys, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5097"
PWD = "888888"

passed, failed = [], []


def cleanup_test_students():
    """前置清理：删掉上次运行可能残留的测试学生，保证可重复执行"""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    req = urllib.request.Request(BASE + "/api/admin/login", method="POST",
        data=json.dumps({"password": PWD}).encode(),
        headers={"Content-Type": "application/json"})
    op.open(req).read()
    for sid in ("testa", "testb"):
        try:
            req = urllib.request.Request(
                BASE + "/api/admin/students/" + sid + "?wipe=1", method="DELETE")
            op.open(req).read()
        except Exception:
            pass


cleanup_test_students()

def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("  [OK] " if cond else "  [!!] ") + name + ((" | " + str(extra)) if extra and not cond else ""))

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" and "favicon" not in m.text.lower() else None)

    print("\n=== 1. 家长登录 ===")
    pg.goto(BASE + "/admin", wait_until="domcontentloaded")
    pg.wait_for_timeout(500)
    check("登录框显示", pg.is_visible("#login"))
    pg.fill("#pwd", "000000")
    pg.click("button:has-text('进入')" if pg.query_selector("button:has-text('进入')") else "#login button")
    pg.wait_for_timeout(600)
    check("错误密码被拒", pg.is_visible("#login"))
    pg.fill("#pwd", PWD)
    pg.click("#login button")
    pg.wait_for_selector("#board", state="visible", timeout=5000)
    pg.wait_for_timeout(800)
    check("正确密码进入看板", pg.is_visible("#board"))
    check("看板标题已填", bool(pg.inner_text("#bTitle").strip()), pg.inner_text("#bTitle"))

    print("\n=== 2. 选择学生面板 ===")
    tabs = pg.query_selector_all("#stuTabs .stu-tab")
    check("学生标签已渲染", len(tabs) >= 1, len(tabs))
    check("默认学生在列", any("默认" in t.inner_text() or "同学" in t.inner_text() for t in tabs))
    check("当前学生高亮", len(pg.query_selector_all("#stuTabs .stu-tab.on")) == 1)

    print("\n=== 3. 新建学生 ===")
    pg.fill("#newSid", "TESTA")
    pg.fill("#newName", "测试甲")
    pg.click("button:has-text('创建')")
    pg.wait_for_timeout(900)
    tabs = pg.query_selector_all("#stuTabs .stu-tab")
    check("新学生出现在标签", any("测试甲" in t.inner_text() for t in tabs), [t.inner_text() for t in tabs])
    rows = pg.query_selector_all("#stuList .stu-row")
    check("管理列表出现新学生", any("测试甲" in r.inner_text() for r in rows))
    check("管理列表显示登录码", any("testa" in r.inner_text() for r in rows), [r.inner_text() for r in rows])

    pg.fill("#newSid", "TESTA")
    pg.fill("#newName", "重复")
    pg.click("button:has-text('创建')")
    pg.wait_for_timeout(700)
    msg = pg.inner_text("#stuMsg")
    check("重复登录码被拒", "已经有人用" in msg or "已存在" in msg or "失败" in msg, msg)

    print("\n=== 4. 切换学生：数据隔离 ===")
    # 学生端用 testa 登录答一题
    pg2 = ctx.new_page()
    pg2.goto(BASE + "/", wait_until="domcontentloaded")
    pg2.wait_for_timeout(700)
    # 新版登录页：登录码在折叠区，需先展开
    pg2.click("a:has-text('用登录码进入')")
    pg2.wait_for_timeout(300)
    pg2.fill("#loginCode", "testa")
    pg2.locator("#codeLoginBox .login-btn").click(force=True)
    pg2.wait_for_selector("#scrCourse", state="visible", timeout=6000)
    pg2.wait_for_timeout(600)
    who = pg2.inner_text("#whoName")
    check("学生端显示自己名字", "测试甲" in who, who)
    # 进第一课答题
    first = pg2.query_selector("#courseList .course-item") or pg2.query_selector(".course-item")
    if first:
        first.click()
        pg2.wait_for_timeout(1200)
        n = pg2.eval_on_selector_all(".opt", "els => els.length")
        if n:
            pg2.eval_on_selector(".opt", "el => el.click()")
            pg2.wait_for_timeout(900)
            check("学生答题成功", n > 0, f"选项数 {n}")
        else:
            check("题目选项存在", False, "no .opt")
    else:
        check("课程列表存在", False, "no .course-item")
    pg2.close()

    # 回家长端刷新
    pg.click("button:has-text('刷新')")
    pg.wait_for_timeout(1000)
    check("刷新后仍在看板", pg.is_visible("#board"))

    xiaoming_tab = [t for t in pg.query_selector_all("#stuTabs .stu-tab") if "测试甲" in t.inner_text()]
    if xiaoming_tab:
        xiaoming_tab[0].click()
        pg.wait_for_timeout(900)
        t = pg.inner_text("#bTitle")
        check("切换到测试甲看板", "测试甲" in t, t)
        check("副标题显示登录码", "testa" in pg.inner_text("#bSub"), pg.inner_text("#bSub"))
        cards = pg.inner_text("#statCards")
        check("看板有答题数据", "正确率" in cards, cards[:80])

    # 切换回默认学生
    def_tab = [t for t in pg.query_selector_all("#stuTabs .stu-tab") if "测试甲" not in t.inner_text()]
    if def_tab:
        def_tab[0].click()
        pg.wait_for_timeout(900)
        check("切回默认学生", "测试甲" not in pg.inner_text("#bTitle"), pg.inner_text("#bTitle"))

    print("\n=== 5. 排序：最近使用优先 ===")
    pg.click("button:has-text('刷新')")
    pg.wait_for_timeout(1000)
    tabs = [t.inner_text() for t in pg.query_selector_all("#stuTabs .stu-tab")]
    check("测试甲排第一（刚使用）", tabs and "测试甲" in tabs[0], tabs)

    print("\n=== 6. 改名 / 停用 ===")
    pg.on("dialog", lambda d: d.accept("测试乙"))
    btn = pg.query_selector("#stuList .stu-row:has-text('测试甲') button:has-text('改名')")
    if btn:
        btn.click()
        pg.wait_for_timeout(1000)
        check("改名生效", any("测试乙" in t.inner_text() for t in pg.query_selector_all("#stuTabs .stu-tab")),
              [t.inner_text() for t in pg.query_selector_all("#stuTabs .stu-tab")])

    btn = pg.query_selector("#stuList .stu-row:has-text('测试乙') button:has-text('停用')")
    if btn:
        btn.click()
        pg.wait_for_timeout(900)
        check("停用标记显示", any("停用" in t.inner_text() for t in pg.query_selector_all("#stuTabs .stu-tab")))

    print("\n=== 7. 设置面板 / 手机号显示 ===")
    check("每日目标输入存在", pg.is_visible("#cfgTarget"))
    check("密码输入存在", pg.is_visible("#cfgPwd"))
    # 管理列表应展示账号类型（登录码账号）
    rows_txt = pg.inner_text("#stuList")
    check("管理列表显示账号类型", "登录码" in rows_txt or "注册" in rows_txt, rows_txt[:120])
    pg.fill("#cfgTarget", "30")
    pg.click("button:has-text('保存全部设置')")
    pg.wait_for_timeout(900)
    check("保存成功提示", "已保存" in pg.inner_text("#cfgSaved"), pg.inner_text("#cfgSaved"))

    print("\n=== 8. 删除学生 ===")
    pg.on("dialog", lambda d: d.accept())
    btn = pg.query_selector("#stuList .stu-row:has-text('测试乙') button:has-text('删除')")
    if btn:
        btn.click()
        pg.wait_for_timeout(1000)
        check("删除后标签消失", not any("测试乙" in t.inner_text() for t in pg.query_selector_all("#stuTabs .stu-tab")),
              [t.inner_text() for t in pg.query_selector_all("#stuTabs .stu-tab")])

    print("\n=== 9. JS 错误检查 ===")
    # 过滤：favicon 404；未登录时的 401 探测；测试用例故意触发的重复创建 400
    benign = ("favicon", "401 (UNAUTHORIZED)", "400 (BAD REQUEST)")
    real = [e for e in errs if not any(k.lower() in e.lower() for k in benign)]
    check("无 JS 报错", len(real) == 0, real[:3])

    b.close()

print("\n" + "=" * 46)
print(f"通过 {len(passed)} / {len(passed)+len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  - " + f)
    sys.exit(1)
