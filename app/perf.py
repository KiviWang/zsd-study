# -*- coding: utf-8 -*-
"""
响应压缩与缓存策略。

背景
    服务器在 AWS 美东（弗吉尼亚），国内访问需跨境。
    实测首页 85KB 且响应头是 `Cache-Control: no-cache, must-revalidate`、
    且**完全没有 Content-Encoding** —— 每次打开都要跨境重下 85KB 明文。
    跨境链路本身就偶发拥塞（出现过持续约 5 分钟的不通），
    这个「全量重传 + 未压缩」把链路抖动放大成了「打不开」。

做了什么
    1. gzip 压缩：文本类响应（HTML/JSON/JS/CSS/SVG/XML）压到约 1/5。
       85KB 的首页 → 约 20KB，跨境传输量直接砍掉 75%。
    2. 分级缓存：
       - 静态页（/、/admin）：带 ETag，且给一个短的 max-age。
         重复访问在有效期内直接用本地副本（0 请求），过期后走 304（只回响应头）。
       - 接口 JSON：不缓存（数据实时性优先），但仍享受 gzip。
    3. 明确 Vary: Accept-Encoding，避免中间缓存把压缩/未压缩版本串味。

为什么不用 Flask-Compress
    多一个第三方依赖，而 PythonAnywhere 免费版安装依赖要额外操作。
    这里用标准库 gzip 手写，零依赖、行为完全可控。

典型效果（实测会略不同）
    首页  85,233B → 约 19,000B（-78%）
    首次访问：跨境传输量下降约 3/4
    二次访问：304，只传几百字节响应头
"""
import gzip
import os
from datetime import timedelta

# 只压缩这些类型的响应（文本类压缩收益大；图片/字体本身已压缩，压了反而更差）
COMPRESSIBLE_TYPES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)

# 小于这个体积不压（省下的还不够 gzip 头开销）
MIN_COMPRESS_SIZE = 1024

# 静态页缓存有效期。取得比较短（10 分钟）是为了「改了内容能较快生效」，
# 同时又能让「连续刷题」这种高频访问走本地副本/304。
PAGE_MAX_AGE = 600

# 需要走「页面级缓存」的路径（其余按接口处理）
PAGE_PATHS = ("/", "/admin")


def _is_compressible(resp):
    """响应类型是否值得压缩。"""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if not any(t in ctype for t in COMPRESSIBLE_TYPES):
        return False
    # 已经是压缩过的就不再压
    if resp.headers.get("Content-Encoding"):
        return False
    return True


def _accepts_gzip():
    """客户端是否声明支持 gzip。"""
    from flask import request
    enc = (request.headers.get("Accept-Encoding") or "").lower()
    return "gzip" in enc


def _should_compress(resp, body):
    """综合判断：类型、体积、客户端能力、状态码。"""
    if resp.status_code < 200 or resp.status_code >= 300:
        return False          # 错误页不值得压，也便于排查
    if not _accepts_gzip():
        return False
    if not _is_compressible(resp):
        return False
    if len(body) < MIN_COMPRESS_SIZE:
        return False
    return True


def _read_body(resp):
    """
    取出响应体字节。

    send_from_directory / send_file 返回的是 direct_passthrough 响应，
    此时 resp.get_data() 会抛 RuntimeError（Werkzeug 有意禁止直接读取）。
    对这种响应，改为从底层 iterable 里把内容取出来，
    再把 direct_passthrough 关掉，这样后续 set_data 才能生效。
    """
    if resp.direct_passthrough:
        chunks = []
        try:
            for chunk in resp.response:
                if isinstance(chunk, str):
                    chunk = chunk.encode(resp.charset or "utf-8")
                chunks.append(chunk)
        except Exception:
            return None
        body = b"".join(chunks)
        resp.response = [body]
        resp.direct_passthrough = False
        return body
    try:
        return resp.get_data()
    except Exception:
        return None


def install(app):
    """
    把压缩与缓存挂到 Flask app 上。在 server.py 里调用一次即可。
    """

    @app.after_request
    def _compress_and_cache(resp):
        from flask import request

        # ---------- 1) 缓存策略 ----------
        path = request.path or "/"
        ctype = (resp.headers.get("Content-Type") or "").lower()

        if resp.status_code == 200:
            if path in PAGE_PATHS or "text/html" in ctype:
                # 页面：允许短期本地缓存，过期后靠 ETag 走 304。
                # 不用 immutable，保证改动后 10 分钟内一定能拿到新版。
                resp.headers["Cache-Control"] = (
                    "public, max-age=%d, must-revalidate" % PAGE_MAX_AGE)
                # ETag 由 Flask/Werkzeug 基于文件 mtime+size 生成，已足够精确
                if "ETag" not in resp.headers:
                    pass
            elif path.startswith("/api/"):
                # 接口数据实时性优先，禁止缓存，但**仍然享受 gzip**
                resp.headers.setdefault("Cache-Control", "no-store")
            else:
                # 其他静态资源（如有）：给较长缓存
                resp.headers.setdefault(
                    "Cache-Control", "public, max-age=86400")

        if "Accept-Encoding" not in (resp.headers.get("Vary") or ""):
            _append_vary(resp, "Accept-Encoding")
        # 确保客户端知道响应可能因登录态而异
        if path.startswith("/api/"):
            _append_vary(resp, "X-ZSD-Token")

        # ---------- 2) gzip 压缩 ----------
        body = _read_body(resp)
        if body is None:
            return resp

        if _should_compress(resp, body):
            try:
                # compresslevel=6 是速度与压缩率的平衡点；
                # 这些是每次都要现压的文本，用 9 会明显拖慢首字节
                packed = gzip.compress(body, compresslevel=6)
                if len(packed) < len(body):
                    resp.set_data(packed)
                    resp.headers["Content-Encoding"] = "gzip"
                    resp.headers["Content-Length"] = str(len(packed))
            except Exception:
                pass    # 压缩失败就用原文，绝不影响功能

        return resp


def _append_vary(resp, field):
    """往 Vary 头追加字段（不覆盖已有的）。"""
    cur = resp.headers.get("Vary")
    if not cur:
        resp.headers["Vary"] = field
    elif field.lower() not in cur.lower():
        resp.headers["Vary"] = cur + ", " + field
