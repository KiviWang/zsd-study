"""浏览器测试：多学生登录、隔离、切换"""
import json, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5097"
ok = 0; bad = []


def prep_students():
    """前置：通过家长端 API 建好测试用学生（清库后必做）"""
    jar = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(jar)
    req = urllib.request.Request(BASE + "/api/admin/login", method="POST",
        data=json.dumps({"password": "888888"}).encode(),
        headers={"Content-Type": "application/json"})
    opener.open(req).read()
    for sid, name in (("xiaoming", "小明"), ("lisi", "李四")):
        req = urllib.request.Request(BASE + "/api/admin/students", method="POST",
            data=json.dumps({"sid": sid, "name": name}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            r = json.loads(opener.open(req).read())
            print(f"  准备学生 {sid}: {r.get('msg', r)}")
        except Exception as e:
            print(f"  准备学生 {sid} 跳过: {e}")


prep_students()

def chk(n, cond, extra=""):
    global ok
    if cond: ok += 1; print("  OK  " + n)
    else: bad.append(n + (f" | {extra}" if extra else "")); print("  X   " + n + (f"  [{extra}]" if extra else ""))

with sync_playwright() as p:
    b = p.chromium.launch()

    # ===== 小明 =====
    pg = b.new_page(viewport={"width": 390, "height": 844})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

    pg.goto(BASE, wait_until="networkidle")
    pg.click("a:has-text('用登录码进入')")
    pg.wait_for_timeout(300)
    chk("显示登录页", pg.locator("#scrLogin").is_visible())
    chk("顶部栏隐藏", not pg.locator("#mainTopbar").is_visible())

    # 错误码
    pg.evaluate("(v) => document.getElementById('loginCode').value = v", "nobody")
    pg.evaluate("() => doLogin()")
    pg.wait_for_timeout(900)
    msg = pg.locator("#loginMsg").inner_text()
    chk("错误码提示", "不存在" in msg or "确认" in msg, msg)

    # 正确码
    pg.evaluate("(v) => document.getElementById('loginCode').value = v", "xiaoming")
    pg.evaluate("() => doLogin()")
    pg.wait_for_timeout(1400)
    chk("登录后进入主界面", pg.locator("#mainTopbar").is_visible())
    chk("登录页隐藏", not pg.locator("#scrLogin").is_visible())
    chk("顶部显示名字", pg.locator("#whoName").inner_text() == "小明", pg.locator("#whoName").inner_text())
    chk("问候语带名字", "小明" in pg.locator("#greet").inner_text(), pg.locator("#greet").inner_text())

    # 答题（答对第1题）
    pg.wait_for_selector(".course-item", state="attached", timeout=8000)
    pg.eval_on_selector(".course-item", "el => el.click()")
    pg.wait_for_timeout(900)
    pg.eval_on_selector('.tab[data-tab="qz"]', "el => el.click()")
    pg.wait_for_timeout(500)
    a0 = pg.evaluate("() => CUR.quiz[0].a")
    pg.evaluate("(i) => document.querySelectorAll('#q_0 .opt')[i].click()", a0)
    pg.wait_for_timeout(800)
    chk("小明答对", "答对" in pg.locator("#e_0").inner_text())

    # 刷新后仍登录（记住登录）
    pg.reload(wait_until="networkidle")
    pg.wait_for_timeout(1200)
    chk("刷新后仍登录", pg.locator("#mainTopbar").is_visible())
    chk("刷新后名字还在", pg.locator("#whoName").inner_text() == "小明")

    # 身份弹层
    pg.eval_on_selector(".who", "el => el.click()")
    pg.wait_for_timeout(400)
    chk("弹层显示", pg.locator("#whoMask").is_visible())
    chk("弹层含登录码", "xiaoming" in pg.locator("#whoSub").inner_text(), pg.locator("#whoSub").inner_text())
    pg.eval_on_selector_all("#whoMask .btn.ghost", "els => els[els.length-1].click()")  # 取消
    pg.wait_for_timeout(400)
    chk("取消后关闭", not pg.locator("#whoMask").is_visible())

    real_errs = [e for e in errs if "favicon" not in e.lower() and "404" not in e]
    chk("小明页无JS报错", len(real_errs) == 0, real_errs[:2])
    pg.screenshot(path="/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/shot_student.png")
    pg.close()

    # ===== 李四（新浏览器上下文，模拟另一台手机）=====
    ctx2 = b.new_context(viewport={"width": 390, "height": 844})
    pg2 = ctx2.new_page()
    errs2 = []
    pg2.on("pageerror", lambda e: errs2.append(str(e)))
    pg2.goto(BASE, wait_until="networkidle")
    chk("新设备显示登录页", pg2.locator("#scrLogin").is_visible())

    pg2.evaluate("(v) => document.getElementById('loginCode').value = v", "lisi")
    pg2.evaluate("() => doLogin()")
    pg2.wait_for_timeout(1400)
    chk("李四登录成功", pg2.locator("#whoName").inner_text() == "李四", pg2.locator("#whoName").inner_text())

    # 李四答错第1题
    pg2.wait_for_selector(".course-item", state="attached", timeout=8000)
    pg2.eval_on_selector(".course-item", "el => el.click()")
    pg2.wait_for_timeout(900)
    pg2.eval_on_selector('.tab[data-tab="qz"]', "el => el.click()")
    pg2.wait_for_timeout(500)
    a0b = pg2.evaluate("() => CUR.quiz[0].a")
    nb = pg2.evaluate("() => CUR.quiz[0].o.length")
    pg2.evaluate("(i) => document.querySelectorAll('#q_0 .opt')[i].click()", (a0b + 1) % nb)
    pg2.wait_for_timeout(800)
    chk("李四答错", "答错" in pg2.locator("#e_0").inner_text())

    # 李四错题本应有1题，小明应该是0题
    pg2.eval_on_selector('.nav[data-scr="scrWrong"]', "el => el.click()")
    pg2.wait_for_timeout(900)
    chk("李四错题本有内容", len(pg2.locator("#wrongList").inner_text().strip()) > 10)

    # 李四切回小明：应看不到李四的错题（数据隔离）
    ctx2.close()

    ctx3 = b.new_context(viewport={"width": 390, "height": 844})
    pg3 = ctx3.new_page()
    pg3.goto(BASE, wait_until="networkidle")
    pg3.evaluate("(v) => document.getElementById('loginCode').value = v", "xiaoming")
    pg3.evaluate("() => doLogin()")
    pg3.wait_for_timeout(1400)
    pg3.eval_on_selector('.nav[data-scr="scrWrong"]', "el => el.click()")
    pg3.wait_for_timeout(900)
    txt = pg3.locator("#wrongList").inner_text()
    chk("小明错题本为空(数据隔离)", "没有错题" in txt or len(txt.strip()) < 30, txt[:40])

    # 退出登录
    pg3.eval_on_selector(".who", "el => el.click()")
    pg3.wait_for_timeout(400)
    pg3.eval_on_selector("#whoMask .btn.ghost", "el => el.click()")  # 退出
    try:
        pg3.wait_for_function("() => document.getElementById('scrLogin').classList.contains('active')", timeout=8000)
        logged_out = True
    except Exception:
        logged_out = False
    chk("退出后回登录页", logged_out)

    ctx3.close()
    b.close()

print()
print(f"通过 {ok}，失败 {len(bad)}")
for x in bad: print("  X", x)
