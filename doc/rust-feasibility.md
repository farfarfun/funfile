# funfile 改用 Rust 开发的可行性分析

分析日期：2026-10-13　　代码基线：`master` @ 8f02309（v1.0.43）

## 0. 结论

**不建议整体改写为 Rust。** funfile 的全部价值在于「与 CPython 标准库同构的 Python API」，
而不在于计算性能；用 Rust 重写必须先用 Rust 重新实现 CPython 的 `tarfile`/`zipfile`/`pickle`
(约 5400 行标准库代码)，才能换掉自己 875 行的封装代码，且换不到任何可测量的性能收益。

如果目标是「让 Rust 参与进来」，可行且划算的只有**窄内核加速**（并行哈希、并行目录遍历、
多线程压缩）与**独立 CLI 工具**两条路，详见第 6 节。

| 方案 | 可行性 | 收益 | 成本 | 判定 |
|---|---|---|---|---|
| A. 纯 Rust crate 替代库 | 技术可行 | 对现有调用方为负 | 高 | ❌ 否 |
| B. PyO3 重写并保留 `funfile` 包名/API | 不可行（见 §3） | 无 | 极高 | ❌ 否 |
| C. Rust 窄内核 + Python 保持公开 API | 可行 | 局部可测 | 中 | ⚠️ 可选 |
| D. 独立 Rust CLI（新产品，不替代库） | 可行 | 新场景 | 中 | ✅ 若有 CLI 需求 |

## 1. 现状盘点

### 代码规模

```
src/funfile 合计 875 行（tests 另 241 行）
  compress/tarfile.py   420   ← 唯一有实质逻辑的文件
  file/concurrent.py    122
  utils/size.py          53
  pickle/core.py         51
  utils/hash.py          46
  utils/tqdm_bar.py      42
  funos.py               32
  compress/allfile.py    30
  其余 9 个文件          79   （全是 re-export / 3 行转发）
```

### 代码性质

逐文件看，funfile 自己几乎不实现算法，只做三件事：**加进度条**、**补安全校验**、**统一导出**。

| 模块 | 实际实现者 | funfile 自己做的事 |
|---|---|---|
| `compress/tarfile.py` | 标准库 `tarfile` | 子类化 + 进度条 + 路径穿越校验 |
| `compress/zipfile.py` | 标准库 `zipfile` | `from zipfile import ZipFile`（3 行） |
| `compress/allfile.py` | 上面两者 | 按后缀分派 |
| `utils/hash.py` | `hashlib`（OpenSSL） | 1MB 分块循环 |
| `utils/size.py` | `os.scandir` | 递归求和 |
| `utils/tqdm_bar.py` | `tqdm` | 预设参数 |
| `pickle/core.py` | `pickle` | 开文件 + 转发 |
| `funos.py` / `file/core.py` | `os` / `shutil` | 转发 |
| `file/concurrent.py` | `threading` / `queue` | **唯一自研逻辑** |

### 消费方（关键约束）

funfile 已发布到 PyPI（1.0.43），且是 farfarfun 组织内的**上游基础库**，本机工作区内有
8 个项目依赖它：

| 调用方 | 使用的 API |
|---|---|
| `funget`（3 处） | `file_tqdm_bar`、`ConcurrentFile` |
| `fundrive`（3 处） | `file_tqdm_bar`、`file_size`、`file_sha1`、`compress.tarfile` |
| `funread`（2 处） | `compress.tarfile`、`funfile.pickle`、`funfile.funos` |
| `funcoin` | `compress.tarfile` |
| `funkaggle` | `compress.zipfile.ZipFile` |
| `funsketch` | 测试里为 `funfile.compress` 打垫片 |
| `funpypi` | 发布清单中登记 |

这意味着任何 API 破坏性变更的成本不是 funfile 一个仓库，而是 8 个仓库 + 未知的 PyPI 外部用户。

## 2. 性能基线实测：没有可优化的空间

重写语言的常见理由是性能。先量一下时间花在哪里（60MB 半可压缩数据，本机实测）：

```
输入大小 60.0 MB
tar 无压缩（纯 Python 侧 tar 组包逻辑上限）   0.06s   967.9 MB/s
tar + gzip                                   0.76s    78.7 MB/s
tar + xz                                     2.20s    27.3 MB/s
sha256 单线程（hashlib/OpenSSL）             0.04s  1439.8 MB/s
```

