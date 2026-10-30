#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AtomPages 采集器

扫描源工作区里的 HTML 产出，解析元信息，判定模块归属，
产出：
  pages/<module>/<slug>.html   收录的 HTML（自包含优先）
  pdfs/<module>/<slug>.pdf      同名 PDF（存在才收）
  thumbs/<module>/<slug>.jpg    首页卡片缩略图（Playwright 真实渲染）
  data/modules.json             模块与条目的元数据清单
  data/collection.log           本次采集的变更日志

设计要点
  · 模块可不断新增：模块表由「已存在的条目」自动聚合，条目按稳定 slug 归档，
    新条目进来只会新增条目编号，不会打乱既有编号。
  · 幂等：内容未变化时不重写文件、不重复生成缩略图。
  · 尺寸归一：源 deck 画布有 1280x720 与 1920x1080 两种，还有的用 100vh，
    预览框统一用 transform:scale 适配，不改源文件。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

CN_TZ = timezone(timedelta(hours=8))

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PAGES_DIR = ROOT / "pages"
PDFS_DIR = ROOT / "pdfs"
THUMBS_DIR = ROOT / "thumbs"
LOG_PATH = DATA_DIR / "collection.log"

# ── 源工作区：默认扫描本机 Claw 目录，可用 --source 覆盖 ────────────────────
DEFAULT_SOURCES = [
    Path("C:/Users/wyg/WorkBuddy/Claw"),
]

# ── 模块判定规则 ─────────────────────────────────────────────────────────
# 目录名 → 模块 slug / 中文名。命中即归类；未命中走关键词兜底。
MODULE_RULES = [
    # (正则, slug, 中文名, 描述)
    (r"microcourse|微课", "microcourse", "智能体微课", "基于智能体的微课全流程制作方法"),
    (r"chatbot|agent", "agent-basics", "智能体基础", "Chatbot 与 Agent 的区别、调用模式与技能化"),
    (r"interface|交互界面", "interface", "交互界面", "交互界面与人的认知瓶颈"),
    (r"model-eats|模型吃掉", "model-eats-app", "模型与应用", "大模型迭代对应用层创新的降维打击"),
    (r"cognition|认知瓶颈", "cognition", "认知瓶颈", "介质转换成本归零之后，认知成为唯一瓶颈"),
    (r"skill-engineering|技能化", "skill-engineering", "技能化开发", "从 Agent 调用模式抽取 Skill 的开发方法"),
    (r"selfmedia|自媒体", "selfmedia-gap", "自媒体鸿沟", "自媒体与自我应用之间的效果落差"),
    (r"training-product|培训交付", "training-product", "培训交付", "培训从交付理念转向直接交付产品"),
    (r"ethics|伦理|policy|政策", "ai-ethics-policy", "伦理与安全", "人工智能伦理与安全的政策依据"),
    (r"atom-system|atom", "atom-system", "Atom 系统", "Atom 规范、运行系统与一稿多线"),
    (r"remotion|video|视频", "video", "视频制作", "口播视频与动效"),
]

DEFAULT_MODULE = "misc"

# 模块显示顺序（未列出的排后面）
MODULE_ORDER = [
    "ai-ethics-policy", "atom-system", "training-product", "selfmedia-gap",
    "microcourse", "skill-engineering", "agent-basics", "interface",
    "model-eats-app", "cognition", "video", "misc",
]

# ── HTML 解析辅助 ────────────────────────────────────────────────────────
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
SLIDE_RE = re.compile(r'class="[^"]*\bslide\b[^"]*"', re.I)
PAGE_RE = re.compile(r'class="[^"]*\bpage\b[^"]*"', re.I)
EXTERNAL_REF_RE = re.compile(r'(?:src|href)\s*=\s*["\'](?!data:|#|https?:|mailto:)([^"\']+)["\']', re.I)
TAG_RE = re.compile(r"<[^>]+>")


def clean_text(s: str) -> str:
    s = TAG_RE.sub("", s or "")
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", s).strip()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def is_self_contained(html_path: Path) -> bool:
    """没有外部相对引用（图片 / 样式 / 脚本）即为自包含。"""
    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    for ref in EXTERNAL_REF_RE.findall(text):
        if ref.startswith(("http:", "https:", "//")):
            continue
        if (html_path.parent / ref).exists():
            return False
    return True


def count_slides(html_path: Path) -> int:
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    n = len(SLIDE_RE.findall(text))
    if n:
        return n
    n = len(PAGE_RE.findall(text))
    return n or 1


