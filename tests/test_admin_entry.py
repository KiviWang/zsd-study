"""
家长入口按钮显隐专项测试

规则：
  - 学生端自己的入口（/、/#scrCourse）→ 顶栏「家长入口」按钮隐藏
  - 从家长端点「学生视角」跳转（/?from=admin）→ 按钮显示
  - 同一标签内后续导航保持显示（sessionStorage），关标签后失效
"""
import pathlib
import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5097"
SHOT = "/root/.codebuddy/artifact/2f8a7d30-9c21-4f0b-1d44-533818912000/shots2"
pathlib.Path(SHOT).mkdir(parents=True, exist_ok=True)

results = []


def ck(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(f"{'✓' if cond else '✗'} {name} {extra}")


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # 准备一个学生账号
        u = "entrytester"
        r = requests.post(BASE + "/api/register", json={
            "username": u, "phone": "13900001234",
            "name": "入口测试", "password": "abc123456"})
        tok = (r.json() or {}).get("token", "")
        ck("注册学生账号成功", r.status_code == 200 and bool(tok), str(r.status_code))

        def entry_disp():
            return pg.evaluate(
                "()=>{const e=document.getElementById('adminEntryBtn');"
                "return e? getComputedStyle(e).display : 'MISSING';}")

        # A) 学生端入口 → 隐藏
        pg.goto(BASE + "/#scrCourse")
        pg.evaluate("(t)=>{try{localStorage.setItem('zsd_token',t);}catch(e){}}", tok)
        pg.goto(BASE + "/#scrCourse")
        pg.wait_for_timeout(1300)
        ck("学生端入口：家长入口隐藏", entry_disp() == "none", entry_disp())
        pg.screenshot(path=f"{SHOT}/entry_student.png")

        # B) 同标签刷新 → 仍隐藏
        pg.reload()
        pg.wait_for_timeout(1300)
        ck("学生端刷新后：仍隐藏", entry_disp() == "none", entry_disp())

        # C) 带 from=admin → 显示
        pg.goto(BASE + "/?from=admin")
        pg.wait_for_timeout(1400)
        ck("家长端跳转：家长入口显示", entry_disp() == "flex", entry_disp())
        pg.screenshot(path=f"{SHOT}/entry_admin.png")

        # D) 同标签内切回 hash 页 → 保持显示
        pg.goto(BASE + "/#scrCourse")
        pg.wait_for_timeout(1300)
        ck("同标签内后续导航：保持显示", entry_disp() == "flex", entry_disp())

        # E) 按钮可点击，能回到家长端
        pg.click("#adminEntryBtn")
        pg.wait_for_timeout(1200)
        ck("点家长入口可跳家长端", "/admin" in pg.url, pg.url)

        # F) 家长端存在「学生视角」按钮，且跳转带 from=admin
        pg.goto(BASE + "/admin.html")
        pg.wait_for_timeout(900)
        ck("家长端有「学生视角」按钮", pg.locator("button:has-text('学生视角')").count() == 1,
           str(pg.locator("button:has-text('学生视角')").count()))

        ck("无 JS 报错", len(errs) == 0, " | ".join(errs[:3]))
        b.close()

    print("\n" + "=" * 56)
    ok = sum(1 for _, c in results if c)
    print(f"家长入口显隐测试: {ok}/{len(results)} 通过")
    for n, c in results:
        if not c:
            print("  ✗ " + n)
    print("=" * 56)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