读法：

- **压缩耗时 97% 在 liblzma（C 代码）里**。xz 全程 2.20s，其中 Python 侧 tar 组包与 I/O
  只占 0.06s（2.7%）。把这 0.06s 用 Rust 优化到 0，端到端提速 **2.7%**。
- **哈希已经是 1.4 GB/s**，`hashlib` 直接走 OpenSSL 汇编实现，远超任何消费级磁盘顺序读取速度。
  Rust 的 `sha2` crate 在这里不会更快，瓶颈是 I/O。
- **纯 tar 组包 968 MB/s**，同样高于磁盘带宽。Python 的解释开销在 1MB 分块粒度下被完全摊薄。

**funfile 的热路径本来就不在 Python 里。** 它是一层薄胶水，真正干活的
liblzma / zlib / OpenSSL 都是 C。换成 Rust 等于把胶水换成另一种胶水。

唯一有真实空间的是**多线程压缩**（xz -T0 / zstd 多线程）——但那是算法与并行度的收益，
Python 侧用 `pyzstd` 或 `subprocess` 调 `xz -T0` 同样拿得到，不需要换语言。

## 3. 方案 B（PyO3 保留 API）为何不可行

这是最诱人也最容易被低估的方案：用 maturin + PyO3 写原生扩展，对外仍叫 `funfile`，
Python 调用方零改动。实际存在四个硬阻塞点。

### 阻塞 1：`TarFile` 是标准库类的子类

```python
# src/funfile/compress/tarfile.py:175
class TarFile(tarfile.TarFile):
    ...

    def extractall(self, path, members=None, *, numeric_owner=False, filter=None):
        return super().extractall(..., filter="data")
```

调用方（`funread`、`funcoin`、`fundrive`）把它当标准库 `TarFile` 用：模式串 `"w|xz"`/`"r:*"`、
`TarInfo` 对象、`getmember()`、`addfile()`、PEP 706 的 `filter="data"`、`add(filter=...)` 回调。
funfile 的 420 行之所以能这么短，**完全是因为 `super()` 继承了标准库 3059 行的 tar 实现**。

PyO3 类型无法有意义地继承一个纯 Python 类并复用其成员处理逻辑。要在 Rust 侧提供等价语义，
必须重新实现：

- tar 格式三种变体的头部读写（USTAR / GNU / PAX），含 PAX 扩展头、长文件名、稀疏文件
- 流式模式 `r|*` / `w|*` 的不可回退语义（`StreamError` 的语义当前代码专门适配过，见 CHANGELOG）
- PEP 706 的 `data` / `tar` / `fully_trusted` 三档过滤器语义
- 与 `TarInfo` 完全兼容的对象模型（调用方会读 `.name` / `.isfile()` / `.size`）

即：**用 Rust 重写 3059 行标准库代码，来替换自己 420 行的封装**。工作量是反向的。

### 阻塞 2：`file_tqdm_bar` 的返回值是 tqdm 对象，已被下游直接使用

```python
# src/funfile/utils/tqdm_bar.py:15
def file_tqdm_bar(...) -> tqdm:   # 返回类型就是 tqdm
```

`funget` 与 `fundrive` 共 4 处直接拿这个返回值当 tqdm 用（`.update()`、`.close()`、
传回 `TarFile.add(progress=...)`）。Rust 侧返回 `indicatif` 对象即破坏这 4 处；
返回 tqdm 对象则意味着这个函数必须留在 Python。

更深一层：`TarFile.add(progress=...)` 允许调用方**传入自己的 tqdm**，进度条是跨库共享的
Python 对象，不是 funfile 的内部状态。这种 duck-typing 协作是动态语言特有的。

### 阻塞 3：`funfile.pickle` 在 Rust 中无法实现

`pickle` 序列化任意活的 Python 对象（走 `__reduce__` 协议、可引用类与函数）。
离开 CPython 运行时**没有等价物**。`funread` 的下载处理器在用它。
这个模块只能是 Python 转发，无论如何都留在 Python 侧。

### 阻塞 4：`ProgressFileIO` 继承 `io.FileIO` 并被塞回标准库

```python
# src/funfile/compress/tarfile.py:11
class ProgressFileIO(io.FileIO):   # 作为 fileobj 传给标准库 tarfile
```

