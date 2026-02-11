# funfile 优化记录

## 1. [BUG] `file/concurrent.py` — `chunk is None` 未调用 `task_done()`

**问题**：当 `chunk` 为 `None` 时直接 `continue`，跳过了 `task_done()` 调用，导致 `wait_for_all_done()` 中的 `self._write_queue.join()` 永久阻塞。

**修复**：在 `continue` 前补上 `self._write_queue.task_done()`。

## 2. [BUG] `file/concurrent.py` — `__exit__` 吞掉所有异常

**问题**：`__exit__` 返回 `True`，会抑制 `with` 块内的所有异常，调用方无法感知错误。

**修复**：改为 `return False`，让异常正常传播。

## 3. [BUG] `compress/tarfile.py` — `FileWrapper.__del__` 重置进度为 0

**问题**：对象被 GC 回收时调用 `self._update(0)`，进度条会突然归零。

**修复**：改为 `self._update(self._fileobj.tell())`，更新到文件实际位置。

## 4. [BUG] `file/core.py` — `follow_symlinks` 参数未传递

**问题**：`copy` 函数接受了 `follow_symlinks` 参数但未传给 `shutil.copy`，参数形同虚设。

**修复**：将 `follow_symlinks` 正确传递给 `shutil.copy`。

## 5. [BUG] `compress/tarfile.py` — `_check_progress_available` 未做 None 检查

**问题**：当 `mode` 不是 `"r"` 时，`self._progress` 为 `None`，调用会抛出 `AttributeError`。

**修复**：增加 `if self._progress is None: return False` 判断。

## 6. [优化] `funos.py` — 冗余的 `os.path.exists` 检查

**问题**：`os.makedirs(path, exist_ok=True)` 本身已处理目录存在的情况，额外的 `os.path.exists` 检查是多余的，且存在 TOCTOU 竞态问题。

**修复**：移除冗余的 `os.path.exists` 检查。

## 7. [优化] `compress/allfile.py` — 归档后缀匹配不完整

**问题**：原后缀 `(".tar", ".gz", ".tz")` 覆盖不全，`.gz` 不一定是 tar 归档，缺少 `.tgz`、`.tar.gz`、`.tar.bz2`、`.tar.xz` 等常见格式。

**修复**：替换为完整的后缀元组 `(".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".txz")`。

## 8. [优化] `compress/tarfile.py` — `open` 遮蔽内建函数

**问题**：模块级别将 `open` 重定义为 `TarFile.open`，遮蔽了 Python 内建 `open()` 函数。

**修复**：重命名为 `tar_open`。

## 9. [优化] `compress/tarfile.py` — `TarFile.__init__` 硬编码所有参数

**问题**：显式列出了 `tarfile.TarFile.__init__` 的所有参数，当 Python 版本变化时容易出错。

**修复**：改为 `def __init__(self, name=None, mode="r", fileobj=None, **kwargs)`。

## 10. [优化] `compress/tarfile.py` — `tar_size` 可简化

**问题**：手动累加循环可用更简洁的表达式替代。

**修复**：改为 `return sum(m.size for m in self.getmembers())`。

## 11. [优化] `compress/utils.py` — `get_size` 缺少路径不存在的处理

**问题**：当 `path` 不存在时会抛出不友好的 `FileNotFoundError`。

**修复**：增加 `os.path.exists` 检查，抛出带路径信息的 `FileNotFoundError`。

## 12. [优化] `compress/__init__.py` — 导出不一致

**问题**：顶层 `__init__.py` 从 `compress` 导入了 `tarfile` 和 `zipfile`，但 `compress/__init__.py` 的 `__all__` 中未包含它们。

**修复**：在 `compress/__init__.py` 中补上 `tarfile` 和 `zipfile` 的导入和导出。
