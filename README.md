# AtomPages

个人独立 Pages 站点：把对话中产出的幻灯片与文档，按主题自动归入模块，长期归档、持续新增。

- **线上**：<https://jeekeagle.github.io/AtomPages/>
- **仓库**：<https://github.com/jeekeagle/AtomPages>

## 它怎么工作

```
源工作区（Claw 各项目目录）
        │
        │  tools/collect.py     扫描 · 解析元信息 · 判定模块 · 去重
        ▼
   pages/<模块>/<条目>.html      收录的 HTML（自包含优先）
   pdfs/<模块>/<条目>.pdf        同名 PDF（存在才收）
   thumbs/<模块>/<条目>.jpg      Playwright 真实渲染的缩略图
   data/modules.json             模块与条目清单（站点唯一数据源）
        │
        │  git push
        ▼
   GitHub Pages
```

### 模块怎么定

模块由**主题**决定，不是手工建目录。采集器按目录名与文件名匹配规则归类：

| 模块 | 来源特征 |
| --- | --- |
| 伦理与安全 | 伦理、政策、policy |
| Atom 系统 | atom、atom-system |
| 培训交付 | training-product |
| 自媒体鸿沟 | selfmedia |
| 智能体微课 | microcourse、微课 |
| 技能化开发 | skill-engineering |
| 智能体基础 | chatbot、agent |
| 交互界面 | interface |
| 模型与应用 | model-eats |
| 认知瓶颈 | cognition |
| 视频制作 | remotion、video |

新主题只要匹配上已有规则就自动进对应模块；全新的主题落进「未归档」，之后在 `tools/collect.py` 的 `MODULE_RULES` 里加一行即可归位。

**模块可以不断增加**。模块与条目的编号（`M01`、`01-01`）按加入时间排序后固定，**新增条目只会往后排，不会打乱既有编号**。

### 去重

同一主题常有新旧两版（如早期版与后来的重制版）。采集器以「项目目录名去掉版本后缀」为指纹跨目录判重，**取文件修改时间最新的一版**收录，旧版留在源工作区不重复上架。同一目录内的不同 deck 各自独立收录。

### 幂等

内容哈希未变的条目不重写文件、不重新渲染缩略图。没有变更时不产生提交。

## 定时任务

**两道保险，同一套逻辑：**

1. **本机定时任务**（主力）——WorkBuddy automation，每三小时执行一次：
   ```
   bash "D:/M-Local/codex/AtomPages/tools/run_collection.sh"
   ```
   本机能访问 Claw 源工作区，所以它负责真正的「扫描源 → 采集 → 推送」。

2. **GitHub Actions**（副手）——`.github/workflows/collect.yml`，`cron: '0 */3 * * *'`。
   云端拿不到本机源目录，它做缩略图重渲染与一致性校验，并在本机任务缺席时兜底。

两者都走 `tools/collect.py`，产物一致。

## 本地开发

```bash
cd D:/M-Local/codex/AtomPages
python tools/collect.py          # 采集
python tools/make_thumbs.py      # 生成缩略图
python -m http.server 8777       # 本地预览 http://127.0.0.1:8777
```

采集依赖：`playwright`、`pillow`（`pip install playwright pillow && python -m playwright install chromium`）。
缩略图生成失败不影响站点可用，只是卡片没有图。

## 目录

```
index.html          首页：模块档案墙（检索 + 模块筛选）
view.html           详情页：iframe 预览 + 条目信息 + 同模块导航
assets/tokens.css   设计令牌与版式
assets/app.js       首页逻辑
assets/view.js      详情页逻辑（含画布尺寸归一）
data/modules.json   清单（公开）
data/.state.json    本地状态（含绝对路径，不入库）
tools/collect.py    采集器
tools/make_thumbs.py 缩略图生成器
tools/run_collection.sh 采集推送脚本
```

## 视觉方向

档案库（Archive Vault）：近黑底 + 琥珀信号色 + 等宽索引。方向理由与拆解规则见 `direction-approved.md`。

## 加入新作品

1. 产出的 HTML 放进 `C:/Users/wyg/WorkBuddy/Claw/<项目目录>/`，定时任务才能扫到。
2. 想让它落进正确的模块，就让项目目录名带上对应关键词（如 `*-huashu`、`*-slides`）。规则见上方模块判定表；命中不了会暂存「未归档」，补一行 `MODULE_RULES` 即可归位。
3. 同一主题的旧版本不用手动清理——采集器按项目名指纹跨目录取最新版，旧版不再上架。

## License

MIT
