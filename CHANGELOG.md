# Changelog

## 未发布

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
