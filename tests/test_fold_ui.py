#!/usr/bin/env python3
"""学科分组折叠专项测试（浏览器）：折叠 / 持久化 / 展开 / 两位数徽章"""
import asyncio, os, sys
from playwright.async_api import async_playwright

BASE = os.environ.get("ZSD_BASE", "http://127.0.0.1:5097")
OUT = os.environ.get("ZSD_SHOT_DIR", "/tmp/zsd_shots")
os.makedirs(OUT, exist_ok=True)
OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1; print("  ✅ " + name)
    else:
        FAIL += 1; print("  ❌ " + name + (f"  [{extra}]" if extra else ""))


async def head_click(page, subj):
    return await page.evaluate("""(s)=>{
      const h=[...document.querySelectorAll('.subj-head')].find(e=>e.textContent.includes(s));
      if(h){ h.click(); return true; } return false;
    }""", subj)


async def visible(page, subj):
    return await page.evaluate("""(s)=>{
      const b=document.getElementById('sb_'+s);
      return b ? b.offsetParent!==null : null;
    }""", subj)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page = await ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" else None)

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(700)
        await page.click("button:has-text('匿名体验')")
        await page.wait_for_timeout(1300)

        print("\n【1】分组与课目")
        n = await page.locator(".subj-head").count()
        chk("学科分组 3 组", n == 3, str(n))
        cards = await page.locator(".course-item").count()
        # 39 门课 + 政治组的 1 张「每日论述」入口卡 = 40
        chk("课程卡 40 张（39 课 + 论述入口）", cards == 40, str(cards))
        ee = await page.locator(".es-entry").count()
        chk("含「每日论述」入口卡", ee == 1, str(ee))
        b2 = await page.locator(".course-badge.b2").count()
        chk("两位数徽章 ≥10", b2 >= 10, str(b2))
        heads = [h.replace("\n", " ") for h in await page.locator(".subj-head").all_inner_texts()]
        print("  ℹ️  " + " | ".join(heads))
        await page.screenshot(path=f"{OUT}/fold_1_expanded.png")

        print("\n【2】折叠 / 持久化 / 展开")
        chk("初始历史组可见", await visible(page, "历史") is True)
        await head_click(page, "历史")
        await page.wait_for_timeout(500)
        chk("点标题后收起", await visible(page, "历史") is False)
        stored = await page.evaluate("()=>localStorage.getItem('zsd_fold_subjects')")
        chk("折叠状态已写入本机", "历史" in (stored or ""), str(stored))
        await page.screenshot(path=f"{OUT}/fold_2_collapsed.png")

        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(1300)
        chk("刷新后仍是收起", await visible(page, "历史") is False)
        await head_click(page, "历史")
        await page.wait_for_timeout(500)
        chk("再点标题可展开", await visible(page, "历史") is True)
        await page.screenshot(path=f"{OUT}/fold_3_reopened.png")

        print("\n【3】折叠不影响其他组")
        chk("政治组仍可见", await visible(page, "政治") is True)
        chk("地理组仍可见", await visible(page, "地理") is True)

        real = [e for e in errs if "401" not in e]
        chk("无 JS 报错", not real, str(real[:2]))
        await b.close()

    print(f"\n{'='*50}\n折叠测试: {OK} 通过 / {FAIL} 失败\n{'='*50}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