`ReadFileWrapper` / `FileWrapper` 还通过 `__getattr__` 做动态代理转发未知属性。
这套机制的全部意义就是「骗过标准库 tarfile 的鸭子类型检查」。Rust 侧没有要骗的对象。

**综合**：方案 B 的终局是一个「Rust 壳 + 必须保留的 Python 实现」的混合体——
`pickle`、`tqdm_bar` 留在 Python，`tarfile` 为了 API 兼容也只能留在 Python，
真正能下到 Rust 的只剩 `hash`、`size`、`concurrent` 约 220 行，而这 220 行里
两个已经跑在 C 的速度上。

## 4. 方案 A（纯 Rust crate）的代价

技术上完全可行——Rust 生态很成熟（`tar`、`zip`、`xz2`/`liblzma-rs`、`zstd`、`sha2`、
`indicatif`、`walkdir`、`rayon`），写一个功能对等的 crate 大约是 1–2 周的事。

但它不是「改写 funfile」，而是「放弃 funfile 并新建一个 Rust 库」：

- 8 个下游 Python 项目全部失去上游，需要各自改回标准库或改调 FFI
- PyPI 上的 `funfile` 包无法继续演进（Rust crate 发到 crates.io，受众完全不同）
- 现有 241 行回归测试、CHANGELOG 里修过的 bug（流式归档 `StreamError`、
  并发写错误传播、`task_done` 死锁）全部作废，需要在新语言里重新踩一遍

换语言不会继承已修复的 bug，只会继承写出这些 bug 的机会。

## 5. 工程与发布成本

当前发布链路：`funbuild build` → 一个 pure-Python wheel（`py3-none-any`），
CI 在 Python 3.10/3.12 两个版本上跑 `unittest` + `ruff`。

引入 Rust 后必须新增：

- **构建工具链**：maturin 替代 hatchling；`Cargo.toml` + MSRV 策略
- **wheel 矩阵**：cibuildwheel 构建 manylinux x86_64/aarch64、musllinux、
  macOS x86_64/arm64、Windows x86_64 ≈ 8–10 个 wheel/次发布（现在是 1 个）
- **abi3 决策**：用 `abi3-py310` 可避免按 Python 小版本再乘一遍，但会限制可用的 PyO3 API
- **sdist 退化路径**：没有匹配 wheel 的用户（如一些国产 ARM 发行版）需要本地装 rustc 才能
  `pip install funfile`——对一个「文件工具库」而言是明显的可用性退步
- **CI 时长**：当前 lint+test 两个 job 共 25 分钟预算，Rust 交叉编译矩阵会推到小时级

考虑到 farfarfun 组织维护着 150+ 个仓库、funfile 自身 4 个月 25 次提交的活跃度，
把一个 875 行的薄封装库升级成需要维护交叉编译矩阵的原生扩展，运维负担与收益明显不成比例。

## 6. 如果确实想用 Rust：两条划算的路

### 路线 C：Rust 窄内核（`funfile-core`），公开 API 全部留在 Python

只把**真正 CPU/syscall 密集且与 Python 对象模型无耦合**的部分下到 Rust，
`funfile` 仍是 pure-Python 包，可选依赖 `funfile-core`，导入失败则回退到当前实现。

候选内核（按收益排序）：

1. **并行目录遍历 + 大小统计** —— 替代 `utils/size.py` 的递归 `scandir`。
   用 `jwalk`/`rayon` 在多核上并行 stat，对十万级文件的目录树有数倍收益（当前实现是单线程递归）。
2. **多线程压缩** —— `zstd` 多线程或 `liblzma` 的 `MT` 模式。这是**唯一能改变压缩吞吐量级**
   的改动（上文 27 MB/s 的 xz 可随核数近线性提升），但注意：新增压缩格式比换语言更重要。
3. **并行多文件哈希** —— 校验大批文件时用 `rayon` 并发，单文件无收益（已 1.4 GB/s）。

这条路的好处是**零 API 破坏、可渐进、可回退、可度量**。先做 1 和 2，各自带 benchmark，
用数据决定要不要做下一个。

### 路线 D：独立 Rust CLI（新产品，不替代库）

如果真实需求是「命令行里快速打包/校验/看目录大小」，写一个 Rust CLI（`clap` + `indicatif`）
是合适的：单文件二进制、零 Python 依赖、启动 ~1ms（Python 冷启动约 30–50ms，
导入 tqdm 后更久）。这类场景 Rust 优势真实存在，且与 Python 库并行共存、互不破坏。

