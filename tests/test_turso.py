"""
Turso 适配层逻辑验证
用本地 mock 服务器模拟 Turso HTTP API 的响应格式，
验证 db.py 中的参数转换和结果解析逻辑正确。
"""
import json, threading, sys, os
from http.server import HTTPServer, BaseHTTPRequestHandler

# 模拟数据存储
DB = {"answers": [], "next_id": 1}

class MockTurso(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))

        # 验证认证头
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer test-token":
            self.send_response(401); self.end_headers(); return

        results = []
        for req in body.get("requests", []):
            if req.get("type") == "close":
                continue
            stmt = req.get("stmt", {})
            sql = stmt.get("sql", "").strip()
            args = stmt.get("args", [])
            up = sql.upper()

            if up.startswith("INSERT"):
                DB["next_id"] += 1
                results.append({"type":"ok","response":{"result":{
                    "cols":[], "rows":[], "affected_row_count":1,
                    "last_insert_rowid":DB["next_id"]}}})
            elif up.startswith("DELETE") or up.startswith("UPDATE") or up.startswith("CREATE"):
                results.append({"type":"ok","response":{"result":{
                    "cols":[], "rows":[], "affected_row_count":1,
                    "last_insert_rowid":None}}})
            elif up.startswith("SELECT"):
                # 返回一行模拟数据，验证类型转换
                results.append({"type":"ok","response":{"result":{
                    "cols":[{"name":"id"},{"name":"name"},{"name":"score"},{"name":"ok"}],
                    "rows":[[
                        {"type":"integer","value":"42"},
                        {"type":"text","value":"测试"},
                        {"type":"float","value":"3.5"},
                        {"type":"null","value":None},
                    ]],
                    "affected_row_count":0, "last_insert_rowid":None}}})
            else:
                results.append({"type":"ok","response":{"result":{
                    "cols":[], "rows":[], "affected_row_count":0,
                    "last_insert_rowid":None}}})

        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"results":results}).encode())

def run():
    srv = HTTPServer(("127.0.0.1", 5099), MockTurso)
    srv.serve_forever()

threading.Thread(target=run, daemon=True).start()

# 设置环境变量，让 db 走 Turso 分支
os.environ["TURSO_URL"] = "http://127.0.0.1:5099"
os.environ["TURSO_TOKEN"] = "test-token"
sys.path.insert(0, "/workspace/政史地学习平台/app")

import importlib
import db
importlib.reload(db)

results = []
def ck(n, c, e=""):
    results.append((n,c,e)); print(f"{'✓' if c else '✗'} {n} {e}")

ck("检测到 Turso 模式", db.USE_TURSO == True, str(db.USE_TURSO))

# 测试参数转换
ck("文本参数转换", db._to_arg("abc") == {"type":"text","value":"abc"})
ck("整数参数转换", db._to_arg(5) == {"type":"integer","value":"5"})
ck("空值参数转换", db._to_arg(None) == {"type":"null"})
ck("浮点参数转换", db._to_arg(1.5) == {"type":"float","value":"1.5"})
ck("布尔参数转整数", db._to_arg(True) == {"type":"integer","value":"1"})

# 测试返回值解析
ck("整数解析", db._from_arg({"type":"integer","value":"42"}) == 42)
ck("文本解析", db._from_arg({"type":"text","value":"测试"}) == "测试")
ck("浮点解析", db._from_arg({"type":"float","value":"3.5"}) == 3.5)
ck("空值解析", db._from_arg({"type":"null","value":None}) is None)

# 测试查询（验证 HTTP 往返）
rows = db.query("SELECT * FROM t")
ck("query 返回结果", len(rows) == 1, str(len(rows)))
if rows:
    r = rows[0]
    ck("列名映射正确", set(r.keys()) == {"id","name","score","ok"}, str(list(r.keys())))
    ck("id 为整数", r["id"] == 42 and isinstance(r["id"], int), f"{r['id']} {type(r['id']).__name__}")
    ck("name 为中文文本", r["name"] == "测试", r["name"])
    ck("score 为浮点", r["score"] == 3.5 and isinstance(r["score"], float))
    ck("ok 为 None", r["ok"] is None)

# 测试写入
res = db.execute("INSERT INTO t VALUES(?)", ("x",))
ck("execute 返回 last_id", res.get("last_id") == 2, str(res))

# 测试业务函数（走 Turso 分支）
try:
    db.init_db()
    ck("init_db 在 Turso 模式可执行", True)
except Exception as e:
    ck("init_db 在 Turso 模式可执行", False, str(e))

print("\n" + "="*50)
ok = sum(1 for _,c,_ in results if c)
print(f"Turso 适配测试: {ok}/{len(results)} 通过")
f = [n for n,c,_ in results if not c]
print("失败项: " + (", ".join(f) if f else "无 ✓"))
