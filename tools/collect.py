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
    (r"workbench|bench|台面|工作台|workspace|medium", "atom-medium", "介质工作台", "Atom 各介质的运行工作台与确认台面"),
    (r"atom-system|atom|规范|一稿多线|多线", "atom-system", "Atom 系统", "Atom 规范、运行系统与一稿多线"),
    (r"remotion|video|视频", "video", "视频制作", "口播视频与动效"),
]

DEFAULT_MODULE = "misc"

# 模块显示顺序（未列出的排后面）
MODULE_ORDER = [
    "ai-ethics-policy", "atom-system", "atom-medium", "training-product", "selfmedia-gap",
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


def has_slide_class(html_path: Path) -> bool:
    """是否含 .slide / .page 幻灯片结构——用来区分幻灯片与普通文档。"""
    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return bool(SLIDE_RE.search(text) or PAGE_RE.search(text))


def deck_title(html_path: Path) -> str:
    text = html_path.read_text(encoding="utf-8", errors="ignore")
    m = TITLE_RE.search(text)
    if m and clean_text(m.group(1)):
        return clean_text(m.group(1))
    m = H1_RE.search(text)
    if m and clean_text(m.group(1)):
        return clean_text(m.group(1))
    return html_path.stem


def guess_module(project_dir_name: str, html_name: str, section: str = "") -> str:
    """判定模块归属。

    section 是 HTML 相对项目根的子目录路径（如 "workspace/structured-script/medium"）。
    Atom 系统的 docs/ 与 workspace/ 是两码事，必须靠 section 区分，
    否则整包都会落进同一个模块。
    """
    probe = f"{project_dir_name} {html_name} {section}".lower()
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
    """去掉版本噪音词后的标题指纹，用于同一项目内多份 deck 的判重。

    同一主题的新旧两版标题常被改写（"交付的不再是理念，而是产品" vs
    "一次培训，直接交付一个产品"），标题对不上时退回项目名指纹，
    保证跨项目的同主题仍然只留一版。
    """
    t = VERSION_NOISE.sub("", title)
    t = re.sub(r"[：:，,。·\-\s]+", "", t)
    t = re.sub(r"(综述|讲座|第一部分|第二部分|部分)$", "", t)
    t = t.strip().lower()
    if not t:
        return project_stem(proj_name)
    # 标题与项目名没有共同词，说明不是同一主题的改写版，各自独立
    stem = project_stem(proj_name)
    head = stem.split("-")[0]
    if head and head not in t:
        return f"{stem}::{t}"
    return t


# ── 采集 ────────────────────────────────────────────────────────────────
# 不收录的目录（依赖、构建产物、缓存）
SKIP_DIRS = {"node_modules", ".git", "shots", "out", "dist", "__pycache__",
             ".venv", "venv", "assets", "templates", "logs"}

# deck 的分页源文件目录：里面是单页碎片，项目根目录已有合并后的单文件版，
# 全收会重复上架。目录名约定为 slides / pages。
FRAGMENT_DIRS = {"slides", "pages"}

# 模板占位文件：标题里带 {{TITLE}} / 〈标题〉 这类未填占位符，不是可展示的作品。
# 只看标题——正文里提到「占位符」三个字的正式文档（如规格书的目录树）不该被误杀。
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}|〈[^〉]{0,20}〉|待填写|占位", re.I)


def is_template(html_path: Path) -> bool:
    """标题含未填占位符的模板文件不收录。"""
    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    m = TITLE_RE.search(text)
    title = clean_text(m.group(1)) if m else ""
    if title and PLACEHOLDER_RE.search(title):
        return True
    # 没有标题标签时退回全量判断
    if not title:
        return bool(PLACEHOLDER_RE.search(text[:4000]))
    return False


def find_candidates(sources: list) -> list:
    """在源工作区里找出所有 HTML 产出，附带所属项目目录。

    递归扫描：项目内部还有子目录（Atom 系统的 docs/ 与 workspace/ 就是这样），
    不递归的话整包交付文档会全部漏收。
    deck 的分页源目录（slides/、pages/）不扫——项目根目录已有合并后的单文件版。
    """
    found = []
    for src in sources:
        if not src.exists():
            continue
        for proj in sorted(p for p in src.iterdir() if p.is_dir()):
            if proj.name in SKIP_DIRS:
                continue
            for html in sorted(proj.rglob("*.html")):
                if any(part in SKIP_DIRS for part in html.parts):
                    continue
                # 相对于项目根的分页碎片目录
                rel_parts = html.parent.relative_to(proj).parts
                if any(part in FRAGMENT_DIRS for part in rel_parts):
                    continue
                found.append((proj, html))
    return found