但要清楚：这是**新增一个产品**，不是「funfile 改为 Rust 开发」。

## 7. 建议

1. **funfile 作为 Python 库维持现状**。它的定位（标准库 API 的薄增强层）与 Rust 的优势区
   （无 GC 的系统级性能）不重叠。
2. 若想提升性能，**优先改算法而非换语言**：引入 zstd / 多线程 xz，收益比重写大一个数量级，
   成本是几十行 Python。
3. 若想引入 Rust，**走路线 C 的第 1、2 项**，以可选加速内核形式落地，带 benchmark 验收，
   保持 pure-Python 回退路径。
4. 若有 CLI 需求，**走路线 D 另开仓库**，不要和库纠缠。

---

附：本文所有规模与性能数字均为本机基线实测（Python 3.12/3.13，60MB 测试集），
重新评估时请以当时实测数据为准。

---

# 附录 A：只看「压缩 / 解压」的专项分析

补充分析日期：2026-10-13。上文第 2 节用的是 60MB 高可压语料，压缩占比被低估；
本附录改用 **127.8 MB 内容各异的混合语料**（1/3 随机 + 1/3 文本 + 1/3 JSON 结构），
专门回答「如果只把压缩和解压拿出来用 Rust 做，值不值」。

## A1. 结论先行

**单看压缩，瓶颈是真实的——但最优解是换编解码格式，不是换语言。**

- 压缩侧确实存在 Python 硬天花板：**CPython 的 `lzma` 模块不暴露多线程编码**
  （已验证：`LZMACompressor` 无 `threads` 参数，模块内无任何 MT 常量），
  而 xz 本身支持 `lzma_stream_encoder_mt`。这是标准库的能力缺口。
- 但打破天花板最划算的手段是 **zstd**：压缩快 11x、解压快 8x、体积只大 4.5%，
  成本是一个 pip 依赖 + 约 40 行 Python。
- **Rust 在编解码层买不到任何东西**：Rust 的 `xz2` / `zstd` / `flate2` crate 都是
  FFI 到 Python 已经绑定的同一批 C 库（liblzma / libzstd / zlib）。写 Rust 编解码扩展
  等于重新实现 `python-zstandard`，算法层面零增益。

## A2. 关键洞察：compress 模块是两层，性质完全相反

这是整个专项分析的核心。`compress/` 里有两件事被混在一起看，但它们的性质是**相反**的：

| 层 | 内容 | 实测吞吐 | 与 Python 对象模型的耦合 |
|---|---|---|---|
| **tar 容器层** | 头部读写、成员迭代、`extractall` 语义、PEP 706 filter、进度条 | **968 MB/s**（远超磁盘） | 极深：子类化 stdlib，3 个下游在用 |
| **编解码层** | xz / gzip / zstd 字节流 | **2.2 MB/s** ← 真瓶颈 | **零**：bytes in / bytes out |

结论自明：**唯一值得动的是编解码层，而它恰好是唯一不需要动 API 的层。**
反过来说，上文第 3 节列的所有 API 阻塞点（子类化、`TarInfo`、filter、tqdm 对象共享）
**全部集中在不需要优化的容器层**。

### 这个接缝已验证可以切开

把 tar 容器层保持为标准库 + funfile，只把编解码层换成外部实现，实测可行：

```python
# 写入侧：funfile 的 TarFile 以无压缩流模式 w| 接到外部压缩器
w = MTXZWriter("out.tar.xz", threads=0)  # 此处可换 zstd / Rust 扩展
with funtar.open(fileobj=w, mode="w|") as tar:
    tar.add("data", arcname="data")  # ← 进度条、成员校验逻辑原样生效
# ✅ 实测通过：产物 63.3 MB
```

读取侧同样可注入（`mode="r|"`），但会先撞上 A5 节的 bug。

## A3. 实测数据：压缩

127.8 MB 混合语料，2 核机器（多线程项受核数限制，见 A4 说明）：

