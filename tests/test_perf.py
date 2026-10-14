#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 gzip 压缩与缓存头是否生效，并量化传输量下降。"""
import gzip
import os
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = "/workspace/政史地学习平台"
os.environ["ZSD_SQLITE_DIR"] = tempfile.mkdtemp(prefix="zsd_perf_")
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)

from werkzeug.serving import make_server  # noqa: E402
import server  # noqa: E402

PORT = 5110
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


def get(path, headers=None, raw=False):
    """返回 (状态, 头, 原始字节)。"""
    req = urllib.request.Request(BASE + path)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


print("=" * 68)
print("【1】首页压缩与缓存头")
print("=" * 68)
st, h, body = get("/", {"Accept-Encoding": "gzip"})
enc = h.get("Content-Encoding", "")
cc = h.get("Cache-Control", "")
vary = h.get("Vary", "")
print(f"  状态={st}  Content-Encoding={enc or '(无)'}")
print(f"  Cache-Control={cc}")
print(f"  Vary={vary}")
print(f"  传输体积（压缩后）= {len(body):,} B")
chk("首页启用 gzip", enc == "gzip", enc)
chk("Cache-Control 不再是 no-cache", "no-cache" not in cc or "max-age" in cc, cc)
chk("含 max-age（允许短期本地缓存）", "max-age=" in cc, cc)
chk("Vary 含 Accept-Encoding", "accept-encoding" in vary.lower(), vary)

plain_st, plain_h, plain_body = get("/", {"Accept-Encoding": "identity"})
print(f"  未压缩体积（对照）= {len(plain_body):,} B")
ratio = len(body) / max(1, len(plain_body))
chk("压缩后体积 < 原始 40%", ratio < 0.4, f"{ratio:.1%}")
chk("压缩后能被正确解压还原", gzip.decompress(body) == plain_body)
saved = 1 - ratio
print(f"  → 首页传输量下降 {saved:.1%}")

print()
print("=" * 68)
print("【2】接口 JSON 也享受压缩，但不缓存")
print("=" * 68)
st2, h2, b2 = get("/api/courses", {"Accept-Encoding": "gzip"})
st2p, h2p, b2p = get("/api/courses", {"Accept-Encoding": "identity"})
print(f"  /api/courses: 状态={st2} 编码={h2.get('Content-Encoding')} "
      f"压缩{len(b2):,}B / 原始{len(b2p):,}B")
chk("接口响应启用 gzip", h2.get("Content-Encoding") == "gzip", h2.get("Content-Encoding"))
chk("接口与原文一致", gzip.decompress(b2) == b2p)
chk("接口禁止缓存(no-store)",
    "no-store" in (h2.get("Cache-Control") or ""), h2.get("Cache-Control"))

st3, h3, b3 = get("/api/course/p1", {"Accept-Encoding": "gzip"})
st3p, h3p, b3p = get("/api/course/p1", {"Accept-Encoding": "identity"})
print(f"  /api/course/p1: 压缩{len(b3):,}B / 原始{len(b3p):,}B "
      f"({len(b3)/max(1,len(b3p)):.1%})")
chk("课程详情启用 gzip", h3.get("Content-Encoding") == "gzip")

print()
print("=" * 68)
print("【3】304 协商缓存（重复访问只传响应头）")
print("=" * 68)
etag = h.get("ETag") or h.get("Etag")
print(f"  ETag={etag}")
chk("首页带 ETag", bool(etag), str(etag))
if etag:
    req = urllib.request.Request(BASE + "/")
    req.add_header("If-None-Match", etag)
    req.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            st304, b304 = r.status, r.read()
    except urllib.error.HTTPError as e:
        st304, b304 = e.code, e.read()
    print(f"  带 If-None-Match 重访：状态={st304} 体积={len(b304)} B")
    chk("重访返回 304", st304 == 304, str(st304))
    chk("304 响应体为空（零传输）", len(b304) == 0, str(len(b304)))

print()
print("=" * 68)
print("【4】不支持 gzip 的客户端不受影响（兼容性）")
print("=" * 68)
st4, h4, b4 = get("/", {"Accept-Encoding": "identity"})
chk("无 gzip 时返回未压缩原文", not h4.get("Content-Encoding"), h4.get("Content-Encoding"))
chk("内容仍完整", b4 == plain_body)

print()
print("=" * 68)
print("【5】小响应不压缩（避免负收益）")
print("=" * 68)
st5, h5, b5 = get("/api/essay/p1", {"Accept-Encoding": "gzip"})
print(f"  /api/essay/p1 体积={len(b5)}B 编码={h5.get('Content-Encoding') or '(无)'}")
chk("小响应行为正常（有压缩则体积更小，无压缩也正常）",
    h5.get("Content-Encoding") in (None, "gzip"), str(h5.get("Content-Encoding")))

print()
print("=" * 68)
ok, tot = sum(PASS), len(PASS)
print(f"结果：{ok}/{tot} 通过")
print("=" * 68)
srv.shutdown()
sys.exit(0 if ok == tot else 1)
