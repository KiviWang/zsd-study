#!/usr/bin/env python3
"""历史全册专项测试：人教版《中外历史纲要（上）》h1-h29 内容完整性

覆盖：课程接入、课序、知识点/视频/题库结构、分层出题、SVG 配图、
      题干质量（占位符/选项数/答案下标/解析）、近代史关键年份抽查。
"""
import json, os, re, urllib.request, urllib.error, time

BASE = "http://127.0.0.1:5097"
OK = FAIL = 0
# 全册 h1-h29 已交付（2024 版新目录）；如需只测前 N 课，设 ZSD_H_BATCH_END=N
BATCH_END = int(os.environ.get("ZSD_H_BATCH_END", "29"))
H_IDS = [f"h{i}" for i in range(1, BATCH_END + 1)]
FULL = BATCH_END >= 29


def req(path, method="GET", body=None, token=None):
    r = urllib.request.Request(BASE + path,
        data=json.dumps(body).encode() if body is not None else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-ZSD-Token", token)
    else:
        r.add_header("X-ZSD-Anon", json.dumps({"answers": [], "wrong": []}))
    try:
        with urllib.request.urlopen(r) as resp:
            return json.loads(resp.read() or b"{}"), resp.status
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read() or b"{}"), e.code
        except Exception: return {}, e.code


def fresh_token():
    ts = str(int(time.time() * 1000))[-8:]
    d, _ = req("/api/register", "POST", {
        "username": "th" + ts, "phone": "138" + ts,
        "name": "历史测试", "password": "test1234"})
    return d.get("token") or d.get("code")


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1; print("  ✅ " + name)
    else:
        FAIL += 1; print("  ❌ " + name + (f"  [{extra}]" if extra else ""))


print("=" * 64)
print(f"【1】历史课目接入：h1-h{BATCH_END} 全部在线" + ("（全册）" if FULL else ""))
print("=" * 64)
d, _ = req("/api/courses")
cs = d.get("courses", [])
hmap = {c["id"]: c for c in cs if c.get("subject") == "历史"}
chk(f"历史共 {len(H_IDS)} 课", len(hmap) == len(H_IDS), f"实际 {len(hmap)}")
missing = [i for i in H_IDS if i not in hmap]
chk(f"h1-h{BATCH_END} 无缺课", not missing, str(missing))
chk("每课都有标题", all(c.get("title") for c in hmap.values()))
chk("每课都有副标题", all(c.get("subtitle") for c in hmap.values()))
chk("每课都有配色", all(c.get("color") for c in hmap.values()))

# 课序：api 返回顺序应与 h1..hN 一致
order = [c["id"] for c in cs if c.get("subject") == "历史"]
chk(f"课序按 h1→h{BATCH_END} 排列", order == H_IDS, str(order[:8]) + "...")

print()
print("=" * 64)
print("【2】逐课结构完整性（知识点7 / 视频3 / 题库10+10）")
print("=" * 64)
bad_struct = []
for cid in H_IDS:
    d, st = req(f"/api/course/{cid}")
    if st != 200:
        bad_struct.append((cid, f"status={st}")); continue
    pts = d.get("points", []); vids = d.get("videos", [])
    qb = d.get("quiz", [])
    if len(pts) < 7: bad_struct.append((cid, f"知识点 {len(pts)}"))
    if len(vids) < 3: bad_struct.append((cid, f"视频 {len(vids)}"))
    if len(qb) != 10: bad_struct.append((cid, f"题库 {len(qb)}"))
chk(f"{len(H_IDS)} 课结构全部达标", not bad_struct, str(bad_struct[:6]))

chk("每课题数 20（基础10+强化10）",
    all(hmap[cid].get("quizCount") == 20 for cid in H_IDS),
    str([(c, hmap[c].get("quizCount")) for c in H_IDS if hmap[c].get("quizCount") != 20]))

print()
print("=" * 64)
print("【3】分层出题：基础 / 强化 / 随机")
print("=" * 64)
tk = fresh_token()
chk("临时学生拿到 token", bool(tk))
nobase = []
for cid in H_IDS:
    b, _ = req(f"/api/course/{cid}?mode=basic&limit=10", token=tk)
    r, _ = req(f"/api/course/{cid}?mode=random&limit=10", token=tk)
    bq = {q["qid"] for q in b.get("quiz", [])}
    rq = {q["qid"] for q in r.get("quiz", [])}
    if len(bq) != 10 or len(rq) != 10 or (bq & rq):
        nobase.append((cid, len(bq), len(rq), len(bq & rq)))
chk(f"{len(H_IDS)} 课：基础10 / 随机10 / 零重叠", not nobase, str(nobase[:5]))

# qid 命名规范：基础 <cid>_NN，强化 <cid>_aNN
d, _ = req("/api/course/h3?mode=basic&limit=10", token=tk)
bq = [q["qid"] for q in d.get("quiz", [])]
chk("基础题 qid 形如 h3_NN", all(q.startswith("h3_") and not q.startswith("h3_a") for q in bq), str(bq[:3]))
chk("基础题 qid 两位数序号", all(re.fullmatch(r"h3_\d{2}", q) for q in bq), str(bq[:3]))
d, _ = req("/api/course/h3?mode=random&limit=10", token=tk)
rq = [q["qid"] for q in d.get("quiz", [])]
chk("强化题 qid 形如 h3_aNN", all(re.fullmatch(r"h3_a\d{2}", q) for q in rq), str(rq[:3]))

