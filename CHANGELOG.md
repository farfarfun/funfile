# Changelog

## 未发布

### 修复

- `TarFile.extractall`/`extract` 的成员校验改为惰性，修复流式归档（`r|*`）二次定位报
  `StreamError` 导致解压失败的问题；`extract` 新增单成员路径越界校验。

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
