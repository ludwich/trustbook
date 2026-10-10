"""What this container can really use: CPUs, physical cores, BF16 support and memory.

Inside containers ``os.cpu_count()`` reports the host's processors, and Docker Desktop (WSL2) can
show a different topology from the one the process may run on, so everything here is read from
the affinity mask, cgroup limits and ``/proc``. No ML imports.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

CGROUP_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CGROUP_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
PROC_CPUINFO = Path("/proc/cpuinfo")
PROC_MEMINFO = Path("/proc/meminfo")

# Native BF16 matmul support (AVX-512 BF16 on Zen 4+/Cooper Lake+, AMX on Sapphire Rapids+).
_BF16_FLAGS = frozenset({"avx512_bf16", "amx_bf16"})


@dataclass(frozen=True)
class CpuInfo:
    model: str | None
    logical: int  # processors the OS shows (may include ones this process cannot use)
    usable: int  # processors this process may run on (affinity, capped by cgroup quota)
    physical: int | None  # physical cores among the usable processors, if the topology is known
    bf16: bool  # native BF16 support


def _affinity() -> set[int] | None:
    try:
        return set(os.sched_getaffinity(0))
    except AttributeError:  # not on Linux
        return None


def usable_cpus(cgroup_cpu_max: Path = CGROUP_CPU_MAX) -> int:
    """CPUs this process may really use: affinity mask, capped by a cgroup (container) quota."""
    affinity = _affinity()
    cores = len(affinity) if affinity else (os.cpu_count() or 2)
    try:
        quota, period = cgroup_cpu_max.read_text("utf-8").split()[:2]
        if quota != "max" and int(period) > 0:
            cores = min(cores, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    return max(1, cores)


def _parse_cpuinfo(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for block in text.strip().split("\n\n"):
        entry: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                entry[key.strip()] = value.strip()
        if entry:
            blocks.append(entry)
    return blocks


def cpu_info(
    cpuinfo_path: Path = PROC_CPUINFO,
    cgroup_cpu_max: Path = CGROUP_CPU_MAX,
    affinity: set[int] | None = None,
) -> CpuInfo:
    usable = usable_cpus(cgroup_cpu_max)
    allowed = affinity if affinity is not None else _affinity()
    try:
        blocks = _parse_cpuinfo(cpuinfo_path.read_text("utf-8", errors="replace"))
    except OSError:
        blocks = []
    model = next((b["model name"] for b in blocks if b.get("model name")), None)
    flags = set(blocks[0].get("flags", "").split()) if blocks else set()

    cores: set[tuple[str, str]] = set()
    for block in blocks:
        processor = block.get("processor", "")
        if allowed is not None and processor.isdigit() and int(processor) not in allowed:
            continue
        if "core id" in block:
            cores.add((block.get("physical id", "0"), block["core id"]))
    physical = min(len(cores), usable) if cores else None

    return CpuInfo(
        model=model,
        logical=os.cpu_count() or usable,
        usable=usable,
        physical=physical,
        bf16=bool(flags & _BF16_FLAGS),
    )


def memory_bytes(
    cgroup_memory_max: Path = CGROUP_MEMORY_MAX, meminfo_path: Path = PROC_MEMINFO
) -> int | None:
    """Memory this container may use: the cgroup limit if set, else the machine's total."""
    total: int | None = None
    try:
        for line in meminfo_path.read_text("utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    try:
        limit = cgroup_memory_max.read_text("utf-8").strip()
        if limit != "max":
            return min(int(limit), total) if total else int(limit)
    except (OSError, ValueError):
        pass
    return total
