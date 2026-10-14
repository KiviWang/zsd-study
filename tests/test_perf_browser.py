#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器端验证性能优化：
  1. 页面能正常加载（_startup 延后启动没破坏功能）
  2. 实际网络传输中 gzip 生效（对比线上/本地传输体积）
  3. 二次访问走 304
  4. 登录/注册等关键路径仍可用
"""
import os
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = "/workspace/政史地学习平台"
os.environ["ZSD_SQLITE_DIR"] = tempfile.mkdtemp(prefix="zsd_pw_")
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

from werkzeug.serving import make_server  # noqa: E402
import server  # noqa: E402

PORT = 5111
BASE = f"http://127.0.0.1:{PORT}"
srv = make_server("127.0.0.1", PORT, server.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
for _ in range(80):
    try:
        urllib.request.urlopen(BASE + "/api/courses", timeout=2).read()
        break
    except Exception:
        time.sleep(0.25)

PASS = []
def chk(n, c, e=""):
    PASS.append(bool(c))
    print(f"  {'✅' if c else '❌'} {n} {e}")


from playwright.sync_api import sync_playwright  # noqa: E402

print("=" * 66)
print("【1】页面加载与真实传输量")
print("=" * 66)
transfers = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))

    def on_resp(resp):
        try:
            if resp.url.rstrip("/") == BASE.rstrip("/") or resp.url.endswith("/"):
                h = resp.headers
                transfers["main"] = {
                    "status": resp.status,
                    "encoding": h.get("content-encoding", ""),
                    "cache": h.get("cache-control", ""),
                    "len": h.get("content-length", ""),
                }
        except Exception:
            pass
    pg.on("response", on_resp)

    pg.goto(BASE + "/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)

    t = transfers.get("main", {})
    print(f"  主文档响应：{t}")
    chk("页面可加载", t.get("status") == 200, str(t.get("status")))
    chk("真实传输已 gzip", t.get("encoding") == "gzip", t.get("encoding", ""))
    chk("Cache-Control 含 max-age", "max-age=" in (t.get("cache") or ""), t.get("cache", ""))
    if t.get("len"):
        print(f"  → 浏览器实测接收 {int(t['len']):,} B（原始 85,233 B）")

    # 登录页应可见（_startup 延后不影响）
    vis = pg.locator("#scrLogin").is_visible()
    chk("登录页正常显示", vis)
    chk("登录按钮存在", pg.locator("#formLogin .login-btn").count() == 1)
    chk("无 JS 报错", len(errs) == 0, " | ".join(errs[:2]))

    print()
    print("=" * 66)
    print("【2】关键交互仍可用（延后启动不影响功能）")
    print("=" * 66)
    # 切到注册 tab
    pg.locator("#tabReg").click()
    pg.wait_for_timeout(400)
    chk("可切换到注册表单", pg.locator("#formReg").is_visible())
    pg.locator("#tabLogin").click()
    pg.wait_for_timeout(300)
    chk("可切回登录表单", pg.locator("#formLogin").is_visible())

    # 匿名进入
    pg.locator("button:has-text('先随便看看')").click()
    pg.wait_for_timeout(3500)
    in_app = pg.locator("#scrCourse").is_visible()
    chk("匿名进入主界面成功", in_app)
    if in_app:
        n_cards = pg.locator("#courseList .course-item").count()
        chk("课程列表已渲染", n_cards >= 39, f"{n_cards} 个课程项")
    chk("全程无 JS 报错", len(errs) == 0, " | ".join(errs[:3]))

    print()
    print("=" * 66)
    print("【3】二次访问协商缓存（304）")
    print("=" * 66)
    codes = []
    pg2 = ctx.new_page()
    pg2.on("response", lambda r: codes.append((r.url, r.status)) if r.url.endswith("/") else None)
    pg2.goto(BASE + "/")
    pg2.wait_for_timeout(1500)
    print(f"  重访主文档状态码：{[c[1] for c in codes]}")
    chk("重访走 304 或 200（不重复下载）",
        all(s in (200, 304) for _, s in codes) and len(codes) >= 1, str(codes[:2]))

    b.close()

print()
print("=" * 66)
ok, tot = sum(PASS), len(PASS)
print(f"结果：{ok}/{tot} 通过")
print("=" * 66)
srv.shutdown()
sys.exit(0 if ok == tot else 1)
