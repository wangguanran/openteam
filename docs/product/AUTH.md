# 认证与登录（OAuth 优先）

本 Team OS 默认使用 **Codex CLI 的 ChatGPT OAuth 登录** 作为 LLM 后端的认证方式（不要求在仓库中保存任何密钥）。

## 1) Codex CLI 登录

交互式（本机有浏览器）：

```bash
codex login
```

无头环境（推荐；会输出 device code 流程）：

```bash
codex login --device-auth
```

检查登录状态：

```bash
codex login status
```

> 说明：认证文件通常保存在 `~/.codex/`（例如 `~/.codex/auth.json`）。这些文件 **不得写入 git**。

## 2) Control Plane 对 OAuth 的要求

- Control Plane 启动后可通过 `GET /v1/auth/status` 查看其对 Codex OAuth 的可用性。
- 每次需要调用 LLM 的能力（需求提炼/语义冲突检测/对话响应）前，Control Plane 必须检查 `codex login status`。
- 未登录时必须返回明确错误，并在事件流中落盘一条 `AUTH_REQUIRED` / `LLM_BLOCKED` 事件（后续增强项）。

## 3) 可选 fallback：API Key（仅显式配置时）

如果你明确允许使用 API Key（例如在无法使用 OAuth 的环境），只能通过环境变量提供：

```bash
export OPENAI_API_KEY="***"
```

禁止将 `OPENAI_API_KEY` 写入 git 或任何仓库文件；仅允许本地 `.env`（不入库）。

