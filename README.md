# 政史地学习平台

一个面向高中生的政史地在线复习平台，含学生端与家长端。

## 功能

**学生端**
- 逐课复习：知识卡、看片清单（含优先级与时间戳）、自测练习
- 电子错题本：错题自动收录，可重做，答对自动移出
- 10 天学习计划打卡
- 学习统计：正确率、答题数、掌握度评价

**家长端**（`/admin`，默认密码 `888888`）
- 答题正确率、错题数、连续学习天数
- 近 7 天学习时长柱状图
- 各课掌握情况、计划完成度、视频观看记录
- 可配置学生称呼、每日目标、管理密码

## 本地运行

```bash
pip install -r requirements.txt
python3 app/server.py
```

访问 `http://127.0.0.1:5000`（学生端）或 `http://127.0.0.1:5000/admin`（家长端）。

## 部署（免费方案）

推荐 **PythonAnywhere 免费版**：不需要信用卡、不休眠、支持 Turso 云数据库。
（`turso.io` / `turso.tech` 已在 PythonAnywhere 免费版的外网允许列表内。）

部署要点：

1. 注册 PythonAnywhere Beginner 账号
2. 创建 Turso 云数据库，拿到 `URL`（`libsql://` 改 `https://`）和 `Token`
3. 用 Bash 控制台从 GitHub 拉取代码，创建虚拟环境并 `pip install -r requirements.txt`
4. Web 标签页 → Add a new web app → **Manual configuration** → 选一致的 Python 版本
5. 设置 Virtualenv 路径
6. 编辑 WSGI 文件，内容替换为：
   ```python
   import sys
   sys.path.insert(0, '/home/你的用户名/zsd-study')
   from wsgi import application
   ```
7. 在 Environment variables 里加 `TURSO_URL`、`TURSO_TOKEN`
8. 点 **Reload**

> 注意：免费版**不要**写 `app.run()`（会导致 504/502）；每 30 天需登录一次续期。

也可用 Render 免费版（已附带 `render.yaml`），但 Render 要求绑定信用卡。

详见 `部署说明.md`。

## 数据库说明

支持两种后端，自动切换：

| 条件 | 使用的数据库 |
|------|------------|
| 设置了 `TURSO_URL` 和 `TURSO_TOKEN` | Turso 云数据库（数据持久化） |
| 未设置 | 本地 SQLite（`data/study.db`） |

Turso 通过 HTTP API 调用，不依赖额外第三方库。

## 目录结构

```
.
├── app/
│   ├── server.py      # Flask 服务与 API
│   ├── db.py          # 数据访问层（SQLite / Turso 自适应）
│   └── courses.py     # 课程数据
├── static/
│   ├── index.html     # 学生端
│   └── admin.html     # 家长端
├── data/              # 本地数据库目录
├── requirements.txt
├── wsgi.py            # PythonAnywhere WSGI 入口
├── render.yaml        # Render 部署配置（备选）
└── 部署说明.md
```

## 扩展科目

在 `app/courses.py` 中追加 `COURSES` 条目与 `DAILY_PLAN` 计划，重启服务即可，前端无需改动。
