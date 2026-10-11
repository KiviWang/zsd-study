# 政史地学习平台

一个面向高中生的政史地在线复习平台，含学生端与家长端。

## 功能

**学生端**
- 逐课复习：知识卡（含示意图）、看片清单（含优先级与时间戳）、自测练习
- 课程列表按学科分组，点学科标题可收起/展开（状态记在本机）
- 电子错题本：错题自动收录，可重做；答对后保留并标记「已订正」，留下订正足迹
- 10 天学习计划打卡
- 学习统计：正确率、答题数、掌握度评价

**课程内容**（当前 25 课 / 500 题）

| 学科 | 课数 | 题量 | 说明 |
|------|------|------|------|
| 政治 | 4 | 80 | 必修 1 |
| 历史 | 15 | 300 | 《中外历史纲要（上）》第 1-15 课（先秦至明清），第二批 16-29 课待补 |
| 地理 | 6 | 120 | 必修 1 第一、二章 |

每课统一结构：7 条知识点 + 3 条视频清单 + 基础 10 题 + 强化 10 题。

**家长端**（`/admin`，默认密码 `888888`）
- 答题正确率、错题数、连续学习天数
- 近 7 天学习时长柱状图
- 各课掌握情况、计划完成度、视频观看记录
- 多学生管理：新增学生、切换查看、改名/停用/删除
- 可配置每日目标分与家长端管理密码

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
│   ├── bank.py        # 题库访问层：分层出题、随机抽题、增量入库
│   ├── courses.py     # 课程总入口（政治 4 课 + 拼装历史/地理）
│   ├── courses_history.py  # 历史课程与题库（含内联 SVG 示意图工具）
│   └── courses_geo.py      # 地理课程与题库
├── tests/             # 端到端与内容专项测试（13 个脚本）
├── static/
│   ├── index.html     # 学生端
│   └── admin.html     # 家长端
├── data/              # 本地数据库目录
├── requirements.txt
├── wsgi.py            # PythonAnywhere WSGI 入口
├── backup_db.py       # 数据库每日备份脚本（推送到 GitHub 私有仓库）
├── render.yaml        # Render 部署配置（备选）
└── 部署说明.md
```

## 数据备份

`backup_db.py` 可将数据库压缩后自动上传到 GitHub **私有仓库**，并只保留最近 N 份。

```bash
# 检查配置与连通性（不实际备份）
BACKUP_REPO=用户名/仓库名 BACKUP_TOKEN=ghp_xxx python3 backup_db.py --check

# 执行备份
BACKUP_REPO=用户名/仓库名 BACKUP_TOKEN=ghp_xxx BACKUP_KEEP=30 python3 backup_db.py
```

搭配 PythonAnywhere 的 **Tasks** 功能可每天自动执行，详见 `部署说明.md` 第十节。

> ⚠️ 备份必须指向 **Private 私有仓库**，否则学习记录会公开可见。

## 扩展科目

每科一个文件（`courses_history.py` / `courses_geo.py`），按既有结构追加课程条目，
重启服务即自动增量入库（已有 qid 的题不会覆盖），前端无需改动。

新增历史课时的课目编号与 qid 规则：课程 id 形如 `h16`，基础题 `h16_01`，强化题 `h16_a01`。
