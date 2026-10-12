"""
家长端两个小修复的专项测试

1) 浏览状态保持：关浏览器再打开，仍停在上次查看的那个学生
2) 学习记录折叠：某天记录 > 5 条时只显示前 5 条，点「显示全部」才展开

前置：服务需在 5097 端口运行，且库里已有 stua / stub 两个学生，
      （小甲）今天 8 条会话、昨天 3 条会话。
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

        # 登录家长端
        pg.goto(BASE + "/admin.html")
        pg.wait_for_timeout(900)
        pg.fill("#pwd", "888888")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1600)
        ck("家长端登录成功", pg.locator("#board").is_visible())

        # ============ 1) 浏览状态保持 ============
        print("\n--- 1) 记住上次查看的学生 ---")
        # 切到 stuB
        pg.locator(".stu-tab:has-text('小乙')").click()
        pg.wait_for_timeout(1300)
        title_now = pg.locator("#bTitle").inner_text()
        ck("切到小乙：标题更新", "小乙" in title_now, title_now)
        saved = pg.evaluate("()=>localStorage.getItem('zsd_admin_last_sid')")
        ck("已把选中学生写入 localStorage", saved == "stub", str(saved))

        # 模拟「关浏览器再打开」：新开一个上下文，但复制 localStorage
        # （真实场景下 localStorage 由浏览器持久化，等价于同一 profile 重开）
        pg.close()
        pg2 = ctx.new_page()
        pg2.on("pageerror", lambda e: errs.append(str(e)))
        pg2.goto(BASE + "/admin.html")
        pg2.wait_for_timeout(1600)
        title_re = pg2.locator("#bTitle").inner_text()
        ck("重开后仍停在小乙（不是默认第一个）", "小乙" in title_re, title_re)
        active_tab = pg2.locator(".stu-tab.on").inner_text()
        ck("标签高亮也对得上", "小乙" in active_tab, active_tab.strip())

        # 换个浏览器 profile（localStorage 为空）→ 应回落，不报错
        ctx2 = b.new_context(viewport={"width": 390, "height": 844})
        pg3 = ctx2.new_page()
        pg3.on("pageerror", lambda e: errs.append(str(e)))
        pg3.goto(BASE + "/admin.html")
        pg3.wait_for_timeout(900)
        pg3.fill("#pwd", "888888")
        pg3.keyboard.press("Enter")
        pg3.wait_for_timeout(1600)
        ck("全新浏览器首次进入不报错且有学生", pg3.locator("#bTitle").inner_text().strip() != "",
           pg3.locator("#bTitle").inner_text())

        # ============ 2) 学习记录折叠 ============
        print("\n--- 2) 学习记录每天 >5 条折叠 ---")
        # 回到 stuA（今天 8 条）
        pg2.locator(".stu-tab:has-text('小甲')").click()
        pg2.wait_for_timeout(1400)

        days = pg2.locator(".lg-day")
        n_days = days.count()
        ck("学习记录有 2 天（今天 + 昨天）", n_days == 2, f"{n_days} 天")

        # 今天：8 条 → 折叠为 5 行 + 显示全部按钮
        d0 = days.nth(0)
        rows0 = d0.locator(".lg-row").count()
        more0 = d0.locator(".lg-more").count()
        ck("今天(8段)默认只显示 5 行", rows0 == 5, f"{rows0} 行")
        ck("今天有「显示全部」按钮", more0 == 1, f"{more0} 个")
        btn_txt = d0.locator(".lg-more").inner_text()
        ck("按钮文案含总条数 8", "8" in btn_txt, btn_txt.strip())
        pg2.screenshot(path=f"{SHOT}/lg_collapsed.png")

        # 点开后 → 8 行 + 收起按钮
        d0.locator(".lg-more").click()
        pg2.wait_for_timeout(500)
        d0 = pg2.locator(".lg-day").nth(0)
        rows0b = d0.locator(".lg-row").count()
        ck("点「显示全部」后展开为 8 行", rows0b == 8, f"{rows0b} 行")
        ck("展开后按钮变「收起」", "收起" in d0.locator(".lg-more").inner_text())
        pg2.screenshot(path=f"{SHOT}/lg_expanded.png")

        # 再点收起来
        d0.locator(".lg-more").click()
        pg2.wait_for_timeout(500)
        rows0c = pg2.locator(".lg-day").nth(0).locator(".lg-row").count()
        ck("再点「收起」回到 5 行", rows0c == 5, f"{rows0c} 行")

        # 昨天：3 条 → 不折叠，无按钮
        d1 = pg2.locator(".lg-day").nth(1)
        rows1 = d1.locator(".lg-row").count()
        more1 = d1.locator(".lg-more").count()
        ck("昨天(3段)全部显示 3 行", rows1 == 3, f"{rows1} 行")
        ck("昨天不需要折叠按钮（≤5 条）", more1 == 0, f"{more1} 个")

        # 展开状态跨「刷新」保持
        pg2.locator(".lg-day").nth(0).locator(".lg-more").click()
        pg2.wait_for_timeout(400)
        pg2.locator("button:has-text('刷新')").click()
        pg2.wait_for_timeout(1500)
        rows_after = pg2.locator(".lg-day").nth(0).locator(".lg-row").count()
        ck("展开状态在点「刷新」后仍保持", rows_after == 8, f"{rows_after} 行")

        # 切换学生后折叠状态重置
        pg2.locator(".stu-tab:has-text('小乙')").click()
        pg2.wait_for_timeout(1300)
        pg2.locator(".stu-tab:has-text('小甲')").click()
        pg2.wait_for_timeout(1400)
        rows_reset = pg2.locator(".lg-day").nth(0).locator(".lg-row").count()
        ck("切换学生回来后又变回折叠（5 行）", rows_reset == 5, f"{rows_reset} 行")

        ck("全程无 JS 报错", len(errs) == 0, " | ".join(errs[:3]))
        b.close()

    print("\n" + "=" * 56)
    ok = sum(1 for _, c in results if c)
    print(f"家长端小修复测试: {ok}/{len(results)} 通过")
    for n, c in results:
        if not c:
            print("  ✗ " + n)
    print("=" * 56)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
