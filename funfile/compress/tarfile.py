import io
import os
import tarfile

from funfile.compress.utils import file_tqdm_bar
from tqdm import tqdm


class ProgressFileIO(io.FileIO):
    def __init__(self, path, progress=None, *args, **kwargs):
        super(ProgressFileIO, self).__init__(path, *args, **kwargs)
        self._progress = progress

    def read(self, size=None) -> bytes:
        self._progress.update(self.tell() - self._progress.n)
        return io.FileIO.read(self, size)


class FileWrapper(object):
    def __init__(self, fileobj, progress: tqdm, *args, **kwargs):
        super(FileWrapper, self).__init__(*args, **kwargs)
        self._fileobj = fileobj
        self._progress = progress

    def _update(self, current):
        if self._progress is not None:
            if current > self._progress.total:
                self._progress.total = current
            self._progress.update(current - self._progress.n)

    def read(self, size=-1):
        self._update(self._fileobj.tell())
        return self._fileobj.read(size)

    def readline(self, size=-1):
        self._update(self._fileobj.tell())
        return self._fileobj.readline(size)

    def __getattr__(self, name):
        return getattr(self._fileobj, name)

    def __del__(self):
        self._update(0)


class TarFile(tarfile.TarFile):
    def __init__(
        self,
        name=None,
        mode="r",
        fileobj=None,
        format=None,
        tarinfo=None,
        dereference=None,
        ignore_zeros=None,
        encoding=None,
        errors="surrogateescape",
        pax_headers=None,
        debug=None,
        errorlevel=None,
        copybufsize=None,
    ):
        self._progress = None
        if "r" in mode:
            self._progress = file_tqdm_bar(name, prefix="解压")
            if fileobj is not None:
                fileobj = FileWrapper(fileobj, progress=self._progress)
            else:
                fileobj = ProgressFileIO(name, progress=self._progress)
        super(TarFile, self).__init__(
            name=name,
            mode=mode,
            fileobj=fileobj,
            format=format,
            tarinfo=tarinfo,
            dereference=dereference,
            ignore_zeros=ignore_zeros,
            encoding=encoding,
            errors=errors,
            pax_headers=pax_headers,
            debug=debug,
            errorlevel=errorlevel,
            copybufsize=copybufsize,
        )
        if "r" in mode and self._progress is not None:
            self._progress.total = self.tar_size()

    def _check_progress_available(self) -> bool:
        return self._progress.n < self._progress.total

    def addfile(self, tarinfo, fileobj=None):
        if fileobj is not None:
            fileobj = FileWrapper(fileobj, progress=self._progress)
        return super(TarFile, self).addfile(tarinfo, fileobj)

    def add(self, name, arcname=None, recursive=True, filter=None, progress=None, *args, **kwargs):
        self._progress = progress or self._progress or file_tqdm_bar(name)
        return super(TarFile, self).add(name=name, arcname=arcname, recursive=recursive, filter=filter)

    def tar_size(self):
        size = 0
        for member in self.getmembers():
            size += member.size
        return size


open:TarFile = TarFile.open


def file_entar(src_path, dst_path=None):
    if dst_path is None:
        dst_path = src_path + ".tar"
    with open(dst_path, "w:xz") as fw:
        fw.add(src_path, arcname=os.path.basename(src_path))
    return dst_path


def file_detar(src_path, dst_path=None):
    if dst_path is None:
        dst_path = os.path.dirname(src_path)
    with open(src_path, "r:xz") as fr:
        fr.extractall(path=dst_path)
    return os.path.join(dst_path, os.listdir(dst_path)[0])