def find_pdf_for(html_path: Path) -> Path | None:
    """给 HTML 找配套 PDF。

    多数产出里 HTML 与 PDF 不同名（如「认知瓶颈-幻灯片.html」对「认知瓶颈.pdf」），
    只按同名匹配会丢掉一大半 PDF。这里按三条规则依次尝试：
      1. 同名同目录；
      2. 同目录内「去掉幻灯片/讲稿/版本后缀」后同名的 PDF；
      3. 同目录内唯一的 PDF。
    """
    same = html_path.with_suffix(".pdf")
    if same.exists():
        return same

    siblings = sorted(html_path.parent.glob("*.pdf"))
    if not siblings:
        return None

    # 规则 2：标题主干相同
    stem = html_path.stem
    for suf in ("-幻灯片", "-讲稿幻灯片", "-讲稿", "幻灯片", "讲稿"):
        if stem.endswith(suf) and stem != suf:
            target = stem[: -len(suf)].strip("-_ ")
            for pdf in siblings:
                if pdf.stem == target:
                    return pdf
            break

    # 规则 3：目录内唯一 PDF
    if len(siblings) == 1:
        return siblings[0]
    return None


# 指向本机绝对路径的链接（file:///C:/...）上线后必然断链，
# 采集时改写成站点内可用的相对路径。
FILE_URL_RE = re.compile(r'href\s*=\s*["\']file:///(?:/)?[A-Za-z]:[\\/][^"\']+["\']', re.I)


