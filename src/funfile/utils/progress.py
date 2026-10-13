"""把字节级进度叠加到任意二进制流上的包装器。

压缩和解压的进度条都是靠包住某个流、在 `read`/`write` 时顺带 `update` 实现的。
tar 和 zip 需要的组合不同（读/写、绝对位置/累加），但差别只有两三行，所以统一
放在这里，由参数区分，避免每种归档格式各写一个几乎一样的包装类。
"""

import io
from types import TracebackType
from typing import Any, TypeVar

# 代替 `typing.Self`：后者要 Python 3.11，本项目最低支持 3.10。
_Wrapper = TypeVar("_Wrapper", bound="_ProgressWrapper")


class _ProgressWrapper:
    """按属性代理底层流，并提供上下文管理协议。

    `__getattr__` 只在实例属性查不到时触发，而 `with` 语句走的是类型查找，
    所以 `__enter__`/`__exit__` 必须显式写出来——`zipfile` 内部就是用
    `with self.open(member) as source` 读成员的。

    Args:
        fileobj: 被包装的二进制流。
        progress: 提供 `n`/`update()` 的进度条，为 `None` 时所有进度更新都跳过。
    """

    def __init__(self, fileobj: Any, progress: Any) -> None:
        self._fileobj = fileobj
        self._progress = progress

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fileobj, name)

    # PYI019 要求改用 `typing.Self`，但它要 Python 3.11，而 3.10 上只能从
    # typing_extensions 取——为一个返回标注加运行时依赖不值得，故沿用 TypeVar。
    def __enter__(self: _Wrapper) -> _Wrapper:  # noqa: PYI019
        self._fileobj.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Any:
        return self._fileobj.__exit__(exc_type, exc_val, exc_tb)


class ProgressReader(_ProgressWrapper):
    """为可读二进制流叠加读取进度。

    两种计数方式：

    - `absolute=True`：进度跟随 `tell()` 的绝对位置，适合「一个流对应一个进度条」
      的场景（如读取整个归档文件），即使底层发生定位也不会重复计数。流不可定位
      时自动退化为累加——`r|*` 允许传入管道、socket、HTTP 响应体，标准库对此是
      支持的，所以不能假设 `tell()` 一定可用。
    - `absolute=False`（默认）：累加已读字节数，适合「多个流共享一个进度条」的
      场景（如把多个成员写进同一个归档），否则每换一个文件进度都会被重置。

    Args:
        fileobj: 可读取的二进制流，不要求支持定位。
        progress: 提供 `n`/`update()` 的进度条，可为 `None`。
        absolute: 是否按绝对位置计数，默认累加。
    """

    def __init__(self, fileobj: Any, progress: Any, *, absolute: bool = False) -> None:
        super().__init__(fileobj, progress)
        self._read_bytes = 0
        self._absolute = absolute and self._probe_seekable()

    def _probe_seekable(self) -> bool:
        """探测底层流能否 `tell()`，不可定位时返回 False。"""
        try:
            self._fileobj.tell()
        except (AttributeError, io.UnsupportedOperation, OSError):
            return False
        return True

    def _advance(self, count: int) -> None:
        """按本次读取的字节数推进进度。"""
        if self._progress is None:
            return
        self._read_bytes += count
        if not self._absolute:
            self._progress.update(count)
            return
        current = self._fileobj.tell()
        if current > self._progress.n:
            self._progress.update(current - self._progress.n)

    def read(self, size: int = -1) -> bytes:
        """读取字节并同步进度。"""
        data = self._fileobj.read(size)
        self._advance(len(data))
        return data

    def readline(self, size: int = -1) -> bytes:
        """读取一行并同步进度。"""
        data = self._fileobj.readline(size)
        self._advance(len(data))
        return data

    def readinto(self, buffer: Any) -> int | None:
        """读取到缓冲区并同步进度。"""
        size = self._fileobj.readinto(buffer)
        self._advance(size or 0)
        return size


class ProgressWriter(_ProgressWrapper):
    """为可写二进制流叠加写入进度，按写入字节数累加。

    Args:
        fileobj: 可写入的二进制流。
        progress: 提供 `update()` 的进度条，可为 `None`。
    """

    def write(self, data: Any) -> int:
        """写入字节并同步进度。"""
        written = self._fileobj.write(data)
        # 部分实现返回 None，此时以入参长度为准。
        count = len(data) if written is None else written
        if self._progress is not None:
            self._progress.update(count)
        return count
