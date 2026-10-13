import warnings
from os import PathLike
from queue import Queue
from threading import Condition, Thread
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
        timeout: 已废弃且不生效，显式传入会触发 `DeprecationWarning`。
    """

    def __init__(
        self,
        filepath: str | PathLike[str],
        mode: str = "w",
        capacity: int = 200,
        timeout: float | None = None,
    ) -> None:
        if timeout is not None:
            warnings.warn(
                "ConcurrentWriteFile 的 timeout 参数不生效，已废弃，将在 2.0 移除",
                DeprecationWarning,
                stacklevel=2,
            )
        self.filepath = filepath
        self.mode = mode
        self.timeout = 3 if timeout is None else timeout  # 为兼容旧接口保留。
        self._write_queue: Queue[Any] = Queue(capacity)
        # 既当状态锁又当信号量：`close()` 要等那些已通过关闭检查、但还没真正
        # 入队的写入落地，见 `write()` 的说明。
        self._state = Condition()
        self._error: Exception | None = None
        self._logging_error: Exception | None = None
        self._closed = False
        self._enqueueing = 0
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

        队列满时 `put` 会阻塞，所以这一步刻意放在锁外：否则一个被队列挡住的写入
        会把其他线程的 `write()` 和 `close()` 一起堵在状态锁上，而它们本来互不相关。

        代价是通过关闭检查到真正入队之间有个窗口，期间 `close()` 可能已经把结束
        标记放进队列，数据就会排在标记后面被丢掉。所以用 `_enqueueing` 记下这些
        在途写入，`close()` 等它们落地后再发结束标记。

        Args:
            chunk: 与打开模式匹配的文本或字节数据。
            offset: 写入前定位的字节偏移；为空时从当前位置写入。

        Returns:
            加入队列的数据长度。

        Raises:
            ValueError: 文件已关闭。
        """
        with self._state:
            if self._closed:
                raise ValueError("write to closed file")
            self._raise_if_failed()
            self._enqueueing += 1
        try:
            self._write_queue.put((offset, chunk))
        finally:
            with self._state:
                self._enqueueing -= 1
                self._state.notify_all()
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
        """等待队列写完、关闭文件，并传播后台错误。

        重复调用是安全的。

        Raises:
            Exception: 后台线程写入或关闭文件时的首个异常。
        """
        with self._state:
            first_close = not self._closed
            self._closed = True
            if first_close:
                # 先挡住新的写入，再等在途的那些入队，结束标记才不会插到数据前面。
                # 队列由 worker 持续消费，所以这个等待一定会结束。
                while self._enqueueing:
                    self._state.wait()
        if first_close:
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