| 压缩方案 | 耗时 | 吞吐 | 产物 | 压缩比 | 相对当前 |
|---|---|---|---|---|---|
| **stdlib lzma preset=6**（当前 `w:xz` 默认） | 58.9s | **2.2 MB/s** | 63.3 MB | 2.02x | 基准 |
| xz CLI -6 -T1 | 59.9s | 2.1 MB/s | 63.3 MB | 2.02x | 1.0x |
| xz CLI -6 -T0（全核） | 62.6s | 2.0 MB/s | 63.2 MB | 2.02x | 0.9x |
| xz CLI -3 -T0（全核） | 36.6s | 3.5 MB/s | 66.1 MB | 1.93x | 1.6x |
| zstd l=19 threads=-1 | 39.7s | 3.2 MB/s | 64.0 MB | 2.00x | **1.5x（同等压缩比）** |
| **zstd l=12 threads=-1** | 5.1s | **24.9 MB/s** | 66.2 MB | 1.93x | **11.3x** ⭐ |
| zstd l=3 threads=-1 | 0.5s | **248 MB/s** | 68.5 MB | 1.87x | **113x** |
| zlib level=6（`w:gz`） | 5.0s | 25.6 MB/s | 67.3 MB | 1.90x | 11.6x |

读法：

- **zstd l=12 全面优于 gzip**：速度持平（24.9 vs 25.6 MB/s），压缩比更好（1.93x vs 1.90x）。
  只要支持 zstd，`w:gz` 就没有存在理由了。
- **同等压缩比下 zstd 也更快**：l=19 拿到 2.00x（xz preset=6 是 2.02x），
  速度 1.5x，且随核数扩展——而 stdlib lzma 根本没有扩展路径。
- **xz -T0 在本机没有加速**（2.0 vs 2.1 MB/s）。2 核 + xz 的 MT 块大小默认 3×字典
  （preset 6 下 24MB）使并行度本就有限，这个数字不能外推到多核机器；
  但它说明「上了多线程 xz 就会快」并不自动成立，需按实际核数与块大小实测。
- **多线程 xz 要付压缩比代价**：MT 编码必须切块，牺牲跨块冗余。上文 60MB 语料里
  这一项从 140.7x 掉到 23.9x，代价在高冗余数据上可能非常大。

## A4. 实测数据：解压——几乎没有优化空间

| 解压方案 | 耗时 | 吞吐 |
|---|---|---|
| **stdlib lzma 解压**（当前） | 1.06s | **120.6 MB/s** |
| xz -d -T0（分块产物） | 0.77s | 166.3 MB/s |
| zlib 解压 | 0.32s | 405.7 MB/s |
| **zstd 解压**（l=3 / l=12 / l=19 产物） | 0.11–0.13s | **984–1128 MB/s** |

**压缩与解压的性质完全不对称，这是本附录最该记住的一点：**

- 压缩 2.2 MB/s —— 远低于任何磁盘，**纯 CPU 瓶颈，有真实优化空间**。
- 解压 120 MB/s（xz）到 1100 MB/s（zstd）—— 已达到或超过常见 SSD 顺序读取速度，
  **基本 I/O 受限，优化语言不会改善端到端时间**。

所以「压缩和解压」里，只有压缩值得投入。解压侧换 zstd 能白拿 8x，但那是换格式的副产品，
不是单独值得做的项目。

## A5. GIL 不构成障碍（Rust 无 GIL 优势在此被抵消）

「Rust 能真并行、Python 被 GIL 锁住」是支持 Rust 的最后一个论点。实测否掉了它：

```
8 文件 / 17.0 MB，lzma preset=6 逐文件压缩，2 核机器
  串行 (1 线程)                4.48s    3.8 MB/s
  ThreadPoolExecutor 2 线程    3.23s    5.3 MB/s   → 1.39x
  lzma 解压 2 线程                                 → 1.31x
```

若 GIL 未释放，2 线程只会得到 ≤1.0x。实测 1.39x（2 核理论上限 2.0x，约 70% 扩展效率）
说明 **CPython 的 `_lzma` 在压缩期间释放了 GIL**，Python 用 `ThreadPoolExecutor`
即可并行压缩多个文件。`zstandard` 同理，且自带 `threads=-1` 的库内多线程。

（注：基准脚本里按 >1.5x 判定的那行自动结论「GIL 未释放」是阈值设错，对 2 核机器不适用；
以 1.39x > 1.0x 这个事实为准。）

补充：zstd l=12 的 2 线程测出 0.74x，是因为单文件仅约 2MB、每次调用重建
`ZstdCompressor` 的开销盖过了收益，不是 GIL 证据。

## A6. 发现一个真实 bug（且正好堵住 codec-swap 架构）

