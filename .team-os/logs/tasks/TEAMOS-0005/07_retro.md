# TEAMOS-0005 - 07 Retro

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 注入机制通过固定标记区块实现，兼容项目已有内容，且易于幂等更新。
- 用 schema 约束 project config，避免“任意键值”造成不可审计的配置漂移。

## 做得不好的/踩坑

- `prompt diff` 为满足项目手册命令新增，当前与 `prompt_compile.py` 存在少量实现重复（通过 import 私有函数降低分叉，但仍需注意同步）。

## 改进项 (必须写成可执行动作)

- 增加 project repo 层的“可选 ship”能力：为 `project agents inject` 提供 `--commit/--push/--pr`（若可用），并在项目任务日志中落盘证据。
- 将 `project_agents_inject.py` 的 leader-only 检查抽象为共享工具，减少重复实现。

## Team OS 自身改进建议

- 在 `workspace doctor` 中加入对项目仓库 `AGENTS.md` 标记区块的只读检查（warn 级别），用于发现遗漏但不阻塞基础诊断。
