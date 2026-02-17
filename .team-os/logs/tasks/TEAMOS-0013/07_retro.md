# TEAMOS-0013 - 07 Retro

- 标题：TEAMOS-VERIFY-0001
- 日期：2026-02-17
- 当前状态：retro

## 做得好的

- 审计与验证均走确定性脚本链路，产物落盘可复现。
- 在不执行真实高风险动作的前提下验证 approvals->DB 记录闭环。

## 做得不好的/踩坑

- 运行态 control-plane（team-os-runtime）与 repo 内 template/pipeline 存在版本差异时，requirements verify/rebuild 可能出现“control-plane PASS 但 pipeline FAIL”的假阳性。

## 改进项 (必须写成可执行动作)

- 增加 `runtime upgrade`/`runtime-init --force` 的审批分类与安全回滚说明（避免误覆盖 runtime 文件）。
- 增加 eval：验证 requirements verify 必须与 rebuild 后一致，避免 drift 复发。
- 增加 CLI 选项：`teamos req verify --local`（直接走 pipeline/template）用于排障与一致性对齐。

## Team OS 自身改进建议

- 将 runtime 目录的版本与 repo commit 绑定（写入 runtime metadata），便于 doctor 直接检测“运行态落后于 template”。
