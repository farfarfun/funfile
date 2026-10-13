# Changelog

## 1.0.46

### 新增

- zstd 后端自动选择：Python 3.14 起用标准库 `compression.zstd`（PEP 784），
  更早版本回落第三方 `zstandard`。3.14 及以上不装 `funfile[zstd]` 也能用 zst，
  且压缩级别默认值与 `threads` 参数不随解释器版本变化。
- `ZipFile` 支持进度条：`extractall`/`extract`/`write`/`writestr` 都按未压缩
  字节数显示进度，并接受 `progress=` 复用调用方的进度条。只读元数据的
  `namelist()`/`infolist()` 不会创建进度条。
- `file_entar` 新增 `**kwargs`，透传压缩参数（`zst` 收 `level`/`threads`，
  `xz` 收 `preset`，`gz`/`bz2` 收 `compresslevel`）。
- 新增 `tarfile.read_mode_for()` 与 `tarfile.ZSTD_SUFFIXES`，按后缀推导读取模式。
- 新增 `funfile.utils.progress` 模块，导出 `ProgressReader`/`ProgressWriter`。
- 新增 `benchmarks/zstd_vs_xz.py`，README 与文档里的压缩数字可直接复现。

### 修复

- `TarFile.add` 对同一归档多次调用时，自建进度条的 `total` 只停留在首次调用的
  路径上，进度会冲过 100%；现在按调用累加。
- 读取 zstd 归档改走显式的 `r:zst`：`r:*` 的探测循环会把 `CompressionError`
  吞掉换成泛化的 `ReadError`，「安装 funfile[zstd]」这条可执行提示会丢失。
  `file_detar` 与 `allfile.extractall` 都已改用 `read_mode_for()`。
- `allfile.extractall` 补齐 `.taz`/`.tbz`/`.tbz2`/`.tb2` 等常见缩写后缀，
  此前这些归档会被当作不支持的格式。
- `ConcurrentWriteFile.write` 把阻塞的入队移出状态锁：此前一个被满队列挡住的
  写入会把其他线程的 `write()` 和 `close()` 一起堵在锁上（16 线程 / capacity=4
  实测 283ms → 51ms）。同时用在途写入计数让 `close()` 等待已受理的写入落地，
  避免结束标记插到数据前面导致丢写。
- `ProgressFileIO` 在 `progress=None` 时不再抛 `AttributeError`。

### 变更

- 更正压缩体积的说法：此前「zst 比 xz 大约 5%」是单一语料的结论，不能外推。
  改为按语料分类给区间（30MB 量级实测）：大量不可压缩内容 +9%、源码树 +22%、
  高重复文本 +41%。`ZSTD_DEFAULT_LEVEL = 12` 的取值理由也补上了实测依据。
  README、`file_entar` docstring 与 `doc/rust-feasibility.md`（新增 A6 节）同步。
- 内部的 `ReadFileWrapper` 与 `FileWrapper` 合并为 `utils.progress.ProgressReader`
  （以 `absolute=` 区分按绝对位置还是按累加计数）。两者此前未在文档中公开。
- CI 测试矩阵补齐 Python 3.11/3.13/3.14；新增不装任何可选依赖的 job，覆盖
  「没有 zstd 后端」的报错分支；lint job 纳入 `mypy`。
- 新增 mypy 配置，按最低支持版本 3.10 检查。

### 废弃

- `ConcurrentWriteFile` 的 `timeout` 参数：一直不生效，显式传入会触发
  `DeprecationWarning`，将在 2.0 移除。

## 1.0.45

### 变更

- CHANGELOG 把 1.0.44 的内容从「未发布」更正为对应版本号。本版本无代码变更。

## 1.0.44

### 新增

- `TarFile` 支持 zstd：`w:zst`/`r:zst`/`x:zst` 以及 `r:*` 自动探测，
  `allfile.extractall` 覆盖 `.tar.zst`/`.tzst`。标准库 `tarfile` 在 Python 3.13
  及更早版本没有 zstd 后端，故以可选依赖 `funfile[zstd]` 形式补齐，未安装时只影响
  zst 模式。注册方式为在子类上新建 `OPEN_METH`，不修改标准库的类属性。
  不支持 `|` 流式模式——标准库把压缩类型硬编码在私有类 `_Stream` 中，无法从子类扩展。
- `file_entar` 新增 `codec` 参数（`tar`/`gz`/`bz2`/`xz`/`zst`），省略 `dst_path`
  时按 codec 拼接后缀。默认值仍为 `xz`，既有调用方的归档路径与格式不变。
- 新增 `doc/rust-feasibility.md`：改用 Rust 开发的可行性分析，含压缩/解压专项实测。

### 修复

- `TarFile.extractall`/`extract` 的成员校验改为惰性，修复流式归档（`r|*`）二次定位报
  `StreamError` 导致解压失败的问题；`extract` 新增单成员路径越界校验。
- `ReadFileWrapper` 不再假设传入的 `fileobj` 可定位，修复以管道、socket、HTTP 响应体
  等不可 seek 的流配合 `r|*` 解压时抛 `io.UnsupportedOperation: seek` 的问题
  （标准库支持该用法，此前 funfile 会失败）。不可定位时进度退化为累加已读字节数。

### 变更

- CI 改为 `uv sync` + `uv run` 执行测试，新增 `ruff check`/`ruff format --check`
  lint job，并纳入代码风格检查（farfarfun/todo-list#675）。

## 1.0.43

### 新增

- 补齐公开 API 的类型标注、中文 docstring 及回归测试。

### 修复

- 并发写入失败时保留日志异常，不再静默吞掉。

### 变更

- 构建后端迁移至 Hatchling。
- `farlog` 最低版本提升至 1.1.7。

### 废弃

（无）

## 1.0.42 及更早版本

早期版本未维护 CHANGELOG，具体变更参见 git 提交历史。
