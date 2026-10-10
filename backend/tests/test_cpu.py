from __future__ import annotations

from pathlib import Path

from app.capabilities.bundled import ProviderRegistry, RuntimeOptions
from app.capabilities.cpu import cpu_info, memory_bytes

# Two physical cores with SMT (processors 0/1 share core 0, 2/3 share core 1).
CPUINFO = "\n\n".join(
    f"processor\t: {n}\nmodel name\t: Test CPU 9000\nphysical id\t: 0\ncore id\t\t: {n // 2}\n"
    f"flags\t\t: fpu avx2 avx512f{' avx512_bf16' if bf16 else ''}"
    for n, bf16 in ((0, True), (1, True), (2, True), (3, True))
)


def test_cpu_info_counts_physical_cores_in_affinity(tmp_path: Path) -> None:
    info_path = tmp_path / "cpuinfo"
    info_path.write_text(CPUINFO)
    info = cpu_info(info_path, tmp_path / "no-cgroup", affinity={0, 1, 2, 3})
    assert (info.model, info.physical, info.bf16) == ("Test CPU 9000", 2, True)
    # Only processors 0 and 1 allowed: both on core 0.
    assert cpu_info(info_path, tmp_path / "no-cgroup", affinity={0, 1}).physical == 1


def test_cpu_info_without_topology_or_bf16(tmp_path: Path) -> None:
    info_path = tmp_path / "cpuinfo"
    info_path.write_text("processor\t: 0\nflags\t\t: fpu avx2\n")
    info = cpu_info(info_path, tmp_path / "no-cgroup")
    assert (info.model, info.physical, info.bf16) == (None, None, False)
    assert cpu_info(tmp_path / "missing", tmp_path / "no-cgroup").usable >= 1


def test_memory_bytes(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemFree: 1 kB\n")
    limit = tmp_path / "memory.max"
    limit.write_text("max\n")
    assert memory_bytes(limit, meminfo) == 16384000 * 1024
    limit.write_text(f"{4 * 1024**3}\n")
    assert memory_bytes(limit, meminfo) == 4 * 1024**3
    assert memory_bytes(tmp_path / "x", tmp_path / "y") is None


def test_configure_drops_loaded_models(tmp_path: Path) -> None:
    from tests.fakes import FakeEmbedder, write_models_lock

    write_models_lock(tmp_path)
    loads: list[RuntimeOptions] = []

    def factory(_info: object, options: RuntimeOptions) -> FakeEmbedder:
        loads.append(options)
        return FakeEmbedder()

    registry = ProviderRegistry(tmp_path, factories={"embeddings": factory})
    first = registry.embedder()
    registry.configure(RuntimeOptions())  # unchanged: keep the loaded model
    assert registry.embedder() is first
    tuned = RuntimeOptions(torch_threads=4, embedding_dtype="bfloat16")
    registry.configure(tuned)
    assert registry.embedder() is not first
    assert loads == [RuntimeOptions(), tuned]
