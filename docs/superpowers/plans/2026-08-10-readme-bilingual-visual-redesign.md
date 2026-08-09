# agent-sync 双语 README 与 GitHub 主页改版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以用户确认的 Network Topology B+ 视觉方向重构 `agent-sync` GitHub 主页，使中英文访客能快速理解价值、完成安装，并核验安全与所有权边界。

**Architecture:** `README.md` 保持默认英文入口，`README.zh-CN.md` 提供结构对等的完整中文版本；两者共享本地双主题 SVG 横幅和同一组命令契约。视觉资产、双语互链和事实边界由标准库 `unittest` 守护，GitHub Description 与 Topics 在代码推送成功后单独更新。

**Tech Stack:** GitHub Flavored Markdown、SVG 1.1、Python 3 标准库 `unittest` / `xml.etree.ElementTree`、Bash、GitHub CLI。

## Global Constraints

- 默认 GitHub 主页必须为英文 `README.md`，顶部提供 `English | 简体中文` 双向切换。
- 中文 `README.zh-CN.md` 必须是完整版本，不是摘要。
- 采用 Network Topology B+：深海军蓝、青色、靛紫、中央 Private Hub、两侧 Agent 节点、轻微线路微光；不使用三角形装饰。
- 横幅必须同时提供浅色和深色本地 SVG，不使用脚本、远程字体、动态图床或追踪资源。
- 不制造下载量、用户量、基准性能或“最好”声明；只展示仓库中可以验证的能力。
- 不修改同步器生产逻辑、Agent Hub 私有配置、凭据、插件状态和 `VERSION`。
- `.superpowers/` 视觉草稿不得进入公开仓库。
- 两份 README 的正常阅读路径均为：价值 → 差异 → 60 秒开始 → 支持范围 → 工作原理 → 安全模型 → 常用命令 → 高级能力。

---

### Task 1: 建立双主题横幅和安全资产契约

**Files:**
- Modify: `.gitignore`
- Create: `tests/test_readme.py`
- Create: `assets/readme/agent-sync-hero-light.svg`
- Create: `assets/readme/agent-sync-hero-dark.svg`

**Interfaces:**
- Consumes: 已批准设计 `docs/superpowers/specs/2026-08-09-readme-bilingual-visual-redesign.md`
- Produces: `HERO_ASSETS: tuple[Path, Path]` 测试常量，以及两份可由 `<picture>` 直接引用的安全 SVG

- [ ] **Step 1: 在 `.gitignore` 中加入本地视觉草稿目录**

在现有 `.gitignore` 末尾加入：

```gitignore
.superpowers/
```

- [ ] **Step 2: 写横幅资产失败测试**

创建 `tests/test_readme.py`：

```python
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HERO_ASSETS = (
    REPO / "assets/readme/agent-sync-hero-light.svg",
    REPO / "assets/readme/agent-sync-hero-dark.svg",
)


class ReadmeContractTests(unittest.TestCase):
    def test_hero_assets_are_local_safe_svg(self):
        for asset in HERO_ASSETS:
            with self.subTest(asset=asset.name):
                root = ET.parse(asset).getroot()
                text = asset.read_text(encoding="utf-8")
                lowered = text.lower()
                self.assertTrue(root.tag.endswith("svg"))
                self.assertNotIn("<script", lowered)
                self.assertNotIn('href="http', lowered)
                self.assertNotIn("href='http", lowered)
                self.assertNotIn("/users/", lowered)
                self.assertNotIn("slavatang", lowered)
                self.assertIn("agent-sync", text)
                self.assertIn("Private Hub", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行聚焦测试并确认因资产缺失而失败**

Run:

```bash
python3 -m unittest -v tests.test_readme.ReadmeContractTests.test_hero_assets_are_local_safe_svg
```

Expected: `ERROR`，错误为 `agent-sync-hero-light.svg` 或 `agent-sync-hero-dark.svg` 不存在。

- [ ] **Step 4: 创建 Network Topology B+ 双主题 SVG**

两份 SVG 使用相同的 `viewBox="0 0 1440 520"` 和相同节点布局：

- 左侧节点：`Codex`、`Cursor`、`Claude`
- 中央节点：`Private Hub`，副标题 `Skills · MCP · Rules · Ownership`
- 右侧节点：`Gemini`、`Antigravity`、`OpenCode`
- 顶部主标题：`agent-sync`
- 英文价值主张：`Sync once. Use every Agent with confidence.`
- 底部终端状态：`20 Skills · 10 MCP · one owner per capability`

浅色版使用 `#F8FBFF` 背景、`#0F172A` 主文字、`#0891B2` 青色线路、`#4F46E5` 靛紫节点；深色版使用 `#06111F` 背景、`#E6F7FF` 主文字、`#22D3EE` 青色线路、`#818CF8` 靛紫节点。只使用 SVG 原生的 `rect`、`path`、`circle`、`text`、`linearGradient` 和有限 `filter` 微光；不得加入 `<script>`、`<image>`、远程 `href` 或外部 CSS。

