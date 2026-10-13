#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键全量回归执行器。

用法（在项目根目录）：
    python scripts/run_all_tests.py            # 跑全部
    python scripts/run_all_tests.py zombie     # 只跑文件名含 zombie 的测试
    python scripts/run_all_tests.py api essay  # 跑多个

原理：
  1. 主进程内用 make_server 在 127.0.0.1:5097 起一个 Flask 服务（threaded），
     数据落在独立临时目录，不污染项目 data/。
  2. 每个测试文件在**独立子进程**里运行，通过 HTTP 访问该服务。
     这样既能共享一个服务（不少测试依赖同一份数据演进），
     又能避免单文件故障拖垮整轮。
  3. 自动解析各测试输出的「x/y 通过」并汇总。

注意：test_zombie_session.py / test_studytime.py / test_session_log.py
      走的是 DB 层（自己建临时库），不依赖 HTTP；其余多数走 HTTP。
"""
import glob
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
APP = os.path.join(ROOT, "app")
PORT = 5097

# 执行顺序：先基础 API，再业务功能，最后 UI（UI 依赖前面的数据演进）
ORDER = [
    "test_api.py", "test_app.py", "test_modes.py", "test_anon.py",
    "test_login.py", "test_signup.py", "test_admin.py", "test_admin_entry.py",
    "test_admin_polish.py", "test_studytime.py", "test_session_log.py",
    "test_zombie_session.py", "test_essay.py", "test_content_hg.py",
    "test_history_full.py", "test_turso.py", "test_backup.py",
    "test_ui.py", "test_fold_ui.py", "test_wrongbook_ui.py",
    "test_browser.py", "test_network_resilience.py",
]


def start_service(tmpdir):
    sys.path.insert(0, APP)
    sys.path.insert(0, ROOT)
    from werkzeug.serving import make_server
    import server
    srv = make_server("127.0.0.1", PORT, server.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/courses", timeout=2).read()
            return srv
        except Exception:
            time.sleep(0.25)
    return None


def parse_counts(txt):
    for pat in (r"合计[：:]\s*(\d+)\s*/\s*(\d+)",
                r"结果[：:]\s*(\d+)\s*/\s*(\d+)\s*通过",
                r"(\d+)\s*/\s*(\d+)\s*通过",
                r"通过[：:]\s*(\d+)\s*/\s*(\d+)"):
        m = re.findall(pat, txt)
        if m:
            return int(m[-1][0]), int(m[-1][1])
    m = re.findall(r"(\d+)\s+通过\s*/\s*(\d+)\s*失败", txt)
    if m:
        a, b = int(m[-1][0]), int(m[-1][1])
        return a, a + b
    return 0, 0


def main():
    picked = sys.argv[1:] or None
    files = [n for n in ORDER if os.path.exists(os.path.join(TESTS, n))]
    extra = sorted(set(os.path.basename(p) for p in
                       glob.glob(os.path.join(TESTS, "test_*.py"))) - set(files))
    files += extra
    if picked:
        files = [f for f in files if any(x in f for x in picked)]
        if not files:
            print("没有匹配的测试文件：" + ", ".join(picked))
            return 2

    tmpdir = tempfile.mkdtemp(prefix="zsd_regress_")
    os.environ["ZSD_SQLITE_DIR"] = tmpdir
    srv = start_service(tmpdir)
    if not srv:
        print("!! 服务启动失败（端口 %d 可能被占用）" % PORT)
        return 2
    print("服务已就绪 http://127.0.0.1:%d  (db=%s)" % (PORT, tmpdir))
    print("=" * 72)

    env = dict(os.environ)
    env["ZSD_SQLITE_DIR"] = tmpdir
    env["PYTHONPATH"] = APP + os.pathsep + ROOT

    ok_all = tot_all = 0
    bad = []
    for f in files:
        try:
            r = subprocess.run([sys.executable, os.path.join(TESTS, f)],
                               cwd=TESTS, env=env, capture_output=True,
                               text=True, timeout=600)
            txt = (r.stdout or "") + (r.stderr or "")
            ok, tot = parse_counts(txt)
            rc = r.returncode
        except subprocess.TimeoutExpired:
            ok, tot, txt, rc = 0, 0, "TIMEOUT", 1
        ok_all += ok
        tot_all += tot
        if ok < tot or (tot == 0 and rc != 0):
            bad.append(f)
            print(f"[FAIL] {f:32s} {ok}/{tot}")
            for l in [x for x in txt.splitlines()
                      if "❌" in x or "✗" in x or "Error" in x or "error" in x][-4:]:
                print("        " + l.strip()[:110])
        else:
            print(f"[ OK ] {f:32s} {ok}/{tot}")
    print("=" * 72)
    print(f"合计：{ok_all}/{tot_all} 通过；问题文件 {len(bad)}")
    if bad:
        print("问题文件：" + ", ".join(bad))
    srv.shutdown()
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
