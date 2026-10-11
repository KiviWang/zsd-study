# -*- coding: utf-8 -*-
"""浏览器测试：开放注册 + 匿名登录 + 记住登录"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5097"
SHOT = "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/"
passed, failed = [], []

def _cleanup(sid):
    """前置清理：删掉可能残留的测试账号，保证可重复运行"""
    import json as _j, urllib.request as _u
    try:
        op = _u.build_opener(_u.HTTPCookieProcessor())
        req = _u.Request(BASE + "/api/admin/login", method="POST",
            data=_j.dumps({"password": "888888"}).encode(),
            headers={"Content-Type": "application/json"})
        op.open(req).read()
        req = _u.Request(BASE + "/api/admin/students/" + sid + "?wipe=1", method="DELETE")
        op.open(req).read()
    except Exception:
        pass


_cleanup("zhangxuan")
_cleanup("xiaoming")



def chk(n, cond, extra=""):
    (passed if cond else failed).append(n)
    print(("  OK  " if cond else "  X   ") + n + (f"  [{extra}]" if extra and not cond else ""))


with sync_playwright() as p:
    b = p.chromium.launch()

    print("\n=== 1. 登录页初始状态 ===")
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="networkidle")
    chk("显示登录页", pg.locator("#scrLogin").is_visible())
    chk("有登录标签", pg.locator("#tabLogin").is_visible())
    chk("有注册标签", pg.locator("#tabReg").is_visible())
    chk("默认显示登录表单", pg.locator("#formLogin").is_visible())
    chk("注册表单默认隐藏", not pg.locator("#formReg").is_visible())
    chk("有匿名按钮", pg.locator("button:has-text('匿名体验')").is_visible())
    chk("登录码入口默认收起", not pg.locator("#codeLoginBox").is_visible())

    print("\n=== 2. 切换到注册 ===")
    pg.click("#tabReg")
    pg.wait_for_timeout(400)
    chk("注册表单显示", pg.locator("#formReg").is_visible())
    chk("登录表单隐藏", not pg.locator("#formLogin").is_visible())
    chk("有用户名框", pg.locator("#regUser").is_visible())
    chk("有手机号框", pg.locator("#regPhone").is_visible())
    chk("有姓名框", pg.locator("#regName").is_visible())
    chk("有密码框", pg.locator("#regPwd").is_visible())

    print("\n=== 3. 注册校验 ===")
    # 用户名太短
    pg.fill("#regUser", "ab"); pg.fill("#regPhone", "13800138000")
    pg.fill("#regName", "小明"); pg.fill("#regPwd", "abc123")
    pg.click("button:has-text('注册并开始学习')")
    pg.wait_for_timeout(500)
    chk("用户名太短被拦", "用户名" in pg.inner_text("#loginMsg"), pg.inner_text("#loginMsg"))
    # 手机号错
    pg.fill("#regUser", "xiaoming2"); pg.fill("#regPhone", "123")
    pg.click("button:has-text('注册并开始学习')")
    pg.wait_for_timeout(500)
    chk("手机号错被拦", "手机号" in pg.inner_text("#loginMsg"), pg.inner_text("#loginMsg"))
    # 密码太短
    pg.fill("#regPhone", "13800138001"); pg.fill("#regPwd", "123")
    pg.click("button:has-text('注册并开始学习')")
    pg.wait_for_timeout(500)
    chk("密码太短被拦", "密码" in pg.inner_text("#loginMsg"), pg.inner_text("#loginMsg"))

    print("\n=== 4. 正常注册 ===")
    pg.fill("#regUser", "xiaoming")
    pg.fill("#regPhone", "13800138000")
    pg.fill("#regName", "小明")
    pg.fill("#regPwd", "abc123456")
    pg.click("button:has-text('注册并开始学习')")
    pg.wait_for_selector("#mainTopbar", state="visible", timeout=8000)
    pg.wait_for_timeout(700)
    chk("注册后进入主界面", pg.locator("#mainTopbar").is_visible())
    chk("顶部显示姓名", pg.inner_text("#whoName") == "小明", pg.inner_text("#whoName"))
    chk("问候语带名字", "小明" in pg.inner_text("#greet"), pg.inner_text("#greet"))
    chk("匿名提示条隐藏", not pg.locator("#anonTip").is_visible())
    pg.screenshot(path=SHOT + "注册后主界面.png")

    print("\n=== 5. 记住登录（刷新免登） ===")
    pg.reload(wait_until="networkidle")
    pg.wait_for_timeout(900)
    chk("刷新后仍在主界面", pg.locator("#mainTopbar").is_visible())
    chk("刷新后名字还在", pg.inner_text("#whoName") == "小明")

    print("\n=== 6. 退出后密码登录 ===")
    pg.locator(".who").click(force=True)
    pg.wait_for_timeout(400)
    chk("身份弹层显示账号", "xiaoming" in pg.inner_text("#whoSub"), pg.inner_text("#whoSub"))
    pg.locator("#whoMask .btn.ghost").first.click(force=True)
    pg.wait_for_selector("#scrLogin", state="visible", timeout=8000)
    pg.wait_for_timeout(600)
    chk("退出后回登录页", pg.locator("#scrLogin").is_visible())
    # 密码登录
    pg.fill("#loginUser", "xiaoming")
    pg.fill("#loginPwd", "wrongpass")
    pg.click("#formLogin .login-btn")
    pg.wait_for_timeout(800)
    chk("错误密码被拒", "密码" in pg.inner_text("#loginMsg"), pg.inner_text("#loginMsg"))
    pg.fill("#loginPwd", "abc123456")
    pg.click("#formLogin .login-btn")
    pg.wait_for_selector("#mainTopbar", state="visible", timeout=8000)
    pg.wait_for_timeout(600)
    chk("正确密码登录成功", pg.inner_text("#whoName") == "小明")
    # 大小写不敏感
    ctx.close()

    print("\n=== 7. 匿名登录 ===")
    ctx2 = b.new_context(viewport={"width": 390, "height": 844})
    pg2 = ctx2.new_page()
    errs2 = []
    pg2.on("pageerror", lambda e: errs2.append(str(e)))
    pg2.goto(BASE, wait_until="networkidle")
    pg2.click("button:has-text('匿名体验')")
    pg2.wait_for_selector("#mainTopbar", state="visible", timeout=8000)
    pg2.wait_for_timeout(700)
    chk("匿名进入主界面", pg2.locator("#mainTopbar").is_visible())
    chk("匿名身份名正确", pg2.inner_text("#whoName") == "匿名体验", pg2.inner_text("#whoName"))
    chk("匿名提示条显示", pg2.locator("#anonTip").is_visible())
    pg2.screenshot(path=SHOT + "匿名体验.png")
    # 匿名也能答题
    pg2.locator(".course-item").first.click(force=True)
    pg2.wait_for_timeout(900)
    pg2.locator('.tab[data-tab="qz"]').click(force=True)
    pg2.wait_for_timeout(600)
    n = pg2.eval_on_selector_all(".opt", "els => els.length")
    chk("匿名可看到题目", n > 0, n)
    pg2.locator(".who").click(force=True)
    pg2.wait_for_timeout(400)
    chk("弹层说明本地保存", "本机" in pg2.inner_text("#whoSub"), pg2.inner_text("#whoSub"))
    ctx2.close()

    print("\n=== 8. 登录码入口（老方式仍可用） ===")
    ctx3 = b.new_context(viewport={"width": 390, "height": 844})
    pg3 = ctx3.new_page()
    pg3.goto(BASE, wait_until="networkidle")
    pg3.click("a:has-text('用登录码进入')")
    pg3.wait_for_timeout(400)
    chk("登录码框展开", pg3.locator("#codeLoginBox").is_visible())
    pg3.fill("#loginCode", "default")
    pg3.locator("#codeLoginBox .login-btn").click(force=True)
    pg3.wait_for_selector("#mainTopbar", state="visible", timeout=8000)
    pg3.wait_for_timeout(600)
    chk("登录码登录成功", pg3.locator("#mainTopbar").is_visible())
    ctx3.close()

    print("\n=== 9. JS 报错 ===")
    benign = ("favicon", "401", "400")
    real = [e for e in errs + errs2 if not any(k in e.lower() for k in benign)]
    chk("无 JS 报错", len(real) == 0, real[:3])

    b.close()

print("\n" + "=" * 46)
print(f"通过 {len(passed)} / {len(passed)+len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  - " + f)
    sys.exit(1)
