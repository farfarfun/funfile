from queue import Queue
from threading import Lock, Thread

from farlog import get_logger

_STOP = object()


class ConcurrentWriteFile:
    def __init__(self, filepath, mode="w", capacity=200, timeout=3):
        self.filepath = filepath
        self.mode = mode
        self.timeout = timeout  # Kept for API compatibility.
        self._write_queue = Queue(capacity)
        self._state_lock = Lock()
        self._error = None
        self._closed = False
        self._file = open(filepath, mode)  # noqa: SIM115 - closed by the worker
        self._thread = Thread(target=self._write, daemon=True)
        self._thread.start()

    def _raise_if_failed(self):
        if self._error is not None:
            raise self._error

    def write(self, chunk, offset=None):
        with self._state_lock:
            if self._closed:
                raise ValueError("write to closed file")
            self._raise_if_failed()
            self._write_queue.put((offset, chunk))
        return len(chunk)

    def _write(self):
        while True:
            item = self._write_queue.get()
            try:
                if item is _STOP:
                    break
                offset, chunk = item
                if offset is not None:
                    self._file.seek(offset)
                self._file.write(chunk)
            except Exception as exc:  # noqa: BLE001 - propagate background failures
                if self._error is None:
                    self._error = exc
                try:
                    get_logger("funfile").exception(f"write error: {exc}")
                except Exception:  # noqa: BLE001, S110 - logging must not stop writes
                    pass
            finally:
                self._write_queue.task_done()

        try:
            self._file.close()
        except Exception as exc:  # noqa: BLE001 - propagate close failures
            if self._error is None:
                self._error = exc

    def close(self):
        with self._state_lock:
            if not self._closed:
                self._closed = True
                self._write_queue.put(_STOP)
        self._write_queue.join()
        self._thread.join()
        self._raise_if_failed()

    def wait_for_all_done(self):
        self._write_queue.join()
        self._raise_if_failed()

    def empty(self):
        return self._write_queue.empty()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


ConcurrentFile = ConcurrentWriteFile
