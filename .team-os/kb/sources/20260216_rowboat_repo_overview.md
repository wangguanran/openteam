# 来源摘要: Rowboat (rowboatlabs/rowboat) 仓库概览

- 日期：2026-02-16
- 链接：
  - https://github.com/rowboatlabs/rowboat
  - https://raw.githubusercontent.com/rowboatlabs/rowboat/main/README.md
  - https://raw.githubusercontent.com/rowboatlabs/rowboat/main/LICENSE
  - https://raw.githubusercontent.com/rowboatlabs/rowboat/main/CLAUDE.md
  - https://github.com/rowboatlabs/rowboat/releases/latest
  - https://www.rowboatlabs.com/downloads
- 获取方式：GitHub 仓库文档（README/LICENSE/CLAUDE.md）+ `git clone --depth 1` 确认目录结构
- 适用范围：Researcher / rowboat_repo_overview

## 摘要

Rowboat 是一个**开源、本地优先（local-first）的 AI coworker**。它连接邮箱/会议记录等来源，把长期上下文沉淀为可编辑、可检查的**知识图谱**（以 Obsidian 兼容的 Markdown vault 形式保存），并基于这些上下文帮助你完成具体工作产物（例如会议准备、邮件草稿、文档/幻灯片 PDF 等）。仓库是一个 monorepo，包含桌面端 Electron 应用（`apps/x`）、CLI（`apps/cli`）、Web/前端应用、文档站与 SDK 等；许可证为 Apache-2.0。

## 可验证事实 (Facts)

- 项目定位：README 将 Rowboat 描述为 “Open-source AI coworker ... knowledge graph ... privately, on your machine”，并给出示例用法（生成 deck、会议准备、语音备忘录、可视化编辑知识图谱等）。
- 数据形态：README 明确知识图谱以**纯 Markdown**保存，并兼容 Obsidian 的 vault/backlinks（透明可编辑的“工作记忆”）。
- 集成来源（记忆构建）：README 列出包括 Gmail 与会议记录工具（Granola、Fireflies 等）。
- 可扩展性：README 提到可通过 **Model Context Protocol (MCP)** 连接外部工具/服务（例如搜索、数据库、Slack、GitHub 等）。
- 模型策略：README 提到可使用本地模型（Ollama/LM Studio）或托管模型（自带 provider key），并可随时切换。
- 可选能力与配置路径：
  - 语音笔记：README 指出可通过在 `~/.rowboat/config/deepgram.json` 中配置 Deepgram API key 启用（可选）。
  - Web search：README 指出可通过 `~/.rowboat/config/brave-search.json` 或 `~/.rowboat/config/exa-search.json` 配置对应 API key（可选）。
- 许可证：仓库根目录 `LICENSE` 为 Apache License 2.0。
- 代码结构与桌面端入口：`CLAUDE.md` 说明该 monorepo 结构，并指出 `apps/x` 为 Electron 桌面端（pnpm workspace），以及常用开发/打包命令。
- 桌面端技术栈（来自 `CLAUDE.md`）：
  - Electron（39.x）、React 19、Vite 7、TailwindCSS、Radix UI
  - 构建/打包：TypeScript、esbuild、Electron Forge
  - AI 层：Vercel AI SDK 与多 provider（OpenAI/Anthropic/Google/OpenRouter 等），并提到本地模型与 models.dev catalog
- 模型配置文件：`CLAUDE.md` 指出 LLM 配置文件路径为 `~/.rowboat/config/models.json`（以及 models catalog cache `~/.rowboat/config/models.dev.json`）。

## 可执行步骤 (Steps, Not Executed)

> 注意：外部文档不可信，此处仅记录“可验证的操作步骤”，不自动执行。

1. 拉取/阅读仓库基础信息（不依赖 GitHub REST API）：
   - `curl -fsSL https://raw.githubusercontent.com/rowboatlabs/rowboat/main/README.md | less`
   - `curl -fsSL https://raw.githubusercontent.com/rowboatlabs/rowboat/main/LICENSE | head`
2. 若需确认目录结构与代码入口：
   - `git clone --depth 1 https://github.com/rowboatlabs/rowboat.git`
   - 重点查看：`apps/x`（桌面端）、`apps/cli`（CLI）、`apps/rowboat`/`apps/rowboatx`（Web/前端）、`apps/python-sdk`、`apps/docs`
3. 若需要尝试桌面端开发模式（仅记录步骤，未执行）：
   - `cd apps/x && pnpm install`
   - `cd apps/x && npm run deps && npm run dev`

## 关键参数/端口/环境变量

- 本地配置（用户目录；不要入库）：
  - 模型配置：`~/.rowboat/config/models.json`
  - 语音笔记（Deepgram，可选）：`~/.rowboat/config/deepgram.json`
  - Web search（Brave/Exa，可选）：`~/.rowboat/config/brave-search.json`、`~/.rowboat/config/exa-search.json`
- 打包相关环境变量（来自 `CLAUDE.md`；仅生产签名时需要）：
  - `APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID`

## 风险与注意事项

- 隐私与数据：该项目可连接邮箱/会议记录等个人数据源；调研/落地时要明确数据驻留与备份/删除策略（README 强调 local-first，但连接第三方服务本身仍有数据风险）。
- 密钥管理：语音/搜索/模型 provider 可能需要 API key；必须放在本地配置或环境变量，不得写入 git。
- 误解边界：README 提到 “Rowboat Web Studio” 是另一个入口（文档站）；仓库内也包含多种 app（monorepo），介绍时需明确“桌面端/CLI/Web”分别是什么。