print()
print("=" * 64)
print("【4】题干质量：占位符 / 选项数 / 答案下标 / 解析")
print("=" * 64)
allq = []
for cid in H_IDS:
    for mode in ("basic", "random"):
        d, _ = req(f"/api/course/{cid}?mode={mode}&limit=10", token=tk)
        allq += d.get("quiz", [])
chk(f"历史累计取题 {len(H_IDS)*20}", len(allq) == len(H_IDS) * 20, str(len(allq)))
chk(f"题量 = {len(H_IDS)} 课 × 20", len(allq) == len(H_IDS) * 20, str(len(allq)))
bad_word = [q for q in allq if any(w in json.dumps(q, ensure_ascii=False)
            for w in ["TODO", "占位", "待补", "restore", "XXX"])]
chk("无占位符", not bad_word, str([q["qid"] for q in bad_word[:5]]))
bad_opt = [q for q in allq if len(q.get("o", [])) != 4]
chk("每题恰好 4 选项", not bad_opt, str([q["qid"] for q in bad_opt[:5]]))
bad_ans = [q for q in allq if not (0 <= q.get("a", -1) <= 3)]
chk("答案下标 0-3", not bad_ans, str([q["qid"] for q in bad_ans[:5]]))
bad_exp = [q for q in allq if len((q.get("e") or "").strip()) < 4]
chk("每题都有实质性解析", not bad_exp, str([q["qid"] for q in bad_exp[:5]]))
bad_stem = [q for q in allq if len((q.get("q") or "").strip()) < 6]
chk("题干非空且够长", not bad_stem, str([q["qid"] for q in bad_stem[:5]]))
dup_opt = [q for q in allq if len(set(q.get("o", []))) != 4]
chk("同题选项互不重复", not dup_opt, str([q["qid"] for q in dup_opt[:5]]))

print()
print("=" * 64)
print("【5】SVG 配图：历史全册知识点图 + 题目图")
print("=" * 64)
n_pt_svg = n_q_svg = 0
bad_svg = []
for cid in H_IDS:
    d, _ = req(f"/api/course/{cid}")
    for i, p in enumerate(d.get("points", [])):
        s = p.get("svg") or ""
        if s:
            n_pt_svg += 1
            if not s.startswith("<svg") or "viewBox" not in s or not s.rstrip().endswith("</svg>"):
                bad_svg.append(f"{cid}.pt{i}")
    for q in d.get("quiz", []):
        s = q.get("svg") or ""
        if s:
            n_q_svg += 1
            if not s.startswith("<svg") or "viewBox" not in s or not s.rstrip().endswith("</svg>"):
                bad_svg.append(q["qid"])
print(f"  ℹ️  知识点配图 {n_pt_svg} 张 / 题目配图 {n_q_svg} 张")
chk("知识点配图 ≥20 张", n_pt_svg >= 20, str(n_pt_svg))
chk("题目配图 ≥25 张", n_q_svg >= 25, str(n_q_svg))
chk("所有 SVG 合法（含 viewBox 且闭合）", not bad_svg, str(bad_svg[:5]))

# 抽查 h1/h2 之外新增课的配图确实存在
d, _ = req("/api/course/h3")
chk("h3 知识点含配图", any(p.get("svg") for p in d.get("points", [])),
    str([i for i, p in enumerate(d.get("points", [])) if p.get("svg")]))

print()
print("=" * 64)
print("【6】近代史史实抽查（年份 / 条约 / 人物）")
print("=" * 64)
# 从接口捞取指定课的题干全文，做关键词抽查
def stems(cid, mode="basic"):
    d, _ = req(f"/api/course/{cid}?mode={mode}&limit=10", token=tk)
    return " ".join((q.get("q") or "") + " " + " ".join(q.get("o", [])) + " " + (q.get("e") or "")
                    for q in d.get("quiz", []))

facts = [
    ("h15", ["鸦片战争", "1840"], "鸦片战争与1840"),
    ("h16", ["太平天国", "洋务"], "太平天国/洋务运动"),
    ("h17", ["义和团", "维新"], "维新变法/义和团"),
    ("h18", ["辛亥革命", "1911"], "辛亥革命与1911"),
    ("h19", ["新文化", "北洋"], "新文化运动/北洋军阀"),
    ("h20", ["五四", "1921"], "五四运动/中共成立"),
    ("h21", ["长征", "井冈山"], "井冈山/长征"),
    ("h22", ["西安事变"], "西安事变"),
    ("h23", ["抗日", "1937"], "抗日战争与1937"),
    ("h24", ["解放战争", "1949"], "解放战争与1949"),
    ("h25", ["1949", "新中国"], "新中国成立"),
    ("h26", ["1954", "一五"], "一五计划/1954宪法"),
    ("h27", ["改革开放", "1978"], "改革开放与1978"),
    ("h28", ["一国两制", "香港"], "一国两制/港澳回归"),
    ("h29", ["新时代", "十八大"], "新时代"),
]
skipped = []
for cid, kws, desc in facts:
    if cid not in hmap:
        skipped.append(cid); continue
    txt = stems(cid)
    hit = [k for k in kws if k in txt]
    chk(f"{cid} 覆盖 {desc}", len(hit) >= 1, f"命中 {hit} / 期望 {kws}")
if skipped:
    print(f"  ℹ️  第二批未上线，跳过史实抽查：{skipped}")

print()
print("=" * 64)
print(f"结果：{OK} 通过 / {FAIL} 失败")
print("=" * 64)
