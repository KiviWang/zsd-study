#!/usr/bin/env python3
"""浏览器端测试：错题本新交互（默认空白 / 看答案 / 重做 / 重做所有）"""
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5097"
OK = FAIL = 0


def check(cond, name, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


with sync_playwright() as p:
    pg = p.chromium.launch()
    ctx = pg.new_context(viewport={"width": 390, "height": 844},
                         user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)")
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE)

    print("=" * 60)
    print("【1】匿名登录")
    print("=" * 60)
    pg.wait_for_timeout(900)
    try:
        pg.eval_on_selector("#btnAnon", "el => el.click()")
    except Exception:
        pg.evaluate("() => doAnonymous()")
    pg.wait_for_timeout(1500)
    check(pg.eval_on_selector("#scrCourse", "el => el.classList.contains('active')"),
          "进入主页")

    print()
    print("=" * 60)
    print("【2】做题制造错题（基础模式）")
    print("=" * 60)
    pg.evaluate("() => openCourse('p1')")
    pg.wait_for_timeout(1200)
    # 切到自测 tab
    pg.evaluate("() => switchTab('qz')")
    pg.wait_for_timeout(800)
    n_q = pg.evaluate("() => document.querySelectorAll('#tabQz .qbox').length")
    check(n_q == 10, f"基础模式显示 10 题（实际 {n_q}）")
    # 全部选 A（第 0 项），必然制造错题
    for i in range(3):
        pg.evaluate(f"(i) => {{ const b=document.querySelector('#q_'+i); if(b && !b.dataset.locked){{ b.querySelectorAll('.opt')[0].click(); }} }}", i)
        pg.wait_for_timeout(400)
    pg.wait_for_timeout(600)

    print()
    print("=" * 60)
    print("【3】错题本默认空白（不显示答案）")
    print("=" * 60)
    pg.evaluate("() => go('scrWrong')")
    pg.wait_for_timeout(1400)
    has_items = pg.evaluate("() => document.querySelectorAll('#wrongList .wq').length")
    check(has_items > 0, f"错题本有 {has_items} 道题")
    # 默认 reveal 不应显示
    shown = pg.evaluate("""() => {
        const rv = document.querySelector('#wrongList .wq .reveal');
        return rv ? getComputedStyle(rv).display : 'none';
    }""")
    check(shown == "none", f"答案默认隐藏（display={shown}）")
    # 应看到"重做这道题"和"看答案"按钮
    btns = pg.evaluate("() => [...document.querySelectorAll('#wrongList .wq-acts button')].map(b=>b.textContent.trim())")
    check(any("重做" in b for b in btns), "有「重做这道题」按钮")
    check(any("看答案" in b for b in btns), "有「看答案」按钮")
    # 顶部"重做所有"
    top_btn = pg.evaluate("() => { const b=document.getElementById('wbAllBtn'); return b?b.textContent.trim():''; }")
    check("重做所有" in top_btn, f"顶部有「重做所有」按钮（{top_btn}）")

    print()
    print("=" * 60)
    print("【4】点「看答案」展开")
    print("=" * 60)
    pg.evaluate("""() => {
        const b=[...document.querySelectorAll('#wrongList .wq-acts button')].find(x=>x.textContent.includes('看答案'));
        b.click();
    }""")
    pg.wait_for_timeout(300)
    shown2 = pg.evaluate("""() => {
        const rv = document.querySelector('#wrongList .wq .reveal');
        return rv ? getComputedStyle(rv).display : 'none';
    }""")
    check(shown2 != "none", "点「看答案」后答案显示")

    print()
    print("=" * 60)
    print("【5】「重做所有」连续模式")
    print("=" * 60)
    pg.evaluate("() => document.getElementById('wbAllBtn').click()")
    pg.wait_for_timeout(1500)
    head = pg.evaluate("() => { const h=document.querySelector('#wrongList .wg-head b'); return h?h.textContent:''; }")
    check("连续重做" in head, f"进入连续重做模式（{head}）")
    has_stage = pg.evaluate("() => !!document.querySelector('#wrongList .redo-stage')")
    check(has_stage, "显示重做作答区")
    # 答一题
    pg.evaluate("() => { const o=document.querySelector('#wrongList .redo-opt'); if(o) o.click(); }")
    pg.wait_for_timeout(2500)
    # 应推进到下一题
    after = pg.evaluate("() => { const h=document.querySelector('#wrongList .wg-head b'); return h?h.textContent:''; }")
    check("还剩" in after or "完毕" in after, f"答完自动推进（{after}）")

    print()
    print("=" * 60)
    print("【6】退出连续模式")
    print("=" * 60)
    pg.evaluate("() => { const b=document.getElementById('wbAllBtn'); if(b.textContent.includes('退出')) b.click(); }")
    pg.wait_for_timeout(1400)
    top2 = pg.evaluate("() => document.getElementById('wbAllBtn').textContent.trim()")
    check("重做所有" in top2, f"回到普通模式（{top2}）")

    print()
    print("=" * 60)
    print("【7】随机模式与基础模式题目不同")
    print("=" * 60)
    pg.evaluate("() => go('scrCourse')")
    pg.wait_for_timeout(500)
    pg.evaluate("() => openCourse('p1')")
    pg.wait_for_timeout(1000)
    pg.evaluate("() => switchTab('qz')")
    pg.wait_for_timeout(600)
    basic_stems = pg.evaluate("() => [...document.querySelectorAll('#tabQz .qtxt')].map(e=>e.textContent.slice(0,10))")
    pg.evaluate("() => { const b=[...document.querySelectorAll('.modebtn')].find(x=>x.textContent.includes('强化')); if(b) b.click(); }")
    pg.wait_for_timeout(1400)
    rand_stems = pg.evaluate("() => [...document.querySelectorAll('#tabQz .qtxt')].map(e=>e.textContent.slice(0,10))")
    overlap = set(basic_stems) & set(rand_stems)
    check(len(rand_stems) == 10, f"随机模式 10 题（实际 {len(rand_stems)}）")
    check(len(overlap) == 0, f"基础与随机题不重叠（重叠 {len(overlap)}）")

    print()
    print("=" * 60)
    check(not errs, f"无 JS 报错", errs[:3])
    print(f"结果：{OK} 通过 / {FAIL} 失败")
    print("=" * 60)
    ctx.close()
    pg.close()

exit(1 if FAIL else 0)
