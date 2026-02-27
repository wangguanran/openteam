#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_yaml_optional(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_policy(policy_path: Path) -> dict[str, Any]:
    return _read_yaml_optional(policy_path)


def _gb(x: float) -> float:
    return round(x / (1024**3), 2)


def _loadavg_1m() -> Optional[float]:
    try:
        return float(os.getloadavg()[0])
    except Exception:
        return None


def _mem_available_bytes() -> Optional[int]:
    # Prefer psutil if present.
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().available)
    except Exception:
        pass

    # Linux /proc/meminfo
    try:
        p = Path("/proc/meminfo")
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    kb = int(parts[1])
                    return kb * 1024
    except Exception:
        pass

    # macOS vm_stat (best-effort)
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(["vm_stat"], stderr=subprocess.DEVNULL, timeout=2).decode("utf-8", errors="replace")
            page_size = 4096
            free_pages = 0
            speculative_pages = 0
            for ln in out.splitlines():
                ln = ln.strip()
                if ln.startswith("page size of"):
                    # "page size of 4096 bytes"
                    try:
                        page_size = int(ln.split("page size of", 1)[1].split("bytes", 1)[0].strip())
                    except Exception:
                        pass
                if ln.startswith("Pages free:"):
                    free_pages = int(ln.split(":")[1].strip().strip("."))
                if ln.startswith("Pages speculative:"):
                    speculative_pages = int(ln.split(":")[1].strip().strip("."))
            return int((free_pages + speculative_pages) * page_size)
    except Exception:
        pass

    return None


def check(*, workdir: Path, policy_path: Path) -> dict[str, Any]:
    pol = _load_policy(policy_path)
    soft = (pol.get("soft_limits") or {}) if isinstance(pol.get("soft_limits"), dict) else {}

    cpu = os.cpu_count() or 1
    la1 = _loadavg_1m()
    mem_avail = _mem_available_bytes()
    du = shutil.disk_usage(str(workdir))

    lim_la = float(soft.get("loadavg_1m_max") or 0) or None
    lim_mem = float(soft.get("mem_free_gb_min") or 0) or None
    lim_disk = float(soft.get("disk_free_gb_min") or 0) or None

    mem_free_gb = _gb(float(mem_avail)) if mem_avail is not None else None
    disk_free_gb = _gb(float(du.free))

    ok = True
    reasons: list[str] = []

    if lim_la is not None and la1 is not None and la1 > lim_la:
        ok = False
        reasons.append(f"loadavg_1m {la1:.2f} > {lim_la:.2f}")
    if lim_mem is not None and mem_free_gb is not None and mem_free_gb < lim_mem:
        ok = False
        reasons.append(f"mem_free_gb {mem_free_gb:.2f} < {lim_mem:.2f}")
    if lim_disk is not None and disk_free_gb < lim_disk:
        ok = False
        reasons.append(f"disk_free_gb {disk_free_gb:.2f} < {lim_disk:.2f}")

    # Recommended max agents is advisory: based on CPU cores and load.
    rec = max(1, min(int(cpu), 8))
    if la1 is not None:
        # If already busy, lower recommendation.
        if la1 > 0.8 * float(cpu):
            rec = max(1, int(cpu / 2))

    return {
        "ok": ok,
        "reasons": reasons,
        "policy_path": str(policy_path),
        "cpu_cores": int(cpu),
        "loadavg_1m": la1,
        "mem_free_gb": mem_free_gb,
        "disk_free_gb": disk_free_gb,
        "recommended_max_agents": int(rec),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Resource guard for Team OS agent concurrency (soft limits)")
    ap.add_argument("--workdir", default=str(_repo_root()), help="Path to check disk free against")
    ap.add_argument("--policy", default=str(_repo_root() / "policies" / "agent_concurrency.yaml"))
    ap.add_argument("--json", action="store_true", help="Print JSON only")
    args = ap.parse_args(argv)

    res = check(workdir=Path(args.workdir), policy_path=Path(args.policy))
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
