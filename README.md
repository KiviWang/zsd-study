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

## 部署到 Render（免费）

本项目已配置 `render.yaml`，可直接部署：

1. 把代码推送到 GitHub 仓库
2. 在 Render 新建 Blueprint，选择该仓库
3. 填入环境变量 `TURSO_URL` 和 `TURSO_TOKEN`
4. 部署完成后即可通过分配的网址访问

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
├── render.yaml        # Render 部署配置
└── 部署说明.md
```

## 扩展科目

在 `app/courses.py` 中追加 `COURSES` 条目与 `DAILY_PLAN` 计划，重启服务即可，前端无需改动。
