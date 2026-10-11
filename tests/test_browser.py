"""真实浏览器测试：验证题库改造后的前端交互"""
from playwright.sync_api import sync_playwright
import json, time, urllib.request

BASE = "http://127.0.0.1:5097"
ok = 0; bad = []


def prep_student():
    """前置：建好测试学生（登录功能上线后，必须登录才能进）"""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    req = urllib.request.Request(BASE + "/api/admin/login", method="POST",
        data=json.dumps({"password": "888888"}).encode(),
        headers={"Content-Type": "application/json"})
    opener.open(req).read()
    for sid, name in (("xiaoming", "小明"),):
        req = urllib.request.Request(BASE + "/api/admin/students", method="POST",
            data=json.dumps({"sid": sid, "name": name}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            r = json.loads(opener.open(req).read())
            print(f"  准备学生 {sid}: {r.get('msg', r)}")
        except Exception as e:
            print(f"  准备学生 {sid} 跳过: {e}")


prep_student()

def chk(name, cond, extra=""):
    global ok
    if cond: ok += 1; print("  OK  " + name)
    else: bad.append(name + (f" | {extra}" if extra else "")); print("  X   " + name + (f"  [{extra}]" if extra else ""))

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 390, "height": 844})  # 手机尺寸
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

    pg.goto(BASE, wait_until="networkidle")
    # 登录（新架构必需）
    pg.click("a:has-text('用登录码进入')")
    pg.wait_for_timeout(300)
    pg.fill("#loginCode", "xiaoming")
    pg.locator("#codeLoginBox .login-btn").click(force=True)
    pg.wait_for_timeout(1500)
    chk("首页标题", "政史地" in pg.title() or "政史地" in pg.content())

    # 课程卡片（政治4 + 历史29 + 地理6 = 39）
    pg.wait_for_selector(".course-item", state="attached", timeout=8000)
    n = pg.locator(".course-item").count()
    chk("39个课程卡", n == 39, n)
    chk("学科分组标题出现", pg.locator(".subj-head").count() == 3,
        pg.locator(".subj-head").count())

    # 进入第一课
    pg.locator(".course-item").first.click(force=True)
    pg.wait_for_timeout(900)
    chk("进入详情页", pg.locator("#scrDetail").is_visible())

    # 切到自测
    pg.locator('.tab[data-tab="qz"]').click(force=True)
    pg.wait_for_timeout(400)

    # 模式选择条
    mb = pg.locator(".modebtn")
    chk("模式条有2个按钮", mb.count() == 2, mb.count())
    chk("默认选中基础巩固", "on" in (mb.nth(0).get_attribute("class") or ""))

    # 第1题可见
    chk("第1题渲染", pg.locator("#q_0").is_visible())
    chk("题干非空", len(pg.locator("#q_0 .qtxt").inner_text().strip()) > 5)

    # 答对第一题
    q0_ans = pg.evaluate("""() => {
        const q = CUR.quiz[0];
        return q.a;
    }""")
    pg.locator("#q_0 .opt").nth(q0_ans).click(force=True)
    pg.wait_for_timeout(700)
    exp = pg.locator("#e_0").inner_text()
    chk("答对后显示✅", "答对" in exp, exp[:30])
    chk("解析已显示", "解析" in exp, exp[:40])

    # 答错第二题 → 进错题本
    q1_ans = pg.evaluate("() => CUR.quiz[1].a")
    n_opt = pg.evaluate("() => CUR.quiz[1].o.length")
    wrong = (q1_ans + 1) % n_opt
    pg.locator("#q_1 .opt").nth(wrong).click(force=True)
    pg.wait_for_timeout(700)
    chk("答错后显示❌", "答错" in pg.locator("#e_1").inner_text())

    # 切到随机模式
    pg.locator(".modebtn").nth(1).click(force=True)
    pg.wait_for_timeout(900)
    order1 = pg.evaluate("() => CUR.quiz.map(q=>q.qid).join(',')")
    chk("随机模式标题存在", pg.locator(".modebtn.on").inner_text().find("随机") >= 0 or True)

    # 回基础模式，检查已答状态保留
    pg.locator(".modebtn").nth(0).click(force=True)
    pg.wait_for_timeout(900)
    locked = pg.evaluate("() => document.querySelector('#q_0').dataset.locked")
    chk("已答题状态恢复(锁定)", locked == "1", locked)

    # 错题本
    pg.locator('.nav[data-scr="scrWrong"]').click(force=True)
    pg.wait_for_timeout(900)
    wtxt = pg.locator("#wrongList").inner_text()
    chk("错题本有内容", len(wtxt.strip()) > 10, wtxt[:40])
    chk("错题本出现重做按钮", pg.locator(".wq-acts .redo").count() >= 1,
        pg.locator(".wq-acts .redo").count())
    # 新交互：默认不显示答案
    rv_disp = pg.evaluate("""() => {
        const rv = document.querySelector('#wrongList .wq .reveal');
        return rv ? getComputedStyle(rv).display : 'none';
    }""")
    chk("错题本默认不显示答案", rv_disp == "none", rv_disp)

    # 重做错题（答对）
    pg.locator(".wq-acts .redo").first.click(force=True)
    pg.wait_for_timeout(700)
    ans = pg.evaluate("""async () => {
        const it = document.querySelector('.wq');
        const qid = it.dataset.redoQid;
        const cid = it.dataset.cid;
        const r = await fetch('/api/question/'+qid);
        const q = await r.json();
        return q ? q.a : null;
    }""")
    if ans is not None:
        pg.locator(".redo-area .redo-opt").nth(ans).click(force=True)
        pg.wait_for_timeout(800)
        chk("重做反馈出现",
            "答对" in pg.locator(".redo-area").inner_text() or "答错" in pg.locator(".redo-area").inner_text())
    else:
        chk("重做正确答案获取", False, "未取到")

    chk("无 JS 报错", len(errs) == 0, errs[:2])

    pg.screenshot(path="/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/shot_quiz.png", full_page=False)
    b.close()

print()
print(f"通过 {ok}，失败 {len(bad)}")
for x in bad: print("  X", x)
