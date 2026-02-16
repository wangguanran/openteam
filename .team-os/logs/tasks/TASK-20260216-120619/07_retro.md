# TASK-20260216-120619 - 07 Retro

- 标题：仓库概览：实现内容梳理
- 日期：2026-02-16
- 当前状态：retro

## 做得好的

- 仓库把“规范(真相源) + 脚本入口 + Runtime 模板 + CLI”放在同一个 repo，落盘结构清晰，便于复用与审计。
- Control Plane 模板提供了最小闭环 API（status/focus/tasks/requirements/panel/cluster/self-improve），便于后续扩展。

## 做得不好的/踩坑

- `new-task --full` 在 `README.md`/usage 中出现，但脚本仅支持 `--short`，导致按文档执行会报错（已在本任务中修正）。
- Runtime 模板里 OpenHands 镜像使用 `latest-*` 且默认挂载 docker socket，供应链与宿主机风险偏高（需进一步收敛默认值与闸门）。

## 改进项 (必须写成可执行动作)

- 为脚本/README 的关键参数做一个“静态一致性检查”（例如在 evals 里校验 `./scripts/teamos.sh new-task` 的 help/usage 与 README 片段同步），避免回归。
- 将 Runtime 模板中的 OpenHands 镜像 tag 固定到可审计版本，并让 docker socket 挂载默认关闭（通过 override 或显式 env gate 开启）。

## Team OS 自身改进建议

- 继续把“安全闸门”从文字约束下沉到模板默认值（例如默认不启用远程写入、不挂载 docker socket、不使用漂移 tag）。
