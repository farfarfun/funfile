import os.path
import pickle
import time
from queue import Queue
from threading import Thread


class ConcurrentWriteFile:
    def __init__(self, filepath, mode="w", capacity=200, timeout=3):
        self.filepath = filepath
        self.mode = mode
        self.timeout = timeout
        self._write_queue = Queue(capacity)
        self._close = False
        self._writen_data = []
        self._writen_size = 0
        self._flush_size = 0
        thread = Thread(target=self._write)
        thread.start()

    def write(self, chunk, offset=None):
        self._write_queue.put((offset, chunk))
        size = len(chunk)
        self.curser_add(offset, size)
        return size

    def _write(self):
        with open(self.filepath, self.mode) as fw:
            while True:
                try:
                    offset, chunk = self._write_queue.get(timeout=self.timeout)
                    if offset is not None:
                        fw.seek(offset)
                    self._writen_size += fw.write(chunk)
                    self._write_queue.task_done()
                    if self._writen_size > self._flush_size + 50 * 1024 * 1024:
                        fw.flush()
                        self._flush_size = self._writen_size
                        self.curser_merge()
                except Exception as e:
                    pass
                if self._close:
                    break
            fw.flush()
            self._flush_size = self._writen_size
            self.curser_merge()

    def close(self):
        self._close = True

    def wait_for_all_done(self):
        self._write_queue.join()

    def empty(self):
        return self._write_queue.empty()

    def curser_add(self, offset, size):
        if offset is None:
            return
        for record in self._writen_data:
            if record[0] <= offset <= record[1]:
                if record[1] < offset + size:
                    record[1] = offset + size
                return
        self._writen_data.append([offset, offset + size])
        return

    def _process_filepath(self):
        return f"{self.filepath}.process"

    def curser_merge(self):
        self._writen_data.sort(key=lambda x: x[0])
        merged = []
        for interval in self._writen_data:
            if not merged or interval[0] > merged[-1][1]:
                merged.append(interval)
            else:
                merged[-1][1] = max(merged[-1][1], interval[1])
        self._writen_data = merged
        with open(self._process_filepath(), 'wb') as fw:
            pickle.dump(self._writen_data, fw)

    def __enter__(self):
        self._handle = self
        if os.path.exists(self._process_filepath()):
            with open(self._process_filepath(), 'rb') as fr:
                self._writen_data = pickle.load(fr)
        return self._handle

    def __exit__(self, exc_type, exc_val, exc_tb):
        while not self.empty():
            time.sleep(1)
        self.wait_for_all_done()
        self.close()
        if os.path.exists(self._process_filepath()):
            os.remove(self._process_filepath())
        return True


class ConcurrentFile(ConcurrentWriteFile):
    def __init__(self, *args, **kwargs):
        super(ConcurrentFile, self).__init__(*args, **kwargs)
