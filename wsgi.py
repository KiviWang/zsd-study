"""
PythonAnywhere WSGI 入口文件

用法（在 PythonAnywhere 的 Web 标签页里）：
1. 打开 WSGI configuration file（通常路径为
   /var/www/你的用户名_pythonanywhere_com_wsgi.py）
2. 把里面内容全部删掉，替换为下面这两行（注意改成你的用户名）：

    import sys
    sys.path.insert(0, '/home/你的用户名/zsd-study')
    from wsgi import application

就这样，不要在这里调用 app.run()。

说明：
- 真正的 Flask app 定义在 app/server.py 里，本文件只是把它暴露成 WSGI 变量 application。
- 数据库（SQLite 或 Turso）的初始化在 app/server.py 模块加载时自动完成。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE, "app")
for p in (BASE, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# 让 PythonAnywhere 的本地 SQLite 落在可持久化目录（项目内 data/）
os.environ.setdefault("ZSD_SQLITE_DIR", os.path.join(BASE, "data"))

from server import app as application  # noqa: E402
