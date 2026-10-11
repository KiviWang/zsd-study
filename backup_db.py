#!/usr/bin/env python3
"""
政史地学习平台 · 数据库每日自动备份

作用：
  把 data/study.db 压缩后上传到 GitHub 私有仓库，并只保留最近 N 份。

为什么用 GitHub API 而不是 git 命令：
  - PythonAnywhere 免费版无需额外配置 git 凭证
  - 单文件上传，简单可靠
  - 全程只需一个 Token

需要设置的环境变量（在 PythonAnywhere 的 Tasks 里配置，或写在本文件顶部）：
  BACKUP_REPO    备份仓库，格式 "用户名/仓库名"，如 KiviWang/zsd-study-backup
  BACKUP_TOKEN   GitHub Personal Access Token（需 repo 权限）
  BACKUP_KEEP    保留份数，默认 30（可选）

用法：
  python3 backup_db.py            # 正常备份
  python3 backup_db.py --check    # 只检查配置与连通性，不实际备份
"""
import os
import sys
import gzip
import json
import base64
import shutil
import tempfile
import requests
from datetime import datetime, date

# ---------- 路径 ----------
BASE = os.path.dirname(os.path.abspath(__file__))
STUDENT_DB = os.path.join(BASE, "data", "study.db")
BACKUP_DIR = os.path.join(BASE, "backups")   # 本地暂存目录（可不存在）

# ---------- 配置 ----------
REPO = os.environ.get("BACKUP_REPO", "").strip()
TOKEN = os.environ.get("BACKUP_TOKEN", "").strip()
KEEP = int(os.environ.get("BACKUP_KEEP", "30") or "30")
API = "https://api.github.com"
HEADERS = {
    "Authorization": "Bearer " + TOKEN,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

CHECK_ONLY = "--check" in sys.argv


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def fail(msg, code=1):
    log("错误: " + msg)
    sys.exit(code)


# =========================================================
# 检查配置
# =========================================================
def check_config():
    problems = []
    if not REPO or "/" not in REPO:
        problems.append("BACKUP_REPO 未设置或格式不对（应为 用户名/仓库名）")
    if not TOKEN:
        problems.append("BACKUP_TOKEN 未设置")
    if problems:
        for p in problems:
            log("  - " + p)
        fail("配置不完整，请先在 Tasks 页面或脚本顶部设置环境变量")

    log(f"备份仓库: {REPO}")
    log(f"保留份数: {KEEP}")

    # 验证仓库可访问、Token 有写权限
    r = requests.get(f"{API}/repos/{REPO}", headers=HEADERS, timeout=20)
    if r.status_code == 401:
        fail("Token 无效或已过期（401）")
    if r.status_code == 404:
        fail(f"仓库不存在或无权限访问（404）：{REPO}。请确认仓库已创建且是 Private，"
             f"Token 勾选了 repo 权限")
    if r.status_code != 200:
        fail(f"访问仓库失败：HTTP {r.status_code} {r.text[:200]}")

    perms = r.json().get("permissions", {})
    if not perms.get("push"):
        fail("Token 对该仓库没有写入权限，请重新生成并勾选 repo 权限")
    log("仓库连接正常，Token 有写入权限")
    return True


# =========================================================
# 压缩数据库
# =========================================================
def make_archive():
    if not os.path.exists(STUDENT_DB):
        fail(f"找不到数据库文件：{STUDENT_DB}（尚未有学习数据？）")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = date.today().isoformat()
    name = f"study-{stamp}.db.gz"
    out_path = os.path.join(BACKUP_DIR, name)

    src_size = os.path.getsize(STUDENT_DB)
    with open(STUDENT_DB, "rb") as fin, gzip.open(out_path, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout)
    out_size = os.path.getsize(out_path)

    log(f"已压缩：{name}（{src_size/1024:.1f}KB → {out_size/1024:.1f}KB）")
    return name, out_path


# =========================================================
# 上传到 GitHub
# =========================================================
def gh_list():
    """列出备份文件（仓库根目录下的 study-*.db.gz）"""
    r = requests.get(f"{API}/repos/{REPO}/contents/", headers=HEADERS, timeout=20)
    if r.status_code != 200:
        fail(f"读取仓库文件列表失败：HTTP {r.status_code} {r.text[:200]}")
    items = r.json()
    if not isinstance(items, list):
        return []
    return [i for i in items if i["name"].startswith("study-") and i["name"].endswith(".db.gz")]


def gh_upload(name, path):
    """上传（若同日已存在则覆盖，保证一天一份）"""
    remote_path = name
    # 查是否已存在（同一天重复运行时覆盖）
    sha = None
    r = requests.get(f"{API}/repos/{REPO}/contents/{remote_path}",
                     headers=HEADERS, timeout=20)
    if r.status_code == 200:
        sha = r.json().get("sha")

    with open(path, "rb") as f:
        content = base64.b64encode(f.read()).decode()

    payload = {"message": f"备份 {name}", "content": content, "branch": "main"}
    if sha:
        payload["sha"] = sha

    r = requests.put(f"{API}/repos/{REPO}/contents/{remote_path}",
                     headers=HEADERS, json=payload, timeout=60)
    if r.status_code not in (200, 201):
        fail(f"上传失败：HTTP {r.status_code} {r.text[:300]}")
    log(f"已上传到云端：{name}" + ("（覆盖今日旧备份）" if sha else ""))


def gh_cleanup():
    """只保留最近 KEEP 份，删除多余的旧备份"""
    items = gh_list()
    # 文件名 study-YYYY-MM-DD.db.gz，按名字倒序即按日期倒序
    items.sort(key=lambda x: x["name"], reverse=True)
    if len(items) <= KEEP:
        log(f"云端现有 {len(items)} 份，未超过 {KEEP} 份上限，无需清理")
        return
    for old in items[KEEP:]:
        r = requests.delete(f"{API}/repos/{REPO}/contents/{old['name']}",
                            headers=HEADERS,
                            json={"message": f"清理旧备份 {old['name']}",
                                  "sha": old["sha"], "branch": "main"},
                            timeout=30)
        if r.status_code == 200:
            log(f"已清理旧备份：{old['name']}")
        else:
            log(f"清理失败（忽略）：{old['name']} HTTP {r.status_code}")


# =========================================================
# 主流程
# =========================================================
def main():
    log("=" * 46)
    log("数据库备份任务开始")
    check_config()
    if CHECK_ONLY:
        log("仅检查模式，流程结束")
        return
    name, path = make_archive()
    gh_upload(name, path)
    gh_cleanup()
    log("备份任务完成")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.Timeout:
        fail("网络超时：访问 GitHub 失败，请稍后重试")
    except requests.exceptions.ConnectionError:
        fail("网络连接失败：无法访问 api.github.com")