def rewrite_local_links(html_path: Path, dest_dir: Path, dest_name: str) -> bool:
    """把 HTML 里的 file:/// 本机绝对路径链接改写成指向站点内文件的相对路径。

    目标文件此刻可能还没归档进站点，所以链接指向的 HTML 用标题去站点里找，
    找不到再退回文件名匹配。返回 True 表示内容被改写过。
    """
    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    # 站点内已有的 HTML，按文件名与标题两条线索索引
    by_name = {}
    by_title = {}
    if PAGES_DIR.exists():
        for f in PAGES_DIR.rglob("*.html"):
            by_name.setdefault(f.stem, f)
            try:
                t = deck_title(f)
            except Exception:
                t = ""
            if t:
                by_title.setdefault(t, f)

    def repl(m: re.Match) -> str:
        raw = m.group(0)
        inner = re.search(r'file:///(?:/)?([A-Za-z]:[\\/][^"\']+)', raw, re.I)
        if not inner:
            return raw
        win = inner.group(1).replace("\\", "/")
        stem = Path(win).stem
        # 先按文件名找，再按标题找
        target = by_name.get(stem)
        if not target:
            try:
                src_text = Path(win).read_text(encoding="utf-8", errors="ignore")
                t = deck_title(Path(win)) if src_text else ""
            except Exception:
                t = ""
            if t:
                target = by_title.get(t)
        if not target:
            return raw
        # 相对当前文件所在目录的相对路径
        try:
            rel = target.relative_to(dest_dir).as_posix()
        except ValueError:
            rel = os.path.relpath(target, dest_dir).replace("\\", "/")
        quote = '"' if '"' in raw else "'"
        return f'href={quote}{rel}{quote}'

    new_text = FILE_URL_RE.sub(repl, text)
    if new_text == text:
        return False
    (dest_dir / dest_name).write_text(new_text, encoding="utf-8")
    return True


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
            # 状态文件结构是 {"items": {key: entry}}，取一次即可。
            # 早先这里多取了一层 items，导致 prev_items 恒为空、幂等失效、
            # 每次采集都把全部条目当成新增。
            prev = state.get("items", {}) or {}
        except Exception:
            prev = {}
    prev_items = prev if isinstance(prev, dict) else {}

    modules = {}
    items = {}
    changes = []
    # 两级判重：
    #   seen_projects —— 跨项目同主题（training-product-slides vs training-product-huashu），
    #                    以「项目名去版本后缀」为指纹，同一主题只留最新一版；
    #   seen_sections —— 同一项目内不同小节并存的多份文档（Atom 的 docs/ 下四份），
    #                    以「子目录 + 主题」为指纹，各自独立收录。
    seen_projects = {}
    seen_sections = {}

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
        # 含占位符的模板不是作品
        if is_template(html):
            continue

        title = deck_title(html)
        # 子目录（如 Atom 系统的 docs/ 与 workspace/）算不同小节，各自独立收录，
        # 也参与模块判定——docs 是规范文档，workspace 是介质工作台。
        rel_sub = html.parent.relative_to(proj).as_posix()
        sub_key = "" if rel_sub == "." else rel_sub
        module_slug = guess_module(proj.name, html.name, sub_key)
        mtime = html.stat().st_mtime

        proj_stem = project_stem(proj.name)

        # 同一项目 + 同一小节内：不同主题各自独立收录（docs/ 下四份文档同属此列）
        sk = f"{module_slug}::{proj_stem}::{sub_key}::{topic_key(title, proj.name)}"
        prev_sec = seen_sections.get(sk)
        if prev_sec and prev_sec[0] >= mtime:
            continue
        seen_sections[sk] = (mtime, html)

        # 跨项目同主题：只留最新一版（老版本目录不再重复上架）。
        # 同一主题的新旧两版几乎都在同名项目目录里（training-product-slides 与
        # training-product-huashu），所以跨项目一律按项目指纹判重，取 mtime 新的。
        # 注意这与上面的「同小节」规则不是一回事：见下。
        if sub_key:
            # 项目内有子目录结构（Atom 系统）：每份文档各自独立，不参与跨项目判重
            pk = f"{module_slug}::{proj_stem}::{sub_key}::{topic_key(title, proj.name)}"
        else:
            pk = f"{module_slug}::{proj_stem}"
        prev_proj = seen_projects.get(pk)
        if prev_proj and prev_proj[0] >= mtime:
            continue
        seen_projects[pk] = (mtime, html)

        sslug = slugify(title, html.stem)
        key = f"{module_slug}/{sslug}"

        # 配套 PDF：同名优先，其次按标题主干匹配
        pdf = find_pdf_for(html)
        has_pdf = pdf is not None

        # 稳定性：优先取自包含版本
        self_contained = is_self_contained(html)
        digest = file_sha(html)
        slides = count_slides(html)
        # 幻灯片还是文档：文档没有 .slide 结构，用它决定前端显示「页」还是「文档」
        kind = "deck" if slides > 1 or has_slide_class(html) else "doc"

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
            "sourceSection": sub_key,
            "html": rel_html,
            "pdf": rel_pdf,
            "thumb": rel_thumb,
            "slides": slides,
            "kind": kind,
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

    # ── 绝对路径链接改写 ────────────────────────────────────────────────
    # 必须等所有文件都落位后再做：台面页里的链接指向别的产物，
    # 目标可能排在后面才被收录，边收边改会漏。
    # 每次都重写一遍，保证链接目标变化时也能跟着更新。
    if not dry_run:
        for e in items.values():
            src = Path(e["source"])
            dest = ROOT / e["html"]
            if not (src.exists() and dest.exists()):
                continue
            try:
                before = dest.read_bytes()
                shutil.copy2(src, dest)
                if rewrite_local_links(src, dest.parent, dest.name):
                    after = dest.read_bytes()
                    # 只有结果真的变了才算变更，避免每次采集都产生提交
                    if after != before:
                        changes.append(("改写链接", e["title"], e["key"]))
            except Exception:
                pass

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
        # 清理孤儿：条目被改名、换模块或被移除后，旧的文件还留在 pages/ pdfs/ thumbs/ 里，
        # 会变成 404 的死链。清单里没有的文件一律删掉。
        keep = set()
        for e in items.values():
            keep.add(e["html"])
            keep.add(e["thumb"])
            if e.get("pdf"):
                keep.add(e["pdf"])
        for base in (PAGES_DIR, PDFS_DIR, THUMBS_DIR):
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(ROOT).as_posix()
                if rel not in keep:
                    f.unlink()
                    changes.append(("清理", rel, rel))

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