验证 A2 的读取侧注入时撞到的，与 Rust 决策无关，但应单独修：

**`src/funfile/compress/tarfile.py:62` —— 传入不可 seek 的 `fileobj` 时崩溃，标准库正常。**

```
1) 标准库 tarfile 处理不可 seek 流:   ✅ 成功，成员: ['mini', 'mini/a.txt']
2) funfile.compress.tarfile 同一个流: ❌ UnsupportedOperation: seek
```

成因：`ReadFileWrapper._update_progress()` 无条件调用 `self._fileobj.tell()`。
同文件的 `_stream_size()`（第 112–123 行）已经对不可 seek 流做了防御并返回 `None`，
但 `_update_progress` 没有——两处对同一前提的处理不一致。

影响面：`r|*` 流式模式的存在意义就是处理管道 / socket / HTTP 响应体这类不可回退的流。
任何「边下载边解压」「从子进程 stdout 解压」的用法都会踩到。修法是把 `tell()`
包进 `try` 或在构造时探测一次可 seek 性，探测失败就退化为不更新进度。

## A7. 专项建议（按性价比排序）

| 优先级 | 动作 | 收益 | 成本 | 状态 |
|---|---|---|---|---|
| 1 | **加 zstd 支持**（`w:zst` / `r:zst`，可选依赖 `zstandard`） | 见 A8 实测 | ~60 行 + 1 个可选依赖 | ✅ 已实现 |
| 2 | 修 `tarfile.py:62` 不可 seek 流 bug | 恢复标准库语义，解锁流式用法 | ~15 行 | ✅ 已修复 |
| 3 | `file_entar` 加 `codec=` 参数 | 让快捷函数也能选 zstd | ~20 行 | ✅ 已实现 |
| 4 | 需要同等压缩比时用 `zstd level=19 threads=-1` 替代 xz preset=6 | 1.5x 且随核数扩展 | 调 `level=` 即可 | 按需 |
| — | **用 Rust 写编解码层** | **0**（同一批 C 库） | 高 | ❌ 不做 |

第 3 项没有把默认格式从 xz 翻成 zstd：`file_entar` 的返回值是归档路径，改默认会让
既有调用方拿到的后缀从 `.tar.xz` 变成 `.tar.zst`，并让一个原本零额外依赖的调用突然
需要 `zstandard`。对一个已发布到 PyPI、组织内有 8 个下游的库，这是破坏性变更，
因此默认保持 `xz`，由调用方显式选 `codec="zst"`。

Rust 在压缩/解压这个子问题上的唯一合理用途，仍然是正文 §6 的**路线 D：独立 CLI**
（单静态二进制、无 Python 运行时、~1ms 启动）。作为 Python 库的一部分，它没有位置。

## A8. 落地后的端到端复测

A3/A4 测的是**纯编解码**（内存进内存出）。实现落地后用 `file_entar`/`file_detar`
复测**端到端**（含 tar 组包、磁盘读写、进度条），36.2 MB 混合语料：

| codec | 压缩 | 吞吐 | 产物 | 压缩比 | 解压 | 吞吐 |
|---|---|---|---|---|---|---|
| `xz`（默认） | 17.11s | 2.1 MB/s | 21.0 MB | 1.72x | 0.27s | 131.9 MB/s |
| `gz` | 6.46s | 5.6 MB/s | 22.3 MB | 1.63x | 0.11s | 322.9 MB/s |
| **`zst`** | **1.12s** | **32.2 MB/s** | 22.1 MB | 1.64x | **0.07s** | **528.6 MB/s** |

与 A3/A4 的纯编解码数字对比，两个方向都需要修正认识：

- **压缩收益比预估更大**：端到端 15.3x（17.11s → 1.12s），高于纯编解码测出的 11x。
  因为 xz 慢到让 tar 组包与磁盘 I/O 的占比可以忽略，而 zstd 快到这些固定开销开始显形。
- **解压收益比预估更小**：端到端 3.9x，低于纯编解码的 8x。这正是 A4 结论的直接验证——
  `file_detar` 要把 36MB 写回磁盘，写盘成为瓶颈，codec 再快也摊薄了。

所以对外的准确说法是：**压缩快一个数量级，解压快数倍，体积大约 5%**。
把纯编解码的微基准数字直接当成用户可感知收益会高估解压、低估压缩。