def deck_title(html_path: Path) -> str:
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    m = TITLE_RE.search(text)
    if m and clean_text(m.group(1)):
        return clean_text(m.group(1))
    m = H1_RE.search(text)
    if m and clean_text(m.group(1)):
        return clean_text(m.group(1))
    return html_path.stem


def guess_module(project_dir_name: str, html_name: str) -> str:
    probe = f"{project_dir_name} {html_name}".lower()
    for pattern, slug, _cn, _desc in MODULE_RULES:
        if re.search(pattern, probe):
            return slug
    return DEFAULT_MODULE


def module_meta(slug: str) -> tuple:
    for _p, s, cn, desc in MODULE_RULES:
        if s == slug:
            return cn, desc
    return "未归档", "自动归集的条目"


def slugify(title: str, fallback: str) -> str:
    """生成 URL / 文件名都安全的 slug。

    中文保留（便于人读），但把全角标点、冒号、间隔号等换成连字符，
    避免 URL 编码后出现 %EF%BC%9A 这类难处理的路径。
    """
    # 全角标点 → 半角占位
    s = re.sub(r"[：:，,。．、；;？?！!（）()【】\[\]「」『』“”\"'’‘…—－·・]", " ", title or "")
    s = re.sub(r'[\\/:*?"<>|]+', " ", s)
    s = re.sub(r"[\s_]+", "-", s.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-. ")
    if not s:
        s = fallback
    # 文件名长度上限（含多字节）
    while len(s.encode("utf-8")) > 100:
        s = s[:-1]
    return s or fallback


# 同一主题常有新旧两版（如早期版与后来的重制版）。以下规则判定「同一个主题」，
# 取文件修改时间最新的一版收录，旧版留在源工作区不重复上架。
VERSION_NOISE = re.compile(r"花叔|结构图版|新版|修订版|最终版|重制版|v\d+", re.I)


def project_stem(proj_name: str) -> str:
    """项目目录去掉版本后缀，得到主题指纹。"""
    p = VERSION_NOISE.sub("", proj_name)
    p = re.sub(r"(-huashu|-slides|-deck|-ppt|项目|课件|演示)$", "", p)
    return p.strip("-_ ").lower()


def topic_key(title: str, proj_name: str) -> str:
    """去掉版本噪音词后的标题指纹，用于同一项目内多份 deck 的判重。"""
    t = VERSION_NOISE.sub("", title)
    t = re.sub(r"[：:，,。·\-\s]+", "", t)
    t = re.sub(r"(综述|讲座|第一部分|第二部分|部分)$", "", t)
    return (t or project_stem(proj_name)).strip().lower()


# ── 采集 ────────────────────────────────────────────────────────────────
def find_candidates(sources: list) -> list:
    """在源工作区里找出所有 HTML 产出，附带所属项目目录。"""
    found = []
    for src in sources:
        if not src.exists():
            continue
        for proj in sorted(p for p in src.iterdir() if p.is_dir()):
            if proj.name in {"node_modules", ".git", "shots", "out", "dist"}:
                continue
            for html in sorted(proj.glob("*.html")):
                found.append((proj, html))
    return found


def collect(source_dirs: list, dry_run: bool = False) -> dict:
    started = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    PDFS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    prev = {}
    mpath = DATA_DIR / "modules.json"
    statepath = DATA_DIR / ".state.json"     # 本地私有状态，不进版本库
    if statepath.exists():
        try:
            state = json.loads(statepath.read_text(encoding="utf-8"))
            prev = state.get("items", {})
        except Exception:
            prev = {}
    prev_items = prev.get("items", {})

    modules = {}
    items = {}
    changes = []
    seen_topics = {}   # topic_key -> (mtime, key)

    for proj, html in find_candidates(source_dirs):
        # 只收「产出型」HTML：排除概览墙 / 目录页（index.html）
        if html.name.lower() == "index.html":
            continue
        try:
            size = html.stat().st_size
        except Exception:
            continue
        if size < 2000:
            continue

        title = deck_title(html)
        module_slug = guess_module(proj.name, html.name)
        mtime = html.stat().st_mtime

        # 同一主题只收最新一版：以「项目目录去版本后缀」为指纹跨目录判重，
        # 同一目录内的不同 deck 各自独立收录。
        tk = f"{module_slug}::{project_stem(proj.name)}"
        prev_best = seen_topics.get(tk)
        if prev_best and prev_best[0] >= mtime:
            continue
        seen_topics[tk] = (mtime, html)

        sslug = slugify(title, html.stem)
        key = f"{module_slug}/{sslug}"

        # 同一项目里若有同名 PDF，一并收
        pdf = html.with_suffix(".pdf")
        has_pdf = pdf.exists()

        # 稳定性：优先取自包含版本
        self_contained = is_self_contained(html)
        digest = file_sha(html)

        rel_html = f"pages/{module_slug}/{sslug}.html"
        rel_pdf = f"pdfs/{module_slug}/{sslug}.pdf" if has_pdf else None
        rel_thumb = f"thumbs/{module_slug}/{sslug}.jpg"

        old = prev_items.get(key)
        changed = (not old) or old.get("hash") != digest

        entry = {
            "key": key,
            "module": module_slug,
            "title": title,
            "slug": sslug,
            "source": str(html),
            "sourceProject": proj.name,
            "html": rel_html,
            "pdf": rel_pdf,
            "thumb": rel_thumb,
            "slides": count_slides(html),
            "bytes": size,
            "hash": digest,
            "selfContained": self_contained,
            "updatedAt": datetime.fromtimestamp(html.stat().st_mtime, CN_TZ).isoformat(),
            "addedAt": (old or {}).get("addedAt") or datetime.now(CN_TZ).isoformat(),
        }
        if changed and not dry_run:
            dest_html = ROOT / rel_html
            dest_html.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(html, dest_html)
            if has_pdf:
                dest_pdf = ROOT / rel_pdf
                dest_pdf.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pdf, dest_pdf)
            changes.append(("新增" if not old else "更新", title, key))

        items[key] = entry
        modules.setdefault(module_slug, []).append(key)

    # ── 编号：模块内按加入时间排，编号永不重排 ──────────────────────────
    ordered_modules = []
    for slug in MODULE_ORDER:
        if slug in modules:
            ordered_modules.append(slug)
    for slug in sorted(modules):
        if slug not in ordered_modules:
            ordered_modules.append(slug)

    out_modules = []
    for mi, slug in enumerate(ordered_modules, start=1):
        keys = modules[slug]
        # 稳定排序：先按首次加入时间，再按标题
        def sort_key(k):
            e = items[k]
            return (e.get("addedAt", ""), e.get("title", ""))
        keys.sort(key=sort_key)
        cn, desc = module_meta(slug)
        entries = []
        for ii, k in enumerate(keys, start=1):
            e = dict(items[k])
            e["no"] = f"{mi:02d}-{ii:02d}"
            e["index"] = ii
            entries.append(e)
        out_modules.append({
            "no": f"M{mi:02d}",
            "slug": slug,
            "name": cn,
            "desc": desc,
            "count": len(entries),
            "items": entries,
        })

    result = {
        "site": "AtomPages",
        "owner": "jeekeagle",
        "updatedAt": datetime.now(CN_TZ).isoformat(),
        "generatedBy": "tools/collect.py",
        "moduleCount": len(out_modules),
        "itemCount": len(items),
        "modules": out_modules,
    }

    if not dry_run:
        # 公开清单不暴露本机绝对路径：写出前剥掉 source 字段。
        pub = json.loads(json.dumps(result, ensure_ascii=False))
        for m in pub.get("modules", []):
            for it in m.get("items", []):
                it.pop("source", None)
        mpath.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")

        # 本地私有状态：保留绝对路径与加入时间，供下次采集比对
        statepath.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        # 写变更日志
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {result['updatedAt']} =====\n")
            f.write(f"模块 {len(out_modules)} 个，条目 {len(items)} 条，耗时 {time.time()-started:.1f}s\n")
            for act, t, k in changes:
                f.write(f"  [{act}] {t}  ->  {k}\n")
            if not changes:
                f.write("  无变更\n")

    return {"modules": out_modules, "items": items, "changes": changes}


def main():
    ap = argparse.ArgumentParser(description="AtomPages 采集器")
    ap.add_argument("--source", action="append", default=None, help="源工作区目录，可多次指定")
    ap.add_argument("--dry-run", action="store_true", help="只报告不落盘")
    ap.add_argument("--thumbs", action="store_true", help="同时生成缩略图")
    args = ap.parse_args()

    sources = [Path(s) for s in args.source] if args.source else DEFAULT_SOURCES
    print(f"[collect] 源工作区：{', '.join(str(s) for s in sources)}")
    res = collect(sources, dry_run=args.dry_run)
    print(f"[collect] 模块 {len(res['modules'])} 个 / 条目 {len(res['items'])} 条")
    for act, t, k in res["changes"]:
        print(f"  [{act}] {t}  ->  {k}")
    if not res["changes"]:
        print("  无变更")

    if args.thumbs and not args.dry_run:
        rc = subprocess.call([sys.executable, str(ROOT / "tools" / "make_thumbs.py")])
        sys.exit(rc)


if __name__ == "__main__":
    main()
