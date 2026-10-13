# funfile

一个实用的 Python 文件操作工具库，提供带进度条的压缩/解压、并发写入、pickle 序列化等功能的增强封装。

## 特性

- 带进度条的 tar / zip 压缩与解压，用法与标准库一致
- 补齐标准库没有的 zstd 支持（`w:zst` / `r:zst`），压缩快一个数量级，体积代价视数据而定
- 自动识别常见 zip/tar 格式，并阻止 tar 路径穿越
- 线程安全的后台文件写入，支持指定偏移量
- pickle 序列化/反序列化的便捷封装
- 常用文件系统操作（创建目录、删除、复制）

## 安装

```bash
pip install funfile

# Python 3.13 及更早版本用 zstd 时额外装可选依赖
pip install "funfile[zstd]"
```

## 依赖

- Python >= 3.10
- [farlog](https://pypi.org/project/farlog/) >= 1.1.7
- [tqdm](https://pypi.org/project/tqdm/) >= 4.66.5
- 可选：[zstandard](https://pypi.org/project/zstandard/) >= 0.22
  （仅 Python 3.13 及更早版本用 zstd 时需要；3.14+ 走标准库 `compression.zstd`）

## 使用

### tar 压缩/解压（带进度条）

用法与标准库 `tarfile` 一致，自动显示进度条：

```python
from funfile import tarfile

# 压缩
with tarfile.open("results.tar.xz", "w|xz") as tar:
    tar.add("a.txt")

# 解压
with tarfile.open("results.tar.xz", "r|xz") as tar:
    tar.extractall("local")
```

也可以使用快捷函数：

```python
from funfile.compress.tarfile import file_entar, file_detar

# 一键压缩
file_entar("mydir", "mydir.tar.xz")

# 一键解压
file_detar("mydir.tar.xz", "output")
```

`file_entar` 可以用 `codec` 选择压缩算法（`tar`、`gz`、`bz2`、`xz`、`zst`），
默认 `xz`。省略 `dst_path` 时按 codec 自动拼接后缀：

```python
file_entar("mydir", codec="zst")  # -> mydir.tar.zst
```

### zstd 压缩/解压

标准库 `tarfile` 在 Python 3.13 及更早版本没有 zstd 后端，funfile 补上了，
用法与其他压缩类型一致：

```python
from funfile import tarfile

with tarfile.open("results.tar.zst", "w:zst") as tar:
    tar.add("mydir")

with tarfile.open("results.tar.zst", "r:zst") as tar:
    tar.extractall("local")
```

后端自动选择：Python 3.14+ 用标准库 `compression.zstd`（PEP 784），不必装
`funfile[zstd]`；更早版本回落到第三方 `zstandard`，两者都没有时报
`CompressionError` 并给出安装提示。无论走哪个后端，默认压缩级别和 `threads`
参数的行为都一致。

`r:*` 自动探测和 `funfile.compress.allfile.extractall` 都已覆盖 `.tar.zst`/`.tzst`。

为什么值得换：实测 34MB 源码树，压缩 9.7s 降到 0.8s，解压也快 3～4 倍。代价是
归档更大，而且大多少**强依赖数据的可压缩性**——换 codec 前先看自己的数据属于哪一类：

| 数据 | zst 相对 xz 的体积 |
| --- | --- |
| 大量不可压缩内容（已压缩的图片、模型权重） | +9% |
| 源码树 | +22% |
| 高度重复的文本（日志、JSON 导出） | +41% |

这组数字可以用 `python benchmarks/zstd_vs_xz.py` 复现，完整分析见
[doc/rust-feasibility.md](doc/rust-feasibility.md) 附录 A。

一个限制：`|` 流式模式不支持 zstd（`w|zst` 会报 `CompressionError`）。标准库把
压缩类型硬编码在私有类 `_Stream` 里，无法从子类扩展。

### zip 压缩/解压（带进度条）

用法与标准库 `zipfile` 一致，`extractall`、`extract`、`write`、`writestr`
都会显示按字节统计的进度：

```python
from funfile import zipfile

# 解压
with zipfile.ZipFile("archive.zip") as zf:
    zf.extractall("output")

# 压缩，多次 write 的总量会累加
with zipfile.ZipFile("archive.zip", "w") as zf:
    zf.write("a.txt")
    zf.write("b.txt")
```

进度按**未压缩字节数**统计。zip 的中央目录在文件末尾，读成员时会在文件里来回
定位，底层读取位置并不单调递增，用它做进度会来回跳。

只读元数据不会创建进度条，`namelist()`、`infolist()`、`read()` 的行为与标准库完全一致。
想复用自己的进度条就传 `progress=`，这种情况下 funfile 不会替你关闭它：

```python
from tqdm import tqdm

with tqdm(total=total_bytes) as bar, zipfile.ZipFile("archive.zip") as zf:
    zf.extractall("output", progress=bar)
```

### 通用解压

根据文件后缀自动选择解压方式，支持 `.zip`、`.tar`、`.tar.gz`、`.tgz`、`.tar.bz2`、`.tar.xz`、`.txz`、`.tar.zst`、`.tzst`。tar 解压默认只接受普通文件和目录，拒绝目标目录之外的路径及链接、设备等特殊成员：

```python
from funfile.compress.allfile import extractall

extractall("archive.tar.gz", "output")
```

### 并发文件写入

线程安全的后台写入，适用于多线程生产数据的场景。离开 `with` 时会等待数据落盘，后台写入错误会传播给调用方：

```python
from funfile import ConcurrentFile

with ConcurrentFile("output.txt", mode="w") as fw:
    fw.write("hello, funfile.")
    fw.write("another line.")
```

支持指定偏移量的随机写入：

```python
with ConcurrentFile("output.bin", mode="wb") as fw:
    fw.write(b"chunk1", offset=0)
    fw.write(b"chunk2", offset=1024)
```

### pickle 序列化

便捷的 pickle 读写封装：

> `pickle` 可以执行序列化数据中的代码，只加载可信来源的数据。

```python
from funfile.pickle import dump, load, dumps, loads

# 写入文件
dump({"key": "value"}, "data.pkl")

# 从文件读取
data = load("data.pkl")

# 序列化为 bytes
raw = dumps({"key": "value"})

# 从 bytes 反序列化
obj = loads(raw)
```

### 文件系统工具

```python
from funfile.funos import makedirs, delete

# 递归创建目录（已存在不报错）
makedirs("path/to/dir")

# 删除文件或递归删除目录
delete("path/to/dir")
```

```python
from funfile.file import copy

# 复制文件
copy("src.txt", "dst.txt")
```

### 获取文件/目录大小

```python
from funfile import get_size

# 获取文件大小（字节）
size = get_size("large_file.bin")

# 递归获取目录大小
size = get_size("mydir", recursive=True)
```

### 文件哈希

```python
from funfile import file_md5, file_sha256

md5 = file_md5("archive.tar.xz")
sha256 = file_sha256("archive.tar.xz")
```

## 项目结构

```
src/funfile/
├── __init__.py            # 顶层导出
├── funos.py               # 文件系统工具（makedirs, delete）
├── file/
│   ├── core.py            # 文件复制
│   └── concurrent.py      # 并发文件写入
├── compress/
│   ├── tarfile.py         # 带进度条的 tar 操作
│   ├── zipfile.py         # zip 操作封装
│   └── allfile.py         # 通用解压
├── utils/
│   ├── size.py            # 文件大小与可读格式
│   ├── hash.py            # 文件哈希
│   └── tqdm_bar.py        # 文件进度条
└── pickle/
    └── core.py            # pickle 序列化封装
```

## 开发

开发依赖（`pytest`、`ruff`）定义在 `pyproject.toml` 的 `[dependency-groups].dev` 中，由 `uv` 管理：

```bash
uv sync
```

测试与静态检查，命令与 CI（`.github/workflows/test.yml`）保持一致：

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check .
uv run ruff format --check .
```

`pyproject.toml` 已配置 `testpaths`，也可以用 pytest 跑同一批用例：

```bash
uv run pytest -q
```

构建与发布走 `funbuild`，不要手写发布脚本：

```bash
funbuild install   # 本地构建并安装，验证当前代码可安装（不发布、不打标签）
funbuild build     # 完整发布流程：递增版本、构建、安装校验、发布 PyPI、推送并打标签
```

---

## 关于 farfarfun

[farfarfun](https://github.com/farfarfun) 是一个专注于实用工具库的开源组织，
涵盖云存储、数据处理、AI、多媒体与开发工具链等方向。

- 🏠 组织主页：<https://github.com/farfarfun>
- 📦 PyPI：<https://pypi.org/user/niuliangtao/>
- 📧 联系：farfarfun@qq.com

本项目基于 [MIT](LICENSE) 协议开源。
