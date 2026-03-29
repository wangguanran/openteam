#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


THRESHOLDS = {
    "scaffolds/runtime/orchestrator/app/domains/delivery_studio/models.py": 100.0,
    "scaffolds/runtime/orchestrator/app/domains/delivery_studio/store.py": 100.0,
    "scaffolds/runtime/orchestrator/app/domains/delivery_studio/runtime.py": 95.0,
    "scaffolds/runtime/orchestrator/app/domains/delivery_studio/review_gate.py": 100.0,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    files = data.get("files") or {}
    failures = []
    for path, threshold in THRESHOLDS.items():
        summary = ((files.get(path) or {}).get("summary") or {})
        pct = float(summary.get("percent_covered") or 0.0)
        if pct < threshold:
            failures.append(f"{path}: {pct:.1f}% < {threshold:.1f}%")
    if failures:
        print("delivery coverage check failed")
        for line in failures:
            print(line)
        return 1
    print("delivery coverage check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