- [ ] **Step 5: 验证 SVG 契约和 XML 语法**

Run:

```bash
python3 -m unittest -v tests.test_readme.ReadmeContractTests.test_hero_assets_are_local_safe_svg
xmllint --noout assets/readme/agent-sync-hero-light.svg assets/readme/agent-sync-hero-dark.svg
```

Expected: 测试 `OK`，`xmllint` 无输出且退出码为 0。

- [ ] **Step 6: 提交资产契约**

```bash
git add .gitignore tests/test_readme.py assets/readme/agent-sync-hero-light.svg assets/readme/agent-sync-hero-dark.svg
git commit -m "docs: add bilingual README hero assets"
```

---

### Task 2: 重写英文默认主页

**Files:**
- Modify: `tests/test_readme.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `assets/readme/agent-sync-hero-light.svg`、`assets/readme/agent-sync-hero-dark.svg`
- Produces: GitHub 默认英文入口，以及中文 README 必须镜像的命令与章节合同

- [ ] **Step 1: 添加英文 README 失败测试**

在 `ReadmeContractTests` 中加入：

```python
    def test_english_readme_has_product_entry_contract(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        for required in (
            "[简体中文](README.zh-CN.md)",
            "assets/readme/agent-sync-hero-light.svg",
            "assets/readme/agent-sync-hero-dark.svg",
            "## Why agent-sync",
            "## What makes it different",
            "## 60-second quick start",
            "## Supported tools",
            "## How it works",
            "## Safety by design",
            "## Command reference",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

        self.assertLessEqual(len(text.splitlines()), 420)
        self.assertNotIn("best MCP", text.lower())
        self.assertNotIn("thousands of users", text.lower())
```

- [ ] **Step 2: 运行英文契约并确认失败**

```bash
python3 -m unittest -v tests.test_readme.ReadmeContractTests.test_english_readme_has_product_entry_contract
```

Expected: `FAIL`，至少缺少中文互链、双主题 Hero 或新章节。

- [ ] **Step 3: 用 B+ 首屏替换当前 README 开头**

`README.md` 必须以以下结构开始：

```markdown
<p align="right">
  English · <a href="README.zh-CN.md">简体中文</a>
</p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/readme/agent-sync-hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="assets/readme/agent-sync-hero-light.svg">
  <img alt="agent-sync connects one private Hub to multiple AI agents" src="assets/readme/agent-sync-hero-light.svg">
</picture>

<p align="center">
  <strong>One private Hub for Skills, MCP servers, rules, and capability ownership across your AI agents.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square"></a>
  <img alt="Version 1.12.0" src="https://img.shields.io/badge/version-1.12.0-6366f1?style=flat-square">
  <img alt="Python and Shell" src="https://img.shields.io/badge/runtime-Python%20%2B%20Shell-0891b2?style=flat-square">
  <img alt="Local-first" src="https://img.shields.io/badge/design-local--first-0f766e?style=flat-square">
</p>

<p align="center">
  <a href="#why-agent-sync">Why</a> ·
  <a href="#60-second-quick-start">Quick start</a> ·
  <a href="#supported-tools">Supported tools</a> ·
  <a href="#safety-by-design">Safety</a> ·
  <a href="#command-reference">Commands</a>
</p>
```

- [ ] **Step 4: 重写英文主叙事**

按以下事实顺序写正文，并将总长度控制在 420 行以内：

1. `Why agent-sync`：每个 Agent 的路径和格式不同；同一插件与 Hub MCP 容易重复；共享配置与本地凭据必须分层。
2. `What makes it different`：Skills + MCP + Rules 一起同步；每个目标的每项能力只有一个所有者；secret-preserving merge；`sources` / `sync --from` 显式提升；`doctor` / `verify` / `fix` 可审计。
3. `60-second quick start` 使用以下真实命令，不加入未经实现的安装器：

```bash
git clone https://github.com/LittlePeter52012/agent-sync.git ~/.local/share/agent-sync
ln -sf ~/.local/share/agent-sync/bin/agent-sync ~/.local/bin/agent-sync
agent-sync init
agent-sync sources
agent-sync sync --from cursor --dry-run
agent-sync sync
agent-sync doctor --runtime
```

4. `Supported tools` 表格覆盖 Cursor、Gemini CLI/global、Antigravity App/CLI/IDE、Claude Code、Codex、OpenCode、VS Code/Copilot、Hermes 和 Multica；明确 Hermes/Multica 的范围限制。
5. `How it works` 保留一个简化 Mermaid 图：`Local Agent source → explicit preview → Private Hub → per-target merge → verify/doctor`。
6. `Safety by design` 明确公共工具仓库/私有 Hub、产品插件所有权、本地执行与云端数据去向的区别、同一 mutation 只用一个接口。
7. `Command reference` 使用紧凑表格覆盖现有 CLI；Antigravity、Multica、远端部署、依赖维护放入 `<details>`，保留现有正确边界但删除重复说明。

- [ ] **Step 5: 运行英文 README 契约与隐私审计**

```bash
python3 -m unittest -v tests.test_readme.ReadmeContractTests.test_english_readme_has_product_entry_contract
agent-sync audit
```

Expected: 两条命令均退出 0。

- [ ] **Step 6: 提交英文主页**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: redesign agent-sync homepage"
```

---

### Task 3: 创建结构对等的完整中文 README

**Files:**
- Modify: `tests/test_readme.py`
- Create: `README.zh-CN.md`

**Interfaces:**
- Consumes: Task 2 的英文章节、命令和本地 Hero 资源
- Produces: 完整中文入口，与英文 README 共享相同可执行命令

- [ ] **Step 1: 添加中英文互链与命令一致性失败测试**

在 `ReadmeContractTests` 中加入：

```python
    def test_chinese_readme_is_complete_and_reciprocal(self):
        english = (REPO / "README.md").read_text(encoding="utf-8")
        chinese = (REPO / "README.zh-CN.md").read_text(encoding="utf-8")

        for required in (
            "[English](README.md)",
            "assets/readme/agent-sync-hero-light.svg",
            "assets/readme/agent-sync-hero-dark.svg",
            "## 为什么选择 agent-sync",
            "## 与普通同步脚本有什么不同",
            "## 60 秒快速开始",
            "## 支持的工具",
            "## 工作原理",
            "## 安全设计",
            "## 命令参考",
        ):
            with self.subTest(required=required):
                self.assertIn(required, chinese)

        commands = (
            "agent-sync sources",
            "agent-sync sync --from cursor --dry-run",
            "agent-sync sync",
            "agent-sync doctor --runtime",
            "agent-sync ownership",
            "agent-sync verify",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, english)
                self.assertIn(command, chinese)

        self.assertLessEqual(len(chinese.splitlines()), 440)
```

- [ ] **Step 2: 运行中文契约并确认因文件缺失而失败**

```bash
python3 -m unittest -v tests.test_readme.ReadmeContractTests.test_chinese_readme_is_complete_and_reciprocal
```

Expected: `ERROR`，`README.zh-CN.md` 不存在。

- [ ] **Step 3: 创建完整中文 README**

复制英文 README 的结构、横幅、徽章、表格、Mermaid 和命令代码块，并按以下对应翻译标题：

| English | 简体中文 |
|---|---|
| Why agent-sync | 为什么选择 agent-sync |
| What makes it different | 与普通同步脚本有什么不同 |
| 60-second quick start | 60 秒快速开始 |
| Supported tools | 支持的工具 |
| How it works | 工作原理 |
| Safety by design | 安全设计 |
| Common workflows | 常用工作流 |
| Command reference | 命令参考 |
| Advanced | 高级能力 |

顶部使用：

```html
<p align="right">
  <a href="README.md">English</a> · 简体中文
</p>
```

保留产品名、CLI、文件路径、配置键、状态值及协议术语的英文拼写；中文说明不得引入英文版没有的能力承诺。

- [ ] **Step 4: 运行完整 README 契约**

```bash
python3 -m unittest -v tests.test_readme
```

Expected: 所有 README 测试 `OK`。

- [ ] **Step 5: 提交中文主页**

```bash
git add README.zh-CN.md tests/test_readme.py
git commit -m "docs: add complete Chinese README"
```

---

### Task 4: 视觉 QA、完整测试与内容审查

**Files:**
- Modify only if QA finds a concrete defect: `README.md`, `README.zh-CN.md`, `assets/readme/*.svg`, `tests/test_readme.py`

**Interfaces:**
- Consumes: 两份 README、双主题 Hero 和测试合同
- Produces: 可安全推送的已验证提交序列

- [ ] **Step 1: 检查链接、XML、私有路径和工作树范围**

```bash
python3 -m unittest -v tests.test_readme
xmllint --noout assets/readme/agent-sync-hero-light.svg assets/readme/agent-sync-hero-dark.svg
agent-sync audit
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: 全部退出 0；状态中不得出现 `.superpowers/`、密钥、私有 Hub 文件或无关改动。

- [ ] **Step 2: 渲染并检查两份 SVG**

```bash
rm -rf /tmp/agent-sync-readme-preview
mkdir -p /tmp/agent-sync-readme-preview
qlmanage -t -s 1440 -o /tmp/agent-sync-readme-preview assets/readme/agent-sync-hero-light.svg assets/readme/agent-sync-hero-dark.svg
```

使用 `view_image` 检查生成的 PNG：标题不截断、线路不遮挡节点、浅色/深色对比足够、窄尺寸缩放后核心文字仍可辨认。发现具体问题时只修改 SVG 并重新执行 Task 1 与本步骤验证。

- [ ] **Step 3: 运行完整项目测试门**

```bash
agent-sync test
```

Expected: 363 项以上 Python 测试与 45 项 Bash 集成检查全部通过；新增 README 测试计入 Python 总数。

- [ ] **Step 4: 只读代码审查**

审查范围为 `origin/main...HEAD`，要求核对：中英文事实一致、没有夸张声明、命令真实存在、SVG 无远程资源、移动端首屏不会依赖固定宽度、公共/私有边界不倒退。Critical 与 Important 必须在推送前修复并重跑 Task 4。

- [ ] **Step 5: 若 QA 产生修正则提交**

```bash
git add README.md README.zh-CN.md assets/readme tests/test_readme.py .gitignore
git diff --cached --quiet || git commit -m "docs: polish README rendering"
```

---

### Task 5: 推送并更新 GitHub 主页元数据

**Files:**
- No repository file changes expected

**Interfaces:**
- Consumes: 通过 Task 4 的本地 `main`
- Produces: GitHub 默认分支上的双语主页、Description 和 Topics

- [ ] **Step 1: 推送已验证的 `main`**

```bash
git push origin main
git fetch origin main --quiet
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
```

Expected: 推送成功，本地 `HEAD` 与 `origin/main` 完全一致。

- [ ] **Step 2: 更新 GitHub Description 与 Topics**

```bash
gh repo edit LittlePeter52012/agent-sync \
  --description "Sync Agent Skills, MCP servers, rules, and capability ownership across Codex, Cursor, Claude, Gemini, Antigravity, OpenCode, Copilot, and Hermes." \
  --add-topic agent-skills \
  --add-topic mcp \
  --add-topic ai-agents \
  --add-topic configuration-management \
  --add-topic codex \
  --add-topic cursor \
  --add-topic claude-code \
  --add-topic gemini-cli \
  --add-topic antigravity \
  --add-topic opencode
```

- [ ] **Step 3: 核验远端主页状态**

```bash
gh repo view LittlePeter52012/agent-sync \
  --json description,repositoryTopics,url,defaultBranchRef \
  --jq '{description, topics: [.repositoryTopics[].name], url, branch: .defaultBranchRef.name}'
gh repo read-file README.md -R LittlePeter52012/agent-sync --ref main | sed -n '1,60p'
gh repo read-file README.zh-CN.md -R LittlePeter52012/agent-sync --ref main | sed -n '1,60p'
```

Expected: Description 精确匹配；10 个 Topics 均存在；默认分支为 `main`；两份 README 首屏均含语言切换和本地双主题 Hero。

- [ ] **Step 4: 最终工作树检查**

```bash
git status --short --branch
```

Expected: `## main...origin/main`，无未提交文件。
