from os import PathLike
from queue import Queue
from threading import Lock, Thread
from types import TracebackType
from typing import Any, Literal

from farlog import get_logger

_STOP = object()


class ConcurrentWriteFile:
    """通过后台线程按队列顺序写入文件。

    Args:
        filepath: 目标文件路径。
        mode: 文件打开模式。
        capacity: 待写队列最大容量。
        timeout: 为兼容旧接口保留，当前不使用。
    """

    def __init__(
        self,
        filepath: str | PathLike[str],
        mode: str = "w",
        capacity: int = 200,
        timeout: float = 3,
    ) -> None:
        self.filepath = filepath
        self.mode = mode
        self.timeout = timeout  # 为兼容旧接口保留。
        self._write_queue: Queue[Any] = Queue(capacity)
        self._state_lock = Lock()
        self._error: Exception | None = None
        self._logging_error: Exception | None = None
        self._closed = False
        self._file = open(filepath, mode)  # noqa: SIM115 - 由 worker 关闭
        self._thread = Thread(target=self._write, daemon=True)
        self._thread.start()

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            if self._logging_error is not None:
                raise self._error from self._logging_error
            raise self._error

    def write(self, chunk: str | bytes, offset: int | None = None) -> int:
        """将数据加入写队列。

        Args:
            chunk: 与打开模式匹配的文本或字节数据。
            offset: 写入前定位的字节偏移；为空时从当前位置写入。

        Returns:
            加入队列的数据长度。
        """
        with self._state_lock:
            if self._closed:
                raise ValueError("write to closed file")
            self._raise_if_failed()
            self._write_queue.put((offset, chunk))
        return len(chunk)

    def _write(self) -> None:
        while True:
            item = self._write_queue.get()
            try:
                if item is _STOP:
                    break
                offset, chunk = item
                if offset is not None:
                    self._file.seek(offset)
                self._file.write(chunk)
            except Exception as exc:  # noqa: BLE001 - 传递后台线程错误
                if self._error is None:
                    self._error = exc
                try:
                    get_logger("funfile").exception(f"write error: {exc}")
                except Exception as logging_exc:  # noqa: BLE001 - 记录日志失败也需保留
                    self._logging_error = logging_exc
            finally:
                self._write_queue.task_done()

        try:
            self._file.close()
        except Exception as exc:  # noqa: BLE001 - 传递关闭错误
            if self._error is None:
                self._error = exc

    def close(self) -> None:
        """等待队列写完、关闭文件，并传播后台错误。"""
        with self._state_lock:
            if not self._closed:
                self._closed = True
                self._write_queue.put(_STOP)
        self._write_queue.join()
        self._thread.join()
        self._raise_if_failed()

    def wait_for_all_done(self) -> None:
        """等待当前队列清空，并传播后台错误。"""
        self._write_queue.join()
        self._raise_if_failed()

    def empty(self) -> bool:
        """返回待写队列是否为空。"""
        return self._write_queue.empty()

    def __enter__(self) -> "ConcurrentWriteFile":  # noqa: PYI034 - Python 3.10
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False


ConcurrentFile = ConcurrentWriteFile
