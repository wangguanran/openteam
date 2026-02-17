# TEAMOS-0016 - 04 Test

- 标题：TEAMOS-CONCURRENCY-LOCKS
- 日期：2026-02-17
- 当前状态：test

## 测试范围

- 并发 req add：两个进程同时写 requirements，不应破坏 YAML
- prompt build vs scope lock：prompt compile 需等待同 scope lock
- stale lock recovery：TTL 过期可接管
- LOCK_BUSY：返回 holder 诊断信息

## 执行记录

```bash
python3 -m unittest -q
# OK (33 tests)
```

## 证据

- 日志/截图/报告路径：
  - evals: `evals/test_concurrency_locks.py`
