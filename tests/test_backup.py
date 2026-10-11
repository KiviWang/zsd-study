"""备份脚本的离线验证：mock requests，覆盖全部分支"""
import sys, os, json, base64, gzip, importlib
from unittest import mock

PROJ = "/workspace/政史地学习平台"
sys.path.insert(0, PROJ)
STUDENT_DB = os.path.join(PROJ, "data", "study.db")

# 准备一个真实数据库供压缩
os.makedirs(os.path.join(PROJ, "data"), exist_ok=True)
import sqlite3
conn = sqlite3.connect(STUDENT_DB)
conn.execute("CREATE TABLE IF NOT EXISTS t(x TEXT)")
conn.execute("INSERT INTO t VALUES('hello')")
conn.commit(); conn.close()
print("测试数据库已就绪:", os.path.getsize(STUDENT_DB), "bytes")

import requests

class Resp:
    def __init__(self, code, data=None, text=""):
        self.status_code = code
        self._data = data if data is not None else {}
        self.text = text or json.dumps(self._data)
    def json(self): return self._data

PASS = []; FAIL = []

def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + (f" | {extra}" if extra else ""))
    print(("  OK  " if cond else "  X   ") + name + (f"  [{extra}]" if extra else ""))

def run_script(env, mock_get, mock_put, mock_del):
    """在干净环境里重新导入脚本并执行 main()"""
    for k in list(sys.modules):
        if k == "backup_db": del sys.modules[k]
    os.environ.update(env)
    import backup_db
    with mock.patch.object(requests, "get", mock_get), \
         mock.patch.object(requests, "put", mock_put), \
         mock.patch.object(requests, "delete", mock_del):
        try:
            backup_db.main()
            return 0
        except SystemExit as e:
            return e.code

print("\n===== 场景1：正常首次备份（云端无文件）=====")
calls = {"put": [], "del": []}
def g1(url, headers=None, timeout=None):
    if url.endswith("/contents/"):
        return Resp(200, [])
    if "/contents/study-" in url:
        return Resp(404, {"message": "Not Found"})
    if url.endswith(f"/repos/KiviWang/zsd-study-backup"):
        return Resp(200, {"permissions": {"push": True}})
    return Resp(404)
def p1(url, headers=None, json=None, timeout=None):
    calls["put"].append(json["message"]); return Resp(201, {})
def d1(*a, **k): return Resp(200, {})
env = {"BACKUP_REPO": "KiviWang/zsd-study-backup", "BACKUP_TOKEN": "ghp_fake", "BACKUP_KEEP": "30"}
rc = run_script(env, g1, p1, d1)
chk("首次备份退出码0", rc == 0, rc)
chk("已执行上传", len(calls["put"]) == 1, calls["put"])
chk("本地已生成压缩包", any(f.endswith(".db.gz") for f in os.listdir(os.path.join(PROJ, "backups"))))
gz = [f for f in os.listdir(os.path.join(PROJ, "backups")) if f.endswith(".db.gz")][0]
with gzip.open(os.path.join(PROJ, "backups", gz), "rb") as f:
    head = f.read(16)
chk("压缩包是合法SQLite(含SQLite字样)", b"SQLite" in head, head[:16])

print("\n===== 场景2：同日重复运行 → 覆盖(带sha) =====")
calls["put"] = []
def g2(url, headers=None, timeout=None):
    if url.endswith("/contents/"):
        return Resp(200, [{"name": gz, "sha": "abc123"}])
    if "/contents/study-" in url:
        return Resp(200, {"sha": "abc123"})
    if url.endswith("/repos/KiviWang/zsd-study-backup"):
        return Resp(200, {"permissions": {"push": True}})
    return Resp(404)
p2 = lambda *a, **k: (calls["put"].append(k.get("json", {}).get("sha")), Resp(200, {}))[1]
rc = run_script(env, g2, p2, d1)
chk("重复备份退出码0", rc == 0, rc)
chk("上传时带上sha(覆盖)", calls["put"] == ["abc123"], calls["put"])

print("\n===== 场景3：超过30份 → 触发清理 =====")
# 造 35 个文件（最新在前）
files = []
for i in range(35, 0, -1):
    files.append({"name": f"study-2026-09-{i:02d}.db.gz", "sha": f"sha{i}"})
files.append({"name": gz, "sha": "today"})
files.sort(key=lambda x: x["name"], reverse=True)
deleted = []
def g3(url, headers=None, timeout=None):
    if url.endswith("/contents/"):
        return Resp(200, files)
    if "/contents/study-" in url:
        return Resp(200, {"sha": "today"})
    if url.endswith("/repos/KiviWang/zsd-study-backup"):
        return Resp(200, {"permissions": {"push": True}})
    return Resp(404)
def d3(url, headers=None, json=None, timeout=None):
    deleted.append(url.split("/contents/")[-1]); return Resp(200, {})
rc = run_script(env, g3, p1, d3)
chk("清理场景退出码0", rc == 0, rc)
chk("共36份→删6份保留30", len(deleted) == 6, f"实际删除{len(deleted)}份")

print("\n===== 场景4：Token 无效 → 明确报错 =====")
def g4(url, headers=None, timeout=None):
    return Resp(401, {"message": "Bad credentials"})
out = []
import io
from contextlib import redirect_stdout
buf = io.StringIO()
with redirect_stdout(buf):
    rc = run_script(env, g4, p1, d1)
chk("401退出码非0", rc != 0, rc)
chk("提示Token无效", "Token 无效" in buf.getvalue(), buf.getvalue().strip()[-40:])

print("\n===== 场景5：仓库无写权限 → 明确报错 =====")
def g5(url, headers=None, timeout=None):
    if url.endswith("/repos/KiviWang/zsd-study-backup"):
        return Resp(200, {"permissions": {"push": False}})
    return Resp(404)
buf = io.StringIO()
with redirect_stdout(buf):
    rc = run_script(env, g5, p1, d1)
chk("无权限退出码非0", rc != 0, rc)
chk("提示无写入权限", "写入权限" in buf.getvalue())

print("\n===== 场景6：缺少配置 → 明确报错 =====")
buf = io.StringIO()
with redirect_stdout(buf):
    rc = run_script({"BACKUP_REPO": "", "BACKUP_TOKEN": ""}, g1, p1, d1)
chk("缺配置退出码非0", rc != 0, rc)
chk("提示配置不完整", "配置不完整" in buf.getvalue())

print("\n===== 场景7：数据库不存在 → 明确报错 =====")
os.rename(STUDENT_DB, STUDENT_DB + ".bak")
buf = io.StringIO()
with redirect_stdout(buf):
    rc = run_script(env, g1, p1, d1)
chk("缺db退出码非0", rc != 0, rc)
chk("提示找不到数据库", "找不到数据库" in buf.getvalue())
os.rename(STUDENT_DB + ".bak", STUDENT_DB)

print("\n" + "="*50)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for f in FAIL: print("  X", f)
