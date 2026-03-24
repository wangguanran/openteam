# 加新节点（SSH 推送部署 + 一键加入脚本）

本手册描述如何把一台新机器加入 OpenTeam 集群。

原则：

- 密码/密钥不落盘：不得写入仓库、日志、命令行参数
- 优先 SSH key；若必须使用密码，仅允许 stdin/交互输入
- 默认 dry-run：只打印计划，不做远程安装/写 systemd/启动服务
- 远程安装依赖/写 systemd/启动服务属于高风险动作，必须审批后执行

## 方式 A：Brain 通过 SSH 推送部署（`openteam node add`）

预期命令（待实现）：

```bash
openteam node add --host 10.0.0.8 --user ubuntu --ssh-key ~/.ssh/id_ed25519 \\
  --role assistant \\
  --capabilities "repo_rw,docker" \\
  --tags "site:bj,device:no"
```

如果只能密码登录（不推荐），必须使用 stdin 或交互式输入，不得出现在参数里：

```bash
printf '%s' "$SSH_PASSWORD" | openteam node add --host 10.0.0.8 --user ubuntu --password-stdin
```

### 远程部署内容（最小闭环）

1. 创建目录（可配置）：`/opt/openteam-node`
2. 拉取 runtime 代码（从本仓库或 release tarball）
3. 写入配置：`/opt/openteam-node/config.yaml`
4. 注册 systemd：服务名 `openteam-node`（推荐）
5. 启动并验证：能在 `CLUSTER-NODES` 更新心跳（远程写需 env gate + 认证）

### 认证要求

- GitHub：建议在远程节点先执行 `gh auth login`（交互式）
- Codex/ChatGPT OAuth（若节点需要模型能力）：
  - `codex login --device-auth`（headless）
  - 远程脚本必须把 device code 打印出来（不落盘），提示操作者完成授权

## 方式 B：新服务器一键加入（`join_node.sh`）

预期：在新服务器上执行一条命令加入集群（幂等、可重复执行）。

示例（命令由 `openteam node join-script` 输出，不包含任何 secrets）：

```bash
curl -fsSL https://example.invalid/openteam/join_node.sh | bash -s -- \\
  --cluster-repo openteam-dev/openteam \\
  --brain-base-url http://10.0.0.1:8787 \\
  --role assistant \\
  --capabilities "repo_rw,docker" \\
  --tags "site:bj,device:no"
```

注意：

- 该脚本不负责自动登录 GitHub/Codex；只会提示你手动完成 `gh auth login` / `codex login --device-auth`
- 该脚本默认不执行系统级安装；若依赖缺失，会输出建议安装命令并停止

## 常见问题

### 1) 远程机器缺少 docker / compose / python3

属于高风险安装动作，必须审批后执行。建议先走 dry-run，输出计划后再决定是否安装。

### 2) SSH 密码如何输入才安全？

- 不要写在命令行参数里（会进入 shell history / `ps`）
- 使用交互式输入或 `--password-stdin`


## Brain Hub Config Push

After node bootstrap, Brain can push hub DB/Redis config directly:

```bash
openteam node add --host <ip> --user <user> --cluster-repo <owner/repo> --execute --push-hub-config --ssh-key ~/.ssh/id_ed25519
```

Password mode:

```bash
printf '%s' "$SSH_PASSWORD" | openteam node add --host <ip> --user <user> --cluster-repo <owner/repo> --execute --password-stdin --push-hub-config
```
