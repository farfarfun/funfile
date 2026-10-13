"""对比 zstd 与 xz 的压缩体积和耗时，用于复现 README 与文档里的数字。

关键结论是「体积代价强依赖语料」，所以这里刻意准备三份可压缩性差异很大的语料，
而不是只跑一份。直接运行即可：

    python benchmarks/zstd_vs_xz.py

语料是按固定随机种子生成的，不依赖外部数据，同一台机器上重跑结果稳定。
需要 `zstandard`（Python 3.13 及更早版本）或 Python 3.14+ 的标准库 zstd。
"""

from __future__ import annotations

import io
import json
import lzma
import random
import sys
import tarfile
import time
from collections.abc import Callable
from functools import partial

# 与 `funfile.compress.tarfile.ZSTD_DEFAULT_LEVEL` 保持一致。
ZSTD_LEVEL = 12
# xz 的标准库默认值。
XZ_PRESET = 6
# 各语料的目标规模，取 30MB 量级：足够让编解码耗时压过固定开销，又不至于跑太久。
CORPUS_BYTES = 30 * 2**20
# 级别扫描用的取值，用来说明 ZSTD_DEFAULT_LEVEL 为什么取 12。
LEVEL_SWEEP = (3, 6, 9, 12, 15, 19)


def _zstd_compress(data: bytes, level: int) -> bytes:
    """用任一可用后端压缩，返回 zstd 帧。

    Args:
        data: 待压缩数据。
        level: zstd 压缩级别。

    Returns:
        压缩后的字节。
    """
    try:
        from compression.zstd import compress  # Python 3.14+
    except ImportError:
        import zstandard

        return zstandard.ZstdCompressor(level=level).compress(data)
    return compress(data, level=level)


def corpus_mixed() -> bytes:
    """1/3 随机 + 1/3 文本 + 1/3 JSON，复刻 doc/rust-feasibility.md 附录 A 的语料。

    随机那一份任何 codec 都压不动，会给归档设一个体积地板，把 codec 之间的差距
    按比例稀释——这正是附录 A 测出 +9% 的原因。

    Returns:
        混合语料字节。
    """
    rnd = random.Random(0)
    third = CORPUS_BYTES // 3
    words = [f"word{i}" for i in range(5000)]
    part_text = (" ".join(rnd.choice(words) for _ in range(third // 6))).encode()
    part_json = json.dumps(
        [
            {"id": i, "v": rnd.random(), "tag": rnd.choice(words)}
            for i in range(third // 60)
        ]
    ).encode()
    return rnd.randbytes(third) + part_text[:third] + part_json[:third]


def corpus_source_tree() -> bytes:
    """当前解释器的 `site-packages`/标准库目录打成的 tar，代表真实源码树。

    混了源码、元数据和少量二进制，是最接近日常用法的一份。

    Returns:
        tar 字节流。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        archive.add(sys.prefix + "/lib", arcname="root")
    return buf.getvalue()


def corpus_log() -> bytes:
    """模板固定、字段变化的 JSON 日志行，代表高度重复的文本。

    注意不要用完全相同的行：那种语料两个 codec 都能压到接近 0，测出来的是地板
    高度而不是 codec 差异。

    Returns:
        换行分隔的日志字节。
    """
    rnd = random.Random(0)
    words = [f"word{i}" for i in range(5000)]
    lines = []
    size = 0
    i = 0
    while size < CORPUS_BYTES:
        line = json.dumps(
            {
                "ts": f"2026-10-13T{i % 24:02d}:{i % 60:02d}:{i % 60:02d}Z",
                "level": rnd.choice(["INFO", "WARN", "ERROR"]),
                "msg": "request handled",
                "path": f"/api/v1/{rnd.choice(words)}",
                "ms": rnd.randint(1, 9999),
            }
        )
        lines.append(line)
        size += len(line) + 1
        i += 1
    return "\n".join(lines).encode()


CORPORA: dict[str, Callable[[], bytes]] = {
    "mixed (1/3 random)": corpus_mixed,
    "source tree": corpus_source_tree,
    "semi-repetitive log": corpus_log,
}


def _timed(fn: Callable[[], bytes]) -> tuple[int, float]:
    """跑一次并返回产物大小与耗时。

    Args:
        fn: 执行压缩的无参函数。

    Returns:
        `(产物字节数, 耗时秒)`。
    """
    start = time.perf_counter()
    out = fn()
    return len(out), time.perf_counter() - start


def main() -> None:
    """跑完三份语料的 xz/zstd 对比，再对源码树做一次级别扫描。"""
    mib = float(2**20)
    print(f"xz preset={XZ_PRESET}  vs  zstd level={ZSTD_LEVEL}\n")
    header = (
        f"{'corpus':22s} {'raw':>8s} {'xz':>8s} {'xz s':>7s} "
        f"{'zst':>8s} {'zst s':>7s} {'zst/xz':>7s}"
    )
    print(header)
    print("-" * len(header))

    source_tree = None
    for label, build in CORPORA.items():
        raw = build()
        if label == "source tree":
            source_tree = raw
        xz_size, xz_sec = _timed(partial(lzma.compress, raw, preset=XZ_PRESET))
        zst_size, zst_sec = _timed(partial(_zstd_compress, raw, ZSTD_LEVEL))
        print(
            f"{label:22s} {len(raw) / mib:7.1f}M {xz_size / mib:7.2f}M {xz_sec:7.1f} "
            f"{zst_size / mib:7.2f}M {zst_sec:7.1f} "
            f"{(zst_size / xz_size - 1) * 100:+6.0f}%"
        )

    assert source_tree is not None
    print(f"\n级别扫描（source tree，对比同一份 xz preset={XZ_PRESET}）")
    xz_size, _ = _timed(partial(lzma.compress, source_tree, preset=XZ_PRESET))
    for level in LEVEL_SWEEP:
        zst_size, zst_sec = _timed(partial(_zstd_compress, source_tree, level))
        print(
            f"  level={level:2d}  {zst_size / mib:6.2f}M  {zst_sec:5.1f}s  "
            f"vs xz {(zst_size / xz_size - 1) * 100:+4.0f}%"
        )


if __name__ == "__main__":
    main()
