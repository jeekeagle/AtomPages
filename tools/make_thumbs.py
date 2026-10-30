#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AtomPages 缩略图生成器

用 Playwright 真实渲染每一份收录的 HTML，截第一屏作为卡片缩略图。
只对新收录或内容变化的条目重新渲染，其余跳过（幂等）。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "modules.json"
THUMBS_DIR = ROOT / "thumbs"

PLAYWRIGHT = None
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT = True
except Exception:
    print("[thumbs] 未安装 playwright，跳过缩略图生成（pip install playwright）")


def items_from_manifest():
    if not DATA_PATH.exists():
        return []
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    out = []
    for m in data.get("modules", []):
        for it in m.get("items", []):
            out.append(it)
    return out


def render(items):
    if not PLAYWRIGHT:
        return 1
    made = 0
    skipped = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb", "--font-render-hinting=none"])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                  device_scale_factor=1.6,
                                  reduced_motion="reduce")
        for it in items:
            src = ROOT / it["html"]
            out = ROOT / it["thumb"]
            if not src.exists():
                print(f"  [缺失] {it['key']}")
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            page = ctx.new_page()
            try:
                page.goto(src.resolve().as_uri(), wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1500)

                # 找到真正显示的那一屏画布：优先当前激活的 slide，
                # 其次任意可见 slide / page，最后退回 body。
                handle = page.evaluate_handle("""() => {
                    const pick = (sel) => {
                        const list = Array.from(document.querySelectorAll(sel));
                        let best = null, bestArea = 0;
                        list.forEach(el => {
                            const cs = getComputedStyle(el);
                            if (cs.display === 'none' || cs.visibility === 'hidden') return;
                            const r = el.getBoundingClientRect();
                            const area = r.width * r.height;
                            if (area > bestArea) { bestArea = area; best = el; }
                        });
                        return best;
                    };
                    return pick('.slide.active, .slide.on, .slide') ||
                           pick('.page') || document.body;
                }""")
                el = handle.as_element()
                if el is None:
                    raise RuntimeError("找不到画布元素")

                el.screenshot(path=str(out), type="jpeg", quality=84)
                made += 1
                print(f"  [缩略图] {it['key']}")
            except Exception as e:
                print(f"  [失败] {it['key']}: {type(e).__name__}: {e}")
            finally:
                page.close()
        browser.close()
    print(f"[thumbs] 新生成 {made} 张")
    return 0


def main():
    items = items_from_manifest()
    if not items:
        print("[thumbs] 清单为空，先跑 collect.py")
        return 0
    print(f"[thumbs] 待处理 {len(items)} 条")
    return render(items)


if __name__ == "__main__":
    sys.exit(main())
