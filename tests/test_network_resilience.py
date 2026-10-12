"""
跨境网络韧性专项测试

背景：
  服务器在 AWS 美东，国内（尤其 iOS Safari）访问偶发跨境抖动/连接重置，
  表现为「页面无响应」或接口空白。

验证点：
  1) 请求自动重试：网络失败时 api()/jfetch() 会自动重试并最终成功
  2) 4xx/5xx 不重试（业务结果，直接返回）
  3) 学生端课程列表：网络彻底失败时用本地缓存兜底，不出现空白
  4) 全程无 JS 报错
"""
import pathlib
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

        # ---------- 1) 重试机制：前 2 次请求强制失败，第 3 次放行 ----------
        print("--- 1) 自动重试（前 2 次失败，第 3 次成功）---")
        pg.goto(BASE)
        pg.wait_for_timeout(800)
        call_count = {"n": 0}

        def flaky(route):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                route.abort("connectionreset")   # 模拟跨境连接被重置
            else:
                route.continue_()

        pg.route("**/api/courses", flaky)
        # 触发一次课程加载
        pg.evaluate("()=>loadCourses()")
        pg.wait_for_timeout(4000)
        ck("重试后请求最终成功", call_count["n"] >= 3, f"共发起 {call_count['n']} 次")
        cards = pg.locator(".course-item").count()
        ck("重试成功后课程正常渲染", cards > 0, f"{cards} 门课")
        pg.unroute("**/api/courses")

        # ---------- 2) 缓存兜底：全部请求失败 ----------
        print("\n--- 2) 缓存兜底（请求彻底失败）---")
        def always_fail(route):
            route.abort("connectionreset")
        pg.route("**/api/courses", always_fail)
        pg.evaluate("()=>{ window.COURSES=[]; document.getElementById('courseList').innerHTML=''; }")
        pg.evaluate("()=>loadCourses()")
        pg.wait_for_timeout(9000)   # 等 3 次重试都失败（0.4s + 1s 退避 + 超时）
        cards2 = pg.locator(".course-item").count()
        ck("彻底失败后仍有课程（走缓存）", cards2 > 0, f"{cards2} 门课")
        body = pg.inner_text("body")
        ck("有网络提示文案", "网络" in body, "")
        pg.screenshot(path=f"{SHOT}/offline_fallback.png")
        pg.unroute("**/api/courses")

        # ---------- 3) 4xx 不重试 ----------
        print("\n--- 3) 4xx 不重试（业务错误直接返回）---")
        cnt2 = {"n": 0}
        def not_found(route):
            cnt2["n"] += 1
            route.fulfill(status=404, content_type="application/json",
                          body='{"ok":false,"msg":"not found"}')
        pg.route("**/api/courses", not_found)
        res = pg.evaluate("""async ()=>{
            const r = await api('/api/courses');
            return r.status;
        }""")
        ck("404 直接返回不重试", cnt2["n"] == 1 and res == 404, f"请求 {cnt2['n']} 次, 状态 {res}")
        pg.unroute("**/api/courses")

        # ---------- 4) 家长端 jfetch 重试 ----------
        print("\n--- 4) 家长端 jfetch 重试 ---")
        pg2 = ctx.new_page()
        pg2.on("pageerror", lambda e: errs.append(str(e)))
        pg2.goto(BASE + "/admin.html")
        pg2.wait_for_timeout(1200)
        cnt3 = {"n": 0}
        def flaky2(route):
            cnt3["n"] += 1
            if cnt3["n"] == 1:
                route.abort("connectionreset")
            else:
                route.continue_()
        pg2.route("**/api/admin/check", flaky2)
        pg2.reload()
        pg2.wait_for_timeout(4000)
        ck("家长端首次失败后重试成功", cnt3["n"] >= 2, f"共 {cnt3['n']} 次")
        ck("重试后登录页正常显示", pg2.locator("#login").is_visible())
        pg2.unroute("**/api/admin/check")

        # ---------- 5) 无 JS 报错 ----------
        ck("全程无 JS 报错", len(errs) == 0, " | ".join(errs[:2]))
        b.close()

    print("\n" + "=" * 56)
    ok = sum(1 for _, c in results if c)
    print(f"跨境网络韧性测试: {ok}/{len(results)} 通过")
    for n, c in results:
        if not c:
            print("  ✗ " + n)
    print("=" * 56)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
