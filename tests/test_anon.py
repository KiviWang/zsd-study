# -*- coding: utf-8 -*-
"""浏览器测试：匿名本地隔离 + 注册用户数据落库（对照）"""
import json, sys, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5097"
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


def api(path, data=None, headers=None):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=dict({"Content-Type": "application/json"}, **(headers or {})),
        method="POST" if data is not None else "GET")
    return json.loads(urllib.request.urlopen(req).read())


def server_answer_count():
    """直接从数据库数 answers 表行数（判断匿名有没有偷偷写库）

    库位置优先取 ZSD_SQLITE_DIR（与启动服务时一致），否则回退到历史默认目录。
    """
    import sqlite3, glob, os
    d = os.environ.get("ZSD_SQLITE_DIR") or "/tmp/zsdtest_data"
    dbs = glob.glob(os.path.join(d, "*.db"))
    if not dbs:
        return 0
    c = sqlite3.connect(dbs[0])
    n = c.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    c.close()
    return n


with sync_playwright() as p:
    b = p.chromium.launch()

    print("\n=== 1. 匿名登录 + 答题（本地隔离） ===")
    before = server_answer_count()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="networkidle")
    pg.click("button:has-text('匿名体验')")
    pg.wait_for_selector("#mainTopbar", state="visible", timeout=8000)
    pg.wait_for_timeout(600)
    chk("匿名进入成功", pg.locator("#mainTopbar").is_visible())
    chk("提示条可见", pg.locator("#anonTip").is_visible())

    # 答一题
    pg.locator(".course-item").first.click(force=True)
    pg.wait_for_timeout(900)
    pg.locator('.tab[data-tab="qz"]').click(force=True)
    pg.wait_for_timeout(600)
    n = pg.eval_on_selector_all(".opt", "els => els.length")
    chk("能看到题目", n > 0, n)
    pg.eval_on_selector("#q_0 .opt", "el => el.click()")
    pg.wait_for_timeout(900)

    # 关键：服务器数据库不应新增记录
    after = server_answer_count()
    chk("匿名答题未写入服务器", after == before, f"{before} -> {after}")

    # 本地确实存了
    local = pg.evaluate("() => JSON.parse(localStorage.getItem('zsd_anon_data')||'{}')")
    chk("本地已保存作答", len(local.get("answers", [])) > 0, local)
    pg.screenshot(path="/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/匿名答题后.png")

    print("\n=== 2. 匿名进度显示 ===")
    pg.locator('.nav[data-scr="scrCourse"]').click(force=True)
    pg.wait_for_timeout(900)
    txt = pg.inner_text("#totalTxt")
    chk("课程页显示进度(非0)", "0 /" not in txt or "已答对 0" not in pg.inner_text("#courseList")[:50], txt)

    print("\n=== 3. 匿名错题本 ===")
    pg.locator('.nav[data-scr="scrWrong"]').click(force=True)
    pg.wait_for_timeout(1200)
    w = pg.inner_text("#wrongList")
    chk("错题本有内容或空态正常", len(w.strip()) > 10, w[:40])

    print("\n=== 4. 匿名退出清空本地 ===")
    pg.locator(".who").click(force=True)
    pg.wait_for_timeout(400)
    pg.on("dialog", lambda d: d.accept())
    pg.locator("#whoMask .btn.ghost").first.click(force=True)
    pg.wait_for_selector("#scrLogin", state="visible", timeout=8000)
    pg.wait_for_timeout(800)
    chk("退出后回登录页", pg.locator("#scrLogin").is_visible())
    left = pg.evaluate("() => localStorage.getItem('zsd_anon_data')")
    chk("本地匿名数据已清空", left in (None, "", "{}") or len(json.loads(left or "{}").get("answers", [])) == 0, left)

    print("\n=== 5. 对照：注册用户答题要落库 ===")
    before2 = server_answer_count()
    pg.click("#tabReg")
    pg.wait_for_timeout(400)
    pg.fill("#regUser", "zhangxuan")
    pg.fill("#regPhone", "13800138000")
    pg.fill("#regName", "张同学")
    pg.fill("#regPwd", "abc123456")
    pg.click("button:has-text('注册并开始学习')")
    pg.wait_for_selector("#mainTopbar", state="visible", timeout=8000)
    pg.wait_for_timeout(800)
    chk("注册后进入主界面", pg.locator("#mainTopbar").is_visible())
    chk("实名用户无匿名提示条", not pg.locator("#anonTip").is_visible())
    pg.locator(".course-item").first.click(force=True)
    pg.wait_for_timeout(900)
    pg.locator('.tab[data-tab="qz"]').click(force=True)
    pg.wait_for_timeout(600)
    pg.eval_on_selector("#q_0 .opt", "el => el.click()")
    pg.wait_for_timeout(900)
    after2 = server_answer_count()
    chk("注册用户答题已落库", after2 > before2, f"{before2} -> {after2}")

    print("\n=== 6. JS 报错 ===")
    benign = ("favicon", "401", "400")
    real = [e for e in errs if not any(k in e.lower() for k in benign)]
    chk("无 JS 报错", len(real) == 0, real[:3])

    b.close()

print("\n" + "=" * 46)
print(f"通过 {len(passed)} / {len(passed)+len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  - " + f)
    sys.exit(1)
